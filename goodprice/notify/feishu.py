import base64
import hashlib
import hmac
import time
from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier


class FeishuNotifier(Notifier):
    """飞书自定义机器人 Webhook 通知。"""

    channel = "feishu"

    def __init__(
        self,
        webhook: str = "",
        secret: str = "",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.webhook = webhook.strip()
        self.secret = secret.strip()
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("飞书机器人未配置 webhook")
        content = f"{message.title}\n{message.content}"
        if message.url:
            content += f"\n{message.url}"
        # 飞书自定义机器人请求体上限为 20 KB，按 UTF-8 字节截断。
        content = content.encode("utf-8")[:19000].decode("utf-8", errors="ignore")
        body = {"msg_type": "text", "content": {"text": content}}
        if self.secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{self.secret}"
            sign = base64.b64encode(
                hmac.new(
                    string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
                ).digest()
            ).decode("utf-8")
            body.update({"timestamp": timestamp, "sign": sign})

        with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
            response = client.post(
                self.webhook,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
        code = result.get("code", result.get("StatusCode", -1))
        if code != 0:
            raise RuntimeError(f"飞书机器人发送失败: code={code}, msg={result.get('msg', '')}")
