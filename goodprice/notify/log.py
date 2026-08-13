import logging

from goodprice.notify.base import NotificationMessage, Notifier

logger = logging.getLogger(__name__)


class LogNotifier(Notifier):
    channel = "log"

    def send(self, message: NotificationMessage) -> None:
        logger.info(
            "通知[%s]: %s\n%s\n%s", self.channel, message.title, message.content, message.url
        )
