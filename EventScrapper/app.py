import logging
import os

from EventScrapper import SUCCESS
from EventScrapper.base_extractor import WebScrapperArgs
from EventScrapper.ticket_sasa import ScrapTicketSasa
from shared.contracts.Messages import ScrapSite
from shared.messengers.azure_blob import DundaaBlobClient
from shared.messengers.messenger import BlobMessengerSetting, MessageConsumerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import web_scrapper_setting

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def run(ch, method, properties, body):
    message = ScrapSite.from_json(body.decode())
    logger.info("Starting scheduled scrape %s", message.id)
    extractor = ScrapTicketSasa(args=WebScrapperArgs(
        messenger_setting=BlobMessengerSetting(
            key=web_scrapper_setting.key, service_name=web_scrapper_setting.name,
        ),
        messenger=DundaaBlobClient(logger=logger),
        success_status_codes=[SUCCESS], site_name="ticketsasa",
        site_url=os.environ.get("TICKETSASA_URL", "https://ticketsasa.com"),
    ))
    extractor.run()
    extractor.get_result()
    extractor.log_response()
    if extractor.response.result != "SUCCESS":
        raise RuntimeError(extractor.response.status_details)
    if not extractor.events:
        raise RuntimeError("TicketSasa returned no recognizable events; upstream markup may have changed")
    extractor.send_messages()


if __name__ == "__main__":
    DundaaQueueSDK().consume_messages(
        consumer_settings=MessageConsumerSetting(
            service_name=web_scrapper_setting.listen.exchange,
            listen_keys=web_scrapper_setting.listen.keys,
        ),
        callback=run,
    )
