from pathlib import Path

import pytest

from goodprice.crawler.base import CrawlerAuthError
from goodprice.crawler.xianyu import XianyuAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"


class FakePage:
    def __init__(self, html, url="https://www.goofish.com/search?q=test"):
        self._html = html
        self.url = url

    def goto(self, *args, **kwargs):
        pass

    def wait_for_selector(self, *args, **kwargs):
        pass

    def content(self):
        return self._html


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
