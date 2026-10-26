from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
    data_root: Path = PROJECT_ROOT / "data"
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
    novnc_port: int = 16080
    jev_enabled: bool = False
    jev_auto_threshold: float = 0.85
    jev_backend: str = "adapter"
    jev_api_key: str = ""
    video_analysis_enabled: bool = False
    media_image_count: int = Field(8, ge=1, le=32)
    media_video_count: int = Field(1, ge=1, le=3)
    media_frames_per_video: int = Field(6, ge=1, le=24)
    media_image_quota: int = Field(4, ge=0, le=16)
    media_frame_quota: int = Field(4, ge=0, le=16)
    media_total_limit: int = Field(8, ge=1, le=16)
    media_model_limit: int = Field(8, ge=1, le=16)
    media_video_max_mib: int = Field(100, ge=10, le=200)
    media_video_max_seconds: int = Field(180, ge=15, le=300)
    media_cache_mib: int = Field(1024, ge=256, le=10240)
    media_retention_days: int = Field(7, ge=1, le=30)
    media_download_timeout: int = Field(60, ge=10, le=120)
    media_decode_timeout: int = Field(30, ge=10, le=120)
    media_request_max_mib: int = Field(16, ge=1, le=32)
    media_ffmpeg_threads: int = Field(1, ge=1, le=4)

    @model_validator(mode="after")
    def _media_budgets(self):
        if self.media_total_limit > self.media_model_limit:
            raise ValueError("视觉输入总上限不能超过已确认的模型上限（未知时为8）")
        # Serial assets share the same reservation; retained outputs from earlier
        # assets remain charged. JPEG outputs are capped at 2 MiB per image/frame.
        required = self.media_video_max_mib + 2 * (
            self.media_image_count + self.media_video_count * self.media_frames_per_video)
        if required > self.media_cache_mib:
            raise ValueError("媒体缓存不足以容纳一次处理的下载和输出，请减少视频/采样预算")
        return self

    @field_validator("jev_backend")
    @classmethod
    def _normalize_jev_backend(cls, value: str) -> str:
        """非法后端名回退到默认值，校验逻辑集中在 schema 一处。"""
        return value if value in JEV_BACKENDS else JEV_BACKENDS[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
