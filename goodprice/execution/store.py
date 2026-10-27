import threading
import uuid
from datetime import datetime, timedelta, timezone

from goodprice.execution.types import RunToken, Stage, WorkItemDTO, freeze, thaw
from goodprice.models import (
    AppSetting,
    NotificationDelivery,
    NotificationEvent,
    TaskRun,
    WatchTask,
    WorkItem,
)

_locks = {}
_locks_guard = threading.Lock()
OPEN_WORK = ("pending", "running", "retry")
OPEN_RUN = ("queued", "active", "waiting_capacity")


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

    def _submit(self, session, task, kind, target_id, *, accepted_followup=False):
        room = self._room(session, task.id)
        if not room and not accepted_followup:
            return None
        run = TaskRun(task_id=task.id, task_version=task.version, kind=kind, target_id=target_id)
        used = session.query(TaskRun.cursor["resume_run_id"].as_integer()).filter(
            TaskRun.task_id == task.id, TaskRun.cursor["resume_run_id"].as_integer().is_not(None))
        previous = session.query(TaskRun).filter(TaskRun.task_id == task.id, TaskRun.task_version == task.version,
            TaskRun.state.in_(("deferred", "failed")), TaskRun.kind == kind, TaskRun.target_id == target_id,
            TaskRun.id.not_in(used)).order_by(TaskRun.id.desc()).first()
        if previous:
            run.cursor = {**{k: v for k, v in previous.cursor.items() if k not in {"_draining", "_http_attempts"}}, "resume_run_id": previous.id}
        session.add(run)
        session.flush()
        if room:
            self._add_root(session, run)
        else:
            run.cursor = {**(run.cursor or {}), "_root_pending": True}
        return run.id

    def _add_root(self, session, run):
        session.add(WorkItem(run_id=run.id, task_id=run.task_id, stage=Stage.SEARCH.value,
                             payload={"target_id": run.target_id} if run.target_id else {}))

    def _promote_followup(self, session, run):
        cursor = dict(run.cursor or {})
        manual = cursor.pop("_manual_followup", None)
        due = run.followup
        run.cursor, run.followup = cursor, False
        task = session.get(WatchTask, run.task_id)
        if task is None or not task.enabled or task.version != run.task_version:
            return
        if manual:
            next_id = self._submit(session, task, "reanalyze", manual["target_id"], accepted_followup=True)
            session.get(TaskRun, next_id).followup = due
        elif due:
            self._submit(session, task, "crawl", None, accepted_followup=True)

    def submit(self, task_id: int, kind: str = "crawl", target_id=None):
        with self.lock, self.factory() as session:
            task = session.get(WatchTask, task_id)
            if task is None or not task.enabled:
                return None
            existing = session.query(TaskRun).filter(TaskRun.task_id == task_id, TaskRun.state.in_(OPEN_RUN)).first()
            if existing and existing.task_version == task.version:
                if kind == "reanalyze":
                    if existing.kind == kind and existing.target_id == target_id:
                        return existing.id
                    pending = (existing.cursor or {}).get("_manual_followup")
                    if pending and pending["target_id"] != target_id:
                        return None
                    existing.cursor = {**(existing.cursor or {}), "_manual_followup": {"target_id": target_id}}
                elif existing.state != "queued" or existing.kind == "reanalyze":
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
            protected = {k: v for k, v in (run.cursor or {}).items() if k.startswith("_") or k == "resume_run_id"}
            run.cursor = {**thaw(cursor), **protected}
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
            from sqlalchemy import or_
            waiting_ready = session.query(WorkItem.id).join(TaskRun, TaskRun.id == WorkItem.run_id).filter(
                TaskRun.state == "waiting_capacity", WorkItem.state.in_(("pending", "retry")),
                or_(WorkItem.not_before.is_(None), WorkItem.not_before <= now)).first() is not None
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
                cache_key = (item.payload or {}).get("cache_key")
                if stage == Stage.ANALYZE and cache_key and session.query(WorkItem.id).filter(
                        WorkItem.state == "running", WorkItem.stage == stage.value,
                        WorkItem.payload["cache_key"].as_string() == cache_key).first():
                    continue
                if run.state != "active":
                    if run.state == "queued" and waiting_ready:
                        continue
                    if active >= self.active_limit:
                        continue
                    run.state = "active"
                    if run.started_at is None:
                        run.started_at = now
                        run.deadline_at = now + timedelta(minutes=self.run_minutes)
                item.state, item.lease_id = "running", uuid.uuid4().hex
                item.started_at = now
                item.lease_until = min(run.deadline_at, now + timedelta(seconds=self.lease_seconds))
                item.attempt += 1
                self._last_task[stage] = item.task_id
                session.commit()
                return WorkItemDTO(item.id, RunToken(run.id, run.task_id, run.task_version, item.lease_id),
                    stage, item.input_fingerprint, freeze(item.payload), item.attempt,
                    run.deadline_at.replace(tzinfo=timezone.utc))
            session.commit()
            return None

    def complete(self, result, now: datetime, *, apply=None):
        now = naive(now)
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, result.token.run_id)
            if run is None or not self._valid(session, run, now) or run.task_version != result.token.task_version:
                return False
            from sqlalchemy import update
            gate = session.execute(update(WatchTask).where(WatchTask.id == run.task_id,
                WatchTask.version == run.task_version, WatchTask.enabled.is_(True)).values(
                    version=WatchTask.version).execution_options(synchronize_session=False))
            if gate.rowcount != 1:
                return False
            item = session.query(WorkItem).filter_by(run_id=run.id, lease_id=result.token.lease_id,
                state="running", stage=result.stage.value, input_fingerprint=result.input_fingerprint).first()
            if item is None or item.lease_until <= now:
                return False
            output = thaw(result.output)
            if apply:
                applied = apply(result, session)
                if applied:
                    output.update(thaw(applied))
            if output.get("no_http") and item.payload.get("_admitted_lease") == result.token.lease_id:
                run.cursor = {**(run.cursor or {}), "_http_attempts": max(0, (run.cursor or {}).get("_http_attempts", 0) - 1)}
            item.state, item.output, item.lease_id = "done", output, None
            item.finished_at = now
            session.commit()
            return True

    def finish_run(self, run_id, now):
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, run_id)
            if run is None or not self._valid(session, run, naive(now)):
                return False
            guard = getattr(self, "finish_guard", None)
            if guard and not guard(session, run):
                return False
            if session.query(WorkItem).filter(WorkItem.run_id == run_id, WorkItem.state.in_(OPEN_WORK)).count():
                return False
            if any((item.output or {}).get("next") for item in session.query(WorkItem).filter_by(run_id=run_id, state="done")):
                return False
            run.state = "failed" if session.query(WorkItem).filter_by(run_id=run_id, state="failed").first() else "completed"
            state_for = getattr(self, "completion_state", None)
            if state_for and run.state == "completed":
                run.state = state_for(session, run)
            session.flush()
            self._promote_followup(session, run)
            session.commit()
            return True

    def recover(self, now, *, startup=False):
        now = naive(now)
        count = 0
        with self.lock, self.factory() as session:
            for item in session.query(WorkItem).filter_by(state="running"):
                if startup or item.lease_until and item.lease_until <= now:
                    item.state, item.lease_id = "pending", None
                    count += 1
            for run in session.query(TaskRun).filter(TaskRun.state.in_(OPEN_RUN)).all():
                task = session.get(WatchTask, run.task_id)
                if task is None or not task.enabled or task.version != run.task_version:
                    invalidate_in_session(session, run.task_id)
                elif run.deadline_at and run.deadline_at <= now:
                    run.state = "deferred"
                    session.query(WorkItem).filter(WorkItem.run_id == run.id,
                        WorkItem.state.in_(OPEN_WORK)).update({"state": "deferred", "lease_id": None})
                    session.flush()
                    self._promote_followup(session, run)
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
            from sqlalchemy import func
            for run in session.query(TaskRun).filter(TaskRun.state == "queued",
                    TaskRun.cursor["_root_pending"].as_boolean().is_(True)).order_by(TaskRun.id).all():
                if not self._room(session, run.task_id):
                    break
                cursor = dict(run.cursor)
                cursor.pop("_root_pending", None)
                run.cursor = cursor
                self._add_root(session, run)
                session.flush()
            active_runs = session.query(TaskRun.id).filter(TaskRun.state.in_(OPEN_RUN))
            rows = session.query(WorkItem).filter(WorkItem.state == "done", WorkItem.run_id.in_(active_runs),
                func.json_array_length(WorkItem.output["next"]) > 0).order_by(WorkItem.id).all()
            for item in rows:
                pending = list((item.output or {}).get("next", []))
                if not pending:
                    continue
                run = session.get(TaskRun, item.run_id)
                if not self._valid(session, run, naive(now)):
                    continue
                expanded = 0
                for node in pending:
                    if (run.cursor or {}).get("_draining") and node["stage"] not in {"parse", "aggregate"}:
                        expanded += 1
                        continue
                    if not self._room(session, run.task_id):
                        if not session.query(WorkItem.id).filter(WorkItem.run_id == run.id,
                                WorkItem.state.in_(OPEN_WORK)).first():
                            run.state = "waiting_capacity"
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

    def fail(self, item, now, *, terminal=False, delay_seconds=None, apply=None):
        with self.lock, self.factory() as session:
            row = session.get(WorkItem, item.id)
            if row is None or row.state != "running" or row.lease_id != item.token.lease_id:
                return
            row.state = "retry" if row.attempt < 3 and not terminal else "failed"
            row.lease_id = None
            row.not_before = naive(now) + timedelta(seconds=max(2 ** row.attempt, delay_seconds or 0))
            if row.state == "failed" and apply:
                run = session.get(TaskRun, row.run_id)
                if self._valid(session, run, naive(now)):
                    apply(session, row)
            session.commit()

    def admit(self, item, now, *, gate, interval, http_limit=None, provider=None):
        """Reserve one real request; cache hits never pass through this method."""
        with self.lock, self.factory() as session:
            row = session.get(WorkItem, item.id)
            run = session.get(TaskRun, item.token.run_id)
            if not run or not self._valid(session, run, naive(now)) or row.state != "running" or row.lease_id != item.token.lease_id:
                return False
            key = "$execution:gate:" + gate
            entry = session.get(AppSetting, key)
            allowed = float(entry.value) if entry else 0
            if provider:
                cooldown = session.get(AppSetting, "$execution:gate:provider:" + provider)
                allowed = max(allowed, float(cooldown.value) if cooldown else 0)
            if allowed > now.timestamp():
                row.state, row.lease_id = "pending", None
                row.attempt = max(0, row.attempt - 1)
                row.not_before = datetime.fromtimestamp(min(allowed, run.deadline_at.replace(tzinfo=timezone.utc).timestamp()), timezone.utc).replace(tzinfo=None)
                session.commit()
                return False
            cursor = dict(run.cursor or {})
            if http_limit is not None and cursor.get("_http_attempts", 0) >= http_limit:
                cursor["_draining"] = True
                run.cursor = cursor
                row.state, row.lease_id = "deferred", None
                session.query(WorkItem).filter(WorkItem.run_id == run.id, WorkItem.state.in_(("pending", "retry")),
                    WorkItem.stage.in_(("search", "detail", "media", "analyze"))).update({"state": "deferred"})
                session.commit()
                return False
            if http_limit is not None:
                run.cursor = {**cursor, "_http_attempts": cursor.get("_http_attempts", 0) + 1}
                row.payload = {**row.payload, "_admitted_lease": item.token.lease_id}
            until = str(now.timestamp() + interval)
            if entry:
                entry.value = until
            else:
                session.add(AppSetting(key=key, value=until))
            session.commit()
            return True

    def postpone_gate(self, gate, until):
        with self.lock, self.factory() as session:
            key = "$execution:gate:" + gate
            row = session.get(AppSetting, key)
            if row:
                row.value = str(max(float(row.value), until))
            else:
                session.add(AppSetting(key=key, value=str(until)))
            session.commit()

    def wait(self, item, now, delay):
        with self.lock, self.factory() as session:
            row = session.get(WorkItem, item.id)
            if row and row.state == "running" and row.lease_id == item.token.lease_id:
                row.state, row.lease_id = "pending", None
                row.attempt = max(0, row.attempt - 1)
                row.not_before = naive(now) + timedelta(seconds=delay)
                session.commit()

    def renew(self, token, now):
        with self.lock, self.factory() as session:
            run = session.get(TaskRun, token.run_id)
            if run and self._valid(session, run, naive(now)):
                row = session.query(WorkItem).filter_by(run_id=run.id, state="running", lease_id=token.lease_id).first()
                if row:
                    row.lease_until = min(run.deadline_at, naive(now) + timedelta(seconds=self.lease_seconds))
                    session.commit()

    def has_open_work(self):
        with self.factory() as session:
            return session.query(WorkItem.id).filter(WorkItem.state.in_(OPEN_WORK)).first() is not None
