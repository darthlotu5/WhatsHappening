import datetime
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.core.exceptions import ResourceExistsError

from EventScrapper.base_extractor import BaseExtractor, WebScrapperArgs, WebScrapperResponse
from EventScrapper.ticket_sasa import ScrapTicketSasa
from shared.contracts.Messages import EventBlobCreated
from shared.contracts.event_model import Event
from shared.messengers.messenger import MessengerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK, queue_name


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.queue = self.service.get_queue_client.return_value
        self.sdk = DundaaQueueSDK(self.service)
        self.settings = MessengerSetting(key="discovered_events", service_name="DundaaEvents")

    def test_queue_names_match_azure_rules(self):
        self.assertEqual(queue_name("DundaaEvents", "discovered_events"), "dundaaevents-discovered-events")
        self.assertEqual(
            queue_name("x-delayed-dundaaevents", "discovered_events"),
            "x-delayed-dundaaevents-discovered-events",
        )
        with self.assertRaises(ValueError):
            queue_name("invalid/name", "key")

    def test_existing_queue_is_not_an_error(self):
        self.queue.create_queue.side_effect = ResourceExistsError("exists")
        self.sdk.send_message("{}", self.settings, delay=1.5)
        self.queue.send_message.assert_called_once_with("{}", visibility_timeout=2)

    def test_publish_failure_is_not_silenced(self):
        self.queue.send_message.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            self.sdk.send_message("{}", self.settings)

    def test_acknowledgment_only_after_success(self):
        message = SimpleNamespace(id="id", pop_receipt="receipt", content='{"ok": true}')
        callback = Mock()
        self.sdk.process_message(self.queue, message, callback)
        callback.assert_called_once_with(None, None, None, b'{"ok": true}')
        self.queue.delete_message.assert_called_once_with("id", "receipt")

    def test_failed_handler_keeps_message_for_retry(self):
        message = SimpleNamespace(id="id", pop_receipt="receipt", content="{}")
        with self.assertRaises(ValueError):
            self.sdk.process_message(self.queue, message, Mock(side_effect=ValueError("invalid")))
        self.queue.delete_message.assert_not_called()

    def test_invalid_delay_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sdk.send_message("{}", self.settings, delay=-1)


class ScraperTests(unittest.TestCase):
    def test_event_lists_are_initialized_per_extractor(self):
        args = WebScrapperArgs(
            success_status_codes=[200], site_name="ticketsasa",
            site_url="https://www.ticketsasa.com", messenger=None, messenger_setting=None,
        )
        first, second = BaseExtractor(args), BaseExtractor(args)
        first.events.append("event")
        self.assertEqual(second.events, [])

    def test_response_details_is_a_dataclass_field(self):
        response = WebScrapperResponse(status_code=500, status_details="upstream unavailable")
        self.assertEqual(response.status_details, "upstream unavailable")

    def test_clock_parses_minutes_not_seconds(self):
        self.assertEqual(ScrapTicketSasa._parse_time(" 10:30 AM "), datetime.time(10, 30))

    def test_json_events_do_not_need_generated_protobuf(self):
        event = Event(
            "Test event", "Nairobi", datetime.date(2026, 10, 22),
            datetime.date(2026, 10, 22), datetime.time(10), datetime.time(12),
            "https://example.com/image.jpg", "test", "https://example.com/event",
        )
        data = json.loads(event.serialize("json"))
        self.assertEqual(data["name"], "Test event")
        self.assertGreater(float(data["end_date"]), float(data["start_date"]))

    def test_modern_nuxt_records_are_deduplicated_and_resolved(self):
        args = WebScrapperArgs(
            success_status_codes=[101], site_name="ticketsasa",
            site_url="https://ticketsasa.com", messenger=None, messenger_setting=None,
        )
        record = {"id": 2, "name": 3, "loc": 4, "slug_name": 5, "main_image": 6, "start_at": 7}
        payload = [record, record, 42, "Kenya event", "Nairobi", "kenya-event",
                   "https://example.com/banner.jpg", "Sat 31 Oct 2026 10:30 AM"]
        events = ScrapTicketSasa(args)._parse_modern_events(json.dumps(payload))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].id, "ticketsasa-42")
        self.assertEqual(events[0].url, "https://ticketsasa.com/events/kenya-event")
        serialized = json.loads(events[0].serialize("json"))
        expected = datetime.datetime(2026, 10, 31, 7, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(float(serialized["start_date"]), expected)

    def test_unrecognized_modern_payload_fails_explicitly(self):
        args = WebScrapperArgs(
            success_status_codes=[101], site_name="ticketsasa",
            site_url="https://ticketsasa.com", messenger=None, messenger_setting=None,
        )
        with self.assertRaises(ValueError):
            ScrapTicketSasa(args)._parse_modern_events("[]")


class ProcessorTests(unittest.TestCase):
    def message(self, url):
        return EventBlobCreated(
            api="PutBlob", clientRequestId="client", requestId="request", eTag="etag",
            contentType="application/json", contentLength=2, blobType="BlockBlob",
            url=url, sequencer="",
        )

    @patch.dict(os.environ, {"AZURE_STORAGE_BLOB_ENDPOINT": "https://example.blob.core.windows.net"})
    def test_blob_path_preserves_nested_folders(self):
        from EventsProcessor.app import get_blob
        with patch("EventsProcessor.app.DundaaBlobClient") as blob:
            get_blob(self.message("https://example.blob.core.windows.net/events/a/b.json"))
            blob.return_value.read.assert_called_once_with("events", "a/b.json")

    @patch.dict(os.environ, {"AZURE_STORAGE_BLOB_ENDPOINT": "https://example.blob.core.windows.net"})
    def test_foreign_blob_account_is_rejected(self):
        from EventsProcessor.app import get_blob
        with self.assertRaises(ValueError):
            get_blob(self.message("https://other.blob.core.windows.net/events/b.json"))

    @patch.dict(os.environ, {"COSMOS_ACCOUNT_URI": "https://cosmos.example"})
    def test_blob_ingestion_persists_expected_events(self):
        from EventsProcessor.app import write_cosmos
        with patch("EventsProcessor.app.get_blob", return_value=[{"id": "one"}]), \
                patch("EventsProcessor.app.DundaaCosmosClient") as cosmos:
            write_cosmos(None, None, None, self.message("https://example.com/a/b.json").to_json().encode())
            self.assertEqual(cosmos.return_value.persist.call_args.kwargs["data"], [{"id": "one"}])

    def test_invalid_blob_is_not_acknowledged_as_success(self):
        from EventsProcessor.app import write_cosmos
        with patch("EventsProcessor.app.get_blob", return_value={"not": "events"}):
            with self.assertRaises(ValueError):
                write_cosmos(None, None, None, self.message("https://example.com/a/b.json").to_json().encode())


if __name__ == "__main__":
    unittest.main()
