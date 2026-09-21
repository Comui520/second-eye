# 官方 TypeSafe Jev 后端：通过 typesafe-sdk 直连 api.typesafe.ai，
# 返回与 adapter 后端（judge.JevJudger）同构的结论，供 crawl_service 统一裁决。
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from goodprice.analysis.judge import RequirementVerdict

logger = logging.getLogger(__name__)

VALUE_LEVELS = [f"{i} 分" for i in range(1, 11)]  # Score 评分为索引空间 [0, 9]

REQUIREMENT_NOUL_INSTRUCTION = "该二手商品是否满足买家的硬性需求？"
VALUE_SCORE_INSTRUCTION = "该商品按当前价格是否划算，1 分最不划算，10 分最划算"


@dataclass
class TypeSafeJudger:
    api_key: str
    auto_threshold: float = 0.85
    # 测试注入用：接收 api_key，返回实现 system_one(state, questions) 的客户端
    client_factory: Optional[Callable[[str], Any]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze_requirement(
        self, title: str, description: str = "", requirement: str = ""
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("TypeSafe API Key 未配置")
        state = (
            f"商品标题：{title}\n"
            f"卖家描述：{description or '无'}\n"
            f"买家需求：{requirement or '无'}"
        )
        answer = self._ask(state, {"verdict": _noul_question()})["verdict"]
        noul = float(answer.noul)
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
            scores[external_id] = min(10, max(1, int(round(raw)) + 1))
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

        try:
            return TypeSafeClient(api_key=self.api_key)
        except TypeError:
            os.environ["TYPESAFE_API_KEY"] = self.api_key
            return TypeSafeClient()

    def _ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        response = self._make_client().system_one(state=state, questions=questions)
        return response.answers


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
