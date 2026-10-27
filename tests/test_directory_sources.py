import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from EventScrapper.models import IncompleteScan, Source, SourceBlocked, SourceError, timestamp
from EventScrapper.providers import directories


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, *, detail=False):
        self.calls.append((url, detail))
        value = self.responses[url]
        text = value if isinstance(value, str) else json.dumps(value)
        return SimpleNamespace(
            url=url, text=text, status_code=200, headers={},
            json=lambda: json.loads(text),
        )


def source(name, url):
    return Source(name, name, url, (), name)


def kb_row(number):
    return {
        "event_id": number, "event_slug": f"event-{number}",
        "event_name": f"Event {number}", "event_location": "Nairobi",
        "start_date": "2026-11-07 09:00:00", "end_date": "2026-11-07 12:00:00",
    }


def kb_detail(number, dates=None):
    return {
        "response": "success",
        "data": {
            "event_details": {
                **kb_row(number), "default_location": "Nairobi",
                "poster": "/posters/example.jpg",
            },
            "event_dates": dates if dates is not None else [{
                "event_date_id": number * 10, "ed_date": "2026-11-07",
                "event_start_time": "09:00:00", "event_end_time": "12:00:00",
                "location_name": "Nairobi", "performance_status": "available",
            }],
        },
    }


def eb_row(number, **overrides):
    row = {
        "id": str(number), "eventbrite_event_id": str(number), "name": f"Event {number}",
        "url": f"https://www.eventbrite.com/e/event-tickets-{number}",
        "start_date": "2026-11-07", "end_date": "2026-11-07",
        "start_time": "10:00", "end_time": "12:00", "timezone": "Africa/Nairobi",
        "primary_venue": {"name": "Nairobi Hall", "address": {"country": "KE"}},
        "image": {"url": "https://example.com/poster.jpg"}, "is_online_event": False,
    }
    return {**row, **overrides}


def eb_page(rows, page=1, pages=1, total=None, continuation=None):
    data = {"search_data": {"events": {
        "results": rows, "pagination": {
            "page_number": page, "page_count": pages, "page_size": 20,
            "object_count": len(rows) if total is None else total,
            "continuation": continuation,
        },
    }}}
    return "<script>window.__SERVER_DATA__ = " + json.dumps(data) + ";</script>"


def onepass_card(identity, date_label="Wed, 11 Nov · 07:00 GMT+3", attribution="AllEvents Nairobi"):
    return (
        f'<a href="/api/discover/{identity}/go"><h3>Example {identity}</h3>'
        f'<p>{date_label}</p><p>Nairobi Hall · Nairobi</p>'
        '<img src="/_next/image?url=%2Fapi%2Fdiscover%2Fexample%2Fimage&amp;w=640"/>'
        f'<span>via {attribution}</span></a>'
    )


class KenyaBuzzTests(unittest.TestCase):
    def setUp(self):
        self.source = source("kenyabuzz", "https://www.kenyabuzz.com/events")
        self.api = directories.KB_API

    def client(self, landing, pages, details):
        responses = {f"{self.api}/events/list/landing": {
            "response": "success", "data": {"all_events": [], **landing},
        }}
        responses.update({
            f"{self.api}/events/list/all-events/{page}": {"response": "success", "data": rows}
            for page, rows in enumerate(pages, 1)
        })
        responses.update({f"{self.api}/events/details/view/event-{key}": value
                          for key, value in details.items()})
        return Client(responses)

    def test_unions_nested_landing_and_traverses_until_empty_not_short_page(self):
        client = self.client(
            {"month_events": [{"month": "November", "events": [kb_row(3)]}]},
            [[kb_row(1)], [kb_row(2)], []],
            {i: kb_detail(i) for i in [1, 2, 3]},
        )
        result = directories.kenyabuzz(client, self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 3)
        self.assertEqual({e["id"] for e in result.events},
                         {"kenyabuzz-1-10", "kenyabuzz-2-20", "kenyabuzz-3-30"})
        self.assertIn((f"{self.api}/events/list/all-events/3", False), client.calls)
        self.assertTrue(all(detail for url, detail in client.calls if "/details/" in url))
        self.assertEqual(result.events[0]["banner_url"],
                         "https://static.kenyabuzz.com/public/posters/example.jpg")

    def test_repeated_nonempty_page_is_not_success(self):
        client = self.client({}, [[kb_row(1)], [kb_row(1)]], {})
        with self.assertRaisesRegex(IncompleteScan, "repeated"):
            directories.kenyabuzz(client, self.source)

    def test_missing_occurrence_start_is_reported_not_dropped_silently(self):
        detail = kb_detail(1)
        del detail["data"]["event_dates"][0]["event_start_time"]
        result = directories.kenyabuzz(self.client({}, [[kb_row(1)], []], {1: detail}), self.source)
        self.assertFalse(result.complete)
        self.assertTrue(any("missing date or start" in n for n in result.notes))

    def test_missing_detail_identity_is_incomplete(self):
        detail = kb_detail(2)
        result = directories.kenyabuzz(self.client({}, [[kb_row(1)], []], {1: detail}), self.source)
        self.assertFalse(result.complete)
        self.assertTrue(any("mismatched" in n for n in result.notes))

    def test_missing_optional_end_is_quality_note_not_missing_coverage(self):
        detail = kb_detail(1)
        detail["data"]["event_dates"][0]["event_end_time"] = None
        result = directories.kenyabuzz(self.client({}, [[kb_row(1)], []], {1: detail}), self.source)
        self.assertEqual(len(result.events), 1)
        self.assertTrue(result.complete)
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(any("end datetime" in n for n in result.notes))

    def test_unknown_locations_retain_real_parent_ids_without_losing_coverage(self):
        details = {number: kb_detail(number) for number in [52614, 52734]}
        for detail in details.values():
            detail["data"]["event_details"]["default_location"] = None
            detail["data"]["event_dates"][0]["location_name"] = None
        client = self.client({}, [[kb_row(52614), kb_row(52734)], []], details)
        result = directories.kenyabuzz(client, self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 2)
        self.assertEqual({event["location"] for event in result.events}, {""})
        self.assertTrue(any("52614" in note and "location is unknown" in note
                            for note in result.notes))
        self.assertTrue(any("52734" in note and "location is unknown" in note
                            for note in result.notes))

    def test_end_before_start_is_null_quality_warning_not_coverage_gap(self):
        detail = kb_detail(1)
        detail["data"]["event_dates"][0]["event_end_time"] = "08:00:00"
        result = directories.kenyabuzz(self.client({}, [[kb_row(1)], []], {1: detail}), self.source)
        self.assertTrue(result.complete)
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(any("precedes" in note for note in result.notes))

    def test_multiple_occurrences_keep_stable_occurrence_ids(self):
        detail = kb_detail(1)
        second = {**detail["data"]["event_dates"][0], "event_date_id": 11, "ed_date": "2026-11-08"}
        detail["data"]["event_dates"].append(second)
        result = directories.kenyabuzz(self.client({}, [[kb_row(1)], []], {1: detail}), self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 2)

    def test_weekend_only_slug_is_resolved_without_losing_the_event(self):
        client = self.client(
            {"this_weekend": [{"module": "event", "slug": "event-2", "title": "Event 2"}]},
            [[kb_row(1)], []], {1: kb_detail(1), 2: kb_detail(2)},
        )
        result = directories.kenyabuzz(client, self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 2)

    def test_landing_missing_event_id_is_not_silently_ignored(self):
        row = kb_row(1)
        del row["event_id"]
        client = self.client({"today": [row]}, [[]], {})
        with self.assertRaisesRegex(IncompleteScan, "without event_id"):
            directories.kenyabuzz(client, self.source)

    def test_explicit_empty_catalogue_is_accepted(self):
        result = directories.kenyabuzz(self.client({}, [[]], {}), self.source)
        self.assertTrue(result.complete)
        self.assertEqual(result.events, [])

    def test_invalid_json_does_not_become_empty_success(self):
        client = Client({f"{self.api}/events/list/landing": "<html>unavailable</html>"})
        with self.assertRaisesRegex(SourceError, "valid JSON"):
            directories.kenyabuzz(client, self.source)

    def test_pagination_bound_is_explicit(self):
        client = self.client({}, [[kb_row(1)]], {})
        with patch.object(directories, "MAX_PAGES", 1), self.assertRaises(IncompleteScan):
            directories.kenyabuzz(client, self.source)


class EventbriteTests(unittest.TestCase):
    def setUp(self):
        self.source = source("eventbrite", "https://www.eventbrite.com/d/kenya/events/")
        self.url = directories.EVENTBRITE_LISTING

    def test_reads_only_kenya_pages_with_complete_pagination(self):
        client = Client({
            self.url: eb_page([eb_row(1)], pages=2, total=2, continuation="next"),
            self.url + "?page=2": eb_page([eb_row(2)], page=2, pages=2, total=2),
        })
        result = directories.eventbrite(client, self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0]["start_date"], str(timestamp("2026-11-07T10:00:00+03:00")))
        self.assertTrue(all("/d/kenya/" in u for u, _ in client.calls))

    def test_early_missing_continuation_is_not_complete(self):
        client = Client({self.url: eb_page([eb_row(1)], pages=2, total=2)})
        with self.assertRaisesRegex(IncompleteScan, "before"):
            directories.eventbrite(client, self.source)

    def test_last_page_with_continuation_is_not_complete(self):
        client = Client({self.url: eb_page([eb_row(1)], continuation="unexpected")})
        with self.assertRaisesRegex(IncompleteScan, "final page"):
            directories.eventbrite(client, self.source)

    def test_changing_totals_are_reported(self):
        client = Client({
            self.url: eb_page([eb_row(1)], pages=2, total=3, continuation="next"),
            self.url + "?page=2": eb_page([eb_row(2)], page=2, pages=2, total=2),
        })
        result = directories.eventbrite(client, self.source)
        self.assertFalse(result.complete)
        self.assertTrue(any("not atomic" in n for n in result.notes))

    def test_missing_start_is_incomplete(self):
        client = Client({self.url: eb_page([eb_row(1, start_time=None)])})
        result = directories.eventbrite(client, self.source)
        self.assertFalse(result.complete)
        self.assertTrue(any("start date/time" in n for n in result.notes))

    def test_missing_optional_metadata_does_not_make_eventbrite_catalogue_incomplete(self):
        row = eb_row(1, end_date=None, end_time=None,
                     primary_venue={"name": "", "address": {"country": "KE"}})
        result = directories.eventbrite(Client({self.url: eb_page([row])}), self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["location"], "")
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(any("location is unknown" in note for note in result.notes))
        self.assertTrue(any("optional end" in note for note in result.notes))

    def test_online_and_explicit_foreign_events_are_excluded(self):
        foreign = eb_row(2, primary_venue={"name": "Foreign Hall", "address": {"country": "UG"}})
        client = Client({self.url: eb_page([eb_row(1), foreign, eb_row(3, is_online_event=True)])})
        result = directories.eventbrite(client, self.source)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 1)

    def test_unverified_country_is_incomplete(self):
        client = Client({self.url: eb_page([eb_row(1, primary_venue={})])})
        self.assertFalse(directories.eventbrite(client, self.source).complete)

    def test_changed_page_number_is_an_error(self):
        client = Client({self.url: eb_page([eb_row(1)], page=2)})
        with self.assertRaisesRegex(IncompleteScan, "wrong page"):
            directories.eventbrite(client, self.source)

    def test_duplicate_pages_are_detected(self):
        client = Client({
            self.url: eb_page([eb_row(1)], pages=2, total=2, continuation="next"),
            self.url + "?page=2": eb_page([eb_row(1)], page=2, pages=2, total=2),
        })
        with self.assertRaisesRegex(IncompleteScan, "repeated"):
            directories.eventbrite(client, self.source)

    def test_missing_server_payload_is_error(self):
        with self.assertRaises(SourceError):
            directories.eventbrite(Client({self.url: "<html>challenge</html>"}), self.source)

    def test_malformed_nested_venue_is_reported_as_incomplete(self):
        client = Client({self.url: eb_page([eb_row(1, primary_venue="unexpected")])})
        result = directories.eventbrite(client, self.source)
        self.assertFalse(result.complete)
        self.assertTrue(any("invalid Eventbrite venue" in n for n in result.notes))

    def test_verified_empty_result_is_accepted(self):
        result = directories.eventbrite(Client({self.url: eb_page([], pages=0)}), self.source)
        self.assertTrue(result.complete)
        self.assertEqual(result.events, [])


class LimitedDirectoryTests(unittest.TestCase):
    def test_explicit_tba_can_be_preserved_without_inventing_dates(self):
        events, notes = {}, []
        directories._record(
            events, notes, source("example", "https://example.com/events"),
            native_id="announced", name="Announced event", location="Nairobi",
            url="https://example.com/event/announced", start=None, date_tba=True,
        )
        self.assertEqual(notes, [])
        self.assertIsNone(events["example-announced"]["start_date"])
        self.assertIsNone(events["example-announced"]["end_date"])
        self.assertEqual(events["example-announced"]["date_status"], "to_be_announced")

    def test_tba_never_masks_malformed_nonempty_dates(self):
        events, notes = {}, []
        directories._record(
            events, notes, source("example", "https://example.com/events"),
            native_id="broken", name="Broken date", location="Nairobi",
            url="https://example.com/event/broken", start="not-a-date", date_tba=True,
        )
        self.assertEqual(events, {})
        self.assertTrue(any("Unrecognized event datetime" in note for note in notes))

    def test_visitnairobi_never_fetches_disallowed_feed_or_stale_snapshot(self):
        client = Client({})
        with self.assertRaisesRegex(SourceBlocked, "KenyaBuzz snapshot"):
            directories.visitnairobi(client, source("visitnairobi", "https://visitnairobi.city/events"))
        self.assertEqual(client.calls, [])

    def test_rada_empty_widget_is_not_empty_full_catalogue(self):
        client = Client({directories.RADA_WIDGET: "<p>No matching events listed right now</p>"})
        result = directories.rada_nightlife(
            client, source("rada-nightlife", "https://nightlife.rada.club"))
        self.assertEqual(result.events, [])
        self.assertFalse(result.complete)
        self.assertTrue(any("only DJ events" in n for n in result.notes))

    def test_onepass_traverses_filtered_pages_but_does_not_infer_dates(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({
            url: onepass_card("first") + '<a href="?country=KE&amp;page=2">Next →</a>',
            url + "&page=2": onepass_card("second"),
        })
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 2)
        first = result.events[0]
        self.assertEqual(first["id"], "onepassafrica-first")
        self.assertEqual(first["url"], "https://www.onepassafrica.com/api/discover/first/go")
        self.assertIsNone(first["start_date"])
        self.assertIsNone(first["end_date"])
        self.assertEqual(first["date_status"], "unconfirmed")
        self.assertEqual(first["date_label"], "Wed, 11 Nov · 07:00 GMT+3")
        self.assertEqual(first["upstream_source"], "AllEvents Nairobi")
        self.assertTrue(any("Visited 2 distinct listing pages; observed 2 cards" in n
                            for n in result.notes))
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all("/api/" not in u for u, _ in client.calls))

    def test_onepass_all_112_cards_across_five_pages_are_retained(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        responses = {}
        identity = 0
        for page, count in enumerate([24, 24, 24, 24, 16], 1):
            page_url = url if page == 1 else url + f"&page={page}"
            text = f"<p>112 events · Kenya</p><p>Page {page} of 5</p>"
            for _ in range(count):
                identity += 1
                text += onepass_card(f"opaque{identity}")
            if page < 5:
                text += f'<a href="?country=KE&amp;page={page+1}">Next →</a>'
            responses[page_url] = text
        client = Client(responses)
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(result.events), 112)
        self.assertEqual(len({event["id"] for event in result.events}), 112)
        self.assertFalse(result.complete)
        self.assertEqual(len(client.calls), 5)
        self.assertTrue(all(not detail for _, detail in client.calls))
        self.assertTrue(all(event["date_status"] == "unconfirmed" for event in result.events))
        self.assertTrue(any("Visited 5 distinct listing pages; observed 112 cards" in note
                            for note in result.notes))

    def test_onepass_bad_record_is_reported_without_discarding_other_cards(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: onepass_card("valid") + onepass_card("missing", date_label="")})
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(result.events), 1)
        self.assertFalse(result.complete)
        self.assertTrue(any("missing published OnePass date label" in note for note in result.notes))

    def test_onepass_repeated_same_card_is_deduplicated_without_false_conflict(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: onepass_card("same") + onepass_card("same")})
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(result.events), 1)
        self.assertFalse(any("Conflicting duplicate" in note for note in result.notes))
        self.assertTrue(any("observed 2 cards; emitted 1 distinct" in note for note in result.notes))

    def test_onepass_conflicting_duplicate_reports_and_retains_first(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: onepass_card("same") + onepass_card("same", date_label="Fri, 13 Nov")})
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(result.events), 1)
        self.assertTrue(any("Conflicting duplicate" in note for note in result.notes))

    def test_onepass_missing_next_before_final_indicator_is_reported(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: "<p>Page 1 of 5</p>" + onepass_card("first")})
        result = directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(result.events), 1)
        self.assertTrue(any("Next link missing" in note for note in result.notes))

    def test_onepass_missing_attribution_preserves_record_with_warning(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        card = onepass_card("first").replace("<span>via AllEvents Nairobi</span>", "")
        result = directories.onepassafrica(Client({url: card}), source("onepassafrica", url))
        self.assertEqual(len(result.events), 1)
        self.assertTrue(any("no upstream attribution" in note for note in result.notes))

    def test_onepass_pagination_limit_is_explicit(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: onepass_card("first") + '<a href="?country=KE&amp;page=2">Next →</a>'})
        with patch.object(directories, "MAX_PAGES", 1), self.assertRaisesRegex(IncompleteScan, "safety"):
            directories.onepassafrica(client, source("onepassafrica", url))

    def test_onepass_next_cannot_skip_a_page(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: onepass_card("first") + '<a href="?country=KE&amp;page=3">Next →</a>'})
        with self.assertRaisesRegex(IncompleteScan, "skips or repeats"):
            directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(client.calls), 1)

    def test_onepass_rejects_lost_country_filter(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        client = Client({url: '<a href="/api/discover/id/go">Event</a><a href="?page=2">Next →</a>'})
        with self.assertRaisesRegex(IncompleteScan, "country=KE"):
            directories.onepassafrica(client, source("onepassafrica", url))
        self.assertEqual(len(client.calls), 1)

    def test_onepass_unrecognized_markup_is_error(self):
        url = "https://www.onepassafrica.com/discover?country=KE"
        with self.assertRaises(SourceError):
            directories.onepassafrica(Client({url: "<html></html>"}), source("onepassafrica", url))

    def test_allevents_festival_details_are_useful_but_never_complete(self):
        url = "https://allevents.in/nairobi"
        detail_url = url + "/example-festival/200000000001"
        row = {
            "@type": "Festival", "name": "Example Festival", "url": detail_url,
            "startDate": "2026-11-14", "endDate": "2026-11-14",
            "location": {"name": "Nairobi Hall", "address": {"addressCountry": "KE"}},
        }
        detail = {**row, "startDate": "2026-11-14T10:00:00+03:00",
                  "endDate": "2026-11-14T22:00:00+03:00"}
        def schema(value):
            return '<script type="application/ld+json">' + json.dumps(value) + "</script>"
        client = Client({
            url: schema([row]) + schema({"@type": "ItemList", "itemListElement": [{"item": row}]}),
            detail_url: schema(detail),
        })
        result = directories.allevents(client, source("allevents", url))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["id"], "allevents-200000000001")
        self.assertIn((detail_url, True), client.calls)
        self.assertTrue(any("Not available at this moment" in n for n in result.notes))
        self.assertTrue(all("/api/" not in u for u, _ in client.calls))

    def test_allevents_missing_detail_is_not_silently_dropped(self):
        url = "https://allevents.in/nairobi"
        detail_url = url + "/example/200000000001"
        row = {"name": "Example", "url": detail_url, "startDate": "2026-11-14"}
        listing = '<script type="application/ld+json">' + json.dumps(row) + "</script>"
        result = directories.allevents(Client({url: listing, detail_url: "<html></html>"}),
                                       source("allevents", url))
        self.assertFalse(result.complete)
        self.assertTrue(any("missing or ambiguous" in n for n in result.notes))

    def test_adapter_registry_covers_exact_assigned_scope(self):
        self.assertEqual(set(directories.ADAPTERS),
                         {"kenyabuzz", "visitnairobi", "eventbrite", "rada-nightlife",
                          "onepassafrica", "allevents"})


if __name__ == "__main__":
    unittest.main()
