import threading
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class TaskCreate(BaseModel):
    name: str = ""
    keyword: str
    max_price: float = 0
    min_price: float = 0
    exclude_words: str = ""
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
        "min_price": task.min_price,
        "exclude_words": task.exclude_words,
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
    queued_ids = _queued_ids(request)
    just_ran = request.query_params.get("run")
    running, status, items = _progress_context(request, just_ran)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "tasks": tasks,
            "running_ids": running_ids,
            "queued_ids": queued_ids,
            "just_ran": just_ran,
            "running": running,
            "status": status,
            "items": items,
            "active": "tasks",
        },
    )


def _queued_ids(request) -> set[int]:
    queue = getattr(request.app.state, "task_queue", None)
    if queue is None:
        return set()
    return set(queue.queued_ids())


def _progress_context(request: Request, just_ran=None):
    guard = request.app.state.guard
    running_ids = guard.running_ids()
    queued_ids = _queued_ids(request)
    active = running_ids | queued_ids
    status = None
    if active:
        running = min(active)
        status = "running" if running in running_ids else "queued"
    elif just_ran:
        running = int(just_ran) if str(just_ran).isdigit() else None
        status = "queued"
    else:
        running = None
    items = []
    if running and status == "running":
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
    return running, status, items


@router.get("/tasks/progress")
def tasks_progress(request: Request):
    running, status, items = _progress_context(request)
    return templates.TemplateResponse(
        request, "progress.html", {"running": running, "status": status, "items": items}
    )


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
    min_price: float = Form(0),
    exclude_words: str = Form(""),
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
            "min_price": min_price,
            "exclude_words": exclude_words.strip(),
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
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail_page(request: Request, task_id: int):
    task_service, _ = _services(request)
    task = task_service.get_task(task_id)
    if task is None:
        return RedirectResponse("/tasks", status_code=303)
    running_ids = request.app.state.guard.running_ids()
    queued_ids = _queued_ids(request)
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification

        stats = {
            "listings": session.query(Listing).filter(Listing.task_id == task_id).count(),
            "notifications": (
                session.query(Notification).filter(Notification.task_id == task_id).count()
            ),
        }
        recent = (
            session.query(Listing)
            .filter(Listing.task_id == task_id)
            .order_by(Listing.first_seen_at.desc())
            .limit(10)
            .all()
        )
    return templates.TemplateResponse(
        request,
        "tasks_detail.html",
        {
            "task": task,
            "stats": stats,
            "recent": recent,
            "running_ids": running_ids,
            "queued_ids": queued_ids,
            "active": "tasks",
        },
    )


@router.post("/tasks/{task_id}/edit")
def edit_task_form(
    request: Request,
    task_id: int,
    keyword: str = Form(...),
    name: str = Form(""),
    max_price: float = Form(0),
    min_price: float = Form(0),
    exclude_words: str = Form(""),
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
            "min_price": min_price,
            "exclude_words": exclude_words.strip(),
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
    offset: int = Query(0),
    task_id: str = Query(""),
    sort: str = Query("satisfaction"),
    show: str = Query("active"),
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, WatchTask

        query = session.query(Listing)
        tasks = session.query(WatchTask).order_by(WatchTask.id).all()
        task_id_int = int(task_id) if task_id else None
        if task_id_int:
            query = query.filter(Listing.task_id == task_id_int)
        if show == "active":
            query = query.filter(Listing.status == "active", Listing.blocked.is_(False))
        elif show == "gone":
            query = query.filter(Listing.status == "gone")
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
        query = query.offset(offset)
        listings = query.limit(100).all()
        notify_counts = _notify_counts(session, listings)
        more = len(listings) >= 100
    partial_url = f"/listings?partial=1&task_id={task_id or ''}&sort={sort}&show={show}"
    if partial:
        return templates.TemplateResponse(
            request,
            "listings_grid.html",
            {"listings": listings, "notify_counts": notify_counts},
        )
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
            "notify_counts": notify_counts,
            "more": more,
            "load_more_url": (
                f"/listings/more?offset={offset + len(listings)}"
                f"&task_id={task_id or ''}&sort={sort}&show={show}"
            ),
            "active": "listings",
        },
    )


def _notify_counts(session, listings) -> dict[int, int]:
    from goodprice.models import Notification

    if not listings:
        return {}
    rows = (
        session.query(Notification.listing_id, func.count(Notification.id))
        .filter(
            Notification.listing_id.in_([item.id for item in listings]),
            Notification.status == "sent",
        )
        .group_by(Notification.listing_id)
        .all()
    )
    return {listing_id: count for listing_id, count in rows}


@router.get("/listings/more", response_class=HTMLResponse)
def listings_more(
    request: Request,
    offset: int = Query(0),
    task_id: str = Query(""),
    sort: str = Query("satisfaction"),
    show: str = Query("active"),
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        query = session.query(Listing)
        task_id_int = int(task_id) if task_id else None
        if task_id_int:
            query = query.filter(Listing.task_id == task_id_int)
        if show == "active":
            query = query.filter(Listing.status == "active", Listing.blocked.is_(False))
        elif show == "gone":
            query = query.filter(Listing.status == "gone")
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
        listings = query.offset(offset).limit(100).all()
        notify_counts = _notify_counts(session, listings)
    return templates.TemplateResponse(
        request, "listings_cards.html", {"listings": listings, "notify_counts": notify_counts}
    )


@router.get("/listings/{listing_id}", response_class=HTMLResponse)
def listing_detail_page(request: Request, listing_id: int):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification, PriceSnapshot

        listing = session.get(Listing, listing_id)
        if listing is None:
            return RedirectResponse("/listings", status_code=303)
        snapshots = (
            session.query(PriceSnapshot)
            .filter(PriceSnapshot.listing_id == listing_id)
            .order_by(PriceSnapshot.seen_at, PriceSnapshot.id)
            .all()
        )
        notifications = (
            session.query(Notification)
            .filter(Notification.listing_id == listing_id)
            .order_by(Notification.id.desc())
            .all()
        )
        notify_count = sum(1 for n in notifications if n.status == "sent")
        first_price = snapshots[0].price if snapshots else listing.price
        drop_pct = (first_price - listing.price) / first_price if first_price else 0.0
    return templates.TemplateResponse(
        request,
        "listings_detail.html",
        {
            "listing": listing,
            "snapshots": snapshots,
            "notifications": notifications,
            "notify_count": notify_count,
            "first_price": first_price,
            "drop_pct": drop_pct,
            "active": "listings",
        },
    )


@router.post("/listings/{listing_id}/reanalyze")
def reanalyze_listing(request: Request, listing_id: int):
    threading.Thread(
        target=request.app.state.run_reanalyze, args=(listing_id,), daemon=True
    ).start()
    return RedirectResponse(f"/listings/{listing_id}?toast=已提交重新分析", status_code=303)


@router.post("/listings/delete-batch")
def delete_listings_batch(request: Request, ids: list[int] = Form(...)):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        for row in session.query(Listing).filter(Listing.id.in_(ids)).all():
            session.delete(row)
        session.commit()
    return RedirectResponse(_listings_back(request, "已批量删除"), status_code=303)


@router.post("/listings/{listing_id}/delete")
def delete_listing(request: Request, listing_id: int):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        row = session.get(Listing, listing_id)
        if row:
            session.delete(row)
            session.commit()
    return RedirectResponse(_listings_back(request, "已删除"), status_code=303)


@router.post("/listings/{listing_id}/block")
def block_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=False)
    return RedirectResponse(_listings_back(request, "已拉黑商品"), status_code=303)


@router.post("/listings/{listing_id}/unblock")
def unblock_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=False)
    return RedirectResponse(_listings_back(request, "已恢复"), status_code=303)


@router.post("/listings/{listing_id}/block-seller")
def block_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=True)
    return RedirectResponse(_listings_back(request, "已拉黑卖家"), status_code=303)


@router.post("/listings/{listing_id}/unblock-seller")
def unblock_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=True)
    return RedirectResponse(_listings_back(request, "已恢复卖家"), status_code=303)


def _listings_back(request: Request, toast: str) -> str:
    params = {}
    ref = request.headers.get("referer", "")
    if "?" in ref:
        params = {
            k: v for k, v in parse_qsl(ref.split("?", 1)[1]) if k in ("task_id", "sort", "show")
        }
    params["toast"] = toast
    return "/listings?" + urlencode(params)


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
    login_session = getattr(request.app.state, "login_session", None)
    login_status, login_message = (
        login_session.status() if login_session else ("idle", "")
    )
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": settings,
            "login_status": login_status,
            "login_message": login_message,
            "active": "settings",
        },
    )


@router.post("/settings/login")
def settings_login(request: Request):
    login_session = getattr(request.app.state, "login_session", None)
    if login_session:
        login_session.start()
    return RedirectResponse("/settings?toast=已打开浏览器窗口，请完成登录", status_code=303)


@router.get("/settings/login-status")
def settings_login_status(request: Request):
    login_session = getattr(request.app.state, "login_session", None)
    login_status, login_message = (
        login_session.status() if login_session else ("idle", "")
    )
    return templates.TemplateResponse(
        request,
        "login_status.html",
        {"login_status": login_status, "login_message": login_message},
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
    runtime = settings_service.set_many(values)
    from goodprice.services.satisfaction import backfill_satisfaction

    backfill_satisfaction(request.app.state.session_factory, vision_enabled=runtime.vision_enabled)
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
    offset: int = Query(0),
    task_id: str = Query(""),
    sort: str = Query("satisfaction"),
    show: str = Query("active"),
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        query = session.query(Listing)
        task_id_int = int(task_id) if task_id else None
        if task_id_int:
            query = query.filter(Listing.task_id == task_id_int)
        if show == "active":
            query = query.filter(Listing.status == "active", Listing.blocked.is_(False))
        elif show == "gone":
            query = query.filter(Listing.status == "gone")
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
        rows = query.offset(offset).limit(100).all()
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
