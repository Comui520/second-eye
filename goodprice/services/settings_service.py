from dataclasses import asdict, dataclass
from typing import Optional

from goodprice.config import Settings
from goodprice.models import AppSetting


@dataclass
class RuntimeSettings:
    _INT_FIELDS = {"default_crawl_interval_minutes", "default_crawl_jitter_minutes"}

    xianyu_cookie: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    wecom_corpid: str = ""
    wecom_agentid: str = ""
    wecom_secret: str = ""
    wecom_touser: str = "@all"
    wecom_webhook: str = ""

    @classmethod
    def from_sources(cls, base: Settings, overrides: dict[str, str]) -> "RuntimeSettings":
        values = asdict(cls())
        values.update({k: v for k, v in base.model_dump().items() if k in values})
        values.update({k: v for k, v in overrides.items() if v != "" and k in values})
        for key in cls._INT_FIELDS:
            if values.get(key) not in ("", None):
                values[key] = int(values[key])
        return cls(**values)


class SettingsService:
    def __init__(self, session_factory, base: Optional[Settings] = None):
        self._session_factory = session_factory
        self._base = base or Settings(_env_file=None)

    def _overrides(self, session) -> dict[str, str]:
        return {row.key: row.value for row in session.query(AppSetting).all()}

    def get(self) -> RuntimeSettings:
        with self._session_factory() as session:
            return RuntimeSettings.from_sources(self._base, self._overrides(session))

    def set_many(self, values: dict[str, str]) -> RuntimeSettings:
        with self._session_factory() as session:
            for key, value in values.items():
                row = session.get(AppSetting, key)
                if value == "":
                    if row:
                        session.delete(row)
                elif row:
                    row.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            session.commit()
            return RuntimeSettings.from_sources(self._base, self._overrides(session))
