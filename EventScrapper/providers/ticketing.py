"""Adapters based on public ticketing pages observed on 27 October 2026.

Mookh, mTickets and Hustle disallow their APIs: only their published sitemaps
and permitted detail HTML are used. Sitemap coverage is not API completeness.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from EventScrapper.models import (
    Collection, IncompleteScan, SourceBlocked, SourceError, event_from_fields,
)
from EventScrapper.providers.structured import next_payloads, object_nodes


MAX_PAGES = 100
MAX_DETAILS = 1000
MAX_SITEMAPS = 30
TIPSI_BASE = "https://www.tipsitickets.com"
HUSTLE_BASE = "https://www.hustle.events"
TBA_LABEL = re.compile(r"^(?:(?:event\s+)?date\s*:?\s*)?(?:tba|to be announced)$", re.I)


def _get(client, url, *, detail=False):
    response = client.get(url, detail=detail)
    if response.status_code in (401, 403, 429):
        raise SourceBlocked(f"{url}: HTTP {response.status_code}; no bypass attempted")
    if response.status_code != 200:
        raise SourceError(f"{url}: HTTP {response.status_code}")
    return response


class _Scan:
    def __init__(self, source):
        self.source = source
        self.events = {}
        self.notes = []
        self.complete = True

    def issue(self, message):
        self.complete = False
        self.notes.append(str(message))

    def record(self, **fields):
        label = fields.get("native_id") or fields.get("url") or "unknown event"
        start = fields.get("start")
        explicit_tba = fields.get("date_tba", False)
        if isinstance(start, str) and TBA_LABEL.fullmatch(start.strip()):
            fields["start"] = start = None
            explicit_tba = True
        if start is None or isinstance(start, str) and not start.strip():
            fields["start"] = None
            fields["date_tba"] = True
            if not explicit_tba:
                self.issue(f"{label}: start datetime absent; retained as TBA, public announcement unverified")
        if isinstance(fields.get("end"), str) and TBA_LABEL.fullmatch(fields["end"].strip()):
            fields["end"] = None
        try:
            if not fields.get("url"):
                raise ValueError("missing event URL")
            event = event_from_fields(self.source, **fields)
        except (ValueError, TypeError, KeyError) as error:
            self.issue(f"{label}: {error}")
            return
        for field in ("location", "image"):
            if not fields.get(field):
                self.issue(f"{label}: source did not expose {field}")
        if not fields.get("end"):
            event["end_date"] = None
            if event.get("date_status") != "to_be_announced":
                self.notes.append(f"{label}: source did not expose an optional end datetime")
        if event.get("date_warning"):
            self.issue(f"{label}: {event['date_warning']}")
        if event["id"] in self.events and self.events[event["id"]] != event:
            self.issue(f"Conflicting duplicate event ID {event['id']}")
            return
        self.events[event["id"]] = event

    def result(self):
        return Collection(list(self.events.values()), self.complete, self.notes)


def _rsc_values(soup, key):
    for value in next_payloads(str(soup)):
        for obj in object_nodes(value):
            if key in obj:
                yield obj[key]


def _event_ld(soup, url):
    candidates = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in object_nodes(data):
            types = obj.get("@type", [])
            if types == "Event" or isinstance(types, list) and "Event" in types:
                candidates.append(obj)
    for obj in candidates:
        if obj.get("url") and urljoin(url, obj["url"]).rstrip("/") == url.rstrip("/"):
            return obj
    if len(candidates) == 1:
        return candidates[0]
    raise SourceError(f"{url}: missing or ambiguous public Event JSON-LD")


def _location(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    name = value.get("name") or ""
    address = value.get("address") or ""
    if isinstance(address, dict):
        address = ", ".join(str(address[k]) for k in ("streetAddress", "addressLocality")
                            if address.get(k))
    return ", ".join(dict.fromkeys(str(x) for x in (name, address) if x))


def _image(value, base):
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or ""
    return urljoin(base, value) if isinstance(value, str) and value else ""


def _ld_fields(data, url, soup=None):
    fields = {
        "name": data.get("name"), "start": data.get("startDate"),
        "end": data.get("endDate"), "url": data.get("url") or url,
        "location": _location(data.get("location")),
        "image": _image(data.get("image"), url), "status": data.get("eventStatus"),
    }
    if not fields["start"] and soup is not None:
        fields["date_tba"] = any(
            node.parent.name not in ("script", "style")
            and TBA_LABEL.fullmatch(str(node).strip())
            for node in soup.find_all(string=True)
        )
    return fields


def _expired_signed_image(image):
    params = parse_qs(urlsplit(image).query, keep_blank_values=True)
    if "X-Amz-Date" not in params and "X-Amz-Expires" not in params:
        return False
    try:
        if len(params["X-Amz-Date"]) != 1 or len(params["X-Amz-Expires"]) != 1:
            return True
        signed = datetime.strptime(params["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ")
        expires = int(params["X-Amz-Expires"][0])
        return expires <= 0 or signed.replace(tzinfo=timezone.utc).timestamp() + expires <= time.time()
    except (KeyError, ValueError, OverflowError):
        return True


def _sitemap_detail(client, url, scan):
    response = _get(client, url, detail=True)
    soup = BeautifulSoup(response.text, "html.parser")
    fields = _ld_fields(_event_ld(soup, response.url), response.url, soup)
    # mTickets signatures expire sooner than the shared adaptive detail cache.
    if _expired_signed_image(fields["image"]) and getattr(response, "from_cache", False):
        response = _get(client, url, detail=False)
        soup = BeautifulSoup(response.text, "html.parser")
        fields = _ld_fields(_event_ld(soup, response.url), response.url, soup)
    if _expired_signed_image(fields["image"]):
        scan.issue(f"{url}: public detail HTML contains an expired or malformed signed image URL")
        fields["image"] = ""
    return soup, fields


def _sitemap_urls(client, initial_url):
    queue, seen, locations = [initial_url], set(), []
    while queue:
        url = queue.pop(0)
        if url in seen:
            raise IncompleteScan(f"Repeated sitemap {url}")
        if len(seen) >= MAX_SITEMAPS:
            raise IncompleteScan("Sitemap traversal exceeded its safety bound")
        seen.add(url)
        response = _get(client, url)
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as error:
            raise SourceError(f"{url}: invalid XML sitemap") from error
        tag = root.tag.rsplit("}", 1)[-1]
        if tag not in ("urlset", "sitemapindex"):
            raise SourceError(f"{url}: unexpected sitemap root {tag}")
        entries = [node.text.strip() for node in root.iter()
                   if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]
        if tag == "sitemapindex":
            queue.extend(entries)
        else:
            locations.extend(entries)
    return list(dict.fromkeys(locations))


def _sitemap_source(client, source, base, candidate, *, hustle=False):
    scan = _Scan(source)
    locations = _sitemap_urls(client, base + "/sitemap.xml")
    urls = [url for url in locations if candidate(urlsplit(url).path.rstrip("/"))]
    if not urls:
        raise SourceError(f"{base}: sitemap exposed no event-detail candidates")
    if len(urls) > MAX_DETAILS:
        raise IncompleteScan(f"{base}: {len(urls)} sitemap details exceed safety bound")
    scan.issue("Coverage: published sitemap detail URLs, not a verified complete API catalog")
    for url in urls:
        try:
            soup, fields = _sitemap_detail(client, url, scan)
            fields["native_id"] = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            if hustle:
                embedded = next((value for value in _rsc_values(soup, "event")
                                 if isinstance(value, dict) and value.get("id")), None)
                if embedded:
                    fields["native_id"] = embedded["id"]
                    country = embedded.get("country")
                    fields["country"] = country
                    naive = any(
                        value and not re.search(r"(?:Z|[+-]\d\d:\d\d)$", str(value))
                        for value in (fields.get("start"), fields.get("end"))
                    )
                    if naive:
                        zones = {"KE": "Africa/Nairobi", "UG": "Africa/Kampala",
                                 "TZ": "Africa/Dar_es_Salaam", "RW": "Africa/Kigali",
                                 "ZA": "Africa/Johannesburg", "NG": "Africa/Lagos",
                                 "GH": "Africa/Accra"}
                        if country not in zones:
                            raise SourceError(f"{url}: cannot resolve naive event timezone for {country!r}")
                        fields["timezone"] = zones[country]
                else:
                    raise SourceError(f"{url}: missing native Hustle event/country payload")
            scan.record(**fields)
        except SourceBlocked:
            raise
        except SourceError as error:
            scan.issue(error)
    return scan.result()


def mookh(client, source):
    static = {"", "/calendar", "/about-us", "/features", "/partners", "/careers"}
    return _sitemap_source(
        client, source, "https://mookh.com",
        lambda path: path not in static and not path.startswith("/legal"),
    )


def mtickets(client, source):
    static = {"", "/events", "/travel", "/start-selling", "/tickets/faq",
              "/tickets/terms", "/tickets/privacy"}
    return _sitemap_source(
        client, source, "https://mtickets.com",
        lambda path: path not in static and not path.startswith(("/tickets/", "/travel/")),
    )


def hustlesasa(client, source):
    return _sitemap_source(
        client, source, HUSTLE_BASE,
        lambda path: path.startswith("/events/"), hustle=True,
    )


def _next_listing(soup, current, page, expected_path):
    pagination = soup.select_one(".pagination")
    if pagination is None:
        raise IncompleteScan(f"{current}: missing expected pagination/termination controls")
    anchor = pagination.select_one('a[rel="next"], a[aria-label="Next »"]')
    if anchor:
        target = urljoin(current, anchor.get("href") or "")
        parsed = urlsplit(target)
        params = parse_qs(parsed.query)
        number = params.get("page", [])
        if (parsed.path.rstrip("/") != expected_path
                or parsed.hostname != urlsplit(current).hostname
                or len(number) != 1 or not number[0].isdigit()
                or int(number[0]) != page + 1):
            raise IncompleteScan(f"{current}: invalid, repeated, or skipped next page {target}")
        return target
    for anchor in pagination.select("a[href]"):
        values = parse_qs(urlsplit(anchor["href"]).query).get("page", [])
        if values and values[0].isdigit() and int(values[0]) > page:
            raise IncompleteScan(f"{current}: next link missing before advertised final page")
    return None


def _listing_pages(client, initial, expected_path):
    url = initial
    seen_signatures = set()
    for page in range(1, MAX_PAGES + 1):
        response = _get(client, url)
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select(".event-card")
        if not cards:
            raise SourceError(f"{url}: expected event cards absent; empty catalog not established")
        signature = tuple(str(card) for card in cards)
        if signature in seen_signatures:
            raise IncompleteScan(f"{url}: repeated event listing page")
        seen_signatures.add(signature)
        next_url = _next_listing(soup, response.url, page, expected_path)
        yield response.url, cards
        if next_url is None:
            return
        url = next_url
    raise IncompleteScan(f"{initial}: pagination exceeded {MAX_PAGES} pages")


def tikiti(client, source):
    scan, seen = _Scan(source), set()
    for page_url, cards in _listing_pages(client, "https://tikiti.co.ke/", ""):
        additions = 0
        for card in cards:
            href = card.get("href") if card.name == "a" else None
            if not href or "/e/" not in urlsplit(urljoin(page_url, href)).path:
                scan.issue(f"{page_url}: event card missing its /e/ detail anchor")
                continue
            url = urljoin(page_url, href)
            if url in seen:
                continue
            seen.add(url)
            additions += 1
            if len(seen) > MAX_DETAILS:
                raise IncompleteScan("Tikiti detail count exceeded safety bound")
            try:
                response = _get(client, url, detail=True)
                soup = BeautifulSoup(response.text, "html.parser")
                fields = _ld_fields(_event_ld(soup, response.url), response.url, soup)
                scan.record(**fields, native_id=urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
            except SourceBlocked:
                raise
            except SourceError as error:
                scan.issue(error)
        if not additions:
            raise IncompleteScan(f"{page_url}: listing made no progress")
    return scan.result()


def _kenya_datetime(value):
    value = value.strip()
    return datetime.strptime(value, "%b %d, %Y %H:%M").isoformat() if value else None


def ticketskenya(client, source):
    scan, seen = _Scan(source), set()
    for page_url, cards in _listing_pages(
            client, "https://ticketskenya.com/all-events", "/all-events"):
        additions = 0
        for card in cards:
            title = card.select_one(".card-title")
            link = card.select_one('.card-title a[href*="/all-events/"]')
            if title is None or link is None:
                scan.issue(f"{page_url}: malformed TicketsKenya card title/detail URL")
                continue
            url = urljoin(page_url, link["href"])
            if url in seen:
                continue
            seen.add(url)
            additions += 1
            start = end = None
            try:
                for row in card.select(".card-body > div"):
                    text = row.get_text(" ", strip=True)
                    if text.startswith("Start At:"):
                        start = _kenya_datetime(text.split(":", 1)[1])
                    elif text.startswith("End At:"):
                        end = _kenya_datetime(text.split(":", 1)[1])
            except ValueError as error:
                scan.issue(f"{url}: invalid listing datetime: {error}")
                continue
            icon = card.select_one(".fa-map-location-dot")
            venue = icon.parent.select_one("span") if icon else None
            image = card.select_one("img[src]")
            scan.record(
                name=title.get_text(" ", strip=True), start=start, end=end, url=url,
                native_id=urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
                location=venue.get_text(" ", strip=True) if venue else "",
                image=_image(image["src"], page_url) if image else "",
            )
        if not additions:
            raise IncompleteScan(f"{page_url}: listing made no progress beyond repeated current events")
    return scan.result()


def _tip_json(client, url):
    response = _get(client, url)
    try:
        data = response.json()
    except (ValueError, TypeError) as error:
        raise SourceError(f"{url}: invalid public catalog JSON") from error
    if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
            or type(data.get("count")) is not int or data["count"] < 0
            or "next" not in data):
        raise SourceError(f"{url}: missing count/results/next catalog envelope")
    return data


def _tipsi_feed(client, scan, kind, size, ordering, status=None):
    seen, expected = set(), None
    for page in range(1, MAX_PAGES + 1):
        params = {"ordering": ordering, "page_size": size, "page": page}
        if status:
            params["status"] = status
        url = f"{TIPSI_BASE}/api/proxy/events/{kind}?" + urlencode(params)
        data = _tip_json(client, url)
        if expected is not None and data["count"] != expected:
            scan.issue(f"Tipsi {kind}: count changed during scan; snapshot is not atomic")
        expected = data["count"]
        added = 0
        for row in data["results"]:
            if not isinstance(row, dict) or not row.get("id"):
                scan.issue(f"Tipsi {kind} page {page}: event missing native UUID")
                continue
            identity = str(row["id"])
            if identity in seen:
                scan.issue(f"Tipsi {kind}: repeated event UUID {identity}")
                continue
            seen.add(identity)
            added += 1
            title = row.get("title") or ""
            if not isinstance(title, str):
                scan.issue(f"Tipsi {identity}: event title is not text")
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower().strip()).strip("-")
            if not slug:
                scan.issue(f"Tipsi {identity}: missing title/usable public event slug")
                continue
            event_url = f"{TIPSI_BASE}/event/{slug}?" + urlencode({"eventId": identity})
            variants = row.get("banner_variants") or {}
            if not isinstance(variants, dict):
                scan.issue(f"Tipsi {identity}: malformed banner_variants")
                variants = {}
            scan.record(
                name=title, start=row.get("start_at"), end=row.get("end_at"),
                url=event_url, native_id=identity,
                location=row.get("venue_name") or row.get("venue_location") or row.get("venue_text") or "",
                image=row.get("banner_url") or variants.get("portrait") or variants.get("square") or "",
            )
        next_url = data["next"]
        if next_url is None:
            if len(seen) != expected:
                scan.issue(f"Tipsi {kind}: terminal count mismatch ({len(seen)} IDs, advertised {expected})")
            return
        if not isinstance(next_url, str) or not next_url or not added:
            raise IncompleteScan(f"Tipsi {kind}: invalid next link or non-progressing page {page}")
        parsed = urlsplit(next_url)
        number = parse_qs(parsed.query).get("page", [])
        if (not parsed.path.rstrip("/").endswith(f"/events/{kind}")
                or len(number) != 1 or not number[0].isdigit()
                or int(number[0]) != page + 1):
            raise IncompleteScan(f"Tipsi {kind}: next link skips/repeats a page or changes feed")
    raise IncompleteScan(f"Tipsi {kind}: exceeded {MAX_PAGES} pages")


def tipsitickets(client, source):
    scan = _Scan(source)
    _tipsi_feed(client, scan, "catalog", 24, "start_at", "APPROVED")
    _tipsi_feed(client, scan, "past", 16, "-start_at")
    return scan.result()


def pataticket(client, source):
    scan = _Scan(source)
    response = _get(client, "https://pataticket.co.ke/event/")
    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select(".event-card")
    past = soup.select(".past-event-card")
    if not cards and not past:
        raise SourceError("PataTicket: expected current/past event cards absent")
    if past:
        scan.issue(
            f"PataTicket: {len(past)} historical cards have no verified permitted detail URL; "
            "their PHP/query resolver is robots-disallowed"
        )
    seen = set()
    for card in cards:
        match = re.search(r"""window\.location\.href\s*=\s*['"](/event/[A-Za-z0-9_-]+)['"]""",
                          card.get("onclick") or "")
        if not match:
            scan.issue("PataTicket: current card missing a literal permitted event path")
            continue
        url = urljoin(response.url, match.group(1))
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > MAX_DETAILS:
            raise IncompleteScan("PataTicket detail count exceeded safety bound")
        try:
            detail = _get(client, url, detail=True)
            page = BeautifulSoup(detail.text, "html.parser")
            fields = _ld_fields(_event_ld(page, detail.url), detail.url, page)
            native = None
            for script in page.find_all("script"):
                text = script.get_text()
                if "window.PataTicketPageData" not in text:
                    continue
                match = re.search(r"\bevent\s*:\s*(?=\{)", text)
                if match:
                    try:
                        value, _ = json.JSONDecoder().raw_decode(text, match.end())
                        native = value.get("id") if isinstance(value, dict) else None
                    except ValueError:
                        pass
            if native is None:
                scan.issue(f"{url}: native PataTicket inline event ID missing")
            scan.record(**fields, native_id=native)
        except SourceBlocked:
            raise
        except SourceError as error:
            scan.issue(error)
    return scan.result()


ADAPTERS = {
    "mookh": mookh,
    "hustlesasa": hustlesasa,
    "mtickets": mtickets,
    "tikiti": tikiti,
    "tipsitickets": tipsitickets,
    "pataticket": pataticket,
    "ticketskenya": ticketskenya,
}
