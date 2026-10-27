from shared.contracts.Messages import ScrapSite
from shared.messengers.messenger import MessengerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import event_discovery_setting
from EventScrapper.sources import SOURCES, SOURCE_BY_ID


def schedule_sources(messenger, source_id=None, delay=0):
    sources = (SOURCE_BY_ID[source_id],) if source_id else SOURCES
    for source in sources:
        messenger.send_message(
            data=ScrapSite(delay=delay, source=source.id).to_json(),
            messenger_setting=MessengerSetting(
                service_name=event_discovery_setting.delayed_exchange,
                key=event_discovery_setting.key,
            ),
            delay=delay,
        )
    return len(sources)


if __name__ == "__main__":
    schedule_sources(DundaaQueueSDK())
