import threading
import uuid
from datetime import datetime, timedelta, timezone

from goodprice.execution.types import RunToken, Stage, WorkItemDTO, freeze, thaw
from goodprice.models import NotificationDelivery, NotificationEvent, TaskRun, WatchTask, WorkItem

_locks = {}
_locks_guard = threading.Lock()
OPEN_WORK = ("pending", "running", "retry")
OPEN_RUN = ("queued", "active")


def naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def invalidate_in_session(session, task_id):
    session.query(TaskRun).filter(TaskRun.task_id == task_id, TaskRun.state.in_(OPEN_RUN)).update(
        {"state": "cancelled", "followup": False})
    session.query(WorkItem).filter(WorkItem.task_id == task_id, WorkItem.state.in_(OPEN_WORK)).update({"state": "cancelled"})
    events = session.query(NotificationEvent.id).filter_by(task_id=task_id)
    session.query(NotificationDelivery).filter(NotificationDelivery.event_id.in_(events),
        NotificationDelivery.state.in_(("pending", "failed", "awaiting_revalidation"))).update({"state": "cancelled"}, synchronize_session=False)


class ExecutionStore:
    def __init__(self, session_factory, global_limit=200, task_limit=60, active_limit=2,
                 lease_seconds=120, run_minutes=15):
        self.factory = session_factory
        self.global_limit, self.task_limit, self.active_limit = global_limit, task_limit, active_limit
        self.lease_seconds, self.run_minutes = lease_seconds, run_minutes
        self._last_task = {}
        with _locks_guard:
            self.lock = _locks.setdefault(str(session_factory.kw["bind"].url), threading.RLock())

    def _room(self, session, task_id):
        query = session.query(WorkItem).filter(WorkItem.state.in_(OPEN_WORK))
        return query.count() < self.global_limit and query.filter_by(task_id=task_id).count() < self.task_limit

    def _submit(self, session, task, kind, target_id):
        if not self._room(session, task.id):
            return None
        run = TaskRun(task_id=task.id, task_version=task.version, kind=kind, target_id=target_id)
        previous = session.query(TaskRun).filter_by(task_id=task.id, task_version=task.version,
            state="deferred", kind=kind, target_id=target_id).order_by(TaskRun.id.desc()).first()
        if previous:
            run.cursor = {**previous.cursor, "resume_run_id": previous.id}
        session.add(run)
        session.flush()
        session.add(WorkItem(run_id=run.id, task_id=task.id, stage=Stage.SEARCH.value,
                             payload={"target_id": target_id} if target_id else {}))
        return run.id

    def submit(self, task_id: int, kind: str = "crawl", target_id=None):
        with self.lock, self.factory() as session:
            task = session.get(WatchTask, task_id)
            if task is None or not task.enabled:
                return None
            existing = session.query(TaskRun).filter(TaskRun.task_id == task_id, TaskRun.state.in_(OPEN_RUN)).first()
            if existing and existing.task_version == task.version:
                if existing.state == "active":
                    existing.followup = True
                session.commit()
                return existing.id
            if existing:
                invalidate_in_session(session, task_id)
            result = self._submit(session, task, kind, target_id)
            session.commit()
            return result

    def enqueue(self, run_id, stage, fingerprint, payload):
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, run_id)
            if run is None or run.state not in OPEN_RUN or not self._room(session, run.task_id):
                return False
            if session.query(WorkItem).filter_by(run_id=run_id, stage=stage.value, input_fingerprint=fingerprint).first():
                return True
            session.add(WorkItem(run_id=run_id, task_id=run.task_id, stage=stage.value,
                                 input_fingerprint=fingerprint, payload=thaw(payload)))
            session.commit()
            return True

    def set_cursor(self, run_id, cursor):
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, run_id)
            if run is None or run.state not in OPEN_RUN:
                return False
            run.cursor = thaw(cursor)
            session.commit()
            return True

    def _valid(self, session, run, now):
        task = session.get(WatchTask, run.task_id)
        return (task is not None and task.enabled and task.version == run.task_version
                and run.state in OPEN_RUN and (run.deadline_at is None or run.deadline_at > now))

    def claim(self, stage: Stage, now: datetime):
        now = naive(now)
        with self.lock, self.factory() as session:
            active = session.query(TaskRun).filter_by(state="active").count()
            rows = session.query(WorkItem).join(TaskRun, TaskRun.id == WorkItem.run_id).filter(
                WorkItem.stage == stage.value, WorkItem.state.in_(("pending", "retry")),
                TaskRun.state.in_(OPEN_RUN)).order_by(WorkItem.attempt, WorkItem.id).all()
            task_ids = sorted({row.task_id for row in rows})
            last = self._last_task.get(stage, 0)
            rotated = [i for i in task_ids if i > last] + [i for i in task_ids if i <= last]
            order = {key: i for i, key in enumerate(rotated)}
            rows.sort(key=lambda row: (order[row.task_id], row.id))
            for item in rows:
                run = session.get(TaskRun, item.run_id)
                if not self._valid(session, run, now):
                    invalidate_in_session(session, run.task_id)
                    continue
                if item.not_before and item.not_before > now:
                    continue
                if run.state == "queued":
                    if active >= self.active_limit:
                        continue
                    run.state, run.started_at = "active", now
                    run.deadline_at = now + timedelta(minutes=self.run_minutes)
                item.state, item.lease_id = "running", uuid.uuid4().hex
                item.lease_until = min(run.deadline_at, now + timedelta(seconds=self.lease_seconds))
                item.attempt += 1
                self._last_task[stage] = item.task_id
                session.commit()
                return WorkItemDTO(item.id, RunToken(run.id, run.task_id, run.task_version, item.lease_id),
                    stage, item.input_fingerprint, freeze(item.payload), item.attempt,
                    run.deadline_at.replace(tzinfo=timezone.utc))
            session.commit()
            return None

    def complete(self, result, now: datetime):
        now = naive(now)
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, result.token.run_id)
            if run is None or not self._valid(session, run, now) or run.task_version != result.token.task_version:
                return False
            item = session.query(WorkItem).filter_by(run_id=run.id, lease_id=result.token.lease_id,
                state="running", stage=result.stage.value, input_fingerprint=result.input_fingerprint).first()
            if item is None or item.lease_until <= now:
                return False
            item.state, item.output, item.lease_id = "done", thaw(result.output), None
            session.commit()
            return True

    def finish_run(self, run_id, now):
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, run_id)
            if run is None or not self._valid(session, run, naive(now)):
                return False
            if session.query(WorkItem).filter(WorkItem.run_id == run_id, WorkItem.state.in_(OPEN_WORK)).count():
                return False
            if any((item.output or {}).get("next") for item in session.query(WorkItem).filter_by(run_id=run_id, state="done")):
                return False
            run.state = "failed" if session.query(WorkItem).filter_by(run_id=run_id, state="failed").first() else "completed"
            followup = run.followup
            run.followup = False
            session.flush()
            if followup:
                self._submit(session, session.get(WatchTask, run.task_id), run.kind, run.target_id)
            session.commit()
            return True

    def recover(self, now):
        now = naive(now)
        count = 0
        with self.lock, self.factory() as session:
            for item in session.query(WorkItem).filter_by(state="running"):
                if item.lease_until and item.lease_until <= now:
                    item.state, item.lease_id = "pending", None
                    count += 1
            for run in session.query(TaskRun).filter(TaskRun.state.in_(OPEN_RUN)):
                task = session.get(WatchTask, run.task_id)
                if task is None or not task.enabled or task.version != run.task_version:
                    invalidate_in_session(session, run.task_id)
                elif run.deadline_at and run.deadline_at <= now:
                    run.state = "deferred"
                    session.query(WorkItem).filter(WorkItem.run_id == run.id,
                        WorkItem.state.in_(OPEN_WORK)).update({"state": "deferred", "lease_id": None})
            session.query(NotificationDelivery).filter(NotificationDelivery.state == "sending",
                NotificationDelivery.lease_until <= now).update({"state": "uncertain"})
            session.commit()
        return count

    def invalidate_task(self, task_id):
        with self.lock, self.factory() as session:
            invalidate_in_session(session, task_id)
            session.commit()

    def materialize(self, now):
        """Durably drain bounded fan-out; unexpanded outputs survive a restart."""
        with self.lock, self.factory() as session:
            rows = session.query(WorkItem).filter_by(state="done").order_by(WorkItem.id).all()
            for item in rows:
                pending = list((item.output or {}).get("next", []))
                if not pending:
                    continue
                run = session.get(TaskRun, item.run_id)
                if not self._valid(session, run, naive(now)):
                    continue
                expanded = 0
                for node in pending:
                    if not self._room(session, run.task_id):
                        break
                    stage = Stage(node["stage"])
                    fingerprint = str(node.get("fingerprint", ""))[:64]
                    if not session.query(WorkItem).filter_by(run_id=run.id, stage=stage.value, input_fingerprint=fingerprint).first():
                        session.add(WorkItem(run_id=run.id, task_id=run.task_id, stage=stage.value,
                            input_fingerprint=fingerprint, payload=thaw(node.get("payload", {}))))
                        session.flush()
                    expanded += 1
                item.output = {**item.output, "next": pending[expanded:]}
            session.commit()

    def fail(self, item, now):
        with self.lock, self.factory() as session:
            row = session.get(WorkItem, item.id)
            if row is None or row.state != "running" or row.lease_id != item.token.lease_id:
                return
            row.state = "retry" if row.attempt < 3 else "failed"
            row.lease_id = None
            row.not_before = naive(now) + timedelta(seconds=2 ** row.attempt)
            session.commit()
