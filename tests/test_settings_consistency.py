"""守住「设置字段只在一处定义」的边界。

设置字段的唯一事实来源是 goodprice.config.Settings；.env.example 是它的部署文档。
本测试保证新增字段时不会漏写文档、也不会凭空多出无人读取的变量，从根上防止
「代码 / 模板 / 表单」多处平行维护导致的漂移。
"""
from goodprice.config import PROJECT_ROOT, Settings

ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# 有意不写入 .env.example 的字段：内部字段或由容器注入。
INTERNAL_FIELDS = {"app_name", "runtime_mode"}

# .env.example 中属于 compose/部署层、并非 Settings 字段的变量。
COMPOSE_ONLY_KEYS = {"BIND_ADDRESS", "SECOND_EYE_IMAGE"}


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_documents_every_settings_field():
    keys = _env_example_keys()
    missing = sorted(
        name.upper()
        for name in Settings.model_fields
        if name not in INTERNAL_FIELDS and name.upper() not in keys
    )
    assert not missing, f".env.example 缺少以下设置项: {missing}"


def test_env_example_has_no_unknown_variables():
    known = {name.upper() for name in Settings.model_fields} | COMPOSE_ONLY_KEYS
    unknown = sorted(_env_example_keys() - known)
    assert not unknown, f".env.example 含未知变量（可能与代码脱节）: {unknown}"


def test_env_example_model_defaults_match_settings():
    """默认模型名只在 Settings 定义；.env.example 的取值必须与之一致。"""
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    defaults = Settings(_env_file=None)
    assert values["LLM_MODEL"] == defaults.llm_model
    assert values["VISION_MODEL"] == defaults.vision_model
