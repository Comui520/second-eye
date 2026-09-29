"""提示词核心判据的单一来源守卫。

需求匹配与性价比的判据措辞由 goodprice.analysis.prompts 统一定义，adapter 与
typesafe 两个后端共用；本测试同时锁定措辞，防止重构时无意改动模型行为。
"""
from goodprice.analysis.jev_typesafe import (
    REQUIREMENT_NOUL_INSTRUCTION,
    VALUE_SCORE_INSTRUCTION,
)
from goodprice.analysis.judge import (
    REQUIREMENT_TYPED_SYSTEM_PROMPT,
    REQUIREMENT_TYPED_USER_TEMPLATE,
    VALUE_TYPED_SYSTEM_PROMPT,
)
from goodprice.analysis.prompts import (
    BATCH_VALUE_SYSTEM_PROMPT,
    REQUIREMENT_CRITERION,
    REQUIREMENT_SYSTEM_PREFIX,
    REQUIREMENT_SYSTEM_PROMPT,
    REQUIREMENT_USER_TEMPLATE,
    VALUE_CRITERION,
)


def test_requirement_criterion_shared_across_backends():
    for text in (
        REQUIREMENT_SYSTEM_PROMPT,
        REQUIREMENT_TYPED_SYSTEM_PROMPT,
        REQUIREMENT_NOUL_INSTRUCTION,
    ):
        assert REQUIREMENT_CRITERION in text


def test_value_criterion_shared_across_backends():
    for text in (
        BATCH_VALUE_SYSTEM_PROMPT,
        VALUE_TYPED_SYSTEM_PROMPT,
        VALUE_SCORE_INSTRUCTION,
    ):
        assert VALUE_CRITERION in text


def test_prompt_wording_is_unchanged():
    assert "请判断商品是否满足买家的硬性需求。" in REQUIREMENT_SYSTEM_PROMPT
    assert "请判断商品是否满足买家的硬性需求。" in REQUIREMENT_TYPED_SYSTEM_PROMPT
    assert REQUIREMENT_NOUL_INSTRUCTION == "该二手商品是否满足买家的硬性需求？"
    assert "判断每个商品按当前价格是否划算。" in BATCH_VALUE_SYSTEM_PROMPT
    assert "请仅依据该商品本身独立评估按当前价格是否划算。" in VALUE_TYPED_SYSTEM_PROMPT
    assert VALUE_SCORE_INSTRUCTION == "该商品按当前价格是否划算，1 分最不划算，10 分最划算"


def test_requirement_user_template_is_shared():
    assert REQUIREMENT_TYPED_USER_TEMPLATE == REQUIREMENT_USER_TEMPLATE


def test_requirement_system_prefix_is_shared():
    assert REQUIREMENT_SYSTEM_PROMPT.startswith(REQUIREMENT_SYSTEM_PREFIX)
    assert REQUIREMENT_TYPED_SYSTEM_PROMPT.startswith(REQUIREMENT_SYSTEM_PREFIX)
