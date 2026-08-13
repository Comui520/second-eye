import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from goodprice.config import Settings, get_settings
from goodprice.db import init_db, migrate_schema
from goodprice.scheduler import build_scheduler
from goodprice.scheduler import _sync_tasks, build_scheduler
from goodprice.services.crawl_service import CrawlService, TaskRunGuard
from goodprice.services.seller_service import SellerService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService
from goodprice.web.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _make_crawl_service(session_factory, settings_service, guard):
    runtime = settings_service.get()
    from goodprice.analysis.llm import LLMClient
    from goodprice.crawler.xianyu import XianyuAdapter
    from goodprice.notify.log import LogNotifier
    from goodprice.notify.serverchan import ServerChanNotifier
    from goodprice.notify.wecom_robot import WeComRobotNotifier

    adapter = XianyuAdapter(cookie=runtime.xianyu_cookie, proxy=runtime.proxy)
    seller_service = SellerService(session_factory, adapter=adapter)
    llm = LLMClient(
        base_url=runtime.llm_base_url,
        api_key=runtime.llm_api_key,
        model=runtime.llm_model,
    )
    vision = (
        LLMClient(
            base_url=runtime.vision_base_url,
            api_key=runtime.vision_api_key,
            model=runtime.vision_model,
            allow_image_fallback=False,
        )
        if runtime.vision_enabled
        else LLMClient(base_url="", api_key="", model="")
    )
    notifiers = [("log", LogNotifier())]
    if runtime.serverchan_enabled:
        serverchan = ServerChanNotifier(sendkey=runtime.serverchan_sendkey)
        if serverchan.enabled:
            notifiers.append(("serverchan", serverchan))
    if runtime.wecom_robot_enabled:
        robot = WeComRobotNotifier(webhook=runtime.wecom_webhook)
        if robot.enabled:
            notifiers.append(("wecom_robot", robot))
    return CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=llm,
        vision=vision,
        notifiers=notifiers,
        settings_service=settings_service,
        guard=guard,
        seller_service=seller_service,
    )


def build_app(
    settings: Optional[Settings] = None,
    session_factory=None,
    with_scheduler: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        init_db(settings.database_url)
        from goodprice.db import make_session_factory

        session_factory = make_session_factory(settings.database_url)
    else:
        from goodprice.db import Base

        Base.metadata.create_all(session_factory().get_bind())
    migrate_schema(session_factory)

    settings_service = SettingsService(session_factory, base=settings)
    task_service = TaskService(session_factory)
    from goodprice.services.satisfaction import backfill_satisfaction

    backfill_satisfaction(session_factory, vision_enabled=settings_service.get().vision_enabled)
    guard = TaskRunGuard()

    def run_job(task_id: int) -> None:
        logger.info("任务 %s 开始执行", task_id)
        try:
            stats = _make_crawl_service(session_factory, settings_service, guard).run_task(task_id)
            logger.info("任务 %s 执行完成: %s", task_id, stats)
        except Exception:
            logger.exception("任务 %s 执行失败", task_id)

    scheduler = build_scheduler(session_factory, run_job, task_service) if with_scheduler else None

    def sync_scheduler() -> None:
        if scheduler is not None:
            _sync_tasks(session_factory, run_job, task_service, scheduler)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if scheduler is not None:
            app.state.scheduler = scheduler
            app.state.scheduler.start()
        yield
        if scheduler is not None:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.settings_service = settings_service
    app.state.task_service = task_service
    app.state.run_job = run_job
    app.state.guard = guard
    app.state.sync_scheduler = sync_scheduler
    app.include_router(router)
    return app


app = build_app()


def main() -> None:
    uvicorn.run("goodprice.main:app", host="127.0.0.1", port=8000, reload=False)
