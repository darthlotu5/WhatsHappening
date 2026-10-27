from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import DefaultAzureCredential
import json

from shared.persistors.persistor import PersistorSetting, Persistor


class DundaaCosmosClient(Persistor):
    def __init__(self, cosmos_endpoint, primary_key):
        if not cosmos_endpoint:
            raise ValueError("COSMOS_ACCOUNT_URI is required")
        self.client = CosmosClient(
            url=cosmos_endpoint,
            credential=primary_key or DefaultAzureCredential(),
        )
        self.database = None


    def __get_db(self, database_id):
        if self.database is None:
            self.database = self.client.get_database_client(database_id)
        return self.database


    def __get_container(self, container_id, database_id: str, partionKeyPath):
        database = self.__get_db(database_id)

        return database.get_container_client(container_id)

    def __read(self, db, container_id: str, object_id: str):
        raise NotImplementedError

    def __fetch_all(self, db, container_name: str):
        container = db.get_container_client(container=container_name)
        items = list(container.read_all_items(max_item_count=10))
        return items

    def __fetch_one(self, id: str, container_name: str, partion_key: str, db):
        container = db.get_container_client(container=container_name)
        item = container.read_item(item=id, partition_key=partion_key)

        return item

    def __write(self, items: [dict], database_name: str, container_name: str, partion_key: str):
        """
        TODO: Use proxy objects here
        :param items:
        :param database_name:
        :param container_name:
        :return:
        """
        container = self.__get_container(
            container_id=container_name,
            database_id=database_name,
            partionKeyPath=partion_key
        )

        for item in items:
            try:
                if not isinstance(item, dict):
                    item = dict(json.loads(item))
            except Exception as exc:
                raise RuntimeError("Invalid data type parsed to persitor: %s" % exc)
            print("writing %s %s" % (type(item),item))
            container.upsert_item(item)
        self.container = None

    def query(self, persistor_setting: PersistorSetting, id=None):
        db = self.__get_db(persistor_setting.database_id)
        if id is None:
            res = self.__fetch_all(container_name=persistor_setting.container_id, db=self.database)
        else:
            res = self.__fetch_one(db=db, container_name=persistor_setting.container_id, id=id, partion_key=persistor_setting.partion_key)
        return res

    def persist(self, persitor_setting: PersistorSetting, data: [dict]):
        self.__write(
            database_name= persitor_setting.database_id,
            container_name= persitor_setting.container_id,
            partion_key=persitor_setting.partion_key,
            items=data
        )
