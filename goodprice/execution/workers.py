import threading
from concurrent.futures import ThreadPoolExecutor

from goodprice.execution.browser import BrowserActor
from goodprice.execution.types import Stage, WorkResult

STAGE_LANE = {Stage.SEARCH: "crawler", Stage.DETAIL: "crawler", Stage.PARSE: "local",
    Stage.MEDIA: "media", Stage.ANALYZE: "model", Stage.AGGREGATE: "local", Stage.NOTIFY: "delivery"}


class WorkerPools:
    """The coordinator checks slots before dispatch: no unbounded executor queue."""
    def __init__(self, limits, handlers):
        self.handlers = handlers
        self.capacities = {"crawler": limits.crawler_workers, "model": limits.model_workers,
                           "media": limits.media_workers, "local": 1, "delivery": 1}
        # Threads are created lazily. Hard ceilings allow later confirmed raises
        # without replacing an executor that still owns active work.
        ceilings = {"model": 8, "media": 2, "local": 1, "delivery": 1}
        self.pools = {name: ThreadPoolExecutor(max_workers=count, thread_name_prefix=f"second-eye-{name}")
                      for name, count in ceilings.items()}
        self._busy = dict.fromkeys(self.capacities, 0)
        self._lock = threading.Lock()
        self.browsers = [BrowserActor(f"second-eye-browser-{i}") for i in range(2)]

    def update_limits(self, limits):
        with self._lock:
            self.capacities.update(crawler=limits.crawler_workers, model=limits.model_workers,
                                   media=limits.media_workers)

    def available(self, stage):
        lane = STAGE_LANE[stage]
        with self._lock:
            return (self._busy[lane] < self.capacities[lane] and stage in self.handlers
                    and (lane != "crawler" or any(actor.available() for actor in self.browsers)))

    def dispatch_item(self, item):
        lane = STAGE_LANE[item.stage]
        with self._lock:
            if self._busy[lane] >= self.capacities[lane]:
                raise RuntimeError("Resource lane full")
            actor = next((a for a in self.browsers if a.available()), None) if lane == "crawler" else None
            if lane == "crawler" and actor is None:
                raise RuntimeError("Browser lane full")
            self._busy[lane] += 1
        def execute():
            try:
                output = self.handlers[item.stage](item)
                return WorkResult(item.token, item.stage, item.input_fingerprint, output)
            finally:
                with self._lock:
                    self._busy[lane] -= 1
        return actor.submit(execute) if actor else self.pools[lane].submit(execute)

    def stop(self):
        for actor in self.browsers:
            actor.stop()
        for pool in self.pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
