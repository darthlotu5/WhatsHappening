import hashlib
import html
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


class SourceError(RuntimeError):
    status = "error"


class SourceBlocked(SourceError):
    status = "blocked"


class IncompleteScan(SourceError):
    status = "incomplete"


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    listing_url: str
    hosts: tuple[str, ...]
    adapter: str


@dataclass
class Collection:
    events: list[dict] = field(default_factory=list)
    complete: bool = True
    notes: list[str] = field(default_factory=list)


def canonical_url(value, base=""):
    parts = urlsplit(urljoin(base, value))
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("An absolute public event URL is required")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def timestamp(value, timezone="Africa/Nairobi"):
    if isinstance(value, bool):
        raise ValueError("Event timestamp cannot be a boolean")
    if isinstance(value, (int, float)):
        result = float(value / 1000 if value > 100000000000 else value)
        if not math.isfinite(result):
            raise ValueError("Event timestamp must be finite")
        return result
    text = str(value).strip()
    if re.fullmatch(r"\d{9,13}(?:\.\d+)?", text):
        return timestamp(float(text), timezone)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        formats = (
            "%a %d %b %Y %I:%M %p", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%S%z",
            "%d %B %Y %I:%M %p", "%d %b %Y %I:%M %p",
            "%B %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d",
        )
        for pattern in formats:
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unrecognized event datetime: {text[:100]}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.timestamp()


def event_from_fields(source, *, name, start, url, location="", end=None,
                      image="", native_id=None, status=None, timezone="Africa/Nairobi",
                      country=None, date_tba=False, date_label=None, date_unconfirmed=False):
    name = re.sub(r"\s+", " ", html.unescape(str(name or ""))).strip()
    if not name or (start is None and not (date_tba or (date_unconfirmed and date_label))):
        raise ValueError("Event name and start datetime (or explicit TBA) are required")
    url = canonical_url(url, source.listing_url)
    started = timestamp(start, timezone) if start is not None else None
    ended = timestamp(end, timezone) if end is not None and end != "" else None
    identity = str(native_id) if native_id is not None else url
    if native_id is None or not re.fullmatch(r"[A-Za-z0-9._:-]{1,180}", identity):
        identity = hashlib.sha256(identity.encode()).hexdigest()[:24]
    event = {
        "id": f"{source.id}-{identity}",
        "name": name,
        "location": re.sub(r"\s+", " ", html.unescape(str(location or ""))).strip(),
        "start_date": str(started) if started is not None else None,
        "end_date": str(ended) if ended is not None else None,
        "banner_url": canonical_url(image, source.listing_url) if image else "",
        "site_name": source.id,
        "url": url,
    }
    if started is None:
        event["date_status"] = "unconfirmed" if date_unconfirmed else "to_be_announced"
        if date_label:
            event["date_label"] = re.sub(r"\s+", " ", str(date_label)).strip()
    if ended is not None and started is not None and ended < started:
        event["end_date"] = None
        event["date_warning"] = "Source end time precedes its start time"
    if status:
        event["event_status"] = str(status).rsplit("/", 1)[-1]
    if country:
        event["country"] = str(country)
    return event
