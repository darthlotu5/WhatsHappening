import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from EventScrapper.models import (
    Collection, IncompleteScan, SourceBlocked, SourceError, event_from_fields,
)
from EventScrapper.providers.structured import object_nodes


MAX_PAGES = 100
MAX_DETAILS = 1000
KB_API = "https://api-v3.kenyabuzz.com"
EVENTBRITE_LISTING = "https://www.eventbrite.com/d/kenya/all-events/"
RADA_WIDGET = "https://nightlife.rada.club/articles/top-dj-events-nairobi-this-month"


def _json(response, label):
    try:
        value = response.json()
    except (ValueError, TypeError) as error:
        raise SourceError(f"{label} did not return valid JSON") from error
    if not isinstance(value, dict):
        raise SourceError(f"{label} returned an unexpected JSON root")
    return value


def _kb_rows(data):
    for row in object_nodes(data):
        if "event_id" in row:
            yield row


def _record(events, notes, source, *, extra=None, quality_notes=None, **fields):
    try:
        if not fields.get("native_id"):
            raise ValueError("Missing stable source event ID")
        if not fields.get("url"):
            raise ValueError("Missing event URL")
        event = event_from_fields(source, **fields)
    except (ValueError, TypeError, KeyError) as error:
        notes.append(f"{fields.get('native_id', 'unknown event')}: {error}")
        return
    if quality_notes is not None:
        if not fields.get("location"):
            quality_notes.append(f"{fields['native_id']}: source location is unknown; event retained")
        if (not fields.get("end") and not fields.get("date_tba")
                and not fields.get("date_unconfirmed")):
            quality_notes.append(f"{fields['native_id']}: source did not expose an optional end datetime")
        if event.get("date_warning"):
            quality_notes.append(f"{fields['native_id']}: {event['date_warning']}")
    event.update(extra or {})
    previous = events.get(event["id"])
    if previous is not None and previous != event:
        notes.append(f"Conflicting duplicate event ID {event['id']}")
        return
    events[event["id"]] = event


def _kb_detail(client, source, row, events, notes, quality_notes):
    native_id = row.get("event_id")
    slug = row.get("event_slug") or row.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[\w-]+", slug):
        notes.append(f"{native_id}: missing or invalid KenyaBuzz event slug/ID")
        return
    data = _json(client.get(f"{KB_API}/events/details/view/{slug}", detail=True),
                 f"KenyaBuzz detail {slug}")
    payload = data.get("data")
    if data.get("response") != "success" or not isinstance(payload, dict):
        notes.append(f"{native_id}: KenyaBuzz detail did not return successful event data")
        return
    detail = payload.get("event_details")
    dates = payload.get("event_dates")
    if (not isinstance(detail, dict) or not detail.get("event_id")
            or (native_id is not None and str(detail.get("event_id")) != str(native_id))):
        notes.append(f"{native_id}: missing or mismatched KenyaBuzz detail identity")
        return
    native_id = detail["event_id"]
    fields = {
        "name": detail.get("event_name") or row.get("event_name") or row.get("name"),
        "url": f"https://www.kenyabuzz.com/events/event/{slug}",
        "image": detail.get("poster") or row.get("event_poster") or row.get("poster") or "",
        "country": "KE",
    }
    if fields["image"] and not urlsplit(fields["image"]).scheme:
        fields["image"] = urljoin("https://static.kenyabuzz.com/public/",
                                 fields["image"].lstrip("/"))
    if not isinstance(dates, list):
        notes.append(f"{native_id}: event_dates is missing or not an array")
        return
    if dates:
        for occurrence in dates:
            if not isinstance(occurrence, dict) or not occurrence.get("event_date_id"):
                notes.append(f"{native_id}: occurrence is missing its stable event_date_id")
                continue
            day = occurrence.get("ed_date")
            start = occurrence.get("event_start_time")
            end = occurrence.get("event_end_time")
            if not day or not start:
                notes.append(f"{native_id}: occurrence missing date or start time")
                continue
            _record(
                events, notes, source, **fields,
                quality_notes=quality_notes,
                native_id=f"{native_id}-{occurrence['event_date_id']}",
                location=occurrence.get("location_name") or detail.get("default_location"),
                start=f"{day} {start}", end=f"{day} {end}" if end else None,
                status=occurrence.get("performance_status"),
            )
    else:
        _record(
            events, notes, source, **fields, native_id=str(native_id),
            quality_notes=quality_notes,
            location=detail.get("default_location") or row.get("event_location"),
            start=detail.get("start_date"), end=detail.get("end_date"),
        )


def kenyabuzz(client, source):
    landing = _json(client.get(f"{KB_API}/events/list/landing"), "KenyaBuzz landing")
    if landing.get("response") != "success" or not isinstance(landing.get("data"), dict):
        raise SourceError("KenyaBuzz landing has no successful data object")
    if not isinstance(landing["data"].get("all_events"), list):
        raise SourceError("KenyaBuzz landing is missing its all_events array")
    for row in object_nodes(landing["data"]):
        if ("event_name" in row or "event_slug" in row) and not row.get("event_id"):
            raise IncompleteScan("KenyaBuzz landing contains an event without event_id")
    records = {str(r["event_id"]): r for r in _kb_rows(landing["data"])}
    seen_pages = set()
    for page in range(1, MAX_PAGES + 1):
        data = _json(client.get(f"{KB_API}/events/list/all-events/{page}"),
                     f"KenyaBuzz page {page}")
        rows = data.get("data")
        if data.get("response") != "success" or not isinstance(rows, list):
            raise SourceError(f"KenyaBuzz page {page} has no successful event array")
        if not rows:
            break
        if any(not isinstance(row, dict) or not row.get("event_id") for row in rows):
            raise IncompleteScan(f"KenyaBuzz page {page} contains records without event IDs")
        signature = tuple(str(row["event_id"]) for row in rows)
        if signature in seen_pages:
            raise IncompleteScan("KenyaBuzz pagination repeated a nonempty page")
        seen_pages.add(signature)
        records.update({str(row["event_id"]): row for row in rows})
    else:
        raise IncompleteScan("KenyaBuzz pagination safety limit reached before an empty page")
    known_slugs = {r.get("event_slug") or r.get("slug") for r in records.values()}
    for row in landing["data"].get("this_weekend", []):
        if isinstance(row, dict) and row.get("module") == "event":
            slug = row.get("slug")
            if not slug:
                raise IncompleteScan("KenyaBuzz weekend event has no detail slug")
            if slug not in known_slugs:
                records[f"slug:{slug}"] = row
                known_slugs.add(slug)
    if not records:
        if any(landing["data"].get(key) for key in ("today", "featured_events", "all_events")):
            raise IncompleteScan("KenyaBuzz landing has events without identifiable event IDs")
        return Collection(notes=["KenyaBuzz landing and first numbered page explicitly empty"])
    if len(records) > MAX_DETAILS:
        raise IncompleteScan("KenyaBuzz detail safety limit reached")
    events, errors, quality_notes = {}, [], []
    for row in records.values():
        _kb_detail(client, source, row, events, errors, quality_notes)
    return Collection(
        list(events.values()), not errors,
        errors + quality_notes + [f"Unioned landing and {page} numbered pages, including terminal empty page; "
                  f"resolved {len(records)} parent events into {len(events)} occurrences"],
    )


def _eventbrite_data(text):
    soup = BeautifulSoup(text, "html.parser")
    for script in soup.find_all("script"):
        match = re.search(r"window\.__SERVER_DATA__\s*=\s*", script.get_text())
        if match:
            try:
                data, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():])
            except json.JSONDecodeError as error:
                raise SourceError("Eventbrite server data is invalid JSON") from error
            if isinstance(data, dict):
                return data
    raise SourceError("Eventbrite Kenya page has no recognizable server data")


def eventbrite(client, source):
    events, errors, quality_notes, totals, seen_pages = {}, [], [], set(), set()
    excluded = 0
    for page in range(1, MAX_PAGES + 1):
        url = EVENTBRITE_LISTING + (f"?page={page}" if page > 1 else "")
        data = _eventbrite_data(client.get(url).text)
        search_data = data.get("search_data")
        listing = search_data.get("events") if isinstance(search_data, dict) else None
        if not isinstance(listing, dict):
            raise SourceError(f"Eventbrite page {page} missing its event search object")
        rows, meta = listing.get("results"), listing.get("pagination")
        if not isinstance(rows, list) or not isinstance(meta, dict):
            raise SourceError(f"Eventbrite page {page} missing results/pagination")
        if meta.get("page_number") != page:
            raise IncompleteScan(f"Eventbrite returned wrong page number for page {page}")
        page_count = meta.get("page_count")
        total = meta.get("object_count")
        page_size = meta.get("page_size")
        if (not isinstance(page_count, int) or page_count < 0
                or not isinstance(page_size, int) or page_size <= 0
                or not isinstance(total, int) or total < 0):
            raise IncompleteScan("Eventbrite has invalid pagination totals")
        totals.add(total)
        signature = tuple(str(r.get("id")) for r in rows if isinstance(r, dict))
        if rows and signature in seen_pages:
            raise IncompleteScan("Eventbrite repeated a nonempty page")
        seen_pages.add(signature)
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f"Eventbrite page {page} contains a non-object record")
                continue
            native_id = row.get("eventbrite_event_id") or row.get("id")
            venue = row.get("primary_venue") or {}
            if not isinstance(venue, dict):
                errors.append(f"{native_id}: invalid Eventbrite venue object")
                continue
            address = venue.get("address") or {}
            if not isinstance(address, dict):
                errors.append(f"{native_id}: invalid Eventbrite venue address")
                continue
            country = address.get("country")
            if row.get("is_online_event") or (country and country != "KE"):
                excluded += 1
                continue
            if country != "KE":
                errors.append(f"{native_id}: Kenya location cannot be verified")
                continue
            start_day, start_clock = row.get("start_date"), row.get("start_time")
            end_day, end_clock = row.get("end_date"), row.get("end_time")
            if not start_day or not start_clock:
                errors.append(f"{native_id}: missing Eventbrite start date/time")
                continue
            image = row.get("image") or {}
            if not isinstance(image, dict):
                errors.append(f"{native_id}: invalid Eventbrite image object")
                continue
            original = image.get("original") or {}
            if not isinstance(original, dict):
                errors.append(f"{native_id}: invalid Eventbrite original image object")
                continue
            image = image.get("url") or original.get("url", "")
            _record(
                events, errors, source, native_id=str(native_id) if native_id else None,
                quality_notes=quality_notes,
                name=row.get("name"), location=venue.get("name") or address.get("localized_address_display"),
                start=f"{start_day}T{start_clock}",
                end=f"{end_day}T{end_clock}" if end_day and end_clock else None,
                timezone=row.get("timezone") or "Africa/Nairobi", image=image,
                url=row.get("url"), country=country,
                status="EventCancelled" if row.get("is_cancelled") else None,
            )
        if page >= page_count:
            if meta.get("continuation"):
                raise IncompleteScan("Eventbrite final page still exposes a continuation")
            break
        if not rows or not meta.get("continuation"):
            raise IncompleteScan("Eventbrite pagination ended before its advertised final page")
    else:
        raise IncompleteScan("Eventbrite pagination safety limit reached")
    if len(totals) > 1:
        errors.append("Eventbrite result totals changed during traversal; snapshot is not atomic")
    if len(events) + excluded != total:
        errors.append(f"Eventbrite unique/filtered count differs from final advertised total {total}")
    return Collection(
        list(events.values()), not errors,
        errors + quality_notes + [f"Traversed {page} Kenya-only pages; excluded {excluded} online/non-Kenya rows"],
    )


def visitnairobi(client, source):
    raise SourceBlocked(
        "VisitNairobi live KenyaBuzz feed /api/pub.php?a=kb is disallowed by robots.txt. "
        "The permitted kb.js is an attributed 2026-10-15 KenyaBuzz snapshot, not an "
        "independent current catalogue; refusing stale duplicate publication."
    )


def rada_nightlife(client, source):
    text = BeautifulSoup(client.get(RADA_WIDGET).text, "html.parser").get_text(" ", strip=True)
    if "No matching events listed right now" in text:
        return Collection([], False, [
            "Rada Nightlife SSR DJ widget explicitly reports no matching events, but covers "
            "only DJ events this month. This verifies an empty subset, not an empty full "
            "catalogue; no verified public event pagination/API is exposed."
        ])
    raise SourceError("Rada Nightlife has no implemented, live-verified event data contract")


def onepassafrica(client, source):
    url = source.listing_url
    seen_urls, seen_pages = set(), set()
    events, errors, quality_notes, totals = {}, [], [], set()
    observed_cards = 0
    for page in range(1, MAX_PAGES + 1):
        if url in seen_urls:
            raise IncompleteScan("OnePass pagination cycles")
        if parse_qs(urlsplit(url).query).get("country") != ["KE"]:
            raise IncompleteScan("OnePass pagination lost the required country=KE filter")
        seen_urls.add(url)
        response = client.get(url)
        if parse_qs(urlsplit(response.url).query).get("country") != ["KE"]:
            raise IncompleteScan("OnePass response URL lost the required country=KE filter")
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select('a[href^="/api/discover/"][href$="/go"]')
        if not cards:
            raise SourceError(f"OnePass page {page} has no recognizable event cards")
        observed_cards += len(cards)
        signature = tuple(card["href"] for card in cards)
        if signature in seen_pages:
            raise IncompleteScan("OnePass repeated a nonempty card page")
        seen_pages.add(signature)
        page_text = soup.get_text(" ", strip=True)
        count = re.search(r"\b(\d+)\s+events\b", page_text, re.I)
        if count:
            totals.add(int(count[1]))
        indicator = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", page_text, re.I)
        if indicator and int(indicator[1]) != page:
            raise IncompleteScan("OnePass returned the wrong displayed page number")
        for card in cards:
            identity = re.fullmatch(r"/api/discover/([A-Za-z0-9_-]+)/go", card["href"])
            if not identity:
                errors.append(f"Invalid OnePass card URL: {card['href']}")
                continue
            native_id = identity[1]
            heading = card.find("h3")
            paragraphs = card.find_all("p")
            if len(paragraphs) < 2:
                errors.append(f"{native_id}: missing published OnePass date label or venue")
                continue
            date_label = paragraphs[0].get_text(" ", strip=True)
            if not date_label:
                errors.append(f"{native_id}: missing published OnePass date label")
                continue
            image = card.find("img")
            attribution = next(
                (span.get_text(" ", strip=True) for span in card.find_all("span")
                 if span.get_text(" ", strip=True).startswith("via ")),
                None,
            )
            extra = {}
            if attribution:
                extra["upstream_source"] = attribution[4:].strip()
            else:
                quality_notes.append(f"{native_id}: card has no upstream attribution")
            _record(
                events, errors, source, native_id=native_id,
                name=heading.get_text(" ", strip=True) if heading else None,
                location=paragraphs[1].get_text(" ", strip=True),
                url=urljoin(response.url, card["href"]),
                image=image.get("src", "") if image else "",
                start=None, end=None, date_label=date_label,
                date_unconfirmed=True, country="KE",
                extra=extra,
                quality_notes=quality_notes,
            )
        links = [a for a in soup.find_all("a", href=True)
                 if re.match(r"^Next\b", a.get_text(" ", strip=True))]
        if not links:
            if indicator and int(indicator[1]) < int(indicator[2]):
                errors.append("OnePass Next link missing before advertised final page")
            break
        if len(links) != 1:
            raise IncompleteScan("OnePass exposes ambiguous Next links")
        next_url = urljoin(response.url, links[0]["href"])
        if urlsplit(next_url).netloc != urlsplit(source.listing_url).netloc:
            raise IncompleteScan("OnePass Next link changes host")
        if parse_qs(urlsplit(next_url).query).get("page") != [str(page + 1)]:
            raise IncompleteScan("OnePass Next link skips or repeats a page number")
        url = next_url
    else:
        raise IncompleteScan("OnePass pagination safety limit reached")
    if len(totals) > 1:
        errors.append("OnePass advertised totals changed during traversal")
    if totals and len(events) not in totals:
        errors.append(f"OnePass emitted count {len(events)} differs from advertised totals {sorted(totals)}")
    errors.extend([
        "Published OnePass short date labels lack a verified year/end datetime. "
        "Dates remain unconfirmed (null timestamps), not TBA; no year was invented.",
        "Card URLs are opaque user-facing /api/discover/ links, not resolved canonical "
        "upstream event URLs. Robots-disallowed redirect/image API routes were not fetched.",
        f"Visited {len(seen_urls)} distinct listing pages; observed {observed_cards} cards; "
        f"emitted {len(events)} distinct events with upstream attribution when published.",
    ])
    return Collection(list(events.values()), False, errors + quality_notes)


def _schema_events(text):
    soup = BeautifulSoup(text, "html.parser")
    for script in soup.find_all("script"):
        if (script.get("type") or "").strip() != "application/ld+json":
            continue
        try:
            data = json.loads(script.get_text())
        except json.JSONDecodeError as error:
            raise SourceError("AllEvents returned invalid JSON-LD") from error
        for node in object_nodes(data):
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            is_event = isinstance(types, list) and any(
                isinstance(kind, str)
                and (kind.rsplit("/", 1)[-1].endswith("Event") or kind == "Festival")
                for kind in types
            )
            if is_event or ("startDate" in node and "name" in node):
                yield node


def allevents(client, source):
    rows = list(_schema_events(client.get(source.listing_url).text))
    if not rows:
        raise SourceError("AllEvents Nairobi HTML contains no recognizable event JSON-LD")
    urls, errors, quality_notes, events = {}, [], [], {}
    for row in rows:
        url = row.get("url")
        parts = urlsplit(url or "")
        match = re.fullmatch(r"/nairobi/[^/]+/(\d+)/?", parts.path)
        if parts.hostname != "allevents.in" or not match:
            errors.append(f"AllEvents event has a missing/non-Nairobi canonical URL: {url}")
            continue
        urls[url] = match[1]
    if len(urls) > MAX_DETAILS:
        raise IncompleteScan("AllEvents detail safety limit reached")
    for url, native_id in urls.items():
        details = [r for r in _schema_events(client.get(url, detail=True).text)
                   if r.get("url") == url]
        if len(details) != 1:
            errors.append(f"{native_id}: missing or ambiguous AllEvents detail event schema")
            continue
        row = details[0]
        location = row.get("location") or {}
        address = location.get("address") or {} if isinstance(location, dict) else {}
        country = address.get("addressCountry") if isinstance(address, dict) else None
        if country not in ("KE", "Kenya"):
            errors.append(f"{native_id}: AllEvents detail does not verify a Kenya location")
            continue
        start, end = row.get("startDate"), row.get("endDate")
        if not isinstance(start, str) or "T" not in start:
            errors.append(f"{native_id}: AllEvents detail has no precise start datetime")
            continue
        image = row.get("image") or ""
        if isinstance(image, list):
            image = image[0] if image else ""
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl") or ""
        _record(
            events, errors, source, native_id=native_id, name=row.get("name"),
            quality_notes=quality_notes,
            start=start, end=end, url=url, image=image, country="KE",
            location=location.get("name") if isinstance(location, dict) else location,
            status=row.get("eventStatus"),
        )
    errors.append(
        "AllEvents HTML is only a partial catalogue. Verified client pagination requires "
        "POST /api/index.php/categorization/web/v1/list; the public research probe returned "
        "'Not available at this moment' instead of JSON. No complete API traversal verified."
    )
    return Collection(list(events.values()), False, errors + quality_notes)


ADAPTERS = {
    "kenyabuzz": kenyabuzz,
    "visitnairobi": visitnairobi,
    "eventbrite": eventbrite,
    "rada-nightlife": rada_nightlife,
    "onepassafrica": onepassafrica,
    "allevents": allevents,
}
