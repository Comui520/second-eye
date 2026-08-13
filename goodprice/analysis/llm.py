import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from goodprice.analysis.prompts import (
    BATCH_VALUE_SYSTEM_PROMPT,
    BATCH_VALUE_USER_TEMPLATE,
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


def parse_batch_value_json(raw: str) -> dict[str, Any]:
    """解析批量性价比输出：{"items": [{"id", "value_score", "reason"}], "best": id}。"""
    data = _extract_json(raw)
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"批量性价比输出缺少 items: {raw!r}")
    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}
    for it in items:
        item_id = str(it.get("id", "")).strip()
        if not item_id:
            continue
        score = max(1, min(10, int(it.get("value_score", 0))))
        scores[item_id] = score
        reasons[item_id] = str(it.get("reason", ""))[:200]
    best = str(data.get("best", "")).strip()
    if not best or best not in scores:
        raise ValueError(f"批量性价比输出缺少有效 best: {raw!r}")
    return {"scores": scores, "best": best, "reasons": reasons}


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
        allow_image_fallback: bool = True,
        retry_delay: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport
        self.allow_image_fallback = allow_image_fallback
        self.retry_delay = retry_delay

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

    def analyze_batch_value(
        self, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """对同一批商品做横向性价比对比，返回 {scores, best, reasons}。"""
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        if not items:
            return {"scores": {}, "best": None, "reasons": {}}
        lines = []
        for i, it in enumerate(items, 1):
            defects = "、".join(str(d) for d in (it.get("defects") or [])[:5]) or "无"
            lines.append(
                f"[{i}] id={it.get('external_id')} 标题={it.get('title')} "
                f"价格={it.get('price')}元 品相分={it.get('condition_score') or '未评估'} "
                f"瑕疵={defects} 卖家风险={it.get('seller_risk') or '未知'}"
            )
        text = BATCH_VALUE_USER_TEMPLATE.format(items="\n".join(lines))
        return self._complete(
            self._payload(
                [{"type": "text", "text": text}], system=BATCH_VALUE_SYSTEM_PROMPT
            ),
            parser=parse_batch_value_json,
        )

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
        for attempt in range(3):
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if response.status_code == 429 and attempt < 2:
                if self.retry_delay:
                    time.sleep(self.retry_delay)
                continue
            if response.status_code >= 400:
                detail = response.text[:300]
                raise httpx.HTTPStatusError(
                    f"LLM 请求失败 {response.status_code}: {detail}",
                    request=response.request,
                    response=response,
                )
            raw = response.json()["choices"][0]["message"]["content"]
            return parser(raw)
        raise RuntimeError("LLM 请求重试耗尽")  # pragma: no cover
