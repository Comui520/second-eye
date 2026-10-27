from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


def build_scheduler(session_factory, submit_fn, task_service, jitter_seconds=0) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _sync_tasks,
        trigger=IntervalTrigger(minutes=5),
        args=[session_factory, submit_fn, task_service, scheduler, jitter_seconds],
        id="sync_tasks",
        replace_existing=True,
        max_instances=1,
    )
    _sync_tasks(session_factory, submit_fn, task_service, scheduler, jitter_seconds)
    return scheduler


def _sync_tasks(session_factory, submit_fn, task_service, scheduler, jitter_seconds=0) -> None:
    jitter = max(0, float(jitter_seconds() if callable(jitter_seconds) else jitter_seconds))
    enabled_ids = sorted(task.id for task in task_service.enabled_tasks())
    job_ids = {job.id for job in scheduler.get_jobs()}
    for task_id in enabled_ids:
        job_id = f"crawl_{task_id}"
        task = task_service.get_task(task_id)
        if job_id in job_ids:
            job = scheduler.get_job(job_id)
            if job.trigger.interval.total_seconds() != max(1, task.interval_minutes) * 60 or job.trigger.jitter != jitter:
                scheduler.reschedule_job(job_id, trigger=IntervalTrigger(minutes=max(1, task.interval_minutes), jitter=jitter))
            continue
        scheduler.add_job(
            submit_fn,
            trigger=IntervalTrigger(minutes=max(1, task.interval_minutes), jitter=jitter),
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
