from dataclasses import dataclass


class DeliveryRejected(RuntimeError):
    """The channel rejected the request or no transmission was attempted."""


@dataclass
class NotificationMessage:
    title: str
    content: str
    url: str = ""


class Notifier:
    channel = "base"

    def send(self, message: NotificationMessage) -> None:
        raise NotImplementedError
