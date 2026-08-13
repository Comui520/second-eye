import logging
import threading
import time
from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier

logger = logging.getLogger(__name__)

GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


class WeComNotifier(Notifier):
    channel = "wecom"

    def __init__(
        self,
        corpid: str = "",
        agentid: str = "",
        secret: str = "",
        touser: str = "@all",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.corpid = corpid
        self.agentid = agentid
        self.secret = secret
        self.touser = touser or "@all"
        self._transport = transport
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.corpid and self.agentid and self.secret)

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self.timeout)

    def _fetch_token(self) -> tuple[str, int]:
        response = self._client().get(
            GET_TOKEN_URL, params={"corpid": self.corpid, "corpsecret": self.secret}
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"企业微信获取 access_token 失败: {data}")
        return data["access_token"], int(data.get("expires_in", 7200))

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            token, expires = self._fetch_token()
            self._token = token
            self._token_expires_at = time.time() + expires
            return token

    def _send_once(self, token: str, content: str) -> dict:
        try:
            agentid = int(self.agentid)
        except (TypeError, ValueError):
            raise RuntimeError("企业微信 agentid 必须为数字")
        response = self._client().post(
            SEND_URL,
            params={"access_token": token},
            json={
                "touser": self.touser,
                "msgtype": "text",
                "agentid": agentid,
                "text": {"content": content},
                "safe": 0,
            },
        )
        response.raise_for_status()
        return response.json()

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("企业微信未配置 corpid/agentid/secret")
        content = f"{message.title}\n{message.content}\n{message.url}"
        token = self._get_token()
        data = self._send_once(token, content)
        if data.get("errcode") in (40014, 42001):
            with self._lock:
                self._token = None
            token = self._get_token()
            data = self._send_once(token, content)
        errcode = data.get("errcode", -1)
        if errcode == 60020:
            raise RuntimeError(
                "企业微信报错：IP 不在可信 IP 列表中，请在企业微信后台把本机出口 IP 加入企业可信 IP"
            )
        if errcode != 0:
            raise RuntimeError(f"企业微信发送失败: {data}")
