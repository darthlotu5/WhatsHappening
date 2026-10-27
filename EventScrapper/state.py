import gzip
import hashlib
import json
import os
from pathlib import Path

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

from shared.azure_clients import blob_service

CONTAINER = "scrape-state"


def new_state():
    return {"version": 1, "fingerprints": {}, "pages": {}, "robots": {}, "report": {}}


class BlobStateStore:
    def __init__(self, service=None):
        self.service = service if service is not None else blob_service()
        try:
            self.service.create_container(CONTAINER)
        except ResourceExistsError:
            pass

    def load(self, source_id):
        blob = self.service.get_blob_client(CONTAINER, f"state/{source_id}.json.gz")
        try:
            download = blob.download_blob()
            return json.loads(gzip.decompress(download.readall())), download.properties.etag
        except ResourceNotFoundError:
            return new_state(), None

    def save(self, source_id, state, etag):
        blob = self.service.get_blob_client(CONTAINER, f"state/{source_id}.json.gz")
        body = gzip.compress(json.dumps(state, ensure_ascii=False).encode())
        options = {"overwrite": True, "etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {"overwrite": False}
        blob.upload_blob(body, content_settings=ContentSettings(content_type="application/gzip"), **options)
        self.service.get_blob_client(CONTAINER, f"status/{source_id}.json").upload_blob(
            json.dumps(state["report"]), overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

    def status(self, source_id):
        try:
            return json.loads(self.service.get_blob_client(CONTAINER, f"status/{source_id}.json").download_blob().readall())
        except ResourceNotFoundError:
            return {"source": source_id, "status": "not_run"}


class LocalStateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(self, source_id):
        path = self.directory / f"{source_id}.json.gz"
        if not path.exists():
            return new_state(), None
        body = path.read_bytes()
        return json.loads(gzip.decompress(body)), hashlib.sha256(body).hexdigest()

    def save(self, source_id, state, etag):
        path = self.directory / f"{source_id}.json.gz"
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != etag:
            raise RuntimeError(f"Concurrent local scrape state update for {source_id}")
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(gzip.compress(json.dumps(state, ensure_ascii=False).encode()))
        temporary.replace(path)

    def status(self, source_id):
        return self.load(source_id)[0].get("report") or {"source": source_id, "status": "not_run"}
