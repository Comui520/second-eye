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
    running, items = _progress_context(request, just_ran)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "tasks": tasks,
            "running_ids": running_ids,
            "just_ran": just_ran,
            "running": running,
            "items": items,
            "active": "tasks",
        },
    )


def _progress_context(request: Request, just_ran=None):
    guard = request.app.state.guard
    running_ids = guard.running_ids()
    running = next(iter(running_ids), None) or just_ran
    items = []
    if running:
        with request.app.state.session_factory() as session:
            from goodprice.models import Listing, WatchTask

            task = session.get(WatchTask, running)
            if task and task.last_run_at:
                items = (
                    session.query(Listing)
                    .filter(Listing.first_seen_at >= task.last_run_at)
                    .order_by(Listing.id)
                    .all()
                )
    return running, items


@router.get("/tasks/progress")
def tasks_progress(request: Request):
    running, items = _progress_context(request)
    return templates.TemplateResponse(request, "progress.html", {"running": running, "items": items})


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification

        rows = (
            session.query(Notification, Listing)
            .outerjoin(Listing, Listing.id == Notification.listing_id)
            .order_by(Notification.id.desc())
            .limit(200)
            .all()
        )
    return templates.TemplateResponse(
        request, "notifications.html", {"rows": rows, "active": "notifications"}
    )


@router.post("/notifications/delete-batch")
def delete_notifications_batch(request: Request, ids: list[int] = Form(...)):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        session.query(Notification).filter(Notification.id.in_(ids)).delete(
            synchronize_session=False
        )
        session.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/{notification_id}/delete")
def delete_notification(request: Request, notification_id: int):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        row = session.get(Notification, notification_id)
        if row:
            session.delete(row)
            session.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.get("/api/notifications")
def api_list_notifications(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        rows = session.query(Notification).order_by(Notification.id.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "channel": r.channel,
            "status": r.status,
            "title": r.title,
            "content": r.content,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


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


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_page(request: Request, task_id: int):
    task_service, _ = _services(request)
    task = task_service.get_task(task_id)
    if task is None:
        return RedirectResponse("/tasks", status_code=303)
    return templates.TemplateResponse(request, "tasks_edit.html", {"task": task, "active": "tasks"})


@router.post("/tasks/{task_id}/edit")
def edit_task_form(
    request: Request,
    task_id: int,
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
    task_service.update_task(
        task_id,
        {
            "keyword": keyword.strip(),
            "name": name.strip(),
            "max_price": max_price,
            "condition_requirement": condition_requirement,
            "min_condition_score": min_condition_score,
            "interval_minutes": interval_minutes,
            "fetch_detail": bool(fetch_detail),
            "enabled": bool(enabled),
        },
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
def listings_page(
    request: Request,
    partial: int = 0,
    task_id: Optional[int] = None,
    sort: str = "satisfaction",
    show: str = "active",
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, WatchTask

        query = session.query(Listing)
        tasks = session.query(WatchTask).order_by(WatchTask.id).all()
        if task_id:
            query = query.filter(Listing.task_id == task_id)
        if show == "active":
            query = query.filter(Listing.blocked.is_(False))
        elif show == "blocked":
            query = query.filter(Listing.blocked.is_(True))
        if sort == "price_asc":
            query = query.order_by(Listing.price.asc())
        elif sort == "price_desc":
            query = query.order_by(Listing.price.desc())
        elif sort == "newest":
            query = query.order_by(Listing.first_seen_at.desc())
        else:
            query = query.order_by(Listing.satisfaction.desc(), Listing.first_seen_at.desc())
        listings = query.limit(100).all()
    partial_url = f"/listings?partial=1&task_id={task_id or ''}&sort={sort}&show={show}"
    if partial:
        return templates.TemplateResponse(request, "listings_grid.html", {"listings": listings})
    running_ids = request.app.state.guard.running_ids()
    return templates.TemplateResponse(
        request,
        "listings.html",
        {
            "listings": listings,
            "tasks": tasks,
            "running_ids": running_ids,
            "task_id": task_id,
            "sort": sort,
            "show": show,
            "partial_url": partial_url,
            "active": "listings",
        },
    )


@router.post("/listings/{listing_id}/block")
def block_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=False)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/unblock")
def unblock_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=False)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/block-seller")
def block_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=True)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/unblock-seller")
def unblock_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=True)
    return RedirectResponse("/listings", status_code=303)


def _set_blocked(request: Request, listing_id: int, blocked: bool, seller: bool) -> None:
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Seller

        listing = session.get(Listing, listing_id)
        if listing is None:
            return
        if seller and listing.seller_uid:
            s = (
                session.query(Seller)
                .filter_by(platform=listing.platform, seller_uid=listing.seller_uid)
                .first()
            )
            if s is None:
                s = Seller(platform=listing.platform, seller_uid=listing.seller_uid)
                session.add(s)
            s.blocked = blocked
            session.query(Listing).filter(
                Listing.platform == listing.platform,
                Listing.seller_uid == listing.seller_uid,
            ).update({Listing.blocked: blocked})
        else:
            listing.blocked = blocked
        session.commit()


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
    wecom_webhook: str = Form(""),
    serverchan_enabled: Optional[int] = Form(None),
    wecom_robot_enabled: Optional[int] = Form(None),
    vision_enabled: Optional[int] = Form(None),
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
        "wecom_webhook": wecom_webhook,
        "serverchan_enabled": "1" if serverchan_enabled else "0",
        "wecom_robot_enabled": "1" if wecom_robot_enabled else "0",
        "vision_enabled": "1" if vision_enabled else "0",
    }
    for key in ("llm_api_key", "serverchan_sendkey", "vision_api_key", "wecom_webhook"):
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


@router.put("/api/tasks/{task_id}")
def api_update_task(request: Request, task_id: int, data: TaskCreate):
    from fastapi import HTTPException

    task_service, _ = _services(request)
    task = task_service.update_task(task_id, data.model_dump())
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    request.app.state.sync_scheduler()
    return _task_dict(task)


@router.get("/api/listings")
def api_list_listings(
    request: Request,
    task_id: Optional[int] = None,
    sort: str = "satisfaction",
    show: str = "active",
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        query = session.query(Listing)
        if task_id:
            query = query.filter(Listing.task_id == task_id)
        if show == "active":
            query = query.filter(Listing.blocked.is_(False))
        elif show == "blocked":
            query = query.filter(Listing.blocked.is_(True))
        if sort == "price_asc":
            query = query.order_by(Listing.price.asc())
        elif sort == "price_desc":
            query = query.order_by(Listing.price.desc())
        elif sort == "newest":
            query = query.order_by(Listing.first_seen_at.desc())
        else:
            query = query.order_by(Listing.satisfaction.desc(), Listing.first_seen_at.desc())
        rows = query.limit(100).all()
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
