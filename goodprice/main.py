import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from goodprice.config import Settings, get_settings
from goodprice.db import init_db, migrate_schema
from goodprice.scheduler import build_scheduler
from goodprice.services.crawl_service import CrawlService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService
from goodprice.web.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _make_crawl_service(session_factory, settings_service):
    runtime = settings_service.get()
    from goodprice.analysis.llm import LLMClient
    from goodprice.crawler.xianyu import XianyuAdapter
    from goodprice.notify.log import LogNotifier
    from goodprice.notify.serverchan import ServerChanNotifier

    adapter = XianyuAdapter(cookie=runtime.xianyu_cookie, proxy=runtime.proxy)
    llm = LLMClient(
        base_url=runtime.llm_base_url,
        api_key=runtime.llm_api_key,
        model=runtime.llm_model,
    )
    notifiers = [("log", LogNotifier())]
    serverchan = ServerChanNotifier(sendkey=runtime.serverchan_sendkey)
    if serverchan.enabled:
        notifiers.append(("serverchan", serverchan))
    return CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=llm,
        notifiers=notifiers,
        settings_service=settings_service,
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
    run_job = lambda task_id: _make_crawl_service(session_factory, settings_service).run_task(task_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if with_scheduler:
            app.state.scheduler = build_scheduler(session_factory, run_job, task_service)
            app.state.scheduler.start()
        yield
        if with_scheduler:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.settings_service = settings_service
    app.state.task_service = task_service
    app.state.run_job = run_job
    app.include_router(router)
    return app


app = build_app()


def main() -> None:
    uvicorn.run("goodprice.main:app", host="127.0.0.1", port=8000, reload=False)
