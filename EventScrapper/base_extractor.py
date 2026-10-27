import logging
from dataclasses import dataclass

from shared.contracts.event_model import Event
from shared.messengers.messenger import Messenger
from shared.messengers.messenger import MessengerSetting


@dataclass
class WebScrapperResponse():
    status_details: str = ""
    status_code: int = -1
    result: str = None


@dataclass
class WebScrapperArgs():
    success_status_codes: [int]
    site_name: str
    site_url: str
    messenger: Messenger
    messenger_setting: MessengerSetting
    events: [Event] = None
    response: WebScrapperResponse = None


class BaseExtractor():
    """Exctractor base class
    Handles:
    1. Adding messages to rabbit
    2. Adding custom run options
    3. formatting the response
    Arguments:
        sevice_settings(dict): Settings for the service
    """

    def __init__(self, args: WebScrapperArgs):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.success_status_codes = args.success_status_codes
        self.site_name = args.site_name
        self.site_url = args.site_url
        self.messenger = args.messenger
        self.messenger_setting = args.messenger_setting
        self.events = args.events if args.events is not None else []
        self.response = args.response

    def run(self):
        raise NotImplementedError

    def get_result(self):
        if self.response and isinstance(self.response, WebScrapperResponse):
            status_code = self.response.status_code
            if status_code in self.success_status_codes:
                self.response.result = "SUCCESS"
            else:
                self.response.result = "FAILED"

    def log_response(self):
        if self.response.result == "SUCCESS":
            self.logger.info(f"extracted {self.site_name} successfully!")
        else:
            self.logger.error(f"Encountered error {self.response.status_code} : {self.response.status_details}")

    def send_messages(self):
        self.logger.info("Sending messages to {}".format(self.messenger_setting.key))
        self.logger.info("found %d events" % len(self.events))
        if self.messenger is not None:
            self.result = self.messenger.send_message(data=[event.serialize("json") for event in self.events],
                                                      messenger_setting=self.messenger_setting)
