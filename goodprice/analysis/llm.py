import json
import logging
import re
from typing import Any, Optional

import httpx

from goodprice.analysis.prompts import (
    CONDITION_SYSTEM_PROMPT,
    CONDITION_USER_TEMPLATE,
    REQUIREMENT_SYSTEM_PROMPT,
    REQUIREMENT_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

_IMAGE_ERROR_MARKERS = (
    "image_url",
    "image",
    "图片",
    "vision",
    "multimodal",
    "多模态",
    "unsupported",
    "not support",
)


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中没有 JSON: {raw!r}")
    return json.loads(text[start : end + 1])


def parse_analysis_json(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    score = max(1, min(10, int(data.get("condition_score", 0))))
    defects = [str(d) for d in data.get("defects", [])][:10]
    return {
        "condition_score": score,
        "defects": defects,
        "recommended": bool(data.get("recommended", False)),
        "reason": str(data.get("reason", ""))[:500],
    }


def parse_requirement_json(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    matched = data.get("matched")
    if not isinstance(matched, bool):
        raise ValueError(f"需求判断输出缺少布尔 matched: {raw!r}")
    return {"matched": matched, "reason": str(data.get("reason", ""))[:500]}


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
        allow_image_fallback: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport
        self.allow_image_fallback = allow_image_fallback

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def analyze_requirement(
        self, title: str, description: str = "", requirement: str = ""
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        text = REQUIREMENT_USER_TEMPLATE.format(
            title=title, description=description or "无", requirement=requirement or "无"
        )
        return self._complete(
            self._payload([{"type": "text", "text": text}], system=REQUIREMENT_SYSTEM_PROMPT),
            parser=parse_requirement_json,
        )

    def analyze_condition(
        self,
        title: str,
        price: float,
        description: str = "",
        requirement: str = "",
        image_urls: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        image_urls = image_urls or []
        text = CONDITION_USER_TEMPLATE.format(
            title=title,
            price=price,
            description=description or "无",
            requirement=requirement or "无",
            image_count=len(image_urls),
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(
            {"type": "image_url", "image_url": {"url": url}} for url in image_urls[:4]
        )
        try:
            return self._complete(self._payload(content))
        except httpx.HTTPStatusError as exc:
            # 部分模型（如 DeepSeek）是纯文本模型，会拒绝 image_url 内容。
            # 仅当报错明确与图片输入相关时，降级为纯文本重试，保证仍能给出评分。
            error_text = (exc.response.text or "").lower()
            image_rejected = any(marker in error_text for marker in _IMAGE_ERROR_MARKERS)
            if (
                image_urls
                and image_rejected
                and exc.response.status_code in (400, 422)
                and self.allow_image_fallback
            ):
                logger.info("模型不支持图片输入，降级为纯文本分析（%s）", exc.response.status_code)
                fallback_text = f"{text}\n（注：当前模型不支持图片输入，本次仅依据文字信息判断品相）"
                return self._complete(self._payload([{"type": "text", "text": fallback_text}]))
            raise

    def _payload(
        self, content: list[dict[str, Any]], system: str = CONDITION_SYSTEM_PROMPT
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }

    def _complete(
        self, payload: dict[str, Any], parser=parse_analysis_json
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return parser(raw)
