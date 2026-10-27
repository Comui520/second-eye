import logging
import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.exc import OperationalError

from goodprice.execution.types import Stage

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(self, store, limits, workers, clock=time.time, apply=None):
        self.store, self.limits, self.workers, self.clock = store, limits, workers, clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._futures = {}
        self._delivery_futures = {}
        self.apply = apply
        self._last_renew = self.clock()
        self._requested_limits = None
        self.effective = None

    def submit(self, task_id):
        result = self.store.submit(task_id, "crawl")
        self._wake.set()
        return result if result is not None else False

    def submit_reanalysis(self, listing_id):
        from goodprice.models import Listing
        with self.store.factory() as session:
            listing = session.get(Listing, listing_id)
            if listing is None or not listing.task_id:
                return False
            result = self.store.submit(listing.task_id, "reanalyze", listing_id)
        self._wake.set()
        return result if result is not None else False

    def pause(self, task_id):
        self.store.invalidate_task(task_id)

    def running_ids(self):
        from goodprice.models import TaskRun
        with self.store.factory() as session:
            return {r[0] for r in session.query(TaskRun.task_id).filter_by(state="active")}

    def queued_ids(self):
        from goodprice.models import TaskRun
        with self.store.factory() as session:
            return [r[0] for r in session.query(TaskRun.task_id).filter(
                TaskRun.state.in_(("queued", "waiting_capacity"))).order_by(TaskRun.id)]

    def wake(self):
        self._wake.set()

    def describe(self, task_id):
        from goodprice.models import TaskRun, WorkItem
        with self.store.factory() as session:
            run = session.query(TaskRun).filter_by(task_id=task_id).order_by(TaskRun.id.desc()).first()
            if run is None:
                return None
            work = session.query(WorkItem).filter_by(run_id=run.id).all()
            cursor = run.cursor or {}
            remaining = set(cursor.get("remaining_ids", []))
            stages = sorted({item.stage for item in work if item.state == "running"})
            return {"run_id": run.id, "state": run.state, "stages": stages,
                "planned": len(cursor.get("planned_ids", [])),
                "completed": len(set(cursor.get("planned_ids", [])) - remaining),
                "deferred_candidates": len(remaining), "model_http_attempts": cursor.get("_http_attempts", 0),
                "cache_hits": sum(bool((item.output or {}).get("cache_hit")) for item in work),
                "failed_steps": sum(item.state == "failed" for item in work)}

    def update_limits(self, limits):
        self._requested_limits = limits
        self._wake.set()

    def _apply_limits(self, limits):
        self.workers.update_limits(limits)
        with self.store.lock:
            self.store.active_limit = limits.active_runs
            self.store.global_limit = limits.global_items
            self.store.task_limit = limits.task_items
            self.store.run_minutes = limits.run_minutes
        self.limits = limits
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.store.recover(self._now(), startup=True)
        pipeline = getattr(self, "pipeline", None)
        if pipeline:
            pipeline.delivery.recover(self._now(), startup=True)
        self._thread = threading.Thread(target=self._loop, name="second-eye-coordinator", daemon=True)
        self._thread.start()

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def tick(self):
        now = self._now()
        if self._requested_limits is not None:
            self._apply_limits(self._requested_limits)
            self._requested_limits = None
        pipeline = getattr(self, "pipeline", None)
        if pipeline:
            from goodprice.execution.limits import ResourceLimits
            requested = ResourceLimits.from_settings(pipeline.settings.get())
            if requested != self.limits:
                self._apply_limits(requested)
            self.effective = pipeline.resources(self.limits)
            self.workers.update_limits(self.effective.limits)
            if self.effective.low:
                for actor in self.workers.browsers:
                    actor.trim_idle()
        if self.clock() - self._last_renew >= 30:
            for item in self._futures.values():
                self.store.renew(item.token, now)
            self._last_renew = self.clock()
        for future in list(self._delivery_futures):
            if future.done():
                del self._delivery_futures[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.warning("投递状态暂未完成 (%s)", type(exc).__name__)
        for future, item in list(self._futures.items()):
            if not future.done():
                continue
            try:
                result = future.result()
            except Exception as exc:
                del self._futures[future]
                # Never log worker response bodies, URLs or credentials.
                logger.warning("阶段 %s 失败 (%s)", item.stage.value, type(exc).__name__)
                delay = self._retry_after(exc, now)
                if delay:
                    provider = item.payload.get("provider_gate")
                    self.store.postpone_gate("provider:" + provider if provider else "model", now.timestamp() + delay)
                hook = getattr(getattr(self, "pipeline", None), "failed", None)
                self.store.fail(item, now, delay_seconds=delay, apply=hook)
                self.store.finish_run(item.token.run_id, now)
                continue
            try:
                self.store.complete(result, now, apply=self.apply)
            except OperationalError:
                # Keep the completed response in the bounded in-flight set.
                # A transient SQLite lock must not repeat the paid HTTP call.
                return
            except Exception as exc:
                logger.warning("结果写入失败 (%s)", type(exc).__name__)
                self.store.fail(item, now, terminal=True)
            del self._futures[future]
            self.store.materialize(now)
            self.store.finish_run(item.token.run_id, now)
        self.store.recover(now)
        self.store.materialize(now)
        pipeline = getattr(self, "pipeline", None)
        if pipeline:
            for run_id in pipeline.maintain(self.store, now):
                self.store.finish_run(run_id, now)
        for stage in Stage:
            while self.workers.available(stage):
                lane = {Stage.SEARCH: "crawler", Stage.DETAIL: "crawler", Stage.MEDIA: "media"}.get(stage)
                if self.effective and lane in self.effective.paused:
                    break
                if pipeline and stage in {Stage.SEARCH, Stage.DETAIL} and any(
                        pipeline.platform_io(work) for work in self._futures.values()):
                    break
                item = self.store.claim(stage, now)
                if item is None:
                    break
                if pipeline:
                    cached = pipeline.cached(item)
                    if cached is not None:
                        self.store.complete(cached, now, apply=self.apply)
                        self.store.materialize(now)
                        self.store.finish_run(item.token.run_id, now)
                        continue
                    if self.effective and item.payload.get("http") and "model" in self.effective.paused:
                        self.store.wait(item, now, 5)
                        break
                    if item.payload.get("http") and item.attempt > 3:
                        self.store.fail(item, now, terminal=True, apply=pipeline.failed)
                        self.store.finish_run(item.token.run_id, now)
                        continue
                    if item.payload.get("http") and not self.store.admit(item, now,
                            gate="model", interval=60 / self.limits.model_rpm, http_limit=self.limits.run_http_attempts,
                            provider=item.payload.get("provider_gate")):
                        break
                    if pipeline.platform_io(item) and not self.store.admit(item, now,
                            gate="platform", interval=self.limits.platform_interval_seconds):
                        break
                future = self.workers.dispatch_item(item)
                self._futures[future] = item
                future.add_done_callback(lambda _: self._wake.set())
        if pipeline and self.workers.side_available("delivery"):
            events = pipeline.delivery_events(now)
            if events:
                event_id = events[0]
                future = self.workers.dispatch_side("delivery", lambda: pipeline.delivery.deliver(event_id, self._now()))
                self._delivery_futures[future] = event_id
                future.add_done_callback(lambda _: self._wake.set())

    def _retry_after(self, exc, now):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) or getattr(exc, "status", None)
        if status != 429:
            return None
        headers = getattr(response, "headers", None) or getattr(exc, "headers", {})
        raw = headers.get("retry-after", "5")
        try:
            seconds = float(raw)
        except (ValueError, TypeError):
            try:
                seconds = parsedate_to_datetime(raw).timestamp() - now.timestamp()
            except (ValueError, TypeError, OverflowError):
                return 5
        return max(0, min(seconds, 86400)) if math.isfinite(seconds) else 5

    def _loop(self):
        while not self._stop.is_set():
            delay = 1
            try:
                self.tick()
                delay = 1 if self._futures or self._delivery_futures or self.store.has_open_work() else 10
            except Exception as exc:
                logger.warning("协调器暂未推进 (%s)", type(exc).__name__)
            self._wake.wait(delay)
            self._wake.clear()

    def stop(self, timeout=10):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self.workers.stop()
        pipeline = getattr(self, "pipeline", None)
        if pipeline:
            pipeline.registry.close()
