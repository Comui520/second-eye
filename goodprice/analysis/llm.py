import json
import re
from typing import Any, Optional

import httpx

from goodprice.analysis.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def parse_analysis_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中没有 JSON: {raw!r}")
    data = json.loads(text[start : end + 1])
    score = max(1, min(10, int(data.get("condition_score", 0))))
    defects = [str(d) for d in data.get("defects", [])][:10]
    return {
        "condition_score": score,
        "defects": defects,
        "recommended": bool(data.get("recommended", False)),
        "reason": str(data.get("reason", ""))[:500],
    }


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def analyze_listing(
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
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": USER_PROMPT_TEMPLATE.format(
                    title=title,
                    price=price,
                    description=description or "无",
                    requirement=requirement or "无",
                    image_count=len(image_urls),
                ),
            }
        ]
        for url in image_urls[:4]:
            content.append({"type": "image_url", "image_url": {"url": url}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return parse_analysis_json(raw)
