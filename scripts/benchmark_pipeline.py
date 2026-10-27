"""Offline, synthetic baseline; never constructs real network clients or uses .env."""
import argparse
import json
import socket
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event  # noqa: E402

from goodprice.config import Settings  # noqa: E402
from goodprice.crawler.base import ListingData, ListingDetail  # noqa: E402
from goodprice.db import Base, make_session_factory  # noqa: E402
from goodprice.services.crawl_service import CrawlService  # noqa: E402
from goodprice.services.settings_service import SettingsService  # noqa: E402
from goodprice.services.task_service import TaskService  # noqa: E402


class SyntheticAdapter:
    def __init__(self):
        self.detail_calls = 0

    def search(self, keyword):
        return [ListingData(str(i), f"synthetic {i}", 100, f"https://invalid.test/{i}")
                for i in range(30)]

    def fetch_detail(self, url):
        self.detail_calls += 1
        return ListingDetail(description="Synthetic offline description")


def run(rounds, mode):
    # Hard fail if future pipeline changes accidentally attempt real network I/O.
    def deny_network(*args, **kwargs):
        raise AssertionError("External network prohibited in benchmark")

    original = socket.socket.connect
    socket.socket.connect = deny_network
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="second-eye-benchmark-") as folder:
            for index in range(rounds):
                factory = make_session_factory(f"sqlite:///{Path(folder).as_posix()}/{index}.db")
                engine = factory.kw["bind"]
                Base.metadata.create_all(engine)
                settings = Settings(_env_file=None, database_url=str(engine.url),
                                    default_crawl_jitter_minutes=0, vision_enabled=False)
                task = TaskService(factory).create_task({"keyword": "synthetic"})
                adapter = SyntheticAdapter()
                service = CrawlService(factory, adapter, SimpleNamespace(enabled=False),
                                       SimpleNamespace(enabled=False), [],
                                       SettingsService(factory, settings))
                if mode == "warm":
                    service.run_task(task.id)
                queries = []
                event.listen(engine, "before_cursor_execute", lambda *args, q=queries: q.append(1))
                tracemalloc.start()
                start = time.perf_counter()
                stats = service.run_task(task.id)
                elapsed = time.perf_counter() - start
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                results.append({**stats, "seconds": elapsed, "sql_count": len(queries),
                                "python_peak_bytes": peak, "detail_calls": adapter.detail_calls,
                                "model_http_attempts": 0, "browser_starts": 0})
                engine.dispose()
    finally:
        socket.socket.connect = original
    times = sorted(row["seconds"] for row in results)
    return {"dataset": "synthetic-30", "mode": mode, "results": results,
            "external_network_requests": 0, "median_seconds": statistics.median(times),
            "p95_seconds": times[min(len(times) - 1, int(len(times) * .95))],
            "limits": "No real browser or model latency is represented; no real-platform speed claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["synthetic-30"], default="synthetic-30")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--mode", choices=["cold", "warm"], default="cold")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 100:
        parser.error("rounds must be between 1 and 100")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "benchmark.json").write_text(
        json.dumps(run(args.rounds, args.mode), ensure_ascii=False, indent=2), encoding="utf-8")
