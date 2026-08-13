from dataclasses import dataclass


@dataclass
class NotificationMessage:
    title: str
    content: str
    url: str = ""


class Notifier:
    channel = "base"

    def send(self, message: NotificationMessage) -> None:
        raise NotImplementedError
