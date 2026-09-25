from __future__ import annotations

import math
from typing import Optional

from goodprice.models import WatchTask


def normalize_task_data(data: dict) -> dict:
    """Validate and normalize task input before it reaches the database."""
    keyword = str(data.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("关键词不能为空")

    def number(name: str) -> float:
        value = float(data.get(name) or 0)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} 必须是非负的有限数字")
        return value

    min_price = number("min_price")
    max_price = number("max_price")
    if max_price and min_price > max_price:
        raise ValueError("最低价不能高于最高价")

    try:
        score_value = data.get("min_condition_score", 0)
        interval_value = data.get("interval_minutes", 20)
        min_condition_score = int(0 if score_value in (None, "") else score_value)
        interval_minutes = int(20 if interval_value in (None, "") else interval_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("品相分数和抓取间隔必须是整数") from exc
    if not 0 <= min_condition_score <= 10:
        raise ValueError("最低品相分必须在 0 到 10 之间")
    if interval_minutes < 1:
        raise ValueError("抓取间隔不能小于 1 分钟")

    return {
        **data,
        "keyword": keyword,
        "name": str(data.get("name") or "").strip(),
        "max_price": max_price,
        "min_price": min_price,
        "exclude_words": str(data.get("exclude_words") or "").strip(),
        "condition_requirement": str(data.get("condition_requirement") or "").strip(),
        "min_condition_score": min_condition_score,
        "platform": str(data.get("platform") or "xianyu").strip() or "xianyu",
        "interval_minutes": interval_minutes,
        "fetch_detail": bool(data.get("fetch_detail", True)),
        "enabled": bool(data.get("enabled", True)),
    }


class TaskService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_tasks(self) -> list[WatchTask]:
        with self._session_factory() as session:
            return session.query(WatchTask).order_by(WatchTask.id).all()

    def get_task(self, task_id: int) -> Optional[WatchTask]:
        with self._session_factory() as session:
            return session.get(WatchTask, task_id)

    def create_task(self, data: dict) -> WatchTask:
        values = normalize_task_data(data)
        task = WatchTask(
            name=values["name"],
            keyword=values["keyword"],
            max_price=values["max_price"],
            min_price=values["min_price"],
            exclude_words=values["exclude_words"],
            condition_requirement=values["condition_requirement"],
            min_condition_score=values["min_condition_score"],
            platform=values["platform"],
            interval_minutes=values["interval_minutes"],
            fetch_detail=values["fetch_detail"],
            enabled=values["enabled"],
        )
        with self._session_factory() as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def toggle_task(self, task_id: int) -> Optional[WatchTask]:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.enabled = not task.enabled
                session.commit()
                session.refresh(task)
            return task

    def delete_task(self, task_id: int) -> bool:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if not task:
                return False
            session.delete(task)
            session.commit()
            return True

    def enabled_tasks(self) -> list[WatchTask]:
        with self._session_factory() as session:
            return session.query(WatchTask).filter(WatchTask.enabled.is_(True)).all()

    def update_task(self, task_id: int, data: dict) -> Optional[WatchTask]:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if not task:
                return None
            values = normalize_task_data({
                "name": task.name,
                "keyword": task.keyword,
                "max_price": task.max_price,
                "min_price": task.min_price,
                "exclude_words": task.exclude_words,
                "condition_requirement": task.condition_requirement,
                "min_condition_score": task.min_condition_score,
                "platform": task.platform,
                "interval_minutes": task.interval_minutes,
                "fetch_detail": task.fetch_detail,
                "enabled": task.enabled,
                **data,
            })
            for key in (
                "name", "keyword", "max_price", "min_price", "exclude_words",
                "condition_requirement", "min_condition_score", "platform",
                "interval_minutes", "fetch_detail", "enabled",
            ):
                setattr(task, key, values[key])
            session.commit()
            session.refresh(task)
            return task
