# 闲鱼盯价助手 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个单进程一体化的本地 Web 工具，监控闲鱼关键词新上架商品，价格初筛 + LLM 品相分析后站内记录并通过 Server酱推送微信提醒。

**Architecture:** FastAPI 提供服务端渲染页面与 JSON API，APScheduler 在进程内调度抓取任务，SQLAlchemy + SQLite 持久化。爬虫为可插拔平台适配器（首版闲鱼 Playwright），LLM 走 OpenAI 兼容层，通知为可插拔通道（日志 + Server酱）。

**Tech Stack:** Python 3.11（conda 环境 `good-price`）、FastAPI、SQLAlchemy 2、APScheduler 3、Playwright、BeautifulSoup4、httpx、Jinja2 + HTMX + Tailwind(CDN)、pytest。

---

## Task 0: 仓库骨架与 conda 环境

**Files:**
- Create: `.gitignore`, `LICENSE`, `.env.example`, `environment.yml`, `pyproject.toml`, `goodprice/__init__.py`, `docs/superpowers/specs/2026-08-13-xianyu-price-watch-design.md`
- Test: 无（配置/文档类，TDD 豁免）

- [ ] **Step 1: 初始化 git 与 conda 环境**

```bash
git init -b main
conda create -n good-price python=3.11 -y
```

- [ ] **Step 2: 创建上述文件（内容见设计文档与本计划头部）**
- [ ] **Step 3: 安装项目依赖**

```bash
conda run -n good-price pip install -e ".[dev]"
conda run -n good-price python -m playwright install chromium
```

Expected: 命令退出码 0。

- [ ] **Step 4: 首次提交**

```bash
git add -A
git commit -m "chore: 项目骨架、设计文档与依赖声明"
```

## Task 1: 配置模块

**Files:**
- Create: `goodprice/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

```python
from goodprice.config import Settings


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("XIANYU_COOKIE", "abc=1")
    settings = Settings()
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.xianyu_cookie == "abc=1"
    assert settings.llm_base_url == ""


def test_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.app_name == "闲鱼盯价助手"
    assert settings.default_crawl_interval_minutes == 20
    assert settings.default_crawl_jitter_minutes == 10
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_config.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.config）

- [ ] **Step 3: 最小实现**

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "goodprice.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "闲鱼盯价助手"
    database_url: str = f"sqlite:///{DEFAULT_DB.as_posix()}"
    xianyu_cookie: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen-vl-max"
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/config.py tests/test_config.py
git commit -m "feat: 配置模块（pydantic-settings + .env）"
```

## Task 2: 数据库模型

**Files:**
- Create: `goodprice/db.py`, `goodprice/models.py`
- Test: `tests/test_models.py`, `tests/conftest.py`

- [ ] **Step 1: 写失败测试与夹具**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

import pytest

from goodprice.config import Settings
from goodprice.db import Base, make_session_factory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_db(tmp_path):
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def session_factory(tmp_db):
    factory = make_session_factory(tmp_db)
    Base.metadata.create_all(factory().get_bind())
    return factory


@pytest.fixture
def base_settings(tmp_db):
    return Settings(
        database_url=tmp_db,
        _env_file=None,
        default_crawl_interval_minutes=20,
        default_crawl_jitter_minutes=0,
    )
```

`tests/test_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask


def test_watch_task_crud(session_factory):
    with session_factory() as session:
        task = WatchTask(keyword="iPhone 13", max_price=3000, min_condition_score=6)
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    with session_factory() as session:
        loaded = session.get(WatchTask, task_id)
        assert loaded.keyword == "iPhone 13"
        assert loaded.enabled is True
        assert loaded.last_error is None


def test_listing_unique_platform_external(session_factory):
    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1001", title="a", price=1.0, url="u"))
        session.commit()
    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1001", title="b", price=2.0, url="v"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_listing_relations(session_factory):
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="2001", title="t", price=9.9, url="u")
        session.add(listing)
        session.flush()
        session.add(PriceSnapshot(listing_id=listing.id, price=9.9))
        session.add(Notification(listing_id=listing.id, channel="log", status="sent"))
        session.commit()
        session.refresh(listing)
        assert len(listing.snapshots) == 1
        assert len(listing.notifications) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_models.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.db）

- [ ] **Step 3: 最小实现**

`goodprice/db.py`:

```python
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    path = database_url.removeprefix("sqlite:///")
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def make_session_factory(database_url: str) -> sessionmaker:
    _ensure_sqlite_dir(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(database_url: str) -> None:
    """建表（幂等）。"""
    from goodprice import models  # noqa: F401  确保模型注册

    factory = make_session_factory(database_url)
    Base.metadata.create_all(factory().get_bind())
```

`goodprice/models.py`:

```python
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from goodprice.db import Base


def _now() -> datetime:
    return datetime.now()


class WatchTask(Base):
    __tablename__ = "watch_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    keyword: Mapped[str] = mapped_column(String(200))
    max_price: Mapped[float] = mapped_column(Float, default=0.0)
    condition_requirement: Mapped[str] = mapped_column(Text, default="")
    min_condition_score: Mapped[int] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(50), default="xianyu")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_run_count: Mapped[int] = mapped_column(Integer, default=0)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_listing_platform_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[float] = mapped_column(Float)
    url: Mapped[str] = mapped_column(Text)
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    seller: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    condition_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    condition_detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    price: Mapped[float] = mapped_column(Float)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("watch_tasks.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), default="log")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    listing: Mapped[Optional[Listing]] = relationship(back_populates="notifications")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/db.py goodprice/models.py tests/
git commit -m "feat: SQLAlchemy 数据模型与数据库初始化"
```

## Task 3: 闲鱼结果解析器

**Files:**
- Create: `goodprice/crawler/__init__.py`, `goodprice/crawler/base.py`, `goodprice/crawler/selectors.py`, `goodprice/crawler/parser.py`, `tests/fixtures/xianyu_search.html`
- Test: `tests/test_crawler_parser.py`

- [ ] **Step 1: 写失败测试与 fixture**

`tests/fixtures/xianyu_search.html`：

```html
<!DOCTYPE html>
<html>
<body>
  <div class="s-item-card">
    <a href="https://www.goofish.com/item?id=1001"><h3 class="s-title">iPhone 13 128G 蓝色</h3></a>
    <span class="s-price"><strong>¥2999.00</strong></span>
    <img src="//img.goofish.com/1001.jpg">
    <span class="seller">小明</span>
    <span class="location">杭州</span>
  </div>
  <div class="s-item-card">
    <a href="https://www.goofish.com/item?id=1002"><h3 class="s-title">二手自行车</h3></a>
    <span class="s-price"><strong>¥450</strong></span>
    <img src="https://img.goofish.com/1002.jpg">
  </div>
  <div class="s-item-card">
    <a href="https://www.goofish.com/item?id=1003"><h3 class="s-title">无价格商品</h3></a>
  </div>
  <div class="s-item-card">
    <a href="https://www.goofish.com/item?id=1001"><h3 class="s-title">重复商品</h3></a>
    <span class="s-price"><strong>¥10</strong></span>
  </div>
</body>
</html>
```

`tests/test_crawler_parser.py`:

```python
from pathlib import Path

import pytest

from goodprice.crawler.parser import extract_id, parse_price, parse_search_html

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"


def test_parse_price():
    assert parse_price("¥2999.00") == 2999.0
    assert parse_price(" 450 ") == 450.0
    with pytest.raises(ValueError):
        parse_price("面议")


def test_extract_id():
    assert extract_id("https://www.goofish.com/item?id=1001") == "1001"
    assert extract_id("https://www.goofish.com/item/abc123?x=1") == "abc123"
    assert extract_id("https://www.goofish.com/other") is None


def test_parse_search_html():
    items = parse_search_html(FIXTURE.read_text(encoding="utf-8"))
    assert len(items) == 2
    first = items[0]
    assert first.external_id == "1001"
    assert first.title == "iPhone 13 128G 蓝色"
    assert first.price == 2999.0
    assert first.url == "https://www.goofish.com/item?id=1001"
    assert first.image_urls == ["https://img.goofish.com/1001.jpg"]
    assert first.seller == "小明"
    assert first.location == "杭州"
    second = items[1]
    assert second.external_id == "1002"
    assert second.seller is None
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.crawler.parser）

- [ ] **Step 3: 最小实现**

`goodprice/crawler/base.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ListingData:
    external_id: str
    title: str
    price: float
    url: str
    image_urls: list[str] = field(default_factory=list)
    seller: Optional[str] = None
    location: Optional[str] = None
    published_at: Optional[datetime] = None


class CrawlerAuthError(RuntimeError):
    """登录态失效或需要登录。"""
```

`goodprice/crawler/selectors.py`:

```python
# 闲鱼网页版搜索结果卡片选择器（goofish.com）。平台改版时只需调整本文件。
RESULT_CARD = ".s-item-card"
TITLE = ".s-title"
PRICE = ".s-price strong"
LINK = "a"
IMAGE = "img"
SELLER = ".seller"
LOCATION = ".location"
```

`goodprice/crawler/parser.py`:

```python
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import ListingData

BASE_URL = "https://www.goofish.com"
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_price(text: str) -> float:
    match = _PRICE_RE.search(text or "")
    if not match:
        raise ValueError(f"无法解析价格: {text!r}")
    return float(match.group(1))


def extract_id(href: str) -> Optional[str]:
    match = re.search(r"[?&]id=([^&]+)", href)
    if match:
        return match.group(1)
    match = re.search(r"/item/([^/?#]+)", href)
    if match:
        return match.group(1)
    return None


def _absolute(url: str) -> str:
    return urljoin(BASE_URL, url)


def parse_search_html(html: str) -> list[ListingData]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ListingData] = []
    seen: set[str] = set()
    for card in soup.select(sel.RESULT_CARD):
        link_el = card.select_one(sel.LINK)
        href = link_el.get("href") if link_el else None
        if not href:
            continue
        external_id = extract_id(href)
        if not external_id or external_id in seen:
            continue
        title_el = card.select_one(sel.TITLE)
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        price_el = card.select_one(sel.PRICE)
        try:
            price = parse_price(price_el.get_text() if price_el else "")
        except ValueError:
            continue
        img_el = card.select_one(sel.IMAGE)
        image_urls = [img_el.get("src")] if img_el and img_el.get("src") else []
        seller_el = card.select_one(sel.SELLER)
        location_el = card.select_one(sel.LOCATION)
        items.append(
            ListingData(
                external_id=external_id,
                title=title,
                price=price,
                url=_absolute(href),
                image_urls=[_absolute(u) for u in image_urls],
                seller=seller_el.get_text(strip=True) if seller_el else None,
                location=location_el.get_text(strip=True) if location_el else None,
            )
        )
        seen.add(external_id)
    return items
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/crawler/ tests/test_crawler_parser.py tests/fixtures/
git commit -m "feat: 闲鱼搜索结果 HTML 解析器"
```

## Task 4: 闲鱼 Playwright 适配器

**Files:**
- Create: `goodprice/crawler/xianyu.py`
- Test: `tests/test_crawler_xianyu.py`

- [ ] **Step 1: 写失败测试**

```python
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
    def __init__(self, page):
        self._page = page

    def launch(self, **kwargs):
        return FakeBrowser(self._page)


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
        return FakeChromium(self._page)


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
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_xianyu.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.crawler.xianyu）

- [ ] **Step 3: 最小实现**

```python
from typing import Callable, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import CrawlerAuthError, ListingData
from goodprice.crawler.parser import parse_search_html

SEARCH_URL = "https://www.goofish.com/search?q={keyword}"
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

    def search(self, keyword: str, max_items: int = 30) -> list[ListingData]:
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
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_selector(sel.RESULT_CARD, timeout=30000)
                if "login" in (page.url or ""):
                    raise CrawlerAuthError("闲鱼 Cookie 已失效或未登录，请重新获取")
                return parse_search_html(page.content())[:max_items]
            finally:
                browser.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_xianyu.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/crawler/xianyu.py tests/test_crawler_xianyu.py
git commit -m "feat: 闲鱼 Playwright 适配器"
```

## Task 5: LLM 品相分析客户端

**Files:**
- Create: `goodprice/analysis/__init__.py`, `goodprice/analysis/prompts.py`, `goodprice/analysis/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: 写失败测试**

```python
import json

import httpx
import pytest

from goodprice.analysis.llm import LLMClient, parse_analysis_json


def _client(handler):
    transport = httpx.MockTransport(handler)
    return LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="qwen-vl-max",
        transport=transport,
    )


def test_analyze_listing_returns_verdict():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"condition_score": 8, "defects": ["轻微划痕"], "recommended": true, "reason": "成色不错"}'
                        }
                    }
                ]
            },
        )

    verdict = _client(handler).analyze_listing("iPhone 13", 2999, image_urls=["https://x/1.jpg"])
    assert verdict["condition_score"] == 8
    assert verdict["defects"] == ["轻微划痕"]
    assert verdict["recommended"] is True
    assert verdict["reason"] == "成色不错"


def test_analyze_disabled_without_config():
    client = LLMClient(base_url="", api_key="", model="")
    assert client.enabled is False
    with pytest.raises(RuntimeError):
        client.analyze_listing("t", 1)


def test_parse_analysis_json_tolerates_fence_and_clamps():
    raw = '```json\n{"condition_score": 99, "defects": [], "recommended": false, "reason": "x"}\n```'
    verdict = parse_analysis_json(raw)
    assert verdict["condition_score"] == 10
    assert verdict["recommended"] is False


def test_parse_analysis_json_raises_on_no_json():
    with pytest.raises(ValueError):
        parse_analysis_json("抱歉，我无法判断")
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_llm.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.analysis.llm）

- [ ] **Step 3: 最小实现**

`goodprice/analysis/prompts.py`:

```python
SYSTEM_PROMPT = (
    "你是一位熟悉中国二手交易市场（闲鱼）的验货专家。用户给出商品标题、价格、卖家描述和图片，"
    "请判断商品品相是否符合卖家描述、是否值得按此价格购买。只输出 JSON，不要输出其它文字，格式："
    '{"condition_score": 1到10的整数（越高品相越好）, "defects": ["瑕疵列表"], '
    '"recommended": true或false, "reason": "一句话理由"}'
)

USER_PROMPT_TEMPLATE = (
    "商品标题：{title}\n"
    "价格：{price} 元\n"
    "卖家描述：{description}\n"
    "买家品相要求：{requirement}\n"
    "图片数量：{image_count}\n"
    "请给出结构化 JSON 结论。"
)
```

`goodprice/analysis/llm.py`:

```python
import json
import re
from typing import Any, Optional

import httpx

from goodprice.analysis.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def parse_analysis_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中没有 JSON: {raw!r}")
    data = json.loads(text[start : end + 1])
    score = max(1, min(10, int(data.get("condition_score", 0))))
    defects = [str(d) for d in data.get("defects", [])][:10]
    return {
        "condition_score": score,
        "defects": defects,
        "recommended": bool(data.get("recommended", False)),
        "reason": str(data.get("reason", ""))[:500],
    }


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def analyze_listing(
        self,
        title: str,
        price: float,
        description: str = "",
        requirement: str = "",
        image_urls: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        image_urls = image_urls or []
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": USER_PROMPT_TEMPLATE.format(
                    title=title,
                    price=price,
                    description=description or "无",
                    requirement=requirement or "无",
                    image_count=len(image_urls),
                ),
            }
        ]
        for url in image_urls[:4]:
            content.append({"type": "image_url", "image_url": {"url": url}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return parse_analysis_json(raw)
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/analysis/ tests/test_llm.py
git commit -m "feat: OpenAI 兼容 LLM 品相分析客户端"
```

## Task 6: 通知通道

**Files:**
- Create: `goodprice/notify/__init__.py`, `goodprice/notify/base.py`, `goodprice/notify/log.py`, `goodprice/notify/serverchan.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: 写失败测试**

```python
import httpx
import pytest

from goodprice.notify.base import NotificationMessage
from goodprice.notify.log import LogNotifier
from goodprice.notify.serverchan import ServerChanNotifier


def test_log_notifier_logs(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        LogNotifier().send(NotificationMessage(title="t", content="c", url="u"))
    assert "通知[log]" in caplog.text


def test_serverchan_sends_form(caplog):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    notifier = ServerChanNotifier(sendkey="KEY123", transport=httpx.MockTransport(handler))
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["url"].endswith("/KEY123.send")
    assert "title=%E6%A0%87%E9%A2%98" in captured["body"]


def test_serverchan_raises_on_error_response():
    def handler(request):
        return httpx.Response(200, json={"code": 400, "message": "bad"})

    notifier = ServerChanNotifier(sendkey="KEY", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_serverchan_disabled_without_key():
    assert ServerChanNotifier(sendkey="").enabled is False
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_notify.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.notify.base）

- [ ] **Step 3: 最小实现**

`goodprice/notify/base.py`:

```python
from dataclasses import dataclass


@dataclass
class NotificationMessage:
    title: str
    content: str
    url: str = ""


class Notifier:
    channel = "base"

    def send(self, message: NotificationMessage) -> None:
        raise NotImplementedError
```

`goodprice/notify/log.py`:

```python
import logging

from goodprice.notify.base import NotificationMessage, Notifier

logger = logging.getLogger(__name__)


class LogNotifier(Notifier):
    channel = "log"

    def send(self, message: NotificationMessage) -> None:
        logger.info(
            "通知[%s]: %s\n%s\n%s", self.channel, message.title, message.content, message.url
        )
```

`goodprice/notify/serverchan.py`:

```python
from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier

SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"


class ServerChanNotifier(Notifier):
    channel = "serverchan"

    def __init__(
        self,
        sendkey: str = "",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.sendkey = sendkey
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.sendkey)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("Server酱未配置 sendkey")
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            SERVERCHAN_URL.format(key=self.sendkey),
            data={"title": message.title, "desp": f"{message.content}\n{message.url}"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Server酱返回错误: {data}")
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_notify.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/notify/ tests/test_notify.py
git commit -m "feat: 通知通道（日志 + Server酱）"
```

## Task 7: 设置服务

**Files:**
- Create: `goodprice/services/__init__.py`, `goodprice/services/settings_service.py`
- Test: `tests/test_settings_service.py`

- [ ] **Step 1: 写失败测试**

```python
from goodprice.config import Settings
from goodprice.services.settings_service import RuntimeSettings, SettingsService


def test_defaults_without_overrides(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    settings = service.get()
    assert isinstance(settings, RuntimeSettings)
    assert settings.default_crawl_interval_minutes == 20
    assert settings.xianyu_cookie == ""


def test_set_many_persists_and_merges(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"xianyu_cookie": "a=1", "llm_model": "gpt-4o-mini"})
    settings = service.get()
    assert settings.xianyu_cookie == "a=1"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.default_crawl_jitter_minutes == 0  # env 默认值仍在


def test_empty_value_clears_override(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"xianyu_cookie": "a=1"})
    service.set_many({"xianyu_cookie": ""})
    assert service.get().xianyu_cookie == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_settings_service.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.services.settings_service）

- [ ] **Step 3: 最小实现**

```python
from dataclasses import asdict, dataclass
from typing import Optional

from goodprice.config import Settings
from goodprice.models import AppSetting


@dataclass
class RuntimeSettings:
    xianyu_cookie: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    serverchan_sendkey: str = ""
    proxy: str = ""
    default_crawl_interval_minutes: int = 20
    default_crawl_jitter_minutes: int = 10

    @classmethod
    def from_sources(cls, base: Settings, overrides: dict[str, str]) -> "RuntimeSettings":
        values = asdict(cls())
        values.update({k: v for k, v in base.model_dump().items() if k in values})
        values.update({k: v for k, v in overrides.items() if v != "" and k in values})
        return cls(**values)


class SettingsService:
    def __init__(self, session_factory, base: Optional[Settings] = None):
        self._session_factory = session_factory
        self._base = base or Settings(_env_file=None)

    def _overrides(self, session) -> dict[str, str]:
        return {row.key: row.value for row in session.query(AppSetting).all()}

    def get(self) -> RuntimeSettings:
        with self._session_factory() as session:
            return RuntimeSettings.from_sources(self._base, self._overrides(session))

    def set_many(self, values: dict[str, str]) -> RuntimeSettings:
        with self._session_factory() as session:
            for key, value in values.items():
                row = session.get(AppSetting, key)
                if value == "":
                    if row:
                        session.delete(row)
                elif row:
                    row.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            session.commit()
            return RuntimeSettings.from_sources(self._base, self._overrides(session))
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_settings_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/services/ tests/test_settings_service.py
git commit -m "feat: 设置服务（env 默认值 + 数据库覆盖）"
```

## Task 8: 任务服务 CRUD

**Files:**
- Create: `goodprice/services/task_service.py`
- Test: `tests/test_task_service.py`

- [ ] **Step 1: 写失败测试**

```python
from goodprice.services.task_service import TaskService


def _service(session_factory):
    return TaskService(session_factory)


def test_create_and_get(session_factory):
    service = _service(session_factory)
    task = service.create_task(
        {"keyword": "iPhone 13", "max_price": "3000", "min_condition_score": "6"}
    )
    assert task.id is not None
    loaded = service.get_task(task.id)
    assert loaded.keyword == "iPhone 13"
    assert loaded.max_price == 3000.0
    assert loaded.min_condition_score == 6


def test_list_and_enabled(session_factory):
    service = _service(session_factory)
    service.create_task({"keyword": "a"})
    service.create_task({"keyword": "b"})
    assert len(service.list_tasks()) == 2
    assert len(service.enabled_tasks()) == 2


def test_toggle(session_factory):
    service = _service(session_factory)
    task = service.create_task({"keyword": "a"})
    toggled = service.toggle_task(task.id)
    assert toggled.enabled is False
    assert service.get_task(task.id).enabled is False


def test_delete(session_factory):
    service = _service(session_factory)
    task = service.create_task({"keyword": "a"})
    assert service.delete_task(task.id) is True
    assert service.get_task(task.id) is None
    assert service.delete_task(999) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_task_service.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.services.task_service）

- [ ] **Step 3: 最小实现**

```python
from typing import Optional

from goodprice.models import WatchTask


class TaskService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_tasks(self) -> list[WatchTask]:
        with self._session_factory() as session:
            return session.query(WatchTask).order_by(WatchTask.id).all()

    def get_task(self, task_id: int) -> Optional[WatchTask]:
        with self._session_factory() as session:
            return session.get(WatchTask, task_id)

    def create_task(self, data: dict) -> WatchTask:
        task = WatchTask(
            name=data.get("name", ""),
            keyword=data["keyword"],
            max_price=float(data.get("max_price") or 0),
            condition_requirement=data.get("condition_requirement", ""),
            min_condition_score=int(data.get("min_condition_score") or 0),
            platform=data.get("platform", "xianyu"),
            interval_minutes=int(data.get("interval_minutes") or 20),
            enabled=bool(data.get("enabled", True)),
        )
        with self._session_factory() as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def toggle_task(self, task_id: int) -> Optional[WatchTask]:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.enabled = not task.enabled
                session.commit()
                session.refresh(task)
            return task

    def delete_task(self, task_id: int) -> bool:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if not task:
                return False
            session.delete(task)
            session.commit()
            return True

    def enabled_tasks(self) -> list[WatchTask]:
        with self._session_factory() as session:
            return session.query(WatchTask).filter(WatchTask.enabled.is_(True)).all()
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_task_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/services/task_service.py tests/test_task_service.py
git commit -m "feat: 监控任务 CRUD 服务"
```

## Task 9: 核心爬取流水线

**Files:**
- Create: `goodprice/services/crawl_service.py`
- Test: `tests/test_crawl_service.py`

- [ ] **Step 1: 写失败测试**

```python
from datetime import datetime

import pytest

from goodprice.crawler.base import CrawlerAuthError, ListingData
from goodprice.models import Listing, Notification
from goodprice.services.crawl_service import CrawlService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


class FakeAdapter:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error

    def search(self, keyword):
        if self.error:
            raise self.error
        return self.items


class FakeLLM:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {"condition_score": 8, "defects": [], "recommended": True, "reason": "ok"}
        self.error = error

    def analyze_listing(self, **kwargs):
        if self.error:
            raise self.error
        return self.verdict


class FakeNotifier:
    def __init__(self, name="log"):
        self.name = name
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def _item(external_id="1001", price=100.0):
    return ListingData(
        external_id=external_id,
        title=f"商品{external_id}",
        price=price,
        url=f"https://x/{external_id}",
        image_urls=[f"https://x/{external_id}.jpg"],
    )


def _service(session_factory, base_settings, adapter=None, llm=None, notifier=None):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = notifier or FakeNotifier()
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter or FakeAdapter(),
        llm=llm or FakeLLM(),
        notifiers=[(notifier.name, notifier)],
        settings_service=settings_service,
    )
    return crawl, notifier, settings_service


def test_happy_path_and_dedup(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "iPhone", "min_condition_score": "6"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    stats = crawl.run_task(task.id)
    assert stats["new"] == 1
    assert stats["notified"] == 1
    assert len(notifier.messages) == 1

    stats2 = crawl.run_task(task.id)
    assert stats2["new"] == 0
    assert stats2["notified"] == 0
    assert len(notifier.messages) == 1  # 同一商品只通知一次

    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8
        assert listing.notified_at is not None
        assert len(listing.snapshots) == 1


def test_price_filter(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "max_price": "50"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item(price=100.0)]))
    stats = crawl.run_task(task.id)
    assert stats["new"] == 0
    assert stats["notified"] == 0
    with session_factory() as session:
        assert session.query(Listing).count() == 0


def test_condition_gate_blocks_low_score(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "min_condition_score": "6"})
    llm = FakeLLM(verdict={"condition_score": 3, "defects": ["碎屏"], "recommended": False, "reason": "太差"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 3
        assert listing.notified_at is None


def test_llm_failure_falls_back_to_price_only(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM(error=RuntimeError("网络错误"))
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score is None


def test_llm_disabled_skips_analysis(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM(enabled=False)
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1


def test_adapter_error_records_last_error(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    crawl, _, _ = _service(
        session_factory,
        base_settings,
        adapter=FakeAdapter(error=CrawlerAuthError("Cookie 失效")),
    )
    with pytest.raises(CrawlerAuthError):
        crawl.run_task(task.id)
    with session_factory() as session:
        loaded = session.get(type(task), task.id)
        assert "Cookie 失效" in loaded.last_error


def test_price_change_creates_snapshot(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = FakeAdapter([_item(price=100.0)])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    adapter.items = [_item(price=90.0)]
    crawl.run_task(task.id)
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.price == 90.0
        assert len(listing.snapshots) == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.services.crawl_service）

- [ ] **Step 3: 最小实现**

```python
import logging
import random
import time
from datetime import datetime
from typing import Any, Optional

from goodprice.crawler.base import ListingData
from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask
from goodprice.notify.base import NotificationMessage

logger = logging.getLogger(__name__)


class CrawlService:
    def __init__(self, session_factory, adapter, llm, notifiers, settings_service):
        self._session_factory = session_factory
        self.adapter = adapter
        self.llm = llm
        self.notifiers = notifiers  # [(channel_name, notifier), ...]
        self.settings_service = settings_service

    def run_task(self, task_id: int) -> dict[str, int]:
        stats = {"found": 0, "new": 0, "notified": 0}
        settings = self.settings_service.get()
        jitter = int(settings.default_crawl_jitter_minutes)
        if jitter:
            time.sleep(random.uniform(0, jitter * 60))
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task is None:
                raise RuntimeError(f"任务 {task_id} 不存在")
            task.last_run_at = datetime.now()
            task.last_error = None
            session.commit()
        try:
            items = self.adapter.search(task.keyword)
        except Exception as exc:
            self._record_error(task_id, f"抓取失败: {exc}")
            raise
        stats["found"] = len(items)
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            for data in items:
                if task.max_price and data.price > task.max_price:
                    continue
                listing = self._upsert_listing(session, task, data)
                if listing is None:
                    continue
                stats["new"] += 1
                verdict = self._analyze(session, listing, task)
                if verdict is not None and verdict["condition_score"] < task.min_condition_score:
                    continue
                if listing.notified_at is None:
                    self._notify(session, task, listing)
                    stats["notified"] += 1
            session.commit()
        return stats

    def _upsert_listing(
        self, session, task: WatchTask, data: ListingData
    ) -> Optional[Listing]:
        listing = (
            session.query(Listing)
            .filter(Listing.platform == task.platform, Listing.external_id == data.external_id)
            .first()
        )
        if listing is None:
            listing = Listing(
                platform=task.platform,
                external_id=data.external_id,
                title=data.title,
                price=data.price,
                url=data.url,
                image_urls=data.image_urls,
                seller=data.seller,
                location=data.location,
                published_at=data.published_at,
            )
            session.add(listing)
            session.flush()
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
            return listing
        if abs(listing.price - data.price) > 0.001:
            listing.price = data.price
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
        listing.last_seen_at = datetime.now()
        return None

    def _analyze(self, session, listing: Listing, task: WatchTask) -> Optional[dict[str, Any]]:
        if not self.llm.enabled:
            return None
        try:
            verdict = self.llm.analyze_listing(
                title=listing.title,
                price=listing.price,
                description=task.condition_requirement,
                requirement=task.condition_requirement,
                image_urls=listing.image_urls,
            )
        except Exception as exc:
            logger.warning("LLM 分析失败，降级为仅价格命中: %s", exc)
            return None
        listing.condition_score = verdict["condition_score"]
        listing.condition_detail = verdict
        return verdict

    def _notify(self, session, task: WatchTask, listing: Listing) -> None:
        reason = ""
        if listing.condition_detail:
            reason = listing.condition_detail.get("reason", "")
        message = NotificationMessage(
            title=f"[{task.keyword}] {listing.title}",
            content=(
                f"价格：{listing.price} 元\n"
                f"品相分：{listing.condition_score or '未评估'}\n{reason}"
            ),
            url=listing.url,
        )
        for channel, notifier in self.notifiers:
            try:
                notifier.send(message)
                session.add(
                    Notification(
                        listing_id=listing.id, task_id=task.id, channel=channel, status="sent"
                    )
                )
            except Exception as exc:
                logger.warning("通知[%s]失败: %s", channel, exc)
                session.add(
                    Notification(
                        listing_id=listing.id,
                        task_id=task.id,
                        channel=channel,
                        status="failed",
                        detail=str(exc),
                    )
                )
        listing.notified_at = datetime.now()

    def _record_error(self, task_id: int, message: str) -> None:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.last_error = message[:1000]
                session.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/services/crawl_service.py tests/test_crawl_service.py
git commit -m "feat: 核心爬取流水线（价格初筛/去重/品相门控/通知）"
```

## Task 10: 调度器

**Files:**
- Create: `goodprice/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
from goodprice.scheduler import _sync_tasks
from goodprice.services.task_service import TaskService


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger=None, args=None, id=None, **kwargs):
        self.jobs[id] = {"func": func, "args": args}

    def get_jobs(self):
        return list(self.jobs.values())

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def test_sync_adds_job_for_enabled_task(session_factory):
    task_service = TaskService(session_factory)
    task = task_service.create_task({"keyword": "k", "interval_minutes": "30"})
    scheduler = FakeScheduler()
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" in scheduler.jobs


def test_sync_removes_job_for_disabled_or_deleted(session_factory):
    task_service = TaskService(session_factory)
    task = task_service.create_task({"keyword": "k"})
    scheduler = FakeScheduler()
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" in scheduler.jobs
    task_service.toggle_task(task.id)
    _sync_tasks(session_factory, lambda tid: None, task_service, scheduler)
    assert "crawl_1" not in scheduler.jobs
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_scheduler.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.scheduler）

- [ ] **Step 3: 最小实现**

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


def build_scheduler(session_factory, run_job, task_service) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _sync_tasks,
        trigger=IntervalTrigger(minutes=5),
        args=[session_factory, run_job, task_service, scheduler],
        id="sync_tasks",
        replace_existing=True,
        max_instances=1,
    )
    _sync_tasks(session_factory, run_job, task_service, scheduler)
    return scheduler


def _sync_tasks(session_factory, run_job, task_service, scheduler) -> None:
    enabled_ids = {task.id for task in task_service.enabled_tasks()}
    job_ids = {job.id for job in scheduler.get_jobs()}
    for task_id in enabled_ids:
        job_id = f"crawl_{task_id}"
        if job_id in job_ids:
            continue
        task = task_service.get_task(task_id)
        scheduler.add_job(
            run_job,
            trigger=IntervalTrigger(minutes=max(1, task.interval_minutes)),
            args=[task_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
    for job_id in list(job_ids):
        if not job_id.startswith("crawl_"):
            continue
        task_id = int(job_id.removeprefix("crawl_"))
        if task_id not in enabled_ids:
            scheduler.remove_job(job_id)
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/scheduler.py tests/test_scheduler.py
git commit -m "feat: APScheduler 任务调度（启停同步）"
```

## Task 11: Web 路由与模板

**Files:**
- Create: `goodprice/web/__init__.py`, `goodprice/web/routes.py`, `goodprice/web/templates/base.html`, `goodprice/web/templates/dashboard.html`, `goodprice/web/templates/tasks.html`, `goodprice/web/templates/tasks_table.html`, `goodprice/web/templates/listings.html`, `goodprice/web/templates/settings.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

```python
from fastapi.testclient import TestClient

from goodprice.main import build_app


def _client(base_settings, session_factory):
    app = build_app(settings=base_settings, session_factory=session_factory, with_scheduler=False)
    app.state.run_job = lambda task_id: None
    return TestClient(app)


def test_pages_render(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    for path in ["/", "/tasks", "/listings", "/settings"]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_create_and_list_tasks(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    response = client.post(
        "/api/tasks",
        json={"keyword": "iPhone 13", "max_price": 3000, "min_condition_score": 6},
    )
    assert response.status_code == 200
    data = client.get("/api/tasks").json()
    assert len(data) == 1
    assert data[0]["keyword"] == "iPhone 13"
    assert data[0]["max_price"] == 3000.0


def test_toggle_and_delete(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    response = client.post(f"/tasks/{task['id']}/toggle")
    assert response.status_code == 303
    assert client.get("/api/tasks").json()[0]["enabled"] is False
    response = client.post(f"/tasks/{task['id']}/delete")
    assert response.status_code == 303
    assert client.get("/api/tasks").json() == []


def test_run_task_uses_run_job(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    calls = []
    client.app.state.run_job = lambda task_id: calls.append(task_id)
    response = client.post(f"/tasks/{task['id']}/run")
    assert response.status_code == 303
    assert calls == [task["id"]]


def test_settings_save(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    response = client.post(
        "/settings",
        data={
            "xianyu_cookie": "a=1",
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "qwen-vl-max",
            "serverchan_sendkey": "",
            "proxy": "",
            "default_crawl_interval_minutes": "30",
            "default_crawl_jitter_minutes": "5",
        },
    )
    assert response.status_code == 303
    settings = client.app.state.settings_service.get()
    assert settings.xianyu_cookie == "a=1"
    assert settings.default_crawl_interval_minutes == 30
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.web.routes 或 goodprice.main）

- [ ] **Step 3: 最小实现（先做路由与 JSON API，再补模板）**

`goodprice/web/routes.py`:

```python
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
    task_service, _ = _services(request)
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
    return templates.TemplateResponse(
        request, "tasks.html", {"tasks": tasks, "active": "tasks"}
    )


@router.post("/tasks")
def create_task_form(request: Request, keyword: str = Form(...), name: str = Form(""),
                    max_price: float = Form(0), condition_requirement: str = Form(""),
                    min_condition_score: int = Form(0), interval_minutes: int = Form(20),
                    enabled: int = Form(1)):
    task_service, _ = _services(request)
    task_service.create_task(
        {
            "keyword": keyword.strip(),
            "name": name.strip(),
            "max_price": max_price,
            "condition_requirement": condition_requirement,
            "min_condition_score": min_condition_score,
            "interval_minutes": interval_minutes,
            "enabled": bool(enabled),
        }
    )
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.toggle_task(task_id)
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/run")
def run_task(request: Request, task_id: int):
    request.app.state.run_job(task_id)
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.delete_task(task_id)
    return RedirectResponse("/tasks", status_code=303)


@router.get("/listings", response_class=HTMLResponse)
def listings_page(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        listings = session.query(Listing).order_by(Listing.first_seen_at.desc()).limit(100).all()
    return templates.TemplateResponse(
        request, "listings.html", {"listings": listings, "active": "listings"}
    )


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
def save_settings(request: Request, xianyu_cookie: str = Form(""), llm_base_url: str = Form(""),
                  llm_api_key: str = Form(""), llm_model: str = Form(""),
                  serverchan_sendkey: str = Form(""), proxy: str = Form(""),
                  default_crawl_interval_minutes: int = Form(20),
                  default_crawl_jitter_minutes: int = Form(10)):
    _, settings_service = _services(request)
    settings_service.set_many(
        {
            "xianyu_cookie": xianyu_cookie,
            "llm_base_url": llm_base_url,
            "llm_api_key": llm_api_key,
            "llm_model": llm_model,
            "serverchan_sendkey": serverchan_sendkey,
            "proxy": proxy,
            "default_crawl_interval_minutes": str(default_crawl_interval_minutes),
            "default_crawl_jitter_minutes": str(default_crawl_jitter_minutes),
        }
    )
    return RedirectResponse("/settings", status_code=303)


@router.get("/api/tasks")
def api_list_tasks(request: Request):
    task_service, _ = _services(request)
    return [_task_dict(t) for t in task_service.list_tasks()]


@router.post("/api/tasks")
def api_create_task(request: Request, data: TaskCreate):
    task_service, _ = _services(request)
    task = task_service.create_task(data.model_dump())
    return _task_dict(task)


@router.get("/api/listings")
def api_list_listings(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        rows = session.query(Listing).order_by(Listing.first_seen_at.desc()).limit(100).all()
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
```

模板（`base.html` 布局 + 四个页面，Tailwind 与 HTMX 用 CDN）：

`goodprice/web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}闲鱼盯价助手{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
</head>
<body class="bg-gray-100 text-gray-900">
  <nav class="bg-white shadow">
    <div class="max-w-6xl mx-auto px-4 py-3 flex items-center gap-6">
      <span class="font-bold text-lg">闲鱼盯价助手</span>
      <a href="/" class="{{ 'text-blue-600 font-semibold' if active == 'dashboard' else 'text-gray-600' }}">仪表盘</a>
      <a href="/tasks" class="{{ 'text-blue-600 font-semibold' if active == 'tasks' else 'text-gray-600' }}">监控任务</a>
      <a href="/listings" class="{{ 'text-blue-600 font-semibold' if active == 'listings' else 'text-gray-600' }}">命中列表</a>
      <a href="/settings" class="{{ 'text-blue-600 font-semibold' if active == 'settings' else 'text-gray-600' }}">设置</a>
    </div>
  </nav>
  <main class="max-w-6xl mx-auto px-4 py-6">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

`goodprice/web/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}仪表盘 - 闲鱼盯价助手{% endblock %}
{% block content %}
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
  <div class="bg-white rounded shadow p-4"><div class="text-2xl font-bold">{{ stats.tasks }}</div><div class="text-gray-500">监控任务</div></div>
  <div class="bg-white rounded shadow p-4"><div class="text-2xl font-bold">{{ stats.enabled_tasks }}</div><div class="text-gray-500">启用中</div></div>
  <div class="bg-white rounded shadow p-4"><div class="text-2xl font-bold">{{ stats.listings }}</div><div class="text-gray-500">收录商品</div></div>
  <div class="bg-white rounded shadow p-4"><div class="text-2xl font-bold">{{ stats.notified }}</div><div class="text-gray-500">已通知</div></div>
</div>
<h2 class="text-lg font-semibold mb-3">最近收录</h2>
<div class="bg-white rounded shadow divide-y">
  {% for item in recent %}
  <div class="p-3 flex items-center gap-3">
    {% if item.image_urls %}<img src="{{ item.image_urls[0] }}" class="w-14 h-14 object-cover rounded">{% endif %}
    <div class="flex-1">
      <a href="{{ item.url }}" target="_blank" class="font-medium hover:text-blue-600">{{ item.title }}</a>
      <div class="text-sm text-gray-500">{{ item.price }} 元 · 品相分 {{ item.condition_score or '未评估' }}</div>
    </div>
  </div>
  {% else %}
  <div class="p-4 text-gray-500">暂无数据，先去「监控任务」添加一个任务吧。</div>
  {% endfor %}
</div>
{% endblock %}
```

`goodprice/web/templates/tasks.html`:

```html
{% extends "base.html" %}
{% block title %}监控任务 - 闲鱼盯价助手{% endblock %}
{% block content %}
<div class="bg-white rounded shadow p-4 mb-6">
  <h2 class="text-lg font-semibold mb-3">新建监控任务</h2>
  <form method="post" action="/tasks" class="grid grid-cols-2 md:grid-cols-3 gap-3">
    <input name="keyword" required placeholder="关键词，如 iPhone 13" class="border rounded px-3 py-2">
    <input name="max_price" type="number" step="0.01" min="0" placeholder="最高价（0 不限）" class="border rounded px-3 py-2">
    <input name="min_condition_score" type="number" min="0" max="10" value="0" placeholder="最低品相分 0-10" class="border rounded px-3 py-2">
    <input name="interval_minutes" type="number" min="1" value="20" placeholder="间隔分钟" class="border rounded px-3 py-2">
    <input name="name" placeholder="备注名（可选）" class="border rounded px-3 py-2">
    <input name="condition_requirement" placeholder="品相要求，如“无拆修、屏幕完好”" class="border rounded px-3 py-2 md:col-span-2">
    <label class="flex items-center gap-2"><input type="checkbox" name="enabled" value="1" checked> 立即启用</label>
    <button class="bg-blue-600 text-white rounded px-4 py-2">添加</button>
  </form>
</div>
<div class="bg-white rounded shadow divide-y">
  {% for task in tasks %}
  <div class="p-4 flex items-center gap-4">
    <div class="flex-1">
      <div class="font-medium">{{ task.keyword }} <span class="text-gray-400 text-sm">{{ task.name }}</span></div>
      <div class="text-sm text-gray-500">
        最高价 {{ task.max_price }} · 品相分 ≥ {{ task.min_condition_score }} · 间隔 {{ task.interval_minutes }} 分钟
        {% if task.last_run_at %} · 上次运行 {{ task.last_run_at.strftime('%m-%d %H:%M') }}{% endif %}
      </div>
      {% if task.last_error %}<div class="text-sm text-red-600 mt-1">最近错误：{{ task.last_error }}</div>{% endif %}
    </div>
    <span class="px-2 py-1 rounded text-xs {{ 'bg-green-100 text-green-700' if task.enabled else 'bg-gray-200 text-gray-600' }}">
      {{ '启用' if task.enabled else '停用' }}
    </span>
    <form method="post" action="/tasks/{{ task.id }}/toggle" class="inline"><button class="border rounded px-3 py-1">{{ '停用' if task.enabled else '启用' }}</button></form>
    <form method="post" action="/tasks/{{ task.id }}/run" class="inline"><button class="border rounded px-3 py-1 bg-blue-50">立即执行</button></form>
    <form method="post" action="/tasks/{{ task.id }}/delete" class="inline" onsubmit="return confirm('确认删除该任务？')"><button class="border rounded px-3 py-1 text-red-600">删除</button></form>
  </div>
  {% else %}
  <div class="p-4 text-gray-500">还没有监控任务。</div>
  {% endfor %}
</div>
{% endblock %}
```

`goodprice/web/templates/listings.html`:

```html
{% extends "base.html" %}
{% block title %}命中列表 - 闲鱼盯价助手{% endblock %}
{% block content %}
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
  {% for item in listings %}
  <div class="bg-white rounded shadow p-3">
    {% if item.image_urls %}<img src="{{ item.image_urls[0] }}" class="w-full h-40 object-cover rounded mb-2">{% endif %}
    <a href="{{ item.url }}" target="_blank" class="font-medium hover:text-blue-600">{{ item.title }}</a>
    <div class="mt-2 flex items-center gap-2">
      <span class="text-lg font-bold text-red-600">¥{{ item.price }}</span>
      {% if item.condition_score %}
      <span class="px-2 py-0.5 rounded text-xs {{ 'bg-green-100 text-green-700' if item.condition_score >= 7 else 'bg-yellow-100 text-yellow-700' }}">品相 {{ item.condition_score }}/10</span>
      {% endif %}
      {% if item.notified_at %}<span class="px-2 py-0.5 rounded text-xs bg-blue-100 text-blue-700">已通知</span>{% endif %}
    </div>
    {% if item.condition_detail %}
    <div class="text-sm text-gray-600 mt-1">{{ item.condition_detail.reason }}</div>
    {% endif %}
  </div>
  {% else %}
  <div class="text-gray-500">还没有命中商品。</div>
  {% endfor %}
</div>
{% endblock %}
```

`goodprice/web/templates/settings.html`:

```html
{% extends "base.html" %}
{% block title %}设置 - 闲鱼盯价助手{% endblock %}
{% block content %}
<form method="post" action="/settings" class="bg-white rounded shadow p-4 max-w-2xl space-y-4">
  <div>
    <label class="block text-sm font-medium mb-1">闲鱼 Cookie</label>
    <textarea name="xianyu_cookie" rows="4" class="w-full border rounded px-3 py-2 font-mono text-xs">{{ settings.xianyu_cookie }}</textarea>
    <p class="text-xs text-gray-500 mt-1">登录 https://www.goofish.com 后，从浏览器开发者工具复制 Cookie 粘贴到这里。</p>
  </div>
  <div class="grid grid-cols-2 gap-3">
    <div><label class="block text-sm font-medium mb-1">LLM Base URL</label>
      <input name="llm_base_url" class="w-full border rounded px-3 py-2" value="{{ settings.llm_base_url }}" placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1"></div>
    <div><label class="block text-sm font-medium mb-1">LLM API Key</label>
      <input name="llm_api_key" type="password" class="w-full border rounded px-3 py-2" value="{{ settings.llm_api_key }}"></div>
    <div><label class="block text-sm font-medium mb-1">LLM 模型</label>
      <input name="llm_model" class="w-full border rounded px-3 py-2" value="{{ settings.llm_model }}"></div>
    <div><label class="block text-sm font-medium mb-1">Server酱 SendKey</label>
      <input name="serverchan_sendkey" type="password" class="w-full border rounded px-3 py-2" value="{{ settings.serverchan_sendkey }}"></div>
  </div>
  <div class="grid grid-cols-2 gap-3">
    <div><label class="block text-sm font-medium mb-1">代理（可选）</label>
      <input name="proxy" class="w-full border rounded px-3 py-2" value="{{ settings.proxy }}" placeholder="http://127.0.0.1:7890"></div>
    <div><label class="block text-sm font-medium mb-1">默认间隔（分钟）</label>
      <input name="default_crawl_interval_minutes" type="number" min="1" class="w-full border rounded px-3 py-2" value="{{ settings.default_crawl_interval_minutes }}"></div>
    <div><label class="block text-sm font-medium mb-1">随机抖动（分钟）</label>
      <input name="default_crawl_jitter_minutes" type="number" min="0" class="w-full border rounded px-3 py-2" value="{{ settings.default_crawl_jitter_minutes }}"></div>
  </div>
  <button class="bg-blue-600 text-white rounded px-4 py-2">保存设置</button>
</form>
{% endblock %}
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/web/ tests/test_api.py
git commit -m "feat: Web 路由、模板与 JSON API"
```

## Task 12: 应用组装与入口

**Files:**
- Create: `goodprice/main.py`, `goodprice/__main__.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: 写失败测试**

```python
from fastapi.testclient import TestClient

from goodprice.main import build_app


def test_build_app_health(base_settings, session_factory):
    app = build_app(settings=base_settings, session_factory=session_factory, with_scheduler=False)
    with TestClient(app) as client:
        response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json() == {"tasks": 0, "enabled_tasks": 0, "listings": 0, "notified": 0}
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_main.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.main）

- [ ] **Step 3: 最小实现**

`goodprice/main.py`:

```python
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from goodprice.config import Settings, get_settings
from goodprice.db import init_db
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
```

`goodprice/__main__.py`:

```python
from goodprice.main import main


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add goodprice/main.py goodprice/__main__.py tests/test_main.py
git commit -m "feat: FastAPI 应用组装与入口"
```

## Task 13: README、合规说明与最终验证

**Files:**
- Create: `README.md`

- [ ] **Step 1: 编写 README**

内容包含：功能简介、快速开始（conda 环境、playwright 安装、.env 配置、启动命令）、
获取闲鱼 Cookie 的步骤、技术架构、合规与法律免责声明（仅供个人学习研究，遵守平台条款、
滥用风险自负）、路线图（转转适配器、单品盯价、更多通知渠道）。

- [ ] **Step 2: 全量验证**

```bash
conda run -n good-price pytest -v
```

Expected: 全部通过，0 failed。

- [ ] **Step 3: 启动冒烟测试**

```bash
conda run -n good-price python -c "from goodprice.main import app; print(app.title)"
```

Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 4: 最终提交**

```bash
git add README.md
git commit -m "docs: README、快速开始与合规声明"
```

---

## 验收清单

- [ ] `pytest -v` 全部通过（解析、LLM、通知、设置、任务、流水线、调度、API、main）
- [ ] `python -m goodprice` 可启动，浏览器访问 http://127.0.0.1:8000 四个页面正常
- [ ] 服务重启后任务与历史仍在（SQLite 持久化）
- [ ] 真实 Cookie 手工验收：建任务 → 立即执行 → 命中入库、日志通知、品相结论
