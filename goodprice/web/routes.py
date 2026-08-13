import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class TaskCreate(BaseModel):
    name: str = ""
    keyword: str
    max_price: float = 0
    condition_requirement: str = ""
    min_condition_score: int = 0
    platform: str = "xianyu"
    interval_minutes: int = 20
    fetch_detail: bool = True
    enabled: bool = True


def _services(request: Request):
    return request.app.state.task_service, request.app.state.settings_service


def _task_dict(task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "keyword": task.keyword,
        "max_price": task.max_price,
        "condition_requirement": task.condition_requirement,
        "min_condition_score": task.min_condition_score,
        "platform": task.platform,
        "interval_minutes": task.interval_minutes,
        "enabled": task.enabled,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "last_error": task.last_error,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification, WatchTask

        stats = {
            "tasks": session.query(WatchTask).count(),
            "enabled_tasks": session.query(WatchTask).filter(WatchTask.enabled.is_(True)).count(),
            "listings": session.query(Listing).count(),
            "notified": session.query(Notification).filter(Notification.status == "sent").count(),
        }
        recent = (
            session.query(Listing)
            .order_by(Listing.first_seen_at.desc())
            .limit(10)
            .all()
        )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"stats": stats, "recent": recent, "active": "dashboard"},
    )


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request):
    task_service, _ = _services(request)
    tasks = task_service.list_tasks()
    running_ids = request.app.state.guard.running_ids()
    just_ran = request.query_params.get("run")
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {"tasks": tasks, "running_ids": running_ids, "just_ran": just_ran, "active": "tasks"},
    )


@router.post("/tasks")
def create_task_form(
    request: Request,
    keyword: str = Form(...),
    name: str = Form(""),
    max_price: float = Form(0),
    condition_requirement: str = Form(""),
    min_condition_score: int = Form(0),
    interval_minutes: int = Form(20),
    fetch_detail: Optional[int] = Form(None),
    enabled: Optional[int] = Form(None),
):
    task_service, _ = _services(request)
    task_service.create_task(
        {
            "keyword": keyword.strip(),
            "name": name.strip(),
            "max_price": max_price,
            "condition_requirement": condition_requirement,
            "min_condition_score": min_condition_score,
            "interval_minutes": interval_minutes,
            "fetch_detail": bool(fetch_detail),
            "enabled": bool(enabled),
        }
    )
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.toggle_task(task_id)
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/run")
def run_task(request: Request, task_id: int):
    threading.Thread(
        target=request.app.state.run_job, args=(task_id,), daemon=True
    ).start()
    return RedirectResponse(f"/tasks?run={task_id}", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.delete_task(task_id)
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.get("/listings", response_class=HTMLResponse)
def listings_page(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        listings = (
            session.query(Listing).order_by(Listing.first_seen_at.desc()).limit(100).all()
        )
    return templates.TemplateResponse(
        request, "listings.html", {"listings": listings, "active": "listings"}
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    _, settings_service = _services(request)
    settings = settings_service.get()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "active": "settings"},
    )


@router.post("/settings")
def save_settings(
    request: Request,
    xianyu_cookie: str = Form(""),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    serverchan_sendkey: str = Form(""),
    proxy: str = Form(""),
    default_crawl_interval_minutes: int = Form(20),
    default_crawl_jitter_minutes: int = Form(10),
    vision_base_url: str = Form(""),
    vision_api_key: str = Form(""),
    vision_model: str = Form(""),
    wecom_corpid: str = Form(""),
    wecom_agentid: str = Form(""),
    wecom_secret: str = Form(""),
    wecom_touser: str = Form("@all"),
):
    _, settings_service = _services(request)
    values = {
        "xianyu_cookie": xianyu_cookie,
        "llm_base_url": llm_base_url,
        "llm_api_key": llm_api_key,
        "llm_model": llm_model,
        "serverchan_sendkey": serverchan_sendkey,
        "proxy": proxy,
        "default_crawl_interval_minutes": str(default_crawl_interval_minutes),
        "default_crawl_jitter_minutes": str(default_crawl_jitter_minutes),
        "vision_base_url": vision_base_url,
        "vision_api_key": vision_api_key,
        "vision_model": vision_model,
        "wecom_corpid": wecom_corpid,
        "wecom_agentid": wecom_agentid,
        "wecom_secret": wecom_secret,
        "wecom_touser": wecom_touser,
    }
    for key in ("llm_api_key", "serverchan_sendkey", "vision_api_key", "wecom_secret"):
        if values.get(key) == "":
            values.pop(key)  # 留空 = 保持原值
    settings_service.set_many(values)
    return RedirectResponse("/settings", status_code=303)


@router.get("/api/tasks")
def api_list_tasks(request: Request):
    task_service, _ = _services(request)
    return [_task_dict(t) for t in task_service.list_tasks()]


@router.post("/api/tasks")
def api_create_task(request: Request, data: TaskCreate):
    task_service, _ = _services(request)
    task = task_service.create_task(data.model_dump())
    request.app.state.sync_scheduler()
    return _task_dict(task)


@router.get("/api/listings")
def api_list_listings(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        rows = session.query(Listing).order_by(Listing.first_seen_at.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "platform": row.platform,
            "external_id": row.external_id,
            "title": row.title,
            "price": row.price,
            "url": row.url,
            "image_urls": row.image_urls,
            "condition_score": row.condition_score,
            "condition_detail": row.condition_detail,
            "notified_at": row.notified_at.isoformat() if row.notified_at else None,
        }
        for row in rows
    ]


@router.get("/api/stats")
def api_stats(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification, WatchTask

        return {
            "tasks": session.query(WatchTask).count(),
            "enabled_tasks": session.query(WatchTask).filter(WatchTask.enabled.is_(True)).count(),
            "listings": session.query(Listing).count(),
            "notified": session.query(Notification).filter(Notification.status == "sent").count(),
        }
