import base64
import json
import re
from datetime import datetime
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from EventScrapper.models import Collection, IncompleteScan, SourceBlocked, SourceError, event_from_fields
from EventScrapper.providers.structured import crawl_structured, next_payloads, object_nodes, sitemap_urls


def ticketsasa(client, source):
    response = client.get(source.listing_url)
    script = BeautifulSoup(response.text, "html.parser").find("script", id="__NUXT_DATA__")
    if not script:
        raise SourceError("TicketSasa no longer exposes its Nuxt event catalog")
    values = json.loads(script.get_text())
    if not isinstance(values, list):
        raise SourceError("Invalid TicketSasa Nuxt payload")
    required = {"id", "name", "loc", "slug_name", "main_image", "start_at"}

    def resolve(index):
        if type(index) is not int or not 0 <= index < len(values):
            raise SourceError("Invalid TicketSasa scalar reference")
        return values[index]

    catalogs = []
    totals, page_ids = [], set()
    for item in values:
        if isinstance(item, list) and item and all(type(ref) is int and 0 <= ref < len(values) for ref in item):
            records = [values[ref] for ref in item]
            if all(isinstance(record, dict) and required.issubset(record) for record in records):
                catalogs.append(records)
        if isinstance(item, dict) and {"current_page", "last_page", "total", "data"}.issubset(item):
            totals.append(resolve(item["total"]))
            for ref in resolve(item["data"]):
                page_ids.add(str(resolve(values[ref]["id"])))
    if not catalogs:
        raise SourceError("TicketSasa full SSR catalog is missing")
    records = max(catalogs, key=len)
    found = {str(resolve(record["id"])) for record in records}
    if (totals and len(found) < max(totals)) or not page_ids.issubset(found):
        raise IncompleteScan(
            "TicketSasa SSR catalog does not cover its reported pagination total; "
            "upstream paginated API requires provider credentials"
        )
    events = []
    for record in records:
        row = {key: resolve(record[key]) for key in required}
        events.append(event_from_fields(
            source, name=row["name"], location=row["loc"], start=row["start_at"],
            image=row["main_image"], url=f"https://ticketsasa.com/events/{row['slug_name']}",
            native_id=row["id"],
        ))
    return Collection(events, notes=[
        f"Full SSR catalog contains {len(events)} events; reported upcoming total={max(totals) if totals else 'unpaginated'}",
        "Private upstream API is not used; SSR full-catalog coverage is checked against pagination metadata",
    ])


def nyotecreation(client, source):
    return crawl_structured(client, source, r"/event/[^/]+")


def urbangangtour(client, source):
    response = client.get(source.listing_url)
    catalogs = [node["events"] for payload in next_payloads(response.text) for node in object_nodes(payload)
                if isinstance(node.get("events"), list) and node["events"]
                and isinstance(node["events"][0], dict) and "eventDate" in node["events"][0]]
    if len(catalogs) != 1:
        raise SourceError("Urban Gang full event registry is missing or ambiguous")
    records = catalogs[0]
    urls = sitemap_urls(client, "https://urbangangtour.co.ke/sitemap.xml", r"/events/[^/]+")
    found = {f"https://urbangangtour.co.ke/events/{row['slug']}" for row in records}
    if not urls.issubset(found):
        raise IncompleteScan("Urban Gang registry does not cover all sitemap event URLs")
    events = []
    for row in records:
        start = row.get("eventDate")
        if start and row.get("eventTime"):
            clock = datetime.strptime(row["eventTime"], "%I:%M %p").time()
            start = f"{start}T{clock.isoformat()}"
        events.append(event_from_fields(
            source, name=row["name"], start=start, date_tba=start is None,
            location=row.get("venue") or row.get("city", ""),
            image=row.get("image", ""), url=f"https://urbangangtour.co.ke/events/{row['slug']}",
            native_id=row["id"], status=row.get("status"), country="KE",
        ))
    return Collection(events, notes=[f"Full server event registry: {len(records)}; sitemap event URLs: {len(urls)}"])


def rada(client, source):
    response = client.get(source.listing_url)
    soup = BeautifulSoup(response.text, "html.parser")
    records = None
    for payload in next_payloads(response.text):
        for node in object_nodes(payload):
            result = node.get("events")
            if isinstance(result, list):
                if records is not None and records != result:
                    raise IncompleteScan("Rada exposed conflicting event catalogs")
                records = result
    if records is None:
        raise SourceError("Rada did not expose a server-rendered event catalog")
    if not records and "No events match that search" not in soup.get_text(" ", strip=True):
        raise SourceError("Rada empty catalog has no visible empty-state confirmation")
    events = [event_from_fields(
        source, name=row["title"], start=row["starts_at"], end=row.get("ends_at"),
        url=row["slugPath"], image=row.get("cover_url", ""),
        location=row.get("club_name") or row.get("venue_name", ""), native_id=row.get("id"),
    ) for row in records]
    return Collection(events, notes=["Read provider's full server-rendered EventsExplorer catalog"])


def tiket(client, source):
    response = client.get(source.listing_url)
    soup = BeautifulSoup(response.text, "html.parser")
    scripts = [urljoin(response.url, script["src"]) for script in soup.find_all("script", src=True)
               if script["src"].startswith("/assets/index-") and script["src"].endswith(".js")]
    if len(scripts) != 1:
        raise SourceError("TIKET public application bundle was not found")
    bundle = client.get(scripts[0], detail=True).text
    config = re.search(r'Ya=`(https://[^`]+)`\.trim\(\),Bs="([^"]+)"', bundle)
    if not config:
        raise SourceError("TIKET public catalog configuration changed")
    base, public_key = config.groups()
    base = base.strip()
    if urlsplit(base).hostname != "mnncnpwuomknvdzxjvxc.supabase.co":
        raise SourceBlocked("TIKET catalog API host changed; review its public configuration")
    if not public_key.startswith("sb_publishable_"):
        try:
            payload = public_key.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, IndexError) as error:
            raise SourceBlocked("TIKET API key is not a public anonymous credential") from error
        if claims.get("role") != "anon":
            raise SourceBlocked("TIKET API credential is not anonymous; it will not be used")
    events, offset, expected = [], 0, None
    seen = set()
    while True:
        query = urlencode({
            "select": "id,title,date_start,date_end,venue,city,image_url,status,country_code,visibility",
            "status": "eq.active", "visibility": "eq.public", "order": "id.asc",
            "limit": 100, "offset": offset,
        })
        page = client.get(base + "/rest/v1/events?" + query,
                          headers={"apikey": public_key, "Prefer": "count=exact"})
        rows = page.json()
        if not isinstance(rows, list):
            raise SourceError("TIKET catalog returned a non-array response")
        count = page.headers.get("Content-Range", "").rsplit("/", 1)[-1]
        if count.isdigit():
            total = int(count)
            if expected is not None and expected != total:
                raise IncompleteScan("TIKET catalog changed during pagination; retry a consistent scan")
            expected = total
        for row in rows:
            if row["id"] in seen:
                raise IncompleteScan("TIKET pagination repeated an event ID")
            seen.add(row["id"])
            events.append(event_from_fields(
                source, name=row["title"], start=row["date_start"], end=row.get("date_end"),
                location=row.get("venue") or row.get("city", ""),
                image=row.get("image_url", ""), url=f"https://www.tiket.co.ke/events/{row['id']}",
                native_id=row["id"], status=row.get("status"), country=row.get("country_code"),
            ))
        offset += len(rows)
        if not rows or (expected is not None and offset >= expected) or (expected is None and len(rows) < 100):
            break
    if expected is not None and len(events) != expected:
        raise IncompleteScan(f"TIKET returned {len(events)} of {expected} public events")
    return Collection(events, notes=[f"Traversed public anonymous catalog; {len(events)} active public events"])


def now(client, source):
    client.get(source.listing_url)
    raise SourceError("NOW Kenya has no verified public event listing adapter")


ADAPTERS = {
    "ticketsasa": ticketsasa, "nyotecreation": nyotecreation,
    "urbangangtour": urbangangtour, "rada": rada, "tiket": tiket, "now": now,
}
