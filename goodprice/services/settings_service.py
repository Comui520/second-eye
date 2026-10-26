from typing import Optional

from goodprice.config import SECRET_FIELDS, Settings
from goodprice.models import AppSetting, SchemaVersion

# 设置字段只在 goodprice.config.Settings 定义一次。运行时值与数据库覆盖都经同一个
# pydantic 模型校验与类型转换（int/float/bool 由字段类型自动完成），不再维护第二份
# dataclass 字段副本和手写的类型集合，避免两处字段/类型漂移。
RuntimeSettings = Settings


class SettingsService:
    def __init__(self, session_factory, base: Optional[Settings] = None):
        self._session_factory = session_factory
        self._base = base or Settings(_env_file=None)

    def _overrides(self, session) -> dict[str, str]:
        return {row.key: row.value for row in session.query(AppSetting).all()}

    def _merge(self, overrides: dict[str, str], legacy: bool = False) -> RuntimeSettings:
        values = self._base.model_dump()
        if legacy and "media_total_limit" not in self._base.model_fields_set:
            values["media_total_limit"] = 4
        for key, value in overrides.items():
            if key in values and (value != "" or key in SECRET_FIELDS):
                values[key] = value
        return Settings(_env_file=None, **values)

    def get(self) -> RuntimeSettings:
        with self._session_factory() as session:
            marker = session.get(SchemaVersion, 1)
            return self._merge(self._overrides(session), bool(marker and marker.legacy_install))

    def set_many(self, values: dict[str, str], *, clear_keys=frozenset()) -> RuntimeSettings:
        if set(clear_keys) - SECRET_FIELDS:
            raise ValueError("Only credentials can be explicitly cleared")
        values = {**values, **dict.fromkeys(clear_keys, "")}
        with self._session_factory() as session:
            overrides = self._overrides(session)
            for key, value in values.items():
                if value == "" and key not in clear_keys:
                    overrides.pop(key, None)
                else:
                    overrides[key] = value
            marker = session.get(SchemaVersion, 1)
            validated = self._merge(overrides, bool(marker and marker.legacy_install))
            for key, value in values.items():
                row = session.get(AppSetting, key)
                if value == "" and key not in clear_keys:
                    if row:
                        session.delete(row)
                elif row:
                    row.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            session.commit()
            return validated
