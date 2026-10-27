import os

from flask import Flask, jsonify
from flask.views import MethodView
import requests

from shared.persistors.cosmos_client import DundaaCosmosClient
from shared.persistors.persistor import PersistorSetting
from shared.services import events_api_setting as service_setting

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
            "events": events
        })


class RecommendedEventsApi(MethodView):

    def get(self, user_id):
        events = cosmos_client.query(persistor_setting)
        return jsonify({
            "events": events
        })


app.add_url_rule(
    '/events/',
    view_func=EventsAPI.as_view('events')
)

@app.get("/health")
def health():
    return jsonify(status="ok")

app.add_url_rule(
    '/events/recommended/<string:user_id>',
    view_func=RecommendedEventsApi.as_view('recommended_events')
)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000
    )
