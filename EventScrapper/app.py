import logging
from EventDiscoveryApi.schedule import schedule_sources
from EventScrapper.engine import scrape_source
from EventScrapper.sources import SOURCE_BY_ID
from EventScrapper.state import BlobStateStore
from shared.contracts.Messages import ScrapSite
from shared.messengers.azure_blob import DundaaBlobClient
from shared.messengers.messenger import BlobMessengerSetting, MessageConsumerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import web_scrapper_setting

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def run(ch, method, properties, body):
    message = ScrapSite.from_json(body.decode())
    if message.source is None:
        schedule_sources(DundaaQueueSDK())
        return
    source = SOURCE_BY_ID[message.source]
    logger.info("Starting scheduled scrape %s for %s", message.id, source.id)
    from EventScrapper.providers import get_adapter
    scrape_source(source, BlobStateStore(), get_adapter(source.id), publish_changed)


def publish_changed(source, events):
    DundaaBlobClient(logger=logger).send_message(
        messenger_setting=BlobMessengerSetting(
            key=source.id, service_name=web_scrapper_setting.name,
        ),
        data=events,
    )


if __name__ == "__main__":
    DundaaQueueSDK().consume_messages(
        consumer_settings=MessageConsumerSetting(
            service_name=web_scrapper_setting.listen.exchange,
            listen_keys=web_scrapper_setting.listen.keys,
        ),
        callback=run,
    )
