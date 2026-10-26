from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from goodprice.execution.store import ExecutionStore
from goodprice.execution.types import Stage, WorkResult
from goodprice.models import TaskRun, WorkItem
from goodprice.services.task_service import TaskService


def task(factory):
    return TaskService(factory).create_task({"keyword": "controlled fixture"})


def test_submit_deduplicates_and_running_signals_merge_once(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    first = store.submit(row.id, "crawl")
    assert first and store.submit(row.id, "crawl") == first
    now = datetime.now(timezone.utc)
    claimed = store.claim(Stage.SEARCH, now)
    for _ in range(5):
        assert store.submit(row.id, "crawl") == first
    assert store.complete(WorkResult(claimed.token, Stage.SEARCH, "", {}), now)
    assert store.finish_run(first, now)
    with session_factory() as session:
        assert session.query(TaskRun).count() == 2
        assert session.query(TaskRun).filter_by(state="queued").count() == 1


def test_claim_is_exclusive_and_recovery_invalidates_old_lease(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory, lease_seconds=5)
    store.submit(row.id, "crawl")
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(lambda _: store.claim(Stage.SEARCH, now), range(2)))
    claim = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    assert store.recover(now + timedelta(seconds=6)) == 1
    newer = store.claim(Stage.SEARCH, now + timedelta(seconds=6))
    assert newer.token.lease_id != claim.token.lease_id
    assert not store.complete(WorkResult(claim.token, Stage.SEARCH, "", {"late": True}), now + timedelta(seconds=6))


def test_pause_version_change_or_deadline_rejects_late_result(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    store.submit(row.id, "crawl")
    now = datetime.now(timezone.utc)
    claim = store.claim(Stage.SEARCH, now)
    TaskService(session_factory).update_task(row.id, {"target_variant": "256GB"})
    assert not store.complete(WorkResult(claim.token, Stage.SEARCH, "", {"bad": True}), now)
    store.invalidate_task(row.id)
    store.submit(row.id, "crawl")
    claim = store.claim(Stage.SEARCH, now)
    assert not store.complete(WorkResult(claim.token, Stage.SEARCH, "", {}), now + timedelta(minutes=16))


def test_global_and_per_task_backpressure_does_not_drop_cursor(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory, global_limit=3, task_limit=2)
    run_id = store.submit(row.id, "crawl")
    assert store.enqueue(run_id, Stage.DETAIL, "one", {"listing_id": 1})
    assert not store.enqueue(run_id, Stage.DETAIL, "two", {"listing_id": 2})
    assert store.set_cursor(run_id, {"remaining_ids": [2, 3]})
    with session_factory() as session:
        assert session.get(TaskRun, run_id).cursor == {"remaining_ids": [2, 3]}
        assert session.query(WorkItem).filter(WorkItem.state.in_(["pending", "running"])).count() == 2
    other = task(session_factory)
    assert store.submit(other.id, "crawl")
    third = task(session_factory)
    assert store.submit(third.id, "crawl") is None


def test_task_edits_cancel_pending_work_in_same_transaction(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    run_id = store.submit(row.id)
    TaskService(session_factory).toggle_task(row.id)
    with session_factory() as session:
        assert session.get(TaskRun, run_id).state == "cancelled"
        assert session.query(WorkItem).one().state == "cancelled"


def test_expired_run_keeps_resume_reference_for_next_scheduled_run(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    old = store.submit(row.id)
    now = datetime.now(timezone.utc)
    store.claim(Stage.SEARCH, now)
    store.set_cursor(old, {"remaining_ids": [10, 11]})
    store.recover(now + timedelta(minutes=16))
    newer = store.submit(row.id)
    assert newer != old
    with session_factory() as session:
        assert session.get(TaskRun, old).state == "deferred"
        assert session.get(TaskRun, newer).cursor["resume_run_id"] == old
        assert session.get(TaskRun, newer).cursor["remaining_ids"] == [10, 11]


def test_claim_rotates_tasks_instead_of_draining_one_backlog(session_factory):
    first, second = task(session_factory), task(session_factory)
    store = ExecutionStore(session_factory)
    a, b = store.submit(first.id), store.submit(second.id)
    for i in range(4):
        store.enqueue(a, Stage.DETAIL, str(i), {})
    store.enqueue(b, Stage.DETAIL, "other", {})
    now = datetime.now(timezone.utc)
    one = store.claim(Stage.DETAIL, now)
    two = store.claim(Stage.DETAIL, now)
    assert [one.token.task_id, two.token.task_id] == [first.id, second.id]
