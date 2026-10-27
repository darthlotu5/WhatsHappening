import logging
import math
import signal
import threading

from azure.core.exceptions import ResourceExistsError

from shared.azure_clients import queue_service

logger = logging.getLogger(__name__)


def queue_name(exchange, key):
    name = f"{exchange}-{key}".lower().replace("_", "-")
    if not 3 <= len(name) <= 63 or not all(c.isascii() and (c.isalnum() or c == "-") for c in name):
        raise ValueError("Invalid Azure queue name")
    return name


class DundaaQueueSDK:
    def __init__(self, service=None):
        self.service = service if service is not None else queue_service()

    def send_message(self, data, messenger_setting, delay=None):
        queue = self.service.get_queue_client(
            queue_name(messenger_setting.service_name, messenger_setting.key)
        )
        try:
            queue.create_queue()
        except ResourceExistsError:
            pass
        visibility = math.ceil(delay or 0)
        if not 0 <= visibility <= 604800:
            raise ValueError("Queue delay must be between zero and seven days")
        queue.send_message(data, visibility_timeout=visibility)

    def process_message(self, queue, message, callback):
        finished = threading.Event()
        lease_error = []

        def renew():
            while not finished.wait(45):
                try:
                    receipt = queue.update_message(
                        message.id, pop_receipt=message.pop_receipt, visibility_timeout=300
                    )
                    message.pop_receipt = receipt.pop_receipt
                except Exception as error:
                    logger.exception("Unable to renew queue lease for %s", message.id)
                    lease_error.append(error)
                    return

        renewal = threading.Thread(target=renew, daemon=True)
        renewal.start()
        try:
            callback(None, None, None, message.content.encode("utf-8"))
        finally:
            finished.set()
            renewal.join()
        if lease_error:
            raise RuntimeError("Queue lease renewal failed") from lease_error[0]
        queue.delete_message(message.id, message.pop_receipt)

    def consume_messages(self, consumer_settings, callback):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        queues = [
            self.service.get_queue_client(queue_name(consumer_settings.service_name, key))
            for key in consumer_settings.listen_keys
        ]
        for queue in queues:
            try:
                queue.create_queue()
            except ResourceExistsError:
                pass
        while not stop.is_set():
            for queue in queues:
                try:
                    messages = queue.receive_messages(messages_per_page=1, visibility_timeout=300)
                    for message in messages:
                        if stop.is_set():
                            break
                        if message.dequeue_count > 5:
                            poison = self.service.get_queue_client(f"{queue.queue_name}-poison")
                            try:
                                poison.create_queue()
                            except ResourceExistsError:
                                pass
                            poison.send_message(message.content)
                            queue.delete_message(message.id, message.pop_receipt)
                            logger.error("Message %s moved to poison queue %s", message.id, poison.queue_name)
                            continue
                        try:
                            self.process_message(queue, message, callback)
                        except Exception:
                            logger.exception("Message %s failed; it remains queued for retry", message.id)
                        break
                except Exception:
                    logger.exception("Queue polling failed for %s", queue.queue_name)
                    stop.wait(10)
            stop.wait(2)
