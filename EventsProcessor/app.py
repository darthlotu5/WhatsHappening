import json
import logging
import os
from urllib.parse import unquote, urlparse

from shared.contracts.Messages import EventBlobCreated
from shared.messengers.azure_blob import DundaaBlobClient
from shared.messengers.messenger import MessageConsumerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.persistors.cosmos_client import DundaaCosmosClient
from shared.persistors.persistor import PersistorSetting
from shared.services import event_processor_setting as service_setting

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def get_blob(data):
    parts = urlparse(data.url)
    expected = os.environ["AZURE_STORAGE_BLOB_ENDPOINT"]
    if parts.scheme != "https" or parts.netloc != urlparse(expected).netloc:
        raise ValueError("Blob event must refer to this application's storage account")
    container, path = unquote(parts.path.lstrip("/")).split("/", 1)
    return DundaaBlobClient(logger=logger).read(container, path)


def write_cosmos(ch, method, properties, body):
    message = EventBlobCreated(**json.loads(body.decode()))
    events = get_blob(message)
    if not isinstance(events, list):
        raise ValueError("Blob must contain an event array")
    cosmos = DundaaCosmosClient(
        cosmos_endpoint=os.environ["COSMOS_ACCOUNT_URI"],
        primary_key=os.environ.get("COSMOS_ACCOUNT_KEY"),
    )
    cosmos.persist(
        persitor_setting=PersistorSetting(
            container_id=service_setting.cosmos_container_id,
            database_id=service_setting.cosmos_database_id,
            partion_key="/site_name",
        ),
        data=events,
    )
    logger.info("Persisted %d events", len(events))


if __name__ == "__main__":
    DundaaQueueSDK().consume_messages(
        consumer_settings=MessageConsumerSetting(
            service_name=service_setting.listen.exchange,
            listen_keys=service_setting.listen.keys,
        ),
        callback=write_cosmos,
    )
