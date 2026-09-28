from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "goodprice.db"

# 敏感字段：设置页留空表示“保持原值”。保存逻辑与模板提示共用这一份清单，
# 避免“留空白名单”在 routes.py 与 settings.html 两处各维护一份而漂移。
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "llm_api_key",
        "serverchan_sendkey",
        "vision_api_key",
        "wecom_webhook",
        "feishu_webhook",
        "feishu_secret",
        "gotify_token",
        "jev_api_key",
    }
)

# Jev 后端可选值：保存校验与设置页下拉共用。
JEV_BACKENDS: tuple[str, ...] = ("adapter", "typesafe")


class Settings(BaseSettings):
    """全部可配置字段的唯一事实来源。

    运行时值（SettingsService）与部署模板（.env.example）都以本类为准；
    新增字段后请同步 .env.example，tests/test_settings_consistency.py 会校验。
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "闲鱼盯价助手"
    database_url: str = f"sqlite:///{DEFAULT_DB.as_posix()}"
    xianyu_cookie: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "glm-4.7-flash"
    llm_api_format: str = "chat_completions"
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "glm-4.6v-flash"
    vision_api_format: str = "chat_completions"
    wecom_webhook: str = ""
    feishu_webhook: str = ""
    feishu_secret: str = ""
    feishu_enabled: bool = True
    gotify_url: str = ""
    gotify_token: str = ""
    gotify_priority: int = 5
    gotify_enabled: bool = True
    serverchan_enabled: bool = True
    wecom_robot_enabled: bool = True
    vision_enabled: bool = True
    runtime_mode: str = "local"
    enable_novnc: bool = False
    novnc_password_file: str = "/app/data/.novnc-password"
    jev_enabled: bool = False
    jev_auto_threshold: float = 0.85
    jev_backend: str = "adapter"
    jev_api_key: str = ""

    @field_validator("jev_backend")
    @classmethod
    def _normalize_jev_backend(cls, value: str) -> str:
        """非法后端名回退到默认值，校验逻辑集中在 schema 一处。"""
        return value if value in JEV_BACKENDS else JEV_BACKENDS[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
