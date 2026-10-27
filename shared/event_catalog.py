import re
import unicodedata

from EventScrapper.models import canonical_url


def _text(value):
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def merge_event_catalog(events):
    result, urls, facts = [], {}, {}
    for event in events:
        url = canonical_url(event["url"])
        location, name = _text(event.get("location")), _text(event.get("name"))
        start = event.get("start_date")
        normalized_start = float(start) if start is not None else None
        key = (name, location, normalized_start) if name and location and start is not None else None
        occurrence = (url, normalized_start)
        existing = urls.get(occurrence)
        if existing is None and key is not None:
            existing = facts.get(key)
        provenance = {"site_name": event["site_name"], "url": event["url"]}
        if existing is None:
            existing = {**event, "sources": [provenance]}
            result.append(existing)
        elif provenance not in existing["sources"]:
            existing["sources"].append(provenance)
        urls[occurrence] = existing
        if key is not None:
            facts[key] = existing
    return result
