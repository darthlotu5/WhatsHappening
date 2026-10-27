import importlib
import unittest
from unittest.mock import Mock, patch

from EventScrapper.sources import SOURCES


class DiscoveryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("shared.messengers.queue_sdk.DundaaQueueSDK"):
            cls.module = importlib.import_module("EventDiscoveryApi.app")

    def setUp(self):
        self.client = self.module.app.test_client()
        self.messenger = Mock()
        self.patcher = patch.object(self.module, "messenger", self.messenger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_empty_object_schedules_all_nineteen_sources(self):
        response = self.client.post("/api/scrap", json={})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["sources"], 19)
        self.assertEqual(self.messenger.send_message.call_count, 19)

    def test_specific_source_schedules_only_that_provider(self):
        response = self.client.post("/api/scrap", json={"source": "ticketsasa"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["sources"], 1)
        self.assertEqual(self.messenger.send_message.call_count, 1)

    def test_invalid_source_and_json_do_not_schedule_any_work(self):
        for value in ({"source": "missing"}, {"source": []}, {"source": {}}, [], None):
            with self.subTest(value=value):
                if value is None:
                    response = self.client.post("/api/scrap", data="null", content_type="application/json")
                else:
                    response = self.client.post("/api/scrap", json=value, content_type="application/json")
                self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/scrap", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.messenger.send_message.assert_not_called()


class EventsSourceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("shared.persistors.cosmos_client.DundaaCosmosClient"):
            cls.module = importlib.import_module("EventsApi.app")

    def test_source_status_contains_all_registered_sources(self):
        with patch.object(self.module, "BlobStateStore") as store:
            store.return_value.status.side_effect = lambda key: {"source": key, "status": "not_run"}
            response = self.module.app.test_client().get("/sources")
        self.assertEqual(response.status_code, 200)
        reports = response.json["sources"]
        self.assertEqual({row["source"] for row in reports}, {source.id for source in SOURCES})
        self.assertTrue(all(row["status"] == "not_run" for row in reports))

    def test_catalog_keeps_distinct_dates_for_the_same_venue_and_event_url(self):
        first = {"id": "one", "name": "Theatre", "location": "Nairobi", "start_date": "1796500000",
                 "url": "https://events.example/theatre", "site_name": "kenyabuzz"}
        second = {**first, "id": "two", "start_date": "1796600000"}
        with patch.object(self.module, "cosmos_client") as cosmos:
            cosmos.query.return_value = [first, second]
            response = self.module.app.test_client().get("/events/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["events"]), 2)


if __name__ == "__main__":
    unittest.main()
