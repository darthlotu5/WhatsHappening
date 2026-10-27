import json
import logging
import uuid

from azure.core.exceptions import ResourceExistsError

from shared.azure_clients import blob_service
from shared.contracts.Messages import EventBlobCreated
from shared.messengers.messenger import BlobMessengerSetting, Messenger, MessengerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import event_discovery_setting


class DundaaBlobClient(Messenger):
    def __init__(self, connection_string=None, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        if connection_string:
            from azure.storage.blob import BlobServiceClient
            self.blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        else:
            self.blob_service_client = blob_service()

    def read(self, container_name, blob_path):
        blob = self.blob_service_client.get_blob_client(container_name, blob_path)
        return json.loads(blob.download_blob().readall())

    def write(self, data, container_name, folder=None):
        try:
            self.blob_service_client.create_container(container_name)
        except ResourceExistsError:
            pass
        filename = f"{uuid.uuid4()}.json"
        path = f"{folder}/{filename}" if folder else filename
        blob = self.blob_service_client.get_blob_client(container_name, path)
        body = json.dumps(data)
        result = blob.upload_blob(body, overwrite=False)
        message = EventBlobCreated(
            api="PutBlob", clientRequestId=str(uuid.uuid4()), requestId=str(uuid.uuid4()),
            eTag=str(result["etag"]), contentType="application/json",
            contentLength=len(body.encode("utf-8")), blobType="BlockBlob",
            url=blob.url, sequencer="",
        )
        DundaaQueueSDK().send_message(
            data=message.to_json(),
            messenger_setting=MessengerSetting(
                service_name=event_discovery_setting.name,
                key=event_discovery_setting.key,
            ),
        )
        self.logger.info("Uploaded %d events and queued processing for %s", len(data), path)
        return blob.url

    def send_message(self, messenger_setting: BlobMessengerSetting, data):
        return self.write(data, messenger_setting.container_name, messenger_setting.key)
