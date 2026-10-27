import json
import logging
import os

from flask import Flask, jsonify, request

from shared.contracts.Messages import EventBlobCreated, ScrapSite
from shared.messengers.messenger import MessengerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import event_discovery_setting

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
messenger = DundaaQueueSDK()


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/api/scrap")
def discover_events():
    delay = int(os.environ.get("SCRAPER_DELAY_SECONDS", "0"))
    messenger.send_message(
        data=ScrapSite(delay=delay).to_json(),
        messenger_setting=MessengerSetting(
            service_name=event_discovery_setting.delayed_exchange,
            key=event_discovery_setting.key,
        ),
        delay=delay,
    )
    return jsonify(message="Accepted"), 202


@app.post("/api/NewEvents")
def new_events():
    payload = request.get_json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        app.logger.warning("Invalid blob event request")
        return jsonify(error="A blob event data object is required"), 400
    data = {key: value for key, value in payload["data"].items() if key != "storageDiagnostics"}
    try:
        message = EventBlobCreated(**data)
    except TypeError:
        app.logger.warning("Invalid blob event fields")
        return jsonify(error="Invalid blob event fields"), 400
    messenger.send_message(
        data=message.to_json(),
        messenger_setting=MessengerSetting(
            service_name=event_discovery_setting.name,
            key=event_discovery_setting.key,
        ),
    )
    return jsonify(message="Accepted"), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
