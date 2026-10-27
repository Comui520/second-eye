import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from goodprice.execution.pipeline import build_pipeline
from goodprice.main import build_app
from goodprice.models import Listing, TaskRun


def test_application_lifecycle_uses_durable_coordinator_for_manual_task(session_factory, base_settings):
    from test_production_pipeline import Adapter
    adapter = Adapter()
    current = [datetime.now(timezone.utc).timestamp()]
    def execution(factory, settings):
        return build_pipeline(factory, settings, adapter_factory=lambda: adapter.session(),
                              notifier_factory=lambda: {}, clock=lambda: current[0])
    app = build_app(base_settings, session_factory, with_scheduler=False, execution_factory=execution)
    assert app.state.coordinator is app.state.task_queue
    with TestClient(app) as client:
        task = client.post("/api/tasks", json={"keyword": "offline"}).json()
        assert client.post(f"/tasks/{task['id']}/run", follow_redirects=False).status_code == 303
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current[0] += 2
            with session_factory() as session:
                run = session.query(TaskRun).first()
                complete = run is not None and run.state == "completed"
            if complete:
                break
            time.sleep(.02)
        assert complete
        with session_factory() as session:
            listing_id = session.query(Listing).one().id
        assert client.get(f"/api/listings/{listing_id}/offers").json()[0]["evaluation"]["eligible"]


def test_editing_task_interval_updates_existing_scheduler_trigger(session_factory):
    from goodprice.scheduler import _sync_tasks, build_scheduler
    from goodprice.services.task_service import TaskService
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "offline", "interval_minutes": 20})
    scheduler = build_scheduler(session_factory, lambda task_id: None, service)
    service.update_task(task.id, {"interval_minutes": 7})
    _sync_tasks(session_factory, lambda task_id: None, service, scheduler)
    assert scheduler.get_job(f"crawl_{task.id}").trigger.interval.total_seconds() == 420

def test_shutdown_runs_when_lifespan_exits_with_error(session_factory, base_settings):
    import pytest
    class Controller:
        stopped = False
        def start(self): pass
        def stop(self): self.stopped = True
        def running_ids(self): return set()
        def submit(self, task_id): return 1
        def submit_reanalysis(self, listing_id): return 1
    controller = Controller()
    app = build_app(base_settings, session_factory, with_scheduler=False,
                    execution_factory=lambda *a: controller)
    import asyncio
    async def exercise():
        async with app.router.lifespan_context(app):
            raise RuntimeError("fixture exit")
    with pytest.raises(RuntimeError, match="fixture exit"):
        asyncio.run(exercise())
    assert controller.stopped

def test_restart_does_not_rewrite_scores_or_load_every_price_history(session_factory, base_settings):
    from sqlalchemy import event
    with session_factory() as session:
        session.add(Listing(platform="fixture", external_id="kept", title="fixture", price=10, url="x", satisfaction=73))
        session.commit()
    statements = []
    engine = session_factory.kw["bind"]
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", record)
    try:
        build_app(base_settings, session_factory, with_scheduler=False)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    with session_factory() as session:
        assert session.query(Listing).one().satisfaction == 73
    assert not any("from price_snapshots" in s for s in statements)
