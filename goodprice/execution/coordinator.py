import logging
import threading
import time
from datetime import datetime, timezone

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
        self.apply = apply

    def submit(self, task_id):
        result = self.store.submit(task_id, "crawl")
        self._wake.set()
        return result

    def submit_reanalysis(self, listing_id):
        from goodprice.models import Listing
        with self.store.factory() as session:
            listing = session.get(Listing, listing_id)
            if listing is None or not listing.task_id:
                return None
            result = self.store.submit(listing.task_id, "reanalyze", listing_id)
        self._wake.set()
        return result

    def pause(self, task_id):
        self.store.invalidate_task(task_id)

    def update_limits(self, limits):
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
        self.store.recover(self._now())
        self._thread = threading.Thread(target=self._loop, name="second-eye-coordinator", daemon=True)
        self._thread.start()

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def tick(self):
        now = self._now()
        for future, item in list(self._futures.items()):
            if not future.done():
                continue
            try:
                result = future.result()
            except Exception as exc:
                del self._futures[future]
                # Never log worker response bodies, URLs or credentials.
                logger.warning("阶段 %s 失败 (%s)", item.stage.value, type(exc).__name__)
                self.store.fail(item, now)
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
        for stage in Stage:
            while self.workers.available(stage):
                item = self.store.claim(stage, now)
                if item is None:
                    break
                future = self.workers.dispatch_item(item)
                self._futures[future] = item
                future.add_done_callback(lambda _: self._wake.set())

    def _loop(self):
        while not self._stop.is_set():
            delay = 1
            try:
                self.tick()
                delay = 1 if self._futures or self.store.has_open_work() else 10
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
