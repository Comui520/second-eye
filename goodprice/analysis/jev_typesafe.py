# 官方 TypeSafe Jev 后端：通过 typesafe-sdk 直连 api.typesafe.ai，
# 返回与 adapter 后端（judge.JevJudger）同构的结论，供 crawl_service 统一裁决。
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from goodprice.analysis.judge import RequirementVerdict
from goodprice.analysis.prompts import REQUIREMENT_CRITERION, VALUE_CRITERION
from goodprice.constants import SCORE_MAX, SCORE_MIN

logger = logging.getLogger(__name__)

VALUE_LEVELS = [f"{i} 分" for i in range(SCORE_MIN, SCORE_MAX + 1)]  # SDK 返回 0–9 索引，+1 映射到 1–10

REQUIREMENT_NOUL_INSTRUCTION = f"该二手{REQUIREMENT_CRITERION}？"
VALUE_SCORE_INSTRUCTION = f"该商品{VALUE_CRITERION}，1 分最不划算，10 分最划算"


@dataclass
class TypeSafeJudger:
    api_key: str
    auto_threshold: float = 0.85
    # 测试注入用：接收 api_key，返回实现 system_one(state, questions) 的客户端
    client_factory: Optional[Callable[[str], Any]] = None
    single_attempt: bool = False
    timeout: float = 60

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze_requirement(
        self, title: str, description: str = "", requirement: str = ""
    ) -> RequirementVerdict:
        if not self.enabled:
            raise RuntimeError("TypeSafe API Key 未配置")
        state = (
            f"商品标题：{title}\n"
            f"卖家描述：{description or '无'}\n"
            f"买家需求：{requirement or '无'}"
        )
        answer = self._ask(state, {"verdict": _noul_question()})["verdict"]
        noul = float(answer.noul)
        if not math.isfinite(noul):
            raise ValueError("判断概率无效")
        return RequirementVerdict(
            matched=noul >= 0.5,
            confidence=max(noul, 1.0 - noul),
            reason="官方 Jev Noul 判断",
        )

    def analyze_batch_value(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """逐件独立评估；单件失败只损失该件，与 adapter 后端语义一致。"""
        scores: dict[str, int] = {}
        confidences: dict[str, float] = {}
        reasons: dict[str, str] = {}
        for it in items:
            external_id = str(it.get("external_id", "")).strip()
            if not external_id:
                continue
            state = (
                f"买家品相要求：{it.get('requirement') or '无'}\n"
                f"商品：id={external_id} 标题={it.get('title')} 价格={it.get('price')}元 "
                f"品相分={it.get('condition_score') or '未评估'} "
                f"瑕疵={'、'.join(str(d) for d in (it.get('defects') or [])[:5]) or '无'} "
                f"卖家风险={it.get('seller_risk') or '未知'}"
            )
            try:
                answer = self._ask(state, {"value": _score_question()})["value"]
            except Exception as exc:
                logger.warning("TypeSafe 性价比评估失败，跳过 %s: %s", external_id, exc)
                continue
            raw = float(answer.score)
            scores[external_id] = min(SCORE_MAX, max(SCORE_MIN, int(round(raw)) + 1))
            confidences[external_id] = float(getattr(answer, "confidence", 0.0))
            reasons[external_id] = f"官方 Jev Score={raw:.1f}"
        best = None
        if scores:
            best = max(scores, key=lambda k: (scores[k], confidences.get(k, 0.0)))
        return {
            "scores": scores,
            "best": best,
            "reasons": reasons,
            "confidences": confidences,
        }

    def _make_client(self):
        if self.client_factory is not None:
            return self.client_factory(self.api_key)
        from typesafe_sdk import TypeSafeClient

        if self.single_attempt:
            from typesafe_sdk import RetryPolicy
            return TypeSafeClient(api_key=self.api_key, retry=RetryPolicy(max_retries=0), timeout=self.timeout)
        return TypeSafeClient(api_key=self.api_key, timeout=self.timeout)

    def analyze_value(self, item: dict[str, Any]) -> dict[str, Any]:
        state = (f"买家品相要求：{item.get('requirement') or '无'}\n"
                 f"商品：id={item.get('external_id')} 标题={item.get('title')} 价格={item.get('price')}元 "
                 f"品相分={item.get('condition_score') or '未评估'} "
                 f"瑕疵={'、'.join(str(d) for d in (item.get('defects') or [])[:5]) or '无'} "
                 f"卖家风险={item.get('seller_risk') or '未知'}")
        answer = self._ask(state, {"value": _score_question()})["value"]
        raw, confidence = float(answer.score), float(getattr(answer, "confidence", .5))
        if not math.isfinite(raw):
            raise ValueError("评分无效")
        return {"value_score": min(SCORE_MAX, max(SCORE_MIN, int(round(raw)) + 1)),
                "probability": confidence if math.isfinite(confidence) else .5,
                "reason": f"官方 Jev Score={raw:.1f}"}

    def _ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        client = self._make_client()
        try:
            return client.system_one(state=state, questions=questions).answers
        finally:
            close = getattr(client, "close", None)
            if close:
                close()


def _noul_question() -> dict[str, Any]:
    # 兼容两种提问形态：官方 SDK 的 Noul 对象或 REST 的 dict
    try:
        from typesafe_sdk import Noul

        return Noul(instructions=REQUIREMENT_NOUL_INSTRUCTION)
    except ImportError:
        return {"type": "noul", "instructions": REQUIREMENT_NOUL_INSTRUCTION}


def _score_question() -> dict[str, Any]:
    try:
        from typesafe_sdk import Score

        return Score(instructions=VALUE_SCORE_INSTRUCTION, criteria=VALUE_LEVELS)
    except ImportError:
        return {"type": "score", "instructions": VALUE_SCORE_INSTRUCTION, "criteria": VALUE_LEVELS}
