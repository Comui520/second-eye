from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "goodprice.db"


class Settings(BaseSettings):
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
    llm_model: str = "qwen-vl-max"
    llm_api_format: str = "chat_completions"
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "qwen-vl-max"
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
    jev_enabled: bool = False
    jev_auto_threshold: float = 0.85


@lru_cache
def get_settings() -> Settings:
    return Settings()
