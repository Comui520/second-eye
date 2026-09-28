from typing import Optional

from goodprice.config import Settings
from goodprice.models import AppSetting

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

    def _merge(self, overrides: dict[str, str]) -> RuntimeSettings:
        values = self._base.model_dump()
        for key, value in overrides.items():
            if key in values and value != "":
                values[key] = value
        return Settings(_env_file=None, **values)

    def get(self) -> RuntimeSettings:
        with self._session_factory() as session:
            return self._merge(self._overrides(session))

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
            return self._merge(self._overrides(session))
