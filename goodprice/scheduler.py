from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


def build_scheduler(session_factory, run_job, task_service) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _sync_tasks,
        trigger=IntervalTrigger(minutes=5),
        args=[session_factory, run_job, task_service, scheduler],
        id="sync_tasks",
        replace_existing=True,
        max_instances=1,
    )
    _sync_tasks(session_factory, run_job, task_service, scheduler)
    return scheduler


def _sync_tasks(session_factory, run_job, task_service, scheduler) -> None:
    enabled_ids = {task.id for task in task_service.enabled_tasks()}
    job_ids = {job.id for job in scheduler.get_jobs()}
    for task_id in enabled_ids:
        job_id = f"crawl_{task_id}"
        if job_id in job_ids:
            continue
        task = task_service.get_task(task_id)
        scheduler.add_job(
            run_job,
            trigger=IntervalTrigger(minutes=max(1, task.interval_minutes)),
            args=[task_id],
            id=job_id,
            next_run_time=datetime.now(),
            replace_existing=True,
            max_instances=1,
        )
    for job_id in list(job_ids):
        if not job_id.startswith("crawl_"):
            continue
        task_id = int(job_id.removeprefix("crawl_"))
        if task_id not in enabled_ids:
            scheduler.remove_job(job_id)
