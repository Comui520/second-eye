"""Compare identical offline offers, excluding real network/browser/model latency.

The staged clock compresses admission intervals, not worker execution. This is
a Python/SQLite overhead and coverage benchmark, never a platform speed claim.
"""
import argparse
import json
import socket
import statistics
import sys
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil  # noqa: E402
from sqlalchemy import event  # noqa: E402

from goodprice.config import Settings  # noqa: E402
from goodprice.crawler.base import ListingData, ListingDetail  # noqa: E402
from goodprice.db import Base, make_session_factory  # noqa: E402
from goodprice.execution.model_provider import ModelProvider  # noqa: E402
from goodprice.execution.pipeline import build_pipeline  # noqa: E402
from goodprice.models import Listing, TaskRun, WorkItem  # noqa: E402
from goodprice.offers.types import OfferCandidate, PriceClaim  # noqa: E402
from goodprice.services.crawl_service import CrawlService  # noqa: E402
from goodprice.services.settings_service import SettingsService  # noqa: E402
from goodprice.services.task_service import TaskService  # noqa: E402


class Adapter:
    def __init__(self, clock, count):
        self.clock, self.count, self.detail_calls = clock, count, 0

    def search(self, *args, **kwargs):
        return [ListingData(str(i), f"synthetic {i}", 100, f"https://invalid.test/{i}") for i in range(self.count)]

    def fetch_detail(self, url):
        self.detail_calls += 1
        return ListingDetail(description="offline fixture", offers=[OfferCandidate("default", {}, claims=(
            PriceClaim("dom", "exact", "100", self.clock(), "fixture:price", amount_cents=10000),))])

    @contextmanager
    def session(self):
        yield self


class Provider(ModelProvider):
    def __init__(self):
        self.calls = 0

    def enabled(self, role, settings):
        return role != "visual"

    def requirement(self, data, settings=None, fallback=False):
        self.calls += 1
        return {"matched": True, "reason": "offline fixture"}

    def value(self, items, settings=None, fallback=False):
        self.calls += 1
        return {"scores": {i["external_id"]: 8 for i in items}, "best": items[0]["external_id"]}


class SerialClient:
    enabled = True
    def __init__(self, provider):
        self.provider = provider

    def analyze_requirement(self, **data):
        return self.provider.requirement(data)

    def analyze_batch_value(self, items):
        return self.provider.value(items)


def measure(engine_name, folder, index, count):
    factory = make_session_factory(f"sqlite:///{Path(folder).as_posix()}/{engine_name}-{index}.db")
    engine = factory.kw["bind"]
    Base.metadata.create_all(engine)
    current = [datetime.now(timezone.utc).timestamp()]
    settings = Settings(_env_file=None, database_url=str(engine.url), data_root=Path(folder) / "data",
        xianyu_cookie="", llm_api_key="", vision_api_key="", jev_api_key="", vision_enabled=False,
        jev_enabled=False, default_crawl_jitter_minutes=0, execution_run_minutes=60,
        execution_run_http_attempts=200, execution_model_rpm=60)
    service = SettingsService(factory, settings)
    task = TaskService(factory).create_task({"keyword": "synthetic", "condition_requirement": "fixture"})
    provider = Provider()
    def clock():
        return datetime.fromtimestamp(current[0], timezone.utc)
    adapter = Adapter(clock if engine_name == "staged" else lambda: datetime.now(timezone.utc), count)
    controller = (build_pipeline(factory, service, adapter_factory=lambda: adapter.session(),
        provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0]) if engine_name == "staged" else None)
    serial = CrawlService(factory, adapter, SerialClient(provider), SimpleNamespace(enabled=False), [], service)
    queries, results = [], {}
    event.listen(engine, "before_cursor_execute", lambda *args: queries.append(1))
    try:
        for mode in ("cold", "warm"):
            queries.clear()
            before, details = provider.calls, adapter.detail_calls
            tracemalloc.start()
            started, rss = time.perf_counter(), psutil.Process().memory_info().rss
            if controller:
                run = controller.submit(task.id)
                for _ in range(3000):
                    controller.tick()
                    rss = max(rss, psutil.Process().memory_info().rss)
                    current[0] += 5
                    with factory() as session:
                        finished = session.get(TaskRun, run).state in {"completed", "deferred", "failed", "cancelled"}
                    if finished:
                        break
                    time.sleep(.001)
                else:
                    raise RuntimeError("Benchmark graph did not settle")
            else:
                serial.run_task(task.id)
            seconds = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            with factory() as session:
                listings = session.query(Listing).all()
                verified = sum(r.offer_price_cents == 10000 and r.value_score == 8 for r in listings)
                stages = {}
                if controller:
                    for row in session.query(WorkItem).filter_by(run_id=run, state="done"):
                        if row.started_at and row.finished_at:
                            stages.setdefault(row.stage, []).append(max(0, (row.finished_at-row.started_at).total_seconds()))
            results[mode] = {"seconds": seconds, "sql_count": len(queries), "python_peak_bytes": peak,
                "process_peak_rss_bytes": rss, "detail_calls": adapter.detail_calls-details,
                "model_attempts": provider.calls-before, "verified_offers": verified,
                "stage_logical_seconds": {stage: {"median": statistics.median(values), "p95": sorted(values)[min(len(values)-1, int(len(values)*.95))]}
                    for stage, values in stages.items()}}
    finally:
        if controller:
            controller.stop(2)
        engine.dispose()
    return results


def run(rounds, count=30):
    def deny(*args, **kwargs):
        raise AssertionError("Network forbidden in offline benchmark")
    original = socket.socket.connect
    socket.socket.connect = deny
    report = {"dataset": f"synthetic-{count}", "external_network_requests": 0,
        "limits": "Same exact offers, requirement/value inputs and disabled vision. Admission intervals use compressed logical time. No actual browser/model/video latency; stage timings are logical time. RSS sampled for this Python process, not subprocesses."}
    try:
        with tempfile.TemporaryDirectory(prefix="second-eye-stages-") as folder:
            for engine_name in ("serial", "staged"):
                samples = [measure(engine_name, folder, i, count) for i in range(rounds)]
                report[engine_name] = {}
                for mode in ("cold", "warm"):
                    rows = [s[mode] for s in samples]
                    times = sorted(r["seconds"] for r in rows)
                    report[engine_name][mode] = {"results": rows, "median_seconds": statistics.median(times),
                        "p95_seconds": times[min(len(times)-1, int(len(times)*.95))]}
    finally:
        socket.socket.connect = original
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--items", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error("rounds must be 1–20")
    if not 1 <= args.items <= 30:
        parser.error("items must be 1–30")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "stages.json").write_text(json.dumps(run(args.rounds, args.items), indent=2), encoding="utf-8")
