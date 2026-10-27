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


def test_manual_followup_target_survives_an_active_crawl(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    first = store.submit(row.id)
    now = datetime.now(timezone.utc)
    work = store.claim(Stage.SEARCH, now)
    assert store.submit(row.id, "reanalyze", 17) == first
    assert store.submit(row.id, "reanalyze", 18) is None
    assert store.complete(WorkResult(work.token, Stage.SEARCH, "", {}), now)
    store.finish_run(first, now)
    with session_factory() as session:
        following = session.query(TaskRun).filter_by(state="queued").one()
        assert following.kind == "reanalyze" and following.target_id == 17


def test_accepted_followup_is_not_lost_when_global_slots_fill(session_factory):
    first, other = task(session_factory), task(session_factory)
    store = ExecutionStore(session_factory, global_limit=1, active_limit=1)
    run = store.submit(first.id)
    now = datetime.now(timezone.utc)
    work = store.claim(Stage.SEARCH, now)
    store.submit(first.id)
    store.complete(WorkResult(work.token, Stage.SEARCH, "", {}), now)
    other_run = store.submit(other.id)
    store.finish_run(run, now)
    with session_factory() as session:
        assert session.query(TaskRun).filter_by(task_id=first.id, state="queued").count() == 1
        assert session.query(WorkItem).filter(WorkItem.state.in_(["running", "pending"])).count() == 1
    work = store.claim(Stage.SEARCH, now)
    store.complete(WorkResult(work.token, Stage.SEARCH, "", {}), now)
    store.finish_run(other_run, now)
    store.materialize(now)
    assert store.claim(Stage.SEARCH, now).token.task_id == first.id


def test_progress_updates_keep_accepted_manual_request_and_resumption_is_consumed(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    original = store.submit(row.id)
    now = datetime.now(timezone.utc)
    store.claim(Stage.SEARCH, now)
    store.submit(row.id, "reanalyze", 17)
    store.set_cursor(original, {"remaining_ids": [9]})
    with session_factory() as session:
        assert session.get(TaskRun, original).cursor["_manual_followup"]["target_id"] == 17


def test_completed_resumption_does_not_replay_same_deferred_run_forever(session_factory):
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    original = store.submit(row.id)
    now = datetime.now(timezone.utc)
    store.claim(Stage.SEARCH, now)
    later = now + timedelta(minutes=16)
    store.recover(later)
    resumed = store.submit(row.id)
    work = store.claim(Stage.SEARCH, later)
    store.set_cursor(resumed, {})
    store.complete(WorkResult(work.token, Stage.SEARCH, "", {}), later)
    store.finish_run(resumed, later)
    third = store.submit(row.id)
    with session_factory() as session:
        assert session.get(TaskRun, third).cursor.get("resume_run_id") != original


def test_domain_writes_and_step_completion_commit_or_rollback_together(session_factory):
    from goodprice.models import AppSetting
    row = task(session_factory)
    store = ExecutionStore(session_factory)
    store.submit(row.id)
    now = datetime.now(timezone.utc)
    work = store.claim(Stage.SEARCH, now)
    result = WorkResult(work.token, Stage.SEARCH, "", {"fixture": 1})
    def broken(result, session):
        session.add(AppSetting(key="atomic", value="no partial writes"))
        session.flush()
        raise RuntimeError("controlled commit failure")
    import pytest
    with pytest.raises(RuntimeError):
        store.complete(result, now, apply=broken)
    with session_factory() as session:
        assert session.get(AppSetting, "atomic") is None
        assert session.get(WorkItem, work.id).state == "running"
    def good(result, session):
        session.add(AppSetting(key="atomic", value="committed"))
        return {"applied": True}
    assert store.complete(result, now, apply=good)
    with session_factory() as session:
        assert session.get(AppSetting, "atomic").value == "committed"
        assert session.get(WorkItem, work.id).output["applied"] is True
