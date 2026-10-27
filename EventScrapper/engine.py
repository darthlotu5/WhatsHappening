import hashlib
import json
import logging
from datetime import datetime, timezone

from azure.core.exceptions import AzureError

from EventScrapper.http_client import CachedClient
from EventScrapper.models import IncompleteScan, SourceBlocked, SourceError

logger = logging.getLogger(__name__)


def fingerprint(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def scrape_source(source, store, adapter, publish=None, *, client_factory=CachedClient):
    state, etag = store.load(source.id)
    previous_report = state.get("report", {})
    client = client_factory(source, state)
    started = datetime.now(timezone.utc).isoformat()
    report = {
        "source": source.id, "name": source.name, "url": source.listing_url,
        "last_attempt_at": started, "last_success_at": previous_report.get("last_success_at"),
        "status": "running", "event_count": previous_report.get("event_count", 0),
    }
    failure = None
    try:
        collection = adapter(client, source)
        events = {}
        for event in collection.events:
            if event.get("site_name") != source.id or not event.get("id"):
                raise SourceError("Adapter returned an event without a valid source identity")
            if event["id"] in events and fingerprint(event) != fingerprint(events[event["id"]]):
                raise IncompleteScan(f"Conflicting records for event {event['id']}")
            events[event["id"]] = event
        previous = state.setdefault("fingerprints", {})
        changed = [event for key, event in events.items() if previous.get(key) != fingerprint(event)]
        if changed and publish is not None:
            publish(source, changed)
        previous.update({key: fingerprint(event) for key, event in events.items()})
        report.update(
            status="healthy" if collection.complete else "incomplete",
            event_count=len(events), changed_count=len(changed),
            unchanged_count=len(events) - len(changed),
            information_gain=len(changed) / len(events) if events else 0,
            notes=collection.notes,
        )
        if collection.complete:
            report["last_success_at"] = started
        if not collection.complete:
            logger.warning("Source %s coverage incomplete: %s", source.id, "; ".join(collection.notes))
    except (SourceError, ValueError, KeyError, TypeError, AzureError) as error:
        failure = error if isinstance(error, SourceError) else SourceError(f"{type(error).__name__}: {error}")
        report.update(status=failure.status, error=str(failure))
    report.update(client.stats)
    state["report"] = report
    store.save(source.id, state, etag)
    if failure is not None:
        logger.error("Source %s: %s", source.id, failure)
        if not isinstance(failure, SourceBlocked):
            raise failure
    else:
        logger.info("Source scrape result: %s", json.dumps(report))
    return report
