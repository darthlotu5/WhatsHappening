import json
import tempfile
import unittest
from unittest.mock import Mock

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceNotFoundError

from EventDiscoveryApi.schedule import schedule_sources
from EventScrapper.engine import scrape_source
from EventScrapper.http_client import CachedClient, Response, SourceRateLimited, robots_policy
from EventScrapper.models import Collection, IncompleteScan, Source, SourceBlocked, SourceError, event_from_fields
from EventScrapper.providers.local_platforms import ticketsasa, tiket, urbangangtour
from EventScrapper.providers.structured import next_payloads, parse_structured
from EventScrapper.sources import SOURCE_BY_ID
from EventScrapper.state import BlobStateStore, LocalStateStore, new_state
from shared.contracts.Messages import ScrapSite
from shared.event_catalog import merge_event_catalog

SOURCE = Source("sample", "Sample", "https://events.example/events", ("events.example",), "sample")


def event(**kwargs):
    return event_from_fields(
        SOURCE, name="Nairobi concert", start="2026-12-01T18:00:00+03:00",
        url="https://events.example/events/concert", location="Nairobi", **kwargs,
    )


def response(code, body="", headers=None):
    result = Mock(status_code=code, headers=headers or {}, encoding="utf-8")
    result.iter_content.return_value = [body.encode()]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def client_with(*responses, state=None, **kwargs):
    session = Mock()
    session.headers = {}
    session.get.side_effect = responses
    return CachedClient(SOURCE, state if state is not None else new_state(), session=session,
                        clock=lambda: 100000, sleep=lambda _: None, **kwargs)


class RobotsTests(unittest.TestCase):
    def test_longest_match_wins_over_earlier_allow_root(self):
        policy = "User-agent: *\nAllow: /\nDisallow: /api/\nAllow: /api/public/"
        self.assertFalse(robots_policy(policy, "https://events.example/api/private")[0])
        self.assertTrue(robots_policy(policy, "https://events.example/api/public/events")[0])

    def test_agent_specific_group_and_crawl_delay(self):
        policy = "User-agent: *\nAllow: /\nUser-agent: Dundaa\nDisallow: /private\nCrawl-delay: 5"
        self.assertEqual(robots_policy(policy, "https://events.example/events"), (True, 5))
        self.assertFalse(robots_policy(policy, "https://events.example/private")[0])

    def test_wildcards_and_end_anchor(self):
        policy = "User-agent: *\nDisallow: /*?secret=*$"
        self.assertFalse(robots_policy(policy, "https://events.example/event?secret=value")[0])
        self.assertTrue(robots_policy(policy, "https://events.example/event?public=value")[0])

    def test_allow_wins_equal_specificity(self):
        policy = "User-agent: *\nDisallow: /events\nAllow: /events"
        self.assertTrue(robots_policy(policy, SOURCE.listing_url)[0])


class CacheTests(unittest.TestCase):
    def test_robots_disallow_prevents_page_request(self):
        client = client_with(response(200, "User-agent: *\nDisallow: /events"))
        with self.assertRaises(SourceBlocked):
            client.get(SOURCE.listing_url)
        self.assertEqual(client.session.get.call_count, 1)

    def test_conditional_get_reuses_cached_body_on304(self):
        client = client_with(response(404), response(200, '{"events":[]}', {"ETag": '"one"'}),
                             response(304))
        first = client.get(SOURCE.listing_url)
        second = client.get(SOURCE.listing_url)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(second.from_cache)
        self.assertEqual(client.stats["not_modified"], 1)
        self.assertEqual(client.session.get.call_args.kwargs["headers"]["If-None-Match"], '"one"')

    def test_detail_cache_avoids_network_requests(self):
        client = client_with(response(404), response(200, "event"))
        client.get(SOURCE.listing_url, detail=True)
        self.assertTrue(client.get(SOURCE.listing_url, detail=True).from_cache)
        self.assertEqual(client.stats["cache_hits"], 1)
        self.assertEqual(client.session.get.call_count, 2)

    def test_api_robots405_is_unavailable_not_a_prohibition(self):
        client = client_with(response(405), response(200, '{"events":[]}'))
        self.assertEqual(client.get(SOURCE.listing_url).json(), {"events": []})

    def test_no_store_is_not_cached(self):
        client = client_with(response(404), response(200, "one", {"Cache-Control": "no-store"}),
                             response(200, "two", {"Cache-Control": "no-store"}))
        client.get(SOURCE.listing_url, detail=True)
        self.assertEqual(client.get(SOURCE.listing_url, detail=True).text, "two")
        self.assertEqual(client.pages, {})

    def test_cached304_preserves_pagination_headers(self):
        client = client_with(response(404), response(200, "[]", {"ETag": '"one"', "Content-Range": "0-0/1"}),
                             response(304))
        client.get(SOURCE.listing_url)
        self.assertEqual(client.get(SOURCE.listing_url).headers["Content-Range"], "0-0/1")

    def test_expired_explicit_max_age_overrides_adaptive_detail_ttl(self):
        client = client_with(response(404), response(200, "one", {"Cache-Control": "max-age=60"}),
                             response(200, "two"))
        client.get(SOURCE.listing_url, detail=True)
        client.clock = lambda: 100061
        self.assertEqual(client.get(SOURCE.listing_url, detail=True).text, "two")

    def test_no_store_in304_removes_previously_cached_response(self):
        client = client_with(response(404), response(200, "one", {"ETag": '"one"'}),
                             response(304, headers={"Cache-Control": "no-store"}))
        client.get(SOURCE.listing_url)
        self.assertEqual(client.get(SOURCE.listing_url).text, "one")
        self.assertEqual(client.pages, {})

    def test_listing_is_revalidated_even_when_detail_cache_is_fresh(self):
        client = client_with(response(404), response(200, "one"), response(200, "two"))
        client.get(SOURCE.listing_url)
        self.assertEqual(client.get(SOURCE.listing_url).text, "two")
        self.assertEqual(client.session.get.call_count, 3)

    def test_redirect_to_unapproved_host_is_blocked(self):
        client = client_with(response(404), response(302, headers={"Location": "https://other.example/events"}))
        with self.assertRaises(SourceBlocked):
            client.get(SOURCE.listing_url)
        self.assertEqual(client.session.get.call_count, 2)

    def test_http429_is_reported_not_bypassed(self):
        client = client_with(response(404), response(429, headers={"Retry-After": "120"}))
        with self.assertRaises(SourceRateLimited):
            client.get(SOURCE.listing_url)

    def test_request_budget_marks_coverage_incomplete(self):
        client = client_with(response(404), max_requests=1)
        with self.assertRaises(IncompleteScan):
            client.get(SOURCE.listing_url)

    def test_unchanged_response_without_etag_is_detected(self):
        client = client_with(response(404), response(200, "same"), response(200, "same"))
        client.get(SOURCE.listing_url)
        client.get(SOURCE.listing_url)
        self.assertEqual(client.stats["unchanged_pages"], 1)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = LocalStateStore(self.directory.name)
        self.client_factory = lambda *_: Mock(stats={"requests": 1, "cache_hits": 0})

    def run_source(self, events, publish=None, complete=True):
        return scrape_source(SOURCE, self.store, lambda *_: Collection(events, complete),
                             publish, client_factory=self.client_factory)

    def test_second_identical_run_has_zero_information_gain_and_no_publish(self):
        publish = Mock()
        self.run_source([event()], publish)
        report = self.run_source([event()], publish)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(report["changed_count"], 0)
        self.assertEqual(report["information_gain"], 0)
        self.assertEqual(report["unchanged_count"], 1)

    def test_changed_event_only_is_published(self):
        original = event()
        self.run_source([original])
        changed = {**original, "location": "Westlands"}
        publish = Mock()
        report = self.run_source([changed], publish)
        self.assertEqual(report["changed_count"], 1)
        self.assertEqual(publish.call_args.args[1], [changed])

    def test_new_records_do_not_republish_unchanged_existing_records(self):
        original = event()
        self.run_source([original])
        new = {**original, "id": "sample-two", "url": "https://events.example/events/second"}
        publish = Mock()
        self.run_source([original, new], publish)
        self.assertEqual(publish.call_args.args[1], [new])

    def test_publish_failure_does_not_advance_fingerprints(self):
        with self.assertRaises(SourceError):
            self.run_source([event()], Mock(side_effect=AzureError("queue unavailable")))
        state, _ = self.store.load(SOURCE.id)
        self.assertEqual(state["fingerprints"], {})
        self.assertEqual(state["report"]["status"], "error")

    def test_incomplete_scan_is_explicit_but_valid_events_are_preserved(self):
        publish = Mock()
        report = self.run_source([event()], publish, complete=False)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(self.store.status(SOURCE.id)["status"], "incomplete")
        self.assertEqual(publish.call_count, 1)

    def test_partial_catalog_repeat_does_not_republish_or_trigger_immediate_retry(self):
        publish = Mock()
        self.run_source([event()], publish, complete=False)
        report = self.run_source([event()], publish, complete=False)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["changed_count"], 0)
        self.assertEqual(publish.call_count, 1)

    def test_one_source_failure_does_not_corrupt_other_source_state(self):
        self.run_source([event()])
        second = Source("other", "Other", "https://other.example", ("other.example",), "other")
        def blocked(*_):
            raise SourceBlocked("robots.txt disallows catalog")
        report = scrape_source(second, self.store, blocked, client_factory=self.client_factory)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.store.status(SOURCE.id)["status"], "healthy")

    def test_concurrent_state_change_is_rejected(self):
        state, etag = self.store.load(SOURCE.id)
        self.store.save(SOURCE.id, state, etag)
        with self.assertRaises(RuntimeError):
            self.store.save(SOURCE.id, state, etag)


class DurableStateTests(unittest.TestCase):
    def test_blob_state_updates_require_the_observed_etag(self):
        service = Mock()
        store = BlobStateStore(service)
        state = new_state()
        store.save("sample", state, '"previous"')
        call = service.get_blob_client.return_value.upload_blob.call_args_list[0]
        self.assertEqual(call.kwargs["etag"], '"previous"')
        self.assertEqual(call.kwargs["match_condition"], MatchConditions.IfNotModified)

    def test_new_blob_state_cannot_overwrite_another_worker(self):
        service = Mock()
        store = BlobStateStore(service)
        store.save("sample", new_state(), None)
        call = service.get_blob_client.return_value.upload_blob.call_args_list[0]
        self.assertFalse(call.kwargs["overwrite"])

    def test_missing_blob_state_has_no_claimed_success(self):
        service = Mock()
        service.get_blob_client.return_value.download_blob.side_effect = ResourceNotFoundError("missing")
        store = BlobStateStore(service)
        state, etag = store.load("sample")
        self.assertIsNone(etag)
        self.assertEqual(state["fingerprints"], {})
        self.assertEqual(store.status("sample")["status"], "not_run")


class ModelTests(unittest.TestCase):
    def test_unknown_end_is_not_invented(self):
        self.assertIsNone(event()["end_date"])

    def test_invalid_source_end_remains_unknown_with_explicit_warning(self):
        result = event(end="2026-11-30")
        self.assertIsNone(result["end_date"])
        self.assertIn("precedes", result["date_warning"])

    def test_boolean_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            event_from_fields(SOURCE, name="Bad date", start=True, url=SOURCE.listing_url)

    def test_occurrences_with_same_detail_url_are_not_merged(self):
        first = event()
        second = {**first, "id": "sample-second-occurrence", "start_date": "1796500000"}
        self.assertEqual(len(merge_event_catalog([first, second])), 2)

    def test_known_local_time_is_normalized_to_east_africa(self):
        self.assertEqual(event()["start_date"],
                         event_from_fields(SOURCE, name="Nairobi concert", start="2026-12-01T15:00:00Z",
                                           url=SOURCE.listing_url)["start_date"])

    def test_numeric_timestamp_strings_and_milliseconds_are_equivalent(self):
        first = event_from_fields(SOURCE, name="Event", start="1796122800", url=SOURCE.listing_url)
        second = event_from_fields(SOURCE, name="Event", start=1796122800000, url=SOURCE.listing_url)
        self.assertEqual(first["start_date"], second["start_date"])

    def test_published_unpadded_iso_clock_is_preserved_without_losing_events(self):
        for value, normalized in (("2026-10-30T0:0", "2026-10-30T00:00"),
                                  ("2026-10-31T17:8", "2026-10-31T17:08"),
                                  ("2026-10-31T8:0:0", "2026-10-31T08:00:00")):
            with self.subTest(value=value):
                result = event_from_fields(SOURCE, name="Concert", start=value, url=SOURCE.listing_url)
                expected = event_from_fields(SOURCE, name="Concert", start=normalized, url=SOURCE.listing_url)
                self.assertEqual(result["start_date"], expected["start_date"])

    def test_impossible_unpadded_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            event_from_fields(SOURCE, name="Concert", start="2026-10-30T25:0", url=SOURCE.listing_url)

    def test_tba_is_explicit_not_a_fake_epoch_date(self):
        result = event_from_fields(SOURCE, name="Unannounced", start=None, date_tba=True,
                                   url=SOURCE.listing_url)
        self.assertIsNone(result["start_date"])
        self.assertEqual(result["date_status"], "to_be_announced")

    def test_malformed_datetime_is_not_silently_defaulted(self):
        with self.assertRaises(ValueError):
            event_from_fields(SOURCE, name="Bad date", start="not-a-date", url=SOURCE.listing_url)

    def test_incomplete_published_date_preserves_label_without_guessing_year(self):
        record = event_from_fields(SOURCE, name="Conference", start=None, date_unconfirmed=True,
                                   date_label="Wed, 11 Nov · 07:00 GMT+3", url=SOURCE.listing_url)
        self.assertIsNone(record["start_date"])
        self.assertEqual(record["date_status"], "unconfirmed")
        self.assertEqual(record["date_label"], "Wed, 11 Nov · 07:00 GMT+3")

    def test_native_ids_are_safe_for_cosmos(self):
        result = event(native_id="some/foreign?id#value")
        self.assertNotRegex(result["id"], r"[/\\?#]")

    def test_tracking_query_does_not_change_event_identity(self):
        first = event_from_fields(SOURCE, name="Event", start="2026-12-01",
                                  url="https://events.example/events/one?utm_source=directory")
        second = event_from_fields(SOURCE, name="Event", start="2026-12-01",
                                   url="https://events.example/events/one")
        self.assertEqual(first["id"], second["id"])

    def test_jsonld_nested_item_list_and_offers(self):
        node = {"@type": "ItemList", "itemListElement": [{"item": {
            "@type": "MusicEvent", "name": "Concert", "startDate": "2026-12-01",
            "url": SOURCE.listing_url, "offers": {"price": "500", "priceCurrency": "KES"},
            "location": {"name": "Nairobi"}, "image": [{"url": "https://events.example/banner.jpg"}],
        }}]}
        result = parse_structured(f'<script type="application/ld+json">{json.dumps(node)}</script>',
                                  SOURCE, SOURCE.listing_url)
        self.assertTrue(result.complete)
        self.assertEqual(result.events[0]["offers"][0]["price"], "500")

    def test_byte_length_flight_text_frame_does_not_hide_following_registry(self):
        raw = "Public title \u2014 Nairobi"
        record = {"events": [{"name": "Concert", "eventDate": None}]}
        stream = f"1:T{len(raw.encode()):x},{raw}2:{json.dumps(record)}\n"
        html = f"<script>self.__next_f.push({json.dumps([1, stream])})</script>"
        self.assertEqual(next_payloads(html), [record])


class CatalogTests(unittest.TestCase):
    def test_same_event_across_providers_merges_provenance(self):
        first = event()
        second = {**first, "id": "other-one", "site_name": "other",
                  "url": "https://other.example/event"}
        result = merge_event_catalog([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["sources"]), 2)

    def test_same_title_different_time_or_venue_stays_distinct(self):
        first = event()
        second = {**first, "id": "other-one", "site_name": "other", "location": "Mombasa",
                  "url": "https://other.example/event"}
        self.assertEqual(len(merge_event_catalog([first, second])), 2)

    def test_undated_events_are_not_merged_by_name(self):
        first = {**event(), "start_date": None}
        second = {**first, "url": "https://other.example/event", "site_name": "other"}
        self.assertEqual(len(merge_event_catalog([first, second])), 2)


class SchedulingTests(unittest.TestCase):
    def test_all19_sources_have_one_isolated_message_each(self):
        messenger = Mock()
        self.assertEqual(schedule_sources(messenger), 19)
        ids = [json.loads(call.kwargs["data"])["source"] for call in messenger.send_message.call_args_list]
        self.assertEqual(set(ids), set(SOURCE_BY_ID))
        self.assertEqual(len(ids), len(set(ids)))

    def test_single_source_request_does_not_enqueue_other_sources(self):
        messenger = Mock()
        self.assertEqual(schedule_sources(messenger, "ticketsasa"), 1)
        self.assertEqual(json.loads(messenger.send_message.call_args.kwargs["data"])["source"], "ticketsasa")

    def test_old_scheduled_message_is_backward_compatible(self):
        message = json.loads(ScrapSite(delay=0).to_json())
        del message["source"]
        self.assertIsNone(ScrapSite.from_json(json.dumps(message)).source)


class CoverageTests(unittest.TestCase):
    def sasa_client(self, total):
        values = []
        def append(value):
            values.append(value)
            return len(values) - 1
        records = []
        for number in range(2):
            row = {key: append(value) for key, value in {
                "id": number, "name": f"Event{number}", "loc": "Nairobi", "slug_name": f"event-{number}",
                "main_image": "/banner.jpg", "start_at": "Sat 31 Oct 2026 10:30 AM",
            }.items()}
            records.append(append(row))
        append(records)
        append({"current_page": append(1), "last_page": append(2),
                "total": append(total), "data": append([records[0]])})
        client = Mock()
        client.get.return_value.text = f'<script id="__NUXT_DATA__">{json.dumps(values)}</script>'
        return client

    def test_sasa_full_catalog_not_just_paginated_first_page(self):
        result = ticketsasa(self.sasa_client(2), SOURCE_BY_ID["ticketsasa"])
        self.assertEqual(len(result.events), 2)

    def test_sasa_incomplete_ssr_catalog_is_not_reported_complete(self):
        with self.assertRaises(IncompleteScan):
            ticketsasa(self.sasa_client(3), SOURCE_BY_ID["ticketsasa"])

    def test_tiket_traverses_beyond_first100_records(self):
        bundle = 'Ya=`https://mnncnpwuomknvdzxjvxc.supabase.co`.trim(),Bs="sb_publishable_fixture"'
        responses = [
            Response("https://www.tiket.co.ke", '<script src="/assets/index-fixture.js"></script>', 200, {}),
            Response("https://www.tiket.co.ke/assets/index-fixture.js", bundle, 200, {}),
        ]
        for offset, count in ((0, 100), (100, 1)):
            rows = [{"id": str(number), "title": f"Event{number}", "date_start": "2026-12-01",
                     "venue": "Nairobi"} for number in range(offset, offset + count)]
            responses.append(Response("https://api.example/events", json.dumps(rows), 200,
                                      {"Content-Range": f"{offset}-{offset + count - 1}/101"}))
        client = Mock()
        client.get.side_effect = responses
        result = tiket(client, SOURCE_BY_ID["tiket"])
        self.assertEqual(len(result.events), 101)
        self.assertIn("offset=100", client.get.call_args.args[0])

    def test_urban_registry_includes_unannounced_dates_and_matches_sitemap(self):
        rows = [{"id": "upcoming", "slug": "upcoming", "name": "Concert", "eventDate": "2026-12-01"},
                {"id": "tba", "slug": "tba", "name": "Future event", "eventDate": None}]
        stream = "1:" + json.dumps({"events": rows}) + "\n"
        html = "<script>self.__next_f.push(" + json.dumps([1, stream]) + ")</script>"
        sitemap = "<urlset><url><loc>https://urbangangtour.co.ke/events/upcoming</loc></url><url><loc>https://urbangangtour.co.ke/events/tba</loc></url></urlset>"
        client = Mock()
        client.get.side_effect = [
            Response("https://urbangangtour.co.ke/events", html, 200, {}),
            Response("https://urbangangtour.co.ke/sitemap.xml", sitemap, 200, {}),
        ]
        result = urbangangtour(client, SOURCE_BY_ID["urbangangtour"])
        self.assertEqual(len(result.events), 2)
        self.assertIsNone(result.events[1]["start_date"])


if __name__ == "__main__":
    unittest.main()
