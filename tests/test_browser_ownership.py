import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from goodprice.execution.browser import current_browser
from goodprice.execution.limits import ResourceLimits
from goodprice.execution.types import RunToken, Stage, WorkItemDTO
from goodprice.execution.workers import WorkerPools


def test_browser_is_created_reused_and_closed_by_owning_worker():
    events = []
    @contextmanager
    def factory():
        owner = threading.get_ident()
        events.append(("created", owner))
        yield owner
        events.append(("closed", threading.get_ident()))
    def handler(item):
        owner = current_browser("account", factory)
        assert owner == threading.get_ident()
        return {"owner": owner}
    workers = WorkerPools(ResourceLimits(), {Stage.SEARCH: handler})
    def item(i):
        return WorkItemDTO(i, RunToken(i, i, 1, str(i)), Stage.SEARCH, str(i), {}, 1,
                           datetime.now(timezone.utc) + timedelta(minutes=1))
    try:
        first = workers.dispatch_item(item(1)).result(2)
        second = workers.dispatch_item(item(2)).result(2)
        assert first.output["owner"] == second.output["owner"]
        assert len(events) == 1
    finally:
        workers.stop()
    assert events == [("created", first.output["owner"]), ("closed", first.output["owner"])]
    with pytest.raises(RuntimeError):
        current_browser("account", factory)


def test_xianyu_browser_session_reuses_browser_but_isolates_contexts():
    from test_crawler_xianyu import DETAIL_FIXTURE, FIXTURE, FakePage

    from goodprice.crawler.xianyu import XianyuAdapter
    events = []
    page = FakePage("", htmls=[FIXTURE.read_text(encoding="utf-8"), DETAIL_FIXTURE.read_text(encoding="utf-8")])
    class Context:
        def add_cookies(self, cookies): pass
        def new_page(self): return page
        def close(self): events.append("context closed")
    class Browser:
        def new_context(self, **kwargs):
            events.append("context created")
            return Context()
        def close(self): events.append("browser closed")
    class Driver:
        def __enter__(self): return self
        def __exit__(self, *args): events.append("driver closed")
        @property
        def chromium(self): return self
        def launch(self, **kwargs):
            events.append("browser created")
            return Browser()
    adapter = XianyuAdapter(playwright_factory=Driver)
    with adapter.session():
        adapter.search("test", attempts=1)
        adapter.fetch_detail("https://www.goofish.com/item?id=1001")
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(RuntimeError):
                pool.submit(adapter.fetch_detail, "https://www.goofish.com/item?id=1001").result()
    assert events.count("browser created") == events.count("browser closed") == 1
    assert events.count("context created") == events.count("context closed") == 2
    assert events[-1] == "driver closed"


def test_parallel_adapter_startup_is_rejected_before_second_browser_launch():
    from goodprice.crawler.xianyu import XianyuAdapter
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Driver:
        def __enter__(self):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            return self
        def __exit__(self, *args): pass
        @property
        def chromium(self): return self
        def launch(self, **kwargs): return self
        def close(self): pass
    adapter = XianyuAdapter(playwright_factory=Driver)
    def first():
        with adapter.session():
            pass
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(first)
        assert entered.wait(1)
        try:
            with pytest.raises(RuntimeError):
                with adapter.session():
                    pass
        finally:
            release.set()
        future.result(2)
    assert len(calls) == 1
