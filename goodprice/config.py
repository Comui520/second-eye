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
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "qwen-vl-max"
    wecom_corpid: str = ""
    wecom_agentid: str = ""
    wecom_secret: str = ""
    wecom_touser: str = "@all"
    wecom_webhook: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
