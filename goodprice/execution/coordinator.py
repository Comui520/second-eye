import logging
import threading
import time
from datetime import datetime, timezone

from goodprice.execution.types import Stage

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(self, store, limits, workers, clock=time.time):
        self.store, self.limits, self.workers, self.clock = store, limits, workers, clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._futures = {}

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
            del self._futures[future]
            try:
                result = future.result()
                self.store.complete(result, now)
                self.store.materialize(now)
                self.store.finish_run(item.token.run_id, now)
            except Exception as exc:
                # Never log worker response bodies, URLs or credentials.
                logger.warning("阶段 %s 失败 (%s)", item.stage.value, type(exc).__name__)
                self.store.fail(item, now)
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

    def _loop(self):
        while not self._stop.is_set():
            self.tick()
            self._wake.wait(.05)
            self._wake.clear()

    def stop(self, timeout=10):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self.workers.stop()
