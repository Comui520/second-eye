from typing import Callable, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import CrawlerAuthError, ListingData, ListingDetail, SellerData
from goodprice.crawler.parser import parse_detail_html, parse_search_html, parse_seller_html

SEARCH_URL = "https://www.goofish.com/search?q={keyword}&spm=a21ybx.search.searchInput.0"
SEARCH_ATTEMPTS = 3
SEARCH_ATTEMPT_GAP_MS = 5000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


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

    def search(
        self, keyword: str, max_items: int = 30, attempts: int = SEARCH_ATTEMPTS
    ) -> list[ListingData]:
        """搜索并合并多次尝试的结果（闲鱼后端间歇性返回不同结果集，重试可提高命中率）。"""
        seen: dict[str, ListingData] = {}
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
                page = context.new_page()
                url = SEARCH_URL.format(keyword=quote(keyword))
                for attempt in range(max(1, attempts)):
                    if attempt:
                        page.wait_for_timeout(SEARCH_ATTEMPT_GAP_MS)
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if "login" in (page.url or ""):
                        raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
                    card_selector = sel.RESULT_CARD
                    try:
                        page.wait_for_selector(card_selector, timeout=30000)
                    except Exception:
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
                                )
                            raise RuntimeError(
                                f"未在页面中找到商品卡片，页面可能改版或触发风控。页面摘要: {body_text[:150]}"
                            )
                    # 等待真实结果渲染（刚出现卡片时可能只是占位/推荐位）
                    page.wait_for_timeout(3000)
                    for item in parse_search_html(page.content(), card_selector=card_selector):
                        seen.setdefault(item.external_id, item)
                    if len(seen) >= max_items:
                        break
                return list(seen.values())[:max_items]
            finally:
                browser.close()

    def fetch_detail(self, url: str) -> ListingDetail:
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
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if "login" in (page.url or ""):
                    raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
                try:
                    page.wait_for_selector(sel.DETAIL_DESC, timeout=30000)
                except Exception:
                    pass  # 描述缺失时仍解析
                return parse_detail_html(page.content())
            finally:
                browser.close()

    def fetch_seller(self, user_id: str) -> SellerData:
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
                page = context.new_page()
                url = f"https://www.goofish.com/personal?userId={quote(user_id)}"
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if "login" in (page.url or ""):
                    raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
                page.wait_for_timeout(5000)
                page.evaluate(
                    "() => { const re = /信用及评价/; "
                    "const el = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && re.test(e.textContent)); "
                    "if (el) { el.click(); return true; } return false; }"
                )
                page.wait_for_timeout(4000)
                return parse_seller_html(page.content(), user_id)
            finally:
                browser.close()
