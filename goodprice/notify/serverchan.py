from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier

SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"


class ServerChanNotifier(Notifier):
    channel = "serverchan"

    def __init__(
        self,
        sendkey: str = "",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.sendkey = sendkey
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.sendkey)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("Server酱未配置 sendkey")
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            SERVERCHAN_URL.format(key=self.sendkey),
            data={"title": message.title, "desp": f"{message.content}\n{message.url}"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Server酱返回错误: {data}")
