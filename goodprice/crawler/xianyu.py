from contextlib import contextmanager
from typing import Callable, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import CrawlerAuthError, ListingData, ListingDetail, SellerData
from goodprice.crawler.constants import (
    GOOFISH_ORIGIN,
    NAV_TIMEOUT_MS,
    RENDER_SETTLE_MS,
    SELECTOR_TIMEOUT_MS,
    SELLER_DETAIL_SETTLE_MS,
    SELLER_SETTLE_MS,
    USER_AGENT,
)
from goodprice.crawler.parser import parse_detail_html, parse_search_html, parse_seller_html

SEARCH_URL = f"{GOOFISH_ORIGIN}/search?q={{keyword}}&spm=a21ybx.search.searchInput.0"
SEARCH_ATTEMPTS = 3
SEARCH_ATTEMPT_GAP_MS = 5000


class XianyuAdapter:
    platform = "xianyu"

    def __init__(
        self,
        cookie: str = "",
        proxy: str = "",
        headless: bool = True,
        playwright_factory: Optional[Callable] = None,
    ):
        self.cookie = cookie
        self.proxy = proxy
        self.headless = headless
        self._playwright_factory = playwright_factory or sync_playwright

    def _cookies(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in (self.cookie or "").split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @contextmanager
    def _open_page(self):
        """统一的浏览器/上下文/Cookie 装配，三处抓取复用。"""
        with self._playwright_factory() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                proxy={"server": self.proxy} if self.proxy else None,
            )
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                context.add_cookies(
                    [
                        {"name": k, "value": v, "domain": ".goofish.com", "path": "/"}
                        for k, v in self._cookies().items()
                    ]
                )
                yield context.new_page()
            finally:
                browser.close()

    def search(
        self, keyword: str, max_items: int = 30, attempts: int = SEARCH_ATTEMPTS
    ) -> list[ListingData]:
        """搜索并合并多次尝试的结果（闲鱼后端间歇性返回不同结果集，重试可提高命中率）。"""
        seen: dict[str, ListingData] = {}
        with self._open_page() as page:
            url = SEARCH_URL.format(keyword=quote(keyword))
            for attempt in range(max(1, attempts)):
                if attempt:
                    page.wait_for_timeout(SEARCH_ATTEMPT_GAP_MS)
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                if "login" in (page.url or ""):
                    raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
                card_selector = sel.RESULT_CARD
                try:
                    page.wait_for_selector(card_selector, timeout=SELECTOR_TIMEOUT_MS)
                except Exception as exc:
                    if page.locator(sel.RESULT_CARD_FALLBACK).count() > 0:
                        card_selector = sel.RESULT_CARD_FALLBACK
                    else:
                        body_text = ""
                        try:
                            body_text = page.inner_text("body")[:300]
                        except Exception:
                            pass
                        if "加载中" in body_text:
                            raise CrawlerAuthError(
                                "搜索结果一直显示加载中，Cookie 可能已失效或未登录，请重新获取"
                            ) from exc
                        raise RuntimeError(
                            f"未在页面中找到商品卡片，页面可能改版或触发风控。页面摘要: {body_text[:150]}"
                        ) from exc
                # 等待真实结果渲染（刚出现卡片时可能只是占位/推荐位）
                page.wait_for_timeout(RENDER_SETTLE_MS)
                for item in parse_search_html(page.content(), card_selector=card_selector):
                    seen.setdefault(item.external_id, item)
                if len(seen) >= max_items:
                    break
            return list(seen.values())[:max_items]

    def fetch_detail(self, url: str) -> ListingDetail:
        with self._open_page() as page:
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if "login" in (page.url or ""):
                raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
            try:
                page.wait_for_selector(sel.DETAIL_DESC, timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                pass  # 描述缺失时仍解析
            return parse_detail_html(page.content())

    def fetch_seller(self, user_id: str) -> SellerData:
        with self._open_page() as page:
            url = f"{GOOFISH_ORIGIN}/personal?userId={quote(user_id)}"
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if "login" in (page.url or ""):
                raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
            page.wait_for_timeout(SELLER_SETTLE_MS)
            page.evaluate(
                "() => { const re = /信用及评价/; "
                "const el = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && re.test(e.textContent)); "
                "if (el) { el.click(); return true; } return false; }"
            )
            page.wait_for_timeout(SELLER_DETAIL_SETTLE_MS)
            return parse_seller_html(page.content(), user_id)
