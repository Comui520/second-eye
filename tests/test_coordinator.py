import threading
import time

from goodprice.execution.coordinator import Coordinator
from goodprice.execution.limits import ResourceLimits
from goodprice.execution.store import ExecutionStore
from goodprice.execution.types import Stage
from goodprice.execution.workers import WorkerPools
from goodprice.services.task_service import TaskService


def test_slow_model_does_not_block_other_task_crawling(session_factory):
    service = TaskService(session_factory)
    first = service.create_task({"keyword": "first"})
    second = service.create_task({"keyword": "second"})
    analyzing, release, crawled = threading.Event(), threading.Event(), threading.Event()
    def search(item):
        if item.token.task_id == second.id:
            crawled.set()
        return {"next": [{"stage": "analyze", "fingerprint": "one", "payload": {}}]}
    def analyze(item):
        analyzing.set()
        assert release.wait(5)
        return {}
    limits = ResourceLimits()
    store = ExecutionStore(session_factory)
    workers = WorkerPools(limits, {Stage.SEARCH: search, Stage.ANALYZE: analyze})
    coordinator = Coordinator(store, limits, workers)
    try:
        coordinator.start()
        coordinator.submit(first.id)
        assert analyzing.wait(3)
        coordinator.submit(second.id)
        assert crawled.wait(3)
    finally:
        release.set()
        coordinator.stop(5)


def test_active_one_keeps_cross_task_work_serial(session_factory):
    service = TaskService(session_factory)
    first = service.create_task({"keyword": "first"})
    second = service.create_task({"keyword": "second"})
    held, release = threading.Event(), threading.Event()
    calls = []
    def search(item):
        calls.append(item.token.task_id)
        held.set()
        assert release.wait(5)
        return {}
    limits = ResourceLimits(active_runs=1)
    coordinator = Coordinator(ExecutionStore(session_factory, active_limit=1), limits,
        WorkerPools(limits, {Stage.SEARCH: search}))
    try:
        coordinator.start()
        coordinator.submit(first.id)
        assert held.wait(3)
        coordinator.submit(second.id)
        time.sleep(.1)
        assert calls == [first.id]
        release.set()
    finally:
        release.set()
        coordinator.stop(5)


def test_failed_stage_eventually_releases_active_run(session_factory):
    from datetime import datetime, timezone

    from goodprice.models import TaskRun
    task = TaskService(session_factory).create_task({"keyword": "failure"})
    current = [datetime.now(timezone.utc).timestamp()]
    def failed(item): raise OSError("controlled")
    limits = ResourceLimits()
    store = ExecutionStore(session_factory)
    controller = Coordinator(store, limits, WorkerPools(limits, {Stage.SEARCH: failed}), clock=lambda: current[0])
    run_id = controller.submit(task.id)
    try:
        for _ in range(8):
            controller.tick()
            time.sleep(.03)
            current[0] += 30
        with session_factory() as session:
            assert session.get(TaskRun, run_id).state == "failed"
    finally:
        controller.stop(1)


def test_limit_changes_drain_active_workers_and_allow_confirmed_increase():
    from datetime import datetime, timedelta, timezone

    from goodprice.execution.types import RunToken, WorkItemDTO
    entered, release = [], threading.Event()
    def held(item):
        entered.append(item.id)
        assert release.wait(5)
        return {}
    pools = WorkerPools(ResourceLimits(), {Stage.ANALYZE: held})
    def item(i):
        return WorkItemDTO(i, RunToken(i, i, 1, str(i)), Stage.ANALYZE, str(i), {}, 1,
            datetime.now(timezone.utc) + timedelta(minutes=1))
    try:
        one = pools.dispatch_item(item(1))
        pools.update_limits(ResourceLimits(model_workers=2))
        two = pools.dispatch_item(item(2))
        deadline = time.monotonic() + 2
        while len(entered) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(entered) == 2
        pools.update_limits(ResourceLimits(model_workers=1))
        assert not pools.available(Stage.ANALYZE)
        release.set()
        one.result(2)
        two.result(2)
        assert pools.available(Stage.ANALYZE)
    finally:
        release.set()
        pools.stop()


def test_idle_coordinator_does_not_spin_on_database(session_factory):
    from sqlalchemy import event
    queries = []
    engine = session_factory.kw["bind"]
    def count(*args): queries.append(1)
    event.listen(engine, "before_cursor_execute", count)
    limits = ResourceLimits()
    controller = Coordinator(ExecutionStore(session_factory), limits,
        WorkerPools(limits, {Stage.SEARCH: lambda item: {}}))
    try:
        controller.start()
        time.sleep(1)
    finally:
        controller.stop(2)
        event.remove(engine, "before_cursor_execute", count)
    assert len(queries) <= 40, f"idle controller made {len(queries)} SQL requests in one second"
