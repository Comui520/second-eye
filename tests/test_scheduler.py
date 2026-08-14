from datetime import datetime

from goodprice.scheduler import _sync_tasks
from goodprice.services.task_service import TaskService


class FakeJob:
    def __init__(self, job_id):
        self.id = job_id


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger=None, args=None, id=None, **kwargs):
        self.jobs[id] = {"func": func, "args": args, **kwargs}

    def get_jobs(self):
        return [FakeJob(job_id) for job_id in self.jobs]

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def test_sync_adds_job_for_enabled_task(session_factory):
    task_service = TaskService(session_factory)
    task_service.create_task({"keyword": "k", "interval_minutes": "30"})
    scheduler = FakeScheduler()
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" in scheduler.jobs


def test_sync_removes_job_for_disabled_or_deleted(session_factory):
    task_service = TaskService(session_factory)
    task = task_service.create_task({"keyword": "k"})
    scheduler = FakeScheduler()
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" in scheduler.jobs
    task_service.toggle_task(task.id)
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" not in scheduler.jobs


def test_sync_schedules_first_run_immediately(session_factory):
    task_service = TaskService(session_factory)
    task_service.create_task({"keyword": "k", "interval_minutes": "30"})
    scheduler = FakeScheduler()
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    job = scheduler.jobs["crawl_1"]
    assert job["next_run_time"] is not None
    delta = (job["next_run_time"] - datetime.now()).total_seconds()
    assert delta < 5  # 第一次不等待完整间隔
