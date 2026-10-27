import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from goodprice.config import Settings, get_settings
from goodprice.db import init_db, migrate_schema
from goodprice.media.budget import BudgetExceeded
from goodprice.scheduler import _sync_tasks, build_scheduler
from goodprice.services.crawl_service import CrawlService, TaskRunGuard
from goodprice.services.seller_service import SellerService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService
from goodprice.web.media_routes import router as media_router
from goodprice.web.offer_routes import router as offer_router
from goodprice.web.resource_routes import router as resource_router
from goodprice.web.routes import router
from goodprice.web.settings_sections import router as settings_sections_router

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"


def _setup_logging(settings: Optional[Settings] = None) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        log_dir = (settings.data_root / "logs") if settings else LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


logger = logging.getLogger(__name__)


def _make_crawl_service(session_factory, settings_service, guard):
    runtime = settings_service.get()
    from goodprice.analysis.llm import LLMClient
    from goodprice.crawler.xianyu import XianyuAdapter
    from goodprice.media.budget import MediaBudget
    from goodprice.media.processing import MediaProcessor
    from goodprice.notify.log import LogNotifier
    from goodprice.notify.registry import build_enabled_notifiers
    from goodprice.services.media_service import MediaService

    adapter = XianyuAdapter(cookie=runtime.xianyu_cookie, proxy=runtime.proxy)
    seller_service = SellerService(session_factory, adapter=adapter)
    llm = LLMClient(
        base_url=runtime.llm_base_url,
        api_key=runtime.llm_api_key,
        model=runtime.llm_model,
        api_format=runtime.llm_api_format,
    )
    vision = (
        LLMClient(
            base_url=runtime.vision_base_url,
            api_key=runtime.vision_api_key,
            model=runtime.vision_model,
            api_format=runtime.vision_api_format,
            allow_image_fallback=False,
        )
        if runtime.vision_enabled
        else LLMClient(base_url="", api_key="", model="")
    )
    notifiers = [("log", LogNotifier()), *build_enabled_notifiers(runtime)]
    return CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=llm,
        vision=vision,
        notifiers=notifiers,
        settings_service=settings_service,
        guard=guard,
        seller_service=seller_service,
        media_service=MediaService(session_factory, MediaProcessor(MediaBudget(
            runtime.data_root / "media-cache", runtime.media_cache_mib * 1024**2,
            runtime.media_retention_days * 86400))),
    )


def _make_login_session(settings_service, settings: Settings):
    from goodprice.crawler.login import LoginSession

    return LoginSession(
        settings_service,
        runtime_mode=settings.runtime_mode,
        novnc_enabled=settings.enable_novnc,
    )


def build_app(
    settings: Optional[Settings] = None,
    session_factory=None,
    with_scheduler: bool = True,
    execution_factory=None,
) -> FastAPI:
    settings = settings or get_settings()
    _setup_logging(settings)
    if session_factory is None:
        init_db(settings.database_url)
        from goodprice.db import make_session_factory

        session_factory = make_session_factory(settings.database_url)
    else:
        from goodprice.db import Base

        Base.metadata.create_all(session_factory().get_bind())
    migrate_schema(session_factory)

    settings_service = SettingsService(session_factory, base=settings)
    login_session = _make_login_session(settings_service, settings)
    task_service = TaskService(session_factory)
    guard = TaskRunGuard()

    from goodprice.execution.pipeline import build_pipeline
    task_queue = (execution_factory or build_pipeline)(session_factory, settings_service)
    guard.provider = task_queue.running_ids
    scheduler = (
        build_scheduler(session_factory, task_queue.submit, task_service,
                        jitter_seconds=lambda: settings_service.get().default_crawl_jitter_minutes * 60)
        if with_scheduler
        else None
    )

    def sync_scheduler() -> None:
        if scheduler is not None:
            _sync_tasks(session_factory, task_queue.submit, task_service, scheduler,
                        jitter_seconds=lambda: settings_service.get().default_crawl_jitter_minutes * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task_queue.start()
        try:
            if scheduler is not None:
                app.state.scheduler = scheduler
                app.state.scheduler.start()
            yield
        finally:
            try:
                if scheduler is not None and scheduler.running:
                    scheduler.shutdown(wait=False)
            finally:
                task_queue.stop()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    @app.exception_handler(BudgetExceeded)
    async def media_busy_handler(request, exc):
        return JSONResponse({"detail": "媒体处理繁忙或磁盘预算不足，请稍后重试"},
                            status_code=503, headers={"Retry-After": "5"})
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
    app.state.session_factory = session_factory
    app.state.settings_service = settings_service
    app.state.task_service = task_service
    app.state.run_job = task_queue.submit
    app.state.run_reanalyze = task_queue.submit_reanalysis
    app.state.coordinator = task_queue
    app.state.resource_guard = getattr(getattr(task_queue, "pipeline", None), "guard", None)
    app.state.guard = guard
    app.state.sync_scheduler = sync_scheduler
    app.state.task_queue = task_queue
    app.state.login_session = login_session
    app.include_router(router)
    app.include_router(offer_router)
    app.include_router(media_router)
    app.include_router(settings_sections_router)
    app.include_router(resource_router)
    return app


def create_app() -> FastAPI:
    """ASGI factory: importing the module never opens a database or starts work."""
    return build_app()


def main() -> None:
    uvicorn.run("goodprice.main:create_app", factory=True, host="0.0.0.0", port=8000, reload=False)
