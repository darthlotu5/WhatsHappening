from shared.contracts.Messages import ScrapSite
from shared.messengers.messenger import MessengerSetting
from shared.messengers.queue_sdk import DundaaQueueSDK
from shared.services import event_discovery_setting


if __name__ == "__main__":
    DundaaQueueSDK().send_message(
        data=ScrapSite(delay=0).to_json(),
        messenger_setting=MessengerSetting(
            service_name=event_discovery_setting.delayed_exchange,
            key=event_discovery_setting.key,
        ),
    )
