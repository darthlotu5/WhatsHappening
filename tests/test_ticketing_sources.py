import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from EventScrapper.http_client import Response
from EventScrapper.models import IncompleteScan, Source, SourceBlocked, SourceError, timestamp
from EventScrapper.providers import ticketing


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.cached_urls = set()

    def get(self, url, *, detail=False, headers=None):
        self.calls.append((url, detail))
        value = self.responses[url]
        status = 200
        if isinstance(value, tuple):
            status, value = value
        text = value if isinstance(value, str) else json.dumps(value)
        return Response(url, text, status, {}, from_cache=url in self.cached_urls)


def source(name):
    return Source(name, name, "https://example.test/unused", (), name)


def ld(url, number=1, **overrides):
    return {
        "@context": "https://schema.org", "@type": "Event",
        "name": f"Sample event {number}", "startDate": "2026-11-07T10:00:00+03:00",
        "endDate": "2026-11-07T12:00:00+03:00",
        "location": {"@type": "Place", "name": "Sample Hall"},
        "image": "https://images.example.test/sample.jpg", "url": url, **overrides,
    }


def ld_html(data, graph=False):
    if graph:
        data = {"@context": "https://schema.org", "@graph": [data]}
    return '<script type="application/ld+json">' + json.dumps(data) + "</script>"


def rsc(key, value):
    record = "1:" + json.dumps({"props": {key: value}}) + "\n"
    return "<script>self.__next_f.push(" + json.dumps([1, record]) + ")</script>"


def sitemap(urls):
    return ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{url}</loc></url>" for url in urls) + "</urlset>")


def pager(page=1, next_page=None, last=None, path="/"):
    anchors = ""
    if next_page is not None:
        anchors += f'<a aria-label="Next »" href="{path}?page={next_page}">›</a>'
    else:
        anchors += '<li class="disabled" aria-label="Next »"><span>›</span></li>'
    if last:
        anchors += f'<a href="{path}?page={last}">{last}</a>'
    return f'<ul class="pagination"><li>{page}</li>{anchors}</ul>'


def tikiti_card(number):
    return (f'<a class="event-card" href="https://tikiti.co.ke/e/event-{number}">'
            f'<h3>Sample event {number}</h3></a>')


def tk_card(number, *, start="Nov 07, 2026 10:00", end="Nov 07, 2026 12:00"):
    return f"""
    <div class="event-card">
      <a href="https://ticketskenya.com/all-events/event-{number}"><img src="/images/{number}.jpg"></a>
      <div class="card-body">
        <h5 class="card-title"><a href="https://ticketskenya.com/all-events/event-{number}">Event {number}</a></h5>
        <div>Start At: <span>{start}</span></div>
        <div>End At: <span>{end}</span></div>
        <div><i class="fa-map-location-dot"></i><span>Sample Hall</span></div>
      </div>
    </div>
    """


def tipsi_event(number, **overrides):
    return {
        "id": f"uuid-{number}", "title": f"Sample Event {number}",
        "start_at": "2026-11-07T10:00:00+03:00",
        "end_at": "2026-11-07T12:00:00+03:00",
        "venue": None, "venue_text": "Sample Hall",
        "banner_url": "https://media.example.test/poster.webp", **overrides,
    }


def tipsi_url(kind="catalog", page=1):
    params = {"ordering": "start_at" if kind == "catalog" else "-start_at",
              "page_size": 24 if kind == "catalog" else 16, "page": page}
    if kind == "catalog":
        params["status"] = "APPROVED"
    return f"{ticketing.TIPSI_BASE}/api/proxy/events/{kind}?" + urlencode(params)


def api_page(events, total=None, next_page=None, kind="catalog"):
    next_url = (f"https://api.tipsitickets.com/api/public-prefix/events/{kind}/?page={next_page}"
                if next_page is not None else None)
    return {"count": len(events) if total is None else total, "next": next_url,
            "previous": None, "results": events}


class SitemapTests(unittest.TestCase):
    def test_mookh_uses_sitemap_details_not_disallowed_api(self):
        url = "https://mookh.com/sample-event/"
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap(
                ["https://mookh.com/", "https://mookh.com/legal/privacy/", url]),
            url: ld_html(ld(url)),
        })
        result = ticketing.mookh(client, source("mookh"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["id"], "mookh-sample-event")
        self.assertEqual(client.calls, [
            ("https://mookh.com/sitemap.xml", False), (url, True),
        ])
        self.assertTrue(any("sitemap" in note for note in result.notes))

    def test_mtickets_missing_end_is_explicit_and_not_fabricated(self):
        url = "https://mtickets.com/sample-event/"
        data = ld(url)
        del data["endDate"]
        client = Client({
            "https://mtickets.com/sitemap.xml": sitemap([
                "https://mtickets.com/events/", "https://mtickets.com/tickets/faq/", url]),
            url: ld_html(data),
        })
        result = ticketing.mtickets(client, source("mtickets"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(any("optional end datetime" in note for note in result.notes))
        self.assertTrue(any("Coverage:" in note for note in result.notes))

    def test_missing_detail_record_does_not_make_empty_success(self):
        url = "https://mookh.com/sample-event/"
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap([url]),
            url: "<html>Not an event</html>",
        })
        result = ticketing.mookh(client, source("mookh"))
        self.assertFalse(result.complete)
        self.assertEqual(result.events, [])
        self.assertTrue(any("Event JSON-LD" in note for note in result.notes))

    def test_explicit_visible_tba_is_preserved_without_invented_dates(self):
        url = "https://mookh.com/sample-event/"
        data = ld(url)
        del data["startDate"]
        del data["endDate"]
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap([url]),
            url: ld_html(data) + "<div>Date to be announced</div>",
        })
        result = ticketing.mookh(client, source("mookh"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["start_date"])
        self.assertIsNone(result.events[0]["end_date"])
        self.assertEqual(result.events[0]["date_status"], "to_be_announced")

    def test_explicit_source_tba_label_is_not_parsed_as_a_datetime(self):
        url = "https://mookh.com/sample-event/"
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, startDate="TBA", endDate=None)),
        })
        result = ticketing.mookh(client, source("mookh"))
        self.assertFalse(result.complete)
        self.assertIsNone(result.events[0]["start_date"])
        self.assertIsNone(result.events[0]["end_date"])
        self.assertEqual(result.events[0]["date_status"], "to_be_announced")

    def test_nonempty_malformed_date_is_rejected_even_with_tba_text_elsewhere(self):
        url = "https://mookh.com/sample-event/"
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, startDate="November-ish")) + "<div>Date to be announced</div>",
        })
        result = ticketing.mookh(client, source("mookh"))
        self.assertFalse(result.complete)
        self.assertEqual(result.events, [])
        self.assertTrue(result.notes)

    def test_unavailable_sitemap_is_error(self):
        client = Client({"https://mtickets.com/sitemap.xml": (500, "")})
        with self.assertRaisesRegex(SourceError, "500"):
            ticketing.mtickets(client, source("mtickets"))

    def test_sitemap_without_candidates_is_not_success(self):
        client = Client({"https://mookh.com/sitemap.xml": sitemap(["https://mookh.com/"])})
        with self.assertRaisesRegex(SourceError, "no event"):
            ticketing.mookh(client, source("mookh"))

    def test_index_traversal_and_cycles_are_bounded(self):
        root = '<sitemapindex><sitemap><loc>https://mookh.com/sitemap.xml</loc></sitemap></sitemapindex>'
        client = Client({"https://mookh.com/sitemap.xml": root})
        with self.assertRaisesRegex(IncompleteScan, "Repeated sitemap"):
            ticketing.mookh(client, source("mookh"))

    def test_sitemap_detail_safety_limit_is_not_truncation(self):
        client = Client({"https://mookh.com/sitemap.xml": sitemap([
            "https://mookh.com/event-1/", "https://mookh.com/event-2/"])})
        with patch.object(ticketing, "MAX_DETAILS", 1), self.assertRaises(IncompleteScan):
            ticketing.mookh(client, source("mookh"))

    def test_hustle_uses_marketplace_native_id_and_country_timezone(self):
        url = "https://hustle.events/events/sample-event"
        data = ld(url, startDate="2026-11-07T10:00", endDate="2026-11-07T12:00")
        client = Client({
            f"{ticketing.HUSTLE_BASE}/sitemap.xml": sitemap(
                ["https://hustle.events", "https://hustle.events/discover", url]),
            url: ld_html(data) + rsc("event", {
                "id": "native-event-id", "store_id": "merchant-id", "country": "UG",
            }),
        })
        result = ticketing.hustlesasa(client, source("hustlesasa"))
        self.assertFalse(result.complete)
        self.assertEqual(result.events[0]["id"], "hustlesasa-native-event-id")
        self.assertEqual(result.events[0]["country"], "UG")
        self.assertFalse(any("/_next/" in url or "/api/" in url for url, _ in client.calls))

    def test_hustle_ghana_unpadded_naive_datetimes_retain_event_in_accra_timezone(self):
        url = "https://hustle.events/events/ghana-event"
        data = ld(url, startDate="2026-10-30T18:0", endDate="2026-10-31T17:8")
        client = Client({
            f"{ticketing.HUSTLE_BASE}/sitemap.xml": sitemap([url]),
            url: ld_html(data) + rsc("event", {"id": "ghana-event-id", "country": "GH"}),
        })
        result = ticketing.hustlesasa(client, source("hustlesasa"))
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["id"], "hustlesasa-ghana-event-id")
        self.assertEqual(result.events[0]["country"], "GH")
        self.assertEqual(result.events[0]["start_date"],
                         str(timestamp("2026-10-30T18:00:00+00:00")))
        self.assertEqual(result.events[0]["end_date"],
                         str(timestamp("2026-10-31T17:08:00+00:00")))
        self.assertFalse(result.complete)
        self.assertEqual(result.notes, [
            "Coverage: published sitemap detail URLs, not a verified complete API catalog",
        ])

    def test_hustle_does_not_guess_timezone_for_multizone_country(self):
        url = "https://hustle.events/events/sample-event"
        client = Client({
            f"{ticketing.HUSTLE_BASE}/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, startDate="2026-11-07T10:00")) + rsc(
                "event", {"id": "1", "country": "US"}),
        })
        result = ticketing.hustlesasa(client, source("hustlesasa"))
        self.assertFalse(result.complete)
        self.assertTrue(any("timezone" in note for note in result.notes))

    def test_hustle_reuses_flight_framing_and_ignores_json_looking_text_records(self):
        url = "https://hustle.events/events/sample-event"
        text = 'Synthetic text 🙂 1:{"event":{"id":"not-an-event","country":"KE"}}'
        transport = ("a:T" + format(len(text.encode()), "x") + "," + text
                     + "b:" + json.dumps({"props": {"event": {"id": "real-id", "country": "KE"}}})
                     + "\n")
        hydration = ("<script>self.__next_f.push(" + json.dumps([1, transport]) + ")</script>")
        client = Client({
            f"{ticketing.HUSTLE_BASE}/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url)) + hydration,
        })
        result = ticketing.hustlesasa(client, source("hustlesasa"))
        self.assertFalse(result.complete)
        self.assertEqual(result.events[0]["id"], "hustlesasa-real-id")

    def test_hustle_truncated_flight_is_explicit_incomplete(self):
        url = "https://hustle.events/events/sample-event"
        hydration = "<script>self.__next_f.push(" + json.dumps([1, "a:Tff,short"]) + ")</script>"
        client = Client({
            f"{ticketing.HUSTLE_BASE}/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url)) + hydration,
        })
        result = ticketing.hustlesasa(client, source("hustlesasa"))
        self.assertFalse(result.complete)
        self.assertTrue(any("Truncated" in note for note in result.notes))

    def test_blocked_detail_is_not_silently_skipped(self):
        url = "https://mookh.com/sample-event/"
        client = Client({
            "https://mookh.com/sitemap.xml": sitemap([url]), url: (403, "Blocked"),
        })
        with self.assertRaises(SourceBlocked):
            ticketing.mookh(client, source("mookh"))

    @patch.object(ticketing.time, "time", return_value=timestamp("2026-01-01T02:00:00Z"))
    def test_expired_cached_signed_image_revalidates_public_detail_without_stripping_signature(self, _):
        url = "https://mtickets.com/sample-event/"
        expired = ("https://images.example.test/poster.jpg?"
                   "X-Amz-Date=20260101T000000Z&X-Amz-Expires=3600&X-Amz-Signature=synthetic-old")
        fresh = ("https://images.example.test/poster.jpg?"
                 "X-Amz-Date=20260101T020000Z&X-Amz-Expires=3600&X-Amz-Signature=synthetic-new")

        class RefreshClient(Client):
            def get(self, target, *, detail=False, headers=None):
                if target == url and not detail:
                    self.responses[target] = ld_html(ld(url, image=fresh))
                    self.cached_urls.discard(target)
                return super().get(target, detail=detail, headers=headers)

        client = RefreshClient({
            "https://mtickets.com/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, image=expired)),
        })
        client.cached_urls.add(url)
        result = ticketing.mtickets(client, source("mtickets"))
        self.assertFalse(result.complete)
        self.assertIn("X-Amz-Signature=synthetic-new", result.events[0]["banner_url"])
        self.assertEqual(client.calls, [
            ("https://mtickets.com/sitemap.xml", False), (url, True), (url, False),
        ])
        self.assertFalse(any("images.example.test" in target for target, _ in client.calls))

    @patch.object(ticketing.time, "time", return_value=timestamp("2026-01-01T02:00:00Z"))
    def test_expired_image_even_in_fresh_html_is_explicit_incomplete_without_invented_unsigned_url(self, _):
        url = "https://mtickets.com/sample-event/"
        expired = ("https://images.example.test/poster.jpg?"
                   "X-Amz-Date=20260101T000000Z&X-Amz-Expires=3600")
        client = Client({
            "https://mtickets.com/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, image=expired)),
        })
        result = ticketing.mtickets(client, source("mtickets"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["banner_url"], "")
        self.assertTrue(any("signed image" in note for note in result.notes))
        self.assertEqual(len(client.calls), 2)

    @patch.object(ticketing.time, "time", return_value=timestamp("2026-01-01T00:30:00Z"))
    def test_still_valid_cached_signed_image_does_not_trigger_extra_request(self, _):
        url = "https://mtickets.com/sample-event/"
        signed = ("https://images.example.test/poster.jpg?"
                  "X-Amz-Date=20260101T000000Z&X-Amz-Expires=3600")
        client = Client({
            "https://mtickets.com/sitemap.xml": sitemap([url]),
            url: ld_html(ld(url, image=signed)),
        })
        client.cached_urls.add(url)
        result = ticketing.mtickets(client, source("mtickets"))
        self.assertFalse(result.complete)
        self.assertEqual(len(client.calls), 2)


class TikitiTests(unittest.TestCase):
    def test_traverses_short_nonterminal_page_and_final_disabled_next(self):
        first = "https://tikiti.co.ke/"
        second = "https://tikiti.co.ke/?page=2"
        u1, u2 = "https://tikiti.co.ke/e/event-1", "https://tikiti.co.ke/e/event-2"
        event = ld(u1)
        event["exposed-template-instead-of-context"] = event.pop("@context")
        client = Client({
            first: tikiti_card(1) + pager(next_page=2),
            second: tikiti_card(2) + pager(page=2),
            u1: ld_html(event), u2: ld_html(ld(u2, 2)),
        })
        result = ticketing.tikiti(client, source("tikiti"))
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0]["id"], "tikiti-event-1")
        self.assertIn((u1, True), client.calls)
        self.assertIn((second, False), client.calls)

    def test_missing_next_before_numbered_last_page_is_incomplete(self):
        client = Client({"https://tikiti.co.ke/": tikiti_card(1) + pager(last=3)})
        with self.assertRaisesRegex(IncompleteScan, "advertised final"):
            ticketing.tikiti(client, source("tikiti"))

    def test_next_cycle_and_page_gap_are_rejected(self):
        for page in [1, 3]:
            with self.subTest(page=page):
                client = Client({"https://tikiti.co.ke/": tikiti_card(1) + pager(next_page=page)})
                with self.assertRaises(IncompleteScan):
                    ticketing.tikiti(client, source("tikiti"))

    def test_missing_pagination_is_not_assumed_terminal(self):
        client = Client({"https://tikiti.co.ke/": tikiti_card(1)})
        with self.assertRaisesRegex(IncompleteScan, "termination"):
            ticketing.tikiti(client, source("tikiti"))

    def test_missing_detail_date_retains_explicit_issue(self):
        url = "https://tikiti.co.ke/e/event-1"
        data = ld(url)
        del data["startDate"]
        client = Client({
            "https://tikiti.co.ke/": tikiti_card(1) + pager(), url: ld_html(data),
        })
        result = ticketing.tikiti(client, source("tikiti"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["start_date"])
        self.assertEqual(result.events[0]["date_status"], "to_be_announced")
        self.assertTrue(result.notes)

    def test_unavailable_listing_is_not_empty_success(self):
        client = Client({"https://tikiti.co.ke/": "<html>Unavailable</html>"})
        with self.assertRaises(SourceError):
            ticketing.tikiti(client, source("tikiti"))

    def test_page_bound_raises_instead_of_truncating(self):
        url = "https://tikiti.co.ke/e/event-1"
        client = Client({
            "https://tikiti.co.ke/": tikiti_card(1) + pager(next_page=2),
            url: ld_html(ld(url)),
        })
        with patch.object(ticketing, "MAX_PAGES", 1), self.assertRaises(IncompleteScan):
            ticketing.tikiti(client, source("tikiti"))


class TicketsKenyaTests(unittest.TestCase):
    def test_all_pages_deduplicate_repeated_current_section(self):
        initial = "https://ticketskenya.com/all-events"
        client = Client({
            initial: tk_card(1) + tk_card(2) + pager(next_page=2, path="/all-events"),
            initial + "?page=2": tk_card(1) + tk_card(3) + pager(page=2, path="/all-events"),
        })
        result = ticketing.ticketskenya(client, source("ticketskenya"))
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 3)
        self.assertEqual(result.events[0]["start_date"],
                         str(timestamp("2026-11-07T10:00:00+03:00")))
        self.assertEqual(result.events[0]["banner_url"], "https://ticketskenya.com/images/1.jpg")
        self.assertEqual(len(client.calls), 2)
        self.assertFalse(any(detail for _, detail in client.calls))

    def test_optional_missing_end_remains_null_without_incomplete_coverage(self):
        client = Client({"https://ticketskenya.com/all-events": tk_card(1, end="") + pager()})
        result = ticketing.ticketskenya(client, source("ticketskenya"))
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(result.notes)

    def test_bad_card_is_explicit_incomplete(self):
        client = Client({"https://ticketskenya.com/all-events":
                         '<div class="event-card">broken</div>' + pager()})
        with self.assertRaises(IncompleteScan):
            ticketing.ticketskenya(client, source("ticketskenya"))

    def test_missing_venue_is_not_silently_accepted(self):
        html = tk_card(1).replace('<i class="fa-map-location-dot"></i>', "")
        client = Client({"https://ticketskenya.com/all-events": html + pager()})
        result = ticketing.ticketskenya(client, source("ticketskenya"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertTrue(any("location" in note for note in result.notes))

    def test_repeated_current_only_page_cannot_hide_missing_past_pages(self):
        initial = "https://ticketskenya.com/all-events"
        client = Client({
            initial: tk_card(1) + pager(next_page=2, path="/all-events"),
            initial + "?page=2": tk_card(1) + pager(page=2, path="/all-events"),
        })
        with self.assertRaises(IncompleteScan):
            ticketing.ticketskenya(client, source("ticketskenya"))


class TipsiTests(unittest.TestCase):
    def client(self, catalog=None, past=None):
        responses = {
            tipsi_url(): api_page([tipsi_event(1)]),
            tipsi_url("past"): api_page([], kind="past"),
        }
        responses.update(catalog or {})
        responses.update(past or {})
        return Client(responses)

    def test_catalog_and_past_both_paginate_using_proxy_not_next_host(self):
        client = self.client(
            catalog={
                tipsi_url(): api_page([tipsi_event(1)], total=2, next_page=2),
                tipsi_url(page=2): api_page([tipsi_event(2)], total=2),
            },
            past={
                tipsi_url("past"): api_page([tipsi_event(3)], total=2, next_page=2, kind="past"),
                tipsi_url("past", 2): api_page([tipsi_event(4)], total=2, kind="past"),
            },
        )
        result = ticketing.tipsitickets(client, source("tipsitickets"))
        self.assertTrue(result.complete)
        self.assertEqual(len(result.events), 4)
        self.assertTrue(all(url.startswith(ticketing.TIPSI_BASE + "/api/proxy/")
                            for url, _ in client.calls))
        self.assertIn("?eventId=uuid-1", result.events[0]["url"])
        self.assertEqual(result.events[0]["id"], "tipsitickets-uuid-1")

    def test_explicit_zero_both_feeds_is_valid_empty_catalog(self):
        client = self.client(catalog={tipsi_url(): api_page([])})
        result = ticketing.tipsitickets(client, source("tipsitickets"))
        self.assertTrue(result.complete)
        self.assertEqual(result.events, [])

    def test_terminal_count_mismatch_is_not_complete(self):
        result = ticketing.tipsitickets(
            self.client(catalog={tipsi_url(): api_page([tipsi_event(1)], total=2)}),
            source("tipsitickets"),
        )
        self.assertFalse(result.complete)
        self.assertTrue(any("count mismatch" in note for note in result.notes))

    def test_invalid_envelope_and_html_are_errors(self):
        for value in ["<html>Unavailable</html>", {"results": []}, []]:
            with self.subTest(value=value), self.assertRaises(SourceError):
                ticketing.tipsitickets(
                    self.client(catalog={tipsi_url(): value}), source("tipsitickets"))

    def test_repeated_page_cannot_loop(self):
        data = api_page([tipsi_event(1)], total=2, next_page=1)
        with self.assertRaises(IncompleteScan):
            ticketing.tipsitickets(
                self.client(catalog={tipsi_url(): data}), source("tipsitickets"))

    def test_count_changes_mark_nonatomic_snapshot(self):
        client = self.client(catalog={
            tipsi_url(): api_page([tipsi_event(1)], total=2, next_page=2),
            tipsi_url(page=2): api_page([tipsi_event(2)], total=3),
        })
        result = ticketing.tipsitickets(client, source("tipsitickets"))
        self.assertFalse(result.complete)
        self.assertTrue(any("not atomic" in note for note in result.notes))

    def test_missing_uuid_and_start_are_reported(self):
        for row in [tipsi_event(1, id=None), tipsi_event(1, start_at=None)]:
            with self.subTest(row=row):
                result = ticketing.tipsitickets(
                    self.client(catalog={tipsi_url(): api_page([row])}), source("tipsitickets"))
                self.assertFalse(result.complete)
                self.assertTrue(result.notes)

    def test_catalog_null_dates_are_retained_as_uncertain_tba_not_dropped(self):
        result = ticketing.tipsitickets(
            self.client(catalog={tipsi_url(): api_page([
                tipsi_event(1, start_at=None, end_at=None)])}), source("tipsitickets"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["start_date"])
        self.assertIsNone(result.events[0]["end_date"])
        self.assertEqual(result.events[0]["date_status"], "to_be_announced")
        self.assertTrue(any("announcement unverified" in note for note in result.notes))

    def test_no_next_key_is_schema_failure(self):
        value = api_page([])
        del value["next"]
        with self.assertRaises(SourceError):
            ticketing.tipsitickets(
                self.client(catalog={tipsi_url(): value}), source("tipsitickets"))

    def test_wrong_feed_next_is_rejected(self):
        value = api_page([tipsi_event(1)], total=2, next_page=2, kind="past")
        with self.assertRaises(IncompleteScan):
            ticketing.tipsitickets(
                self.client(catalog={tipsi_url(): value}), source("tipsitickets"))

    def test_api_denial_stops_without_empty_result(self):
        client = self.client(catalog={tipsi_url(): (429, "Rate limited")})
        with self.assertRaises(SourceBlocked):
            ticketing.tipsitickets(client, source("tipsitickets"))

    def test_malformed_optional_and_title_shapes_are_reported(self):
        for row in [tipsi_event(1, title=12), tipsi_event(1, banner_variants=["bad"])]:
            with self.subTest(row=row):
                result = ticketing.tipsitickets(
                    self.client(catalog={tipsi_url(): api_page([row])}), source("tipsitickets"))
                self.assertFalse(result.complete)
                self.assertTrue(result.notes)


class PataTicketTests(unittest.TestCase):
    def client(self, *, past=True, native=True, end=True):
        url = "https://pataticket.co.ke/event/sample-event"
        current = ('<div class="event-card" '
                   'onclick="window.location.href=\'/event/sample-event\'">Sample event</div>')
        old = '<div class="past-event-card" onclick="viewPastEvent(99)">Past</div>' if past else ""
        data = ld(url)
        if not end:
            del data["endDate"]
        bootstrap = ('<script>window.PataTicketPageData = { event: '
                     + json.dumps({"id": 55}) + ", ticketTypes: [] };</script>") if native else ""
        return Client({
            "https://pataticket.co.ke/event/": current + old,
            url: '<link rel="canonical" href="https://pataticket.co.ke/event/">'
                 + ld_html(data, graph=True) + bootstrap,
        })

    def test_reads_onclick_graph_and_native_inline_event_id(self):
        client = self.client(past=False)
        result = ticketing.pataticket(client, source("pataticket"))
        self.assertTrue(result.complete)
        self.assertEqual(result.events[0]["id"], "pataticket-55")
        self.assertEqual(result.events[0]["url"], "https://pataticket.co.ke/event/sample-event")
        self.assertTrue(all("?" not in url and ".php" not in url for url, _ in client.calls))
        self.assertTrue(client.calls[-1][1])

    def test_past_resolver_limit_is_honest_incomplete_not_fake_empty(self):
        result = ticketing.pataticket(self.client(), source("pataticket"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertTrue(any("robots-disallowed" in note for note in result.notes))

    def test_missing_native_id_and_end_are_reported(self):
        result = ticketing.pataticket(
            self.client(native=False, past=False, end=False), source("pataticket"))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0]["end_date"])
        self.assertTrue(any("ID missing" in note for note in result.notes))

    def test_unknown_listing_is_error(self):
        client = Client({"https://pataticket.co.ke/event/": "<html>Unavailable</html>"})
        with self.assertRaises(SourceError):
            ticketing.pataticket(client, source("pataticket"))


if __name__ == "__main__":
    unittest.main()
