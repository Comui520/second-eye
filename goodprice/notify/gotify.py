from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier


class GotifyNotifier(Notifier):
    """通过 Gotify HTTP API 发送自托管通知。"""

    channel = "gotify"

    def __init__(
        self,
        url: str = "",
        token: str = "",
        priority: int = 5,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.url = url.rstrip("/")
        self.token = token.strip()
        self.priority = max(0, min(10, int(priority)))
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("Gotify 未配置 URL 或应用 Token")
        endpoint = self.url if self.url.endswith("/message") else f"{self.url}/message"
        content = message.content
        if message.url:
            content += f"\n{message.url}"
        with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
            response = client.post(
                endpoint,
                json={
                    "title": message.title,
                    "message": content,
                    "priority": self.priority,
                },
                headers={"X-Gotify-Key": self.token},
            )
            response.raise_for_status()
