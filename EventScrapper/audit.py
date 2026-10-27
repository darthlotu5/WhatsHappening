import argparse
import concurrent.futures
import json
import logging

from EventScrapper.engine import scrape_source
from EventScrapper.models import SourceError
from EventScrapper.providers import get_adapter
from EventScrapper.sources import SOURCE_BY_ID
from EventScrapper.state import LocalStateStore


def main():
    parser = argparse.ArgumentParser(description="Audit public event coverage without publishing to the live catalog")
    parser.add_argument("--source", action="append", choices=tuple(SOURCE_BY_ID))
    parser.add_argument("--state-dir", required=True, help="Local persistent HTTP cache and fingerprint directory")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("--workers must be between1 and6")
    logging.basicConfig(level=logging.WARNING)
    store = LocalStateStore(args.state_dir)

    def audit(source_id):
        source = SOURCE_BY_ID[source_id]
        try:
            return scrape_source(source, store, get_adapter(source_id))
        except SourceError:
            return store.status(source_id)

    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for report in executor.map(audit, args.source or tuple(SOURCE_BY_ID)):
            print(json.dumps(report, ensure_ascii=False), flush=True)
            failed = failed or report["status"] != "healthy"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
