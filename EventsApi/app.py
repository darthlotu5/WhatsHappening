import os

from flask import Flask, jsonify
from flask.views import MethodView
import requests

from shared.persistors.cosmos_client import DundaaCosmosClient
from shared.persistors.persistor import PersistorSetting
from shared.services import events_api_setting as service_setting
from shared.event_catalog import merge_event_catalog
from EventScrapper.sources import SOURCES
from EventScrapper.state import BlobStateStore

app = Flask(__name__)

cosmos_client = DundaaCosmosClient(
    cosmos_endpoint=os.environ.get("COSMOS_ACCOUNT_URI"),
    primary_key=os.environ.get("COSMOS_ACCOUNT_KEY")
)

persistor_setting = PersistorSetting(
    container_id=service_setting.cosmos_container_id,
    database_id=service_setting.cosmos_database_id,
    partion_key="/site_name"
)


class EventsAPI(MethodView):

    def get(self):
        events = cosmos_client.query(
            persistor_setting
        )
        if len(events) == 0:
            app.logger.info("Could not find events. scheduling scrapper run")
            requests.post(
                f"{os.environ['DISCOVERY_API_URL'].rstrip('/')}/api/scrap",
                json={},
                timeout=15,
            ).raise_for_status()

        return jsonify({
            "events": merge_event_catalog(events)
        })


class RecommendedEventsApi(MethodView):

    def get(self, user_id):
        events = cosmos_client.query(persistor_setting)
        return jsonify({
            "events": merge_event_catalog(events)
        })


app.add_url_rule(
    '/events/',
    view_func=EventsAPI.as_view('events')
)

@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/sources")
def sources():
    store = BlobStateStore()
    return jsonify(sources=[
        {"source": source.id, "name": source.name, "url": source.listing_url, **store.status(source.id)}
        for source in SOURCES
    ])

app.add_url_rule(
    '/events/recommended/<string:user_id>',
    view_func=RecommendedEventsApi.as_view('recommended_events')
)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000
    )
