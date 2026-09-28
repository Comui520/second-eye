"""解析结果的通用规整工具，避免各解析器重复 clamp 逻辑。"""
from typing import Any

from goodprice.constants import SCORE_MAX, SCORE_MIN


def clamp_score(value: Any) -> int:
    """把模型返回的评分规整到 [SCORE_MIN, SCORE_MAX]；非法值抛 ValueError。"""
    return max(SCORE_MIN, min(SCORE_MAX, int(value)))
