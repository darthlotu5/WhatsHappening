import os

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueServiceClient


def storage_client(client_type):
    connection = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection:
        return client_type.from_connection_string(connection)
    variable = (
        "AZURE_STORAGE_BLOB_ENDPOINT"
        if client_type is BlobServiceClient
        else "AZURE_STORAGE_QUEUE_ENDPOINT"
    )
    return client_type(
        account_url=os.environ[variable],
        credential=DefaultAzureCredential(),
    )


def blob_service():
    return storage_client(BlobServiceClient)


def queue_service():
    return storage_client(QueueServiceClient)
