from pathlib import Path

import pytest

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import CrawlerAuthError
from goodprice.crawler.xianyu import XianyuAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail.html"


class FakePage:
    def __init__(
        self,
        html,
        url="https://www.goofish.com/search?q=test",
        wait_ok=True,
        body_text="",
        fallback_count=0,
    ):
        self._html = html
        self.url = url
        self._wait_ok = wait_ok
        self._body_text = body_text
        self._fallback_count = fallback_count

    def goto(self, *args, **kwargs):
        pass

    def wait_for_selector(self, *args, **kwargs):
        if not self._wait_ok:
            raise TimeoutError("Timeout 30000ms exceeded")

    def content(self):
        return self._html

    def inner_text(self, selector):
        return self._body_text

    def locator(self, selector):
        count = self._fallback_count if selector == sel.RESULT_CARD_FALLBACK else 0
        return FakeLocator(count)


class FakeLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class FakeContext:
    def __init__(self, page):
        self._page = page
        self.cookies = None

    def add_cookies(self, cookies):
        self.cookies = cookies

    def new_page(self):
        return self._page


class FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.context = None

    def new_context(self, **kwargs):
        self.context = FakeContext(self._page)
        return self.context

    def close(self):
        pass


class FakeChromium:
    def __init__(self, page, playwright=None):
        self._page = page
        self._playwright = playwright

    def launch(self, **kwargs):
        browser = FakeBrowser(self._page)
        if self._playwright:
            self._playwright.browser = browser
        return browser


class FakePlaywright:
    def __init__(self, page):
        self._page = page
        self.browser = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def chromium(self):
        return FakeChromium(self._page, self)


def _adapter(page, cookie="a=1; b=2"):
    playwright = FakePlaywright(page)
    adapter = XianyuAdapter(cookie=cookie, playwright_factory=lambda: playwright)
    return adapter, playwright


def test_search_parses_and_sets_cookies():
    html = FIXTURE.read_text(encoding="utf-8")
    adapter, playwright = _adapter(FakePage(html))
    items = adapter.search("iPhone")
    assert len(items) == 2
    assert items[0].external_id == "1001"
    cookie_names = {c["name"] for c in playwright.browser.context.cookies}
    assert cookie_names == {"a", "b"}
    assert all(c["domain"] == ".goofish.com" for c in playwright.browser.context.cookies)


def test_search_raises_on_login_redirect():
    html = "<html><body></body></html>"
    page = FakePage(html, url="https://www.goofish.com/login?redirect=search")
    adapter, _ = _adapter(page)
    with pytest.raises(CrawlerAuthError):
        adapter.search("iPhone")


def test_cookie_parsing():
    adapter = XianyuAdapter(cookie="a=1; b=2")
    assert adapter._cookies() == {"a": "1", "b": "2"}
    assert XianyuAdapter(cookie="")._cookies() == {}


def test_search_raises_auth_error_when_results_stuck_loading():
    html = "<html><body>加载中...</body></html>"
    page = FakePage(html, wait_ok=False, body_text="搜索 | 加载中... | 综合")
    adapter, _ = _adapter(page)
    with pytest.raises(CrawlerAuthError, match="加载中"):
        adapter.search("iPhone")


def test_search_uses_fallback_selector_when_primary_times_out():
    html = FIXTURE.read_text(encoding="utf-8")
    page = FakePage(html, wait_ok=False, fallback_count=4)
    adapter, _ = _adapter(page)
    items = adapter.search("iPhone")
    assert len(items) == 2
    assert items[0].external_id == "1001"


def test_fetch_detail_parses_page():
    html = DETAIL_FIXTURE.read_text(encoding="utf-8")
    adapter, playwright = _adapter(FakePage(html))
    detail = adapter.fetch_detail("https://www.goofish.com/item?id=1001")
    assert "屏幕完好" in detail.description
    assert len(detail.image_urls) == 2
    assert playwright.browser.context.cookies
