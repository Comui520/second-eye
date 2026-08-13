from typing import Optional

from goodprice.models import WatchTask


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
        task = WatchTask(
            name=data.get("name", ""),
            keyword=data["keyword"],
            max_price=float(data.get("max_price") or 0),
            condition_requirement=data.get("condition_requirement", ""),
            min_condition_score=int(data.get("min_condition_score") or 0),
            platform=data.get("platform", "xianyu"),
            interval_minutes=int(data.get("interval_minutes") or 20),
            enabled=bool(data.get("enabled", True)),
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
