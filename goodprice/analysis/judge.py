# Jev 判断层（实验性）：需求匹配与批量性价比的类型化逐件评估。
# 与默认路径的区别：每次判断独立成请求并附带置信度，单件失败不拖累整批；
# 置信度低于阈值的判断由调用方回落到原 LLMClient 路径（宁多勿漏）。
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from goodprice.analysis.llm import LLMClient

logger = logging.getLogger(__name__)

REQUIREMENT_TYPED_SYSTEM_PROMPT = (
    "你是二手商品筛选助手。用户给出商品标题、卖家描述和买家需求，"
    "请判断商品是否满足买家的硬性需求。只输出 JSON，不要输出其它文字，格式："
    '{"matched": true或false, "probability": 0到1的小数（该判断成立的概率，越接近1越确定）, '
    '"reason": "一句话理由"}'
)

REQUIREMENT_TYPED_USER_TEMPLATE = (
    "商品标题：{title}\n"
    "卖家描述：{description}\n"
    "买家需求：{requirement}\n"
    "请给出 JSON 结论。"
)

VALUE_TYPED_SYSTEM_PROMPT = (
    "你是二手商品性价比评估专家。用户给出单个商品（标题、价格、品相分、瑕疵、卖家风险）"
    "与买家品相要求，请仅依据该商品本身独立评估按当前价格是否划算。只输出 JSON，不要输出其它文字，格式："
    '{"value_score": 1到10的整数（越高越划算）, "probability": 0到1的小数（该评分的可信度）, '
    '"reason": "一句话理由"}'
)

VALUE_TYPED_USER_TEMPLATE = (
    "买家品相要求：{requirement}\n"
    "商品：id={external_id} 标题={title} 价格={price}元 "
    "品相分={condition_score} 瑕疵={defects} 卖家风险={seller_risk}\n"
    "请给出 JSON 结论。"
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


def parse_requirement_typed(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    matched = data.get("matched")
    if not isinstance(matched, bool):
        raise ValueError(f"类型化需求判断缺少布尔 matched: {raw!r}")
    return {
        "matched": matched,
        "probability": _clamp01(data.get("probability", 0.5)),
        "reason": str(data.get("reason", ""))[:500],
    }


def parse_value_typed(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    try:
        score = max(1, min(10, int(data.get("value_score", 0))))
    except (TypeError, ValueError):
        raise ValueError(f"类型化性价比缺少整数 value_score: {raw!r}")
    return {
        "value_score": score,
        "probability": _clamp01(data.get("probability", 0.5)),
        "reason": str(data.get("reason", ""))[:200],
    }


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


@dataclass
class RequirementVerdict:
    matched: bool
    reason: str
    confidence: float


@dataclass
class JevJudger:
    """基于 OpenAI 兼容 LLM 的类型化判断（jev 的 Noul/Score 语义）。

    后端可无缝替换为 TypeSafe Jev 直连客户端：只要保持
    analyze_requirement / analyze_batch_value 的输入输出不变即可。
    """

    llm: LLMClient
    auto_threshold: float = 0.85

    @property
    def enabled(self) -> bool:
        return self.llm.enabled

    def analyze_requirement(
        self, title: str, description: str = "", requirement: str = ""
    ) -> RequirementVerdict:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        text = REQUIREMENT_TYPED_USER_TEMPLATE.format(
            title=title, description=description or "无", requirement=requirement or "无"
        )
        data = self._ask(REQUIREMENT_TYPED_SYSTEM_PROMPT, text, parse_requirement_typed)
        probability = data["probability"]
        confidence = max(probability, 1.0 - probability)
        return RequirementVerdict(
            matched=data["matched"], reason=data["reason"], confidence=confidence
        )

    def analyze_batch_value(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """逐件独立评估，返回与 analyze_batch_value 同构的结果。

        单件解析失败只损失该件（score 留空），不影响其余商品。
        """
        scores: dict[str, int] = {}
        confidences: dict[str, float] = {}
        reasons: dict[str, str] = {}
        for it in items:
            external_id = str(it.get("external_id", "")).strip()
            if not external_id:
                continue
            text = VALUE_TYPED_USER_TEMPLATE.format(
                requirement=it.get("requirement") or "无",
                external_id=external_id,
                title=it.get("title"),
                price=it.get("price"),
                condition_score=it.get("condition_score") or "未评估",
                defects="、".join(str(d) for d in (it.get("defects") or [])[:5]) or "无",
                seller_risk=it.get("seller_risk") or "未知",
            )
            try:
                data = self._ask(VALUE_TYPED_SYSTEM_PROMPT, text, parse_value_typed)
            except Exception as exc:
                logger.warning("性价比逐件评估失败，跳过 %s: %s", external_id, exc)
                continue
            scores[external_id] = data["value_score"]
            confidences[external_id] = data["probability"]
            reasons[external_id] = data["reason"]
        best = None
        if scores:
            best = max(
                scores,
                key=lambda k: (scores[k], confidences.get(k, 0.0)),
            )
        return {
            "scores": scores,
            "best": best,
            "reasons": reasons,
            "confidences": confidences,
        }

    def _ask(self, system: str, text: str, parser) -> dict[str, Any]:
        payload = self.llm._payload([{"type": "text", "text": text}], system=system)
        return self.llm._complete(payload, parser=parser)


def build_judger(
    llm: LLMClient, jev_enabled: bool = False, auto_threshold: float = 0.85
) -> Optional[JevJudger]:
    if not jev_enabled:
        return None
    try:
        threshold = float(auto_threshold)
    except (TypeError, ValueError):
        threshold = 0.85
    return JevJudger(llm=llm, auto_threshold=min(1.0, max(0.5, threshold)))
