from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier


class WeComRobotNotifier(Notifier):
    channel = "wecom_robot"

    def __init__(
        self,
        webhook: str = "",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.webhook = webhook.strip()
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("企业微信群机器人未配置 webhook")
        content = f"{message.title}\n{message.content}\n{message.url}"
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            self.webhook, json={"msgtype": "text", "text": {"content": content}}
        )
        response.raise_for_status()
        data = response.json()
        errcode = data.get("errcode", -1)
        if errcode == 93000:
            raise RuntimeError("企业微信群机器人 webhook 无效或机器人已被移除，请重新添加")
        if errcode == 93004:
            raise RuntimeError("企业微信群机器人发送太频繁（20 条/分钟），请稍后重试或降低频率")
        if errcode != 0:
            raise RuntimeError(f"企业微信群机器人发送失败: {data}")
