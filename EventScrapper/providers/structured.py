import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from EventScrapper.models import Collection, IncompleteScan, SourceError, canonical_url, event_from_fields


def next_payloads(text):
    decoder, chunks = json.JSONDecoder(), []
    for script in BeautifulSoup(text, "html.parser").find_all("script"):
        for part in script.get_text().split("self.__next_f.push(")[1:]:
            try:
                chunk, _ = decoder.raw_decode(part)
            except json.JSONDecodeError as error:
                raise SourceError("Invalid Next.js hydration chunk") from error
            if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
                chunks.append(chunk[1])
    stream, cursor, results = "".join(chunks).encode(), 0, []
    while cursor < len(stream):
        header = re.match(rb"[0-9a-f]*:", stream[cursor:])
        if not header:
            raise SourceError("Unrecognized Next.js Flight record framing")
        cursor += header.end()
        if stream[cursor:cursor + 1] == b"T":
            length = re.match(rb"T([0-9a-f]+),", stream[cursor:])
            if not length:
                raise SourceError("Invalid Next.js text record length")
            cursor += length.end() + int(length[1], 16)
            if cursor > len(stream):
                raise SourceError("Truncated Next.js text record")
            continue
        end = stream.find(b"\n", cursor)
        if end == -1:
            raise SourceError("Truncated Next.js JSON record")
        payload = stream[cursor:end]
        cursor = end + 1
        if payload.startswith((b"[", b"{")):
            try:
                results.append(json.loads(payload))
            except json.JSONDecodeError as error:
                raise SourceError("Invalid Next.js JSON record") from error
    return results


def object_nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from object_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from object_nodes(item)


def event_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from event_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if any(kind.rsplit("/", 1)[-1].endswith("Event") for kind in types):
            yield value
        else:
            for item in value.values():
                if isinstance(item, (list, dict)):
                    yield from event_nodes(item)


def parse_structured(text, source, base_url):
    soup = BeautifulSoup(text, "html.parser")
    events, errors = [], []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        for node in event_nodes(data):
            location = node.get("location") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address", {}) if isinstance(location, dict) else {}
            country = address.get("addressCountry") if isinstance(address, dict) else None
            if isinstance(country, dict):
                country = country.get("name")
            image = node.get("image") or ""
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url") or image.get("contentUrl") or ""
            try:
                event = event_from_fields(
                    source, name=node.get("name"), start=node.get("startDate"),
                    end=node.get("endDate"), url=node.get("url") or base_url,
                    location=location.get("name", "") if isinstance(location, dict) else location,
                    image=image, status=node.get("eventStatus"), country=country,
                    date_tba=not node.get("startDate"),
                )
                offers = node.get("offers") or []
                if isinstance(offers, dict):
                    offers = [offers]
                prices = [{"price": str(offer["price"]), "currency": offer.get("priceCurrency", ""),
                           "availability": offer.get("availability", "").rsplit("/", 1)[-1]}
                          for offer in offers if isinstance(offer, dict) and offer.get("price") is not None]
                if prices:
                    event["offers"] = sorted(prices, key=lambda offer: json.dumps(offer, sort_keys=True))
                events.append(event)
            except (ValueError, TypeError) as error:
                errors.append(f"{node.get('url', base_url)}: {error}")
    return Collection(events=events, complete=not errors, notes=errors)


def sitemap_urls(client, url, pattern, seen=None):
    seen = set() if seen is None else seen
    if url in seen:
        raise IncompleteScan(f"Cyclic sitemap index: {url}")
    if len(seen) >= 50:
        raise IncompleteScan("Sitemap safety limit reached")
    seen.add(url)
    try:
        root = ET.fromstring(client.get(url).text)
    except ET.ParseError as error:
        raise SourceError(f"Invalid sitemap XML: {url}") from error
    if root.tag.rsplit("}", 1)[-1] not in ("sitemapindex", "urlset"):
        raise SourceError(f"Unrecognized sitemap root: {url}")
    links = [node.text.strip() for node in root.iter()
             if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]
    if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
        urls = set()
        for link in links:
            urls.update(sitemap_urls(client, link, pattern, seen))
        return urls
    return {link for link in links if re.fullmatch(pattern, urlsplit(link).path)}


def crawl_structured(client, source, pattern, *, sitemap=None):
    pending = [source.listing_url]
    pages, event_urls, events, notes = set(), set(), {}, []
    complete = True
    while pending:
        url = pending.pop(0)
        if url in pages:
            continue
        pages.add(url)
        response = client.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        parsed = parse_structured(response.text, source, response.url)
        complete = complete and parsed.complete
        notes.extend(parsed.notes)
        events.update({event["url"]: event for event in parsed.events})
        for anchor in soup.find_all("a", href=True):
            target = canonical_url(anchor["href"], response.url) if not anchor["href"].startswith(("#", "mailto:", "tel:", "javascript:")) else None
            if not target:
                continue
            if re.fullmatch(pattern, urlsplit(target).path):
                event_urls.add(target)
            rel = anchor.get("rel", [])
            if "next" in rel or (anchor.find_parent(class_=re.compile("pagination", re.I)) and urlsplit(target).query):
                if target not in pages:
                    pending.append(target)
        if soup.find(["button", "a"], string=re.compile(r"^\s*(load more|show more)\s*$", re.I)):
            raise IncompleteScan("Listing has a dynamic load-more control without verified pagination")
    if sitemap:
        event_urls.update(sitemap_urls(client, sitemap, pattern))
    for url in sorted(event_urls):
        parsed = parse_structured(client.get(url, detail=True).text, source, url)
        if not parsed.events:
            complete = False
            notes.append(f"No parseable structured event on discovered detail page {url}")
        complete = complete and parsed.complete
        notes.extend(parsed.notes)
        events.update({event["url"]: event for event in parsed.events})
    if not events and not event_urls:
        raise SourceError("Listing contains no recognizable events or explicit verified empty catalog")
    notes.append(f"Traversed {len(pages)} listing pages and {len(event_urls)} distinct event detail URLs")
    return Collection(list(events.values()), complete, notes)
