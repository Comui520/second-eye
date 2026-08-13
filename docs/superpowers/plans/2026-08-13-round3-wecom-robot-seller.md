# 第三轮：企业微信群机器人 + 卖家信用/评价分析 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增企业微信群机器人通知通道；筛选流水线加入卖家信用/评价阶段（详情页信号 + 卖家主页深度数据，按卖家缓存 7 天，风险提示不拦截）。

**Architecture:** 沿用单进程 FastAPI + APScheduler + SQLite；notify 新增 `wecom_robot` 通道；新增 `sellers` 缓存表与 `SellerService`；`ListingDetail` 扩展卖家字段，`XianyuAdapter` 新增 `fetch_seller`。

**Tech Stack:** 沿用 Python 3.11（conda `good-price`）、httpx、Playwright、BeautifulSoup、pytest。

---

## Task 1: 群机器人通道与配置

**Files:**
- Create: `goodprice/notify/wecom_robot.py`
- Modify: `goodprice/config.py`, `goodprice/services/settings_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/settings.html`, `.env.example`, `goodprice/main.py`
- Test: `tests/test_notify.py`, `tests/test_config.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_notify.py` 追加：

```python
from goodprice.notify.wecom_robot import WeComRobotNotifier


def test_wecom_robot_send_success():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    notifier = WeComRobotNotifier(
        webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        transport=httpx.MockTransport(handler),
    )
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["url"].startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
    assert captured["body"]["msgtype"] == "text"
    assert "标题" in captured["body"]["text"]["content"]


def test_wecom_robot_93000_raises():
    def handler(request):
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"})

    notifier = WeComRobotNotifier(webhook="https://x/send?key=abc", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="移除"):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_wecom_robot_93004_raises():
    def handler(request):
        return httpx.Response(200, json={"errcode": 93004, "errmsg": "frequent"})

    notifier = WeComRobotNotifier(webhook="https://x/send?key=abc", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="频繁"):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_wecom_robot_disabled_without_webhook():
    assert WeComRobotNotifier(webhook="").enabled is False
```

`tests/test_config.py` 追加：

```python
def test_wecom_webhook_setting(monkeypatch):
    monkeypatch.setenv("WECOM_WEBHOOK", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc")
    assert Settings(_env_file=None).wecom_webhook == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"
```

`tests/test_api.py` 的 `_settings_form()` 增加 `"wecom_webhook": ""`。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_notify.py tests/test_config.py tests/test_api.py -q`
Expected: FAIL（wecom_robot 不存在 / 字段不存在）

- [ ] **Step 3: 实现**

`goodprice/notify/wecom_robot.py`：

```python
from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier


class WeComRobotNotifier(Notifier):
    channel = "wecom_robot"

    def __init__(
        self,
        webhook: str = "",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.webhook = webhook.strip()
        self._transport = transport
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("企业微信群机器人未配置 webhook")
        content = f"{message.title}\n{message.content}\n{message.url}"
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            self.webhook, json={"msgtype": "text", "text": {"content": content}}
        )
        response.raise_for_status()
        data = response.json()
        errcode = data.get("errcode", -1)
        if errcode == 93000:
            raise RuntimeError("企业微信群机器人 webhook 无效或机器人已被移除，请重新添加")
        if errcode == 93004:
            raise RuntimeError("企业微信群机器人发送太频繁（20 条/分钟），请稍后重试或降低频率")
        if errcode != 0:
            raise RuntimeError(f"企业微信群机器人发送失败: {data}")
```

`goodprice/config.py` 的 `Settings` 与 `goodprice/services/settings_service.py` 的 `RuntimeSettings` 各追加 `wecom_webhook: str = ""`。

`goodprice/web/routes.py` 的 `save_settings` 增加 `wecom_webhook: str = Form("")`，放入 `values`，并把 `"wecom_webhook"` 加入"留空保持原值"列表：

```python
    for key in ("llm_api_key", "serverchan_sendkey", "vision_api_key", "wecom_secret", "wecom_webhook"):
```

`goodprice/web/templates/settings.html` 企微区块追加一行：

```html
      <div class="col-span-2"><label class="block text-sm font-medium mb-1">群机器人 Webhook（推荐，无需域名/IP）</label>
        <input name="wecom_webhook" type="password" class="w-full border rounded px-3 py-2" value="" placeholder="已配置（留空保持不变）"></div>
```

`.env.example` 追加 `WECOM_WEBHOOK=`。

`goodprice/main.py` 的 `_make_crawl_service` 追加：

```python
    from goodprice.notify.wecom_robot import WeComRobotNotifier

    robot = WeComRobotNotifier(webhook=runtime.wecom_webhook)
    if robot.enabled:
        notifiers.append(("wecom_robot", robot))
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_notify.py tests/test_config.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 企业微信群机器人通知通道"
```

## Task 2: 卖家数据模型与迁移

**Files:**
- Modify: `goodprice/models.py`, `goodprice/db.py`
- Test: `tests/test_models.py`, `tests/test_db.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py` 追加：

```python
from goodprice.models import Seller


def test_seller_crud_and_listing_columns(session_factory):
    with session_factory() as session:
        seller = Seller(platform="xianyu", seller_uid="2672367114", positive_count=133, total_count=194)
        session.add(seller)
        session.flush()
        listing = Listing(
            platform="xianyu",
            external_id="3001",
            title="t",
            price=1.0,
            url="u",
            seller_uid="2672367114",
            seller_name="饼住呼吸",
            seller_risk={"risk_level": "低", "risk_reason": "好评率 100%"},
        )
        session.add(listing)
        session.commit()
        assert seller.positive_count == 133
        assert listing.seller_name == "饼住呼吸"
        assert listing.seller_risk["risk_level"] == "低"
```

`tests/test_db.py` 追加：

```python
def test_migrate_adds_seller_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {row[1] for row in session.execute(text("PRAGMA table_info(listings)"))}
    assert {"seller_uid", "seller_name", "seller_risk"} <= cols
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py` 新增 `Seller` 模型：

```python
class Seller(Base):
    __tablename__ = "sellers"
    __table_args__ = (
        UniqueConstraint("platform", "seller_uid", name="uq_seller_platform_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    seller_uid: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    positive_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    positive_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```

`Listing` 追加三列：

```python
    seller_uid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    seller_risk: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

`goodprice/db.py` 的 `migrate_schema` 的 `listings` 列表追加：

```python
            ("seller_uid", "seller_uid TEXT"),
            ("seller_name", "seller_name TEXT"),
            ("seller_risk", "seller_risk JSON"),
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 卖家数据表与 Listing 卖家字段"
```

## Task 3: 详情页卖家区块解析

**Files:**
- Modify: `goodprice/crawler/base.py`, `goodprice/crawler/selectors.py`, `goodprice/crawler/parser.py`, `tests/fixtures/xianyu_detail.html`
- Test: `tests/test_crawler_parser.py`

- [ ] **Step 1: 写失败测试与 fixture 更新**

`tests/fixtures/xianyu_detail.html` 的详情容器内追加卖家区块：

```html
    <a class="item-user-info-container--x" href="https://www.goofish.com/personal?userId=2672367114">
      <span class="item-user-info-nick--rtpDhkmQ">饼住呼吸</span>
      <span class="item-user-info-text--tKOlwunK">河源</span>
      <span class="item-user-info-text--tKOlwunK">卖出264件宝贝</span>
      <span class="item-user-info-text--tKOlwunK">好评率100%</span>
    </a>
    <div class="credit-container--w3dcSvoi"><span class="gradient-image-text--YUZj27iZ">卖家信用极好</span></div>
```

`tests/test_crawler_parser.py` 的 `test_parse_detail_html` 追加断言：

```python
    assert detail.seller_uid == "2672367114"
    assert detail.seller_name == "饼住呼吸"
    assert detail.credit_label == "卖家信用极好"
    assert detail.positive_rate == 100.0
    assert detail.sold_count == 264
```

另加 `extract_user_id` 测试：

```python
def test_extract_user_id():
    from goodprice.crawler.parser import extract_user_id

    assert extract_user_id("https://www.goofish.com/personal?userId=2672367114") == "2672367114"
    assert extract_user_id("https://x/other") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/crawler/base.py` 的 `ListingDetail` 扩展：

```python
@dataclass
class ListingDetail:
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    seller_uid: Optional[str] = None
    seller_name: Optional[str] = None
    credit_label: Optional[str] = None
    positive_rate: Optional[float] = None
    sold_count: Optional[int] = None
```

`goodprice/crawler/selectors.py` 追加：

```python
# 商品详情页卖家区块（实测：链接 /personal?userId=，昵称 item-user-info-nick--，信用 credit-container--）
DETAIL_SELLER_LINK = "a[href*='/personal?userId=']"
DETAIL_SELLER_NICK = "[class*='item-user-info-nick--']"
DETAIL_CREDIT_LABEL = "[class*='credit-container--']"
```

`goodprice/crawler/parser.py` 追加：

```python
def extract_user_id(href: str) -> Optional[str]:
    match = re.search(r"userId=([^&]+)", href or "")
    return match.group(1) if match else None


def _parse_seller_block(seller_link) -> tuple:
    if seller_link is None:
        return None, None, None, None
    block_text = seller_link.get_text(" ", strip=True)
    first_line = seller_link.get_text("\n", strip=True).splitlines()
    name = first_line[0] if first_line else None
    positive_rate = None
    sold_count = None
    m = re.search(r"好评率\s*([\d.]+)%", block_text)
    if m:
        positive_rate = float(m.group(1))
    m = re.search(r"卖出\s*(\d+)\s*件", block_text)
    if m:
        sold_count = int(m.group(1))
    return name, positive_rate, sold_count, block_text
```

`parse_detail_html` 返回前追加：

```python
    seller_link = soup.select_one(sel.DETAIL_SELLER_LINK)
    seller_uid = extract_user_id(seller_link.get("href")) if seller_link else None
    seller_name, positive_rate, sold_count, _ = _parse_seller_block(seller_link)
    nick_el = soup.select_one(sel.DETAIL_SELLER_NICK)
    if nick_el:
        seller_name = nick_el.get_text(strip=True) or seller_name
    credit_el = soup.select_one(sel.DETAIL_CREDIT_LABEL)
    credit_label = credit_el.get_text(strip=True) if credit_el else None
    return ListingDetail(
        description=desc[:2000],
        image_urls=images[:8],
        seller_uid=seller_uid,
        seller_name=seller_name,
        credit_label=credit_label,
        positive_rate=positive_rate,
        sold_count=sold_count,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 详情页卖家区块解析（uid/昵称/信用/好评率/卖出）"
```

## Task 4: 卖家主页抓取与解析

**Files:**
- Modify: `goodprice/crawler/base.py`, `goodprice/crawler/parser.py`, `goodprice/crawler/xianyu.py`
- Create: `tests/fixtures/xianyu_seller.html`
- Test: `tests/test_crawler_parser.py`, `tests/test_crawler_xianyu.py`

- [ ] **Step 1: 写失败测试与 fixture**

`tests/fixtures/xianyu_seller.html`：

```html
<!DOCTYPE html>
<html>
<body>
  <div class="personal-container--x">
    <span class="creditTag--zBHV2NaK">信用及评价 194</span>
    <span>全部</span>
    <span>有图 7</span>
    <span>好评 133</span>
    <span>来自买家 115</span>
    <span>来自卖家 79</span>
    <span>沟通愉快 13</span>
    <span>收货快 11</span>
    <span>回复快 10</span>
  </div>
</body>
</html>
```

`tests/test_crawler_parser.py` 追加：

```python
from goodprice.crawler.parser import parse_seller_html

SELLER_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_seller.html"


def test_parse_seller_html():
    data = parse_seller_html(SELLER_FIXTURE.read_text(encoding="utf-8"), "2672367114")
    assert data.seller_uid == "2672367114"
    assert data.positive_count == 133
    assert data.total_count == 194
    assert any("沟通愉快 13" in t for t in data.tags)
```

`tests/test_crawler_xianyu.py` 追加：

```python
SELLER_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_seller.html"


def test_fetch_seller_parses_page():
    html = SELLER_FIXTURE.read_text(encoding="utf-8")
    adapter, _ = _adapter(FakePage(html))
    data = adapter.fetch_seller("2672367114")
    assert data.positive_count == 133
    assert data.total_count == 194
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawler_xianyu.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/crawler/base.py` 追加：

```python
@dataclass
class SellerData:
    seller_uid: str
    nickname: str = ""
    positive_count: Optional[int] = None
    total_count: Optional[int] = None
    tags: list[str] = field(default_factory=list)
```

`goodprice/crawler/parser.py` 追加：

```python
_TAG_NAMES = ("沟通愉快", "收货快", "回复快", "下单爽快", "描述真实", "发货快")


def parse_seller_html(html: str, seller_uid: str) -> SellerData:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.get_text("\n", strip=True)
    positive = None
    total = None
    m = re.search(r"好评\s*(\d+)", body)
    if m:
        positive = int(m.group(1))
    m = re.search(r"信用及评价\s*(\d+)", body)
    if m:
        total = int(m.group(1))
    tags = []
    for tag in _TAG_NAMES:
        m = re.search(re.escape(tag) + r"\s*(\d+)", body)
        if m:
            tags.append(f"{tag} {m.group(1)}")
    return SellerData(seller_uid=seller_uid, positive_count=positive, total_count=total, tags=tags)
```

`goodprice/crawler/xianyu.py` 追加：

```python
from goodprice.crawler.base import CrawlerAuthError, ListingData, ListingDetail, SellerData
from goodprice.crawler.parser import parse_detail_html, parse_search_html, parse_seller_html


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
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawler_xianyu.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 卖家主页抓取与评价文本解析"
```

## Task 5: 卖家缓存服务与风险分级

**Files:**
- Create: `goodprice/services/seller_service.py`
- Test: `tests/test_seller_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_seller_service.py`：

```python
from datetime import datetime, timedelta

from goodprice.crawler.base import SellerData
from goodprice.models import Seller
from goodprice.services.seller_service import SellerService, compute_risk


class FakeSellerAdapter:
    def __init__(self, data=None, error=None):
        self.data = data or SellerData(seller_uid="1", positive_count=133, total_count=194, tags=["沟通愉快 13"])
        self.error = error
        self.calls = 0

    def fetch_seller(self, user_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.data


def test_fetch_and_cache(session_factory):
    adapter = FakeSellerAdapter()
    service = SellerService(session_factory, adapter=adapter)
    seller = service.ensure_fresh("xianyu", "1", nickname="饼住呼吸")
    assert seller.positive_count == 133
    assert seller.positive_rate == pytest.approx(133 / 194)
    seller2 = service.ensure_fresh("xianyu", "1")
    assert adapter.calls == 1  # 7 天内不重复抓
    assert seller2 is not None


def test_refetch_after_cache_expiry(session_factory):
    adapter = FakeSellerAdapter()
    service = SellerService(session_factory, adapter=adapter)
    service.ensure_fresh("xianyu", "1")
    with session_factory() as session:
        seller = session.query(Seller).one()
        seller.last_fetched_at = datetime.now() - timedelta(days=8)
        session.commit()
    service.ensure_fresh("xianyu", "1")
    assert adapter.calls == 2


def test_fetch_failure_returns_existing(session_factory):
    adapter = FakeSellerAdapter(error=RuntimeError("网络错误"))
    service = SellerService(session_factory, adapter=adapter)
    assert service.ensure_fresh("xianyu", "1") is None
    with session_factory() as session:
        assert session.query(Seller).count() == 0


def test_compute_risk_rules():
    from types import SimpleNamespace

    low = SimpleNamespace(positive_rate=0.99, positive_count=99, total_count=100, credit_label="")
    mid = SimpleNamespace(positive_rate=0.92, positive_count=92, total_count=100, credit_label="")
    high = SimpleNamespace(positive_rate=0.8, positive_count=80, total_count=100, credit_label="")
    assert compute_risk(low)[0] == "低"
    assert compute_risk(mid)[0] == "中"
    assert compute_risk(high)[0] == "高"
    assert compute_risk(None, credit_label="卖家信用极好")[0] == "低"
    assert compute_risk(None)[0] == "未知"


def test_compute_risk_prefers_detail_rate():
    from types import SimpleNamespace

    seller = SimpleNamespace(positive_rate=0.68, positive_count=133, total_count=194, credit_label="")
    assert compute_risk(seller, detail_rate=100.0)[0] == "低"
```

`tests/test_seller_service.py` 顶部补 `import pytest`。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_seller_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/services/seller_service.py`：

```python
import logging
from datetime import datetime, timedelta
from typing import Optional

from goodprice.models import Seller

logger = logging.getLogger(__name__)
CACHE_DAYS = 7


def compute_risk(seller: Optional[Seller], credit_label: Optional[str] = None, detail_rate: Optional[float] = None):
    """返回 (风险等级, 一句话理由)。只提示不拦截。"""
    rate = detail_rate if detail_rate is not None else (seller.positive_rate if seller else None)
    label = credit_label or (seller.credit_label if seller else "")
    if rate is not None:
        pct = rate * 100
        if rate >= 0.98:
            return "低", f"好评率 {pct:.0f}%"
        if rate >= 0.90:
            return "中", f"好评率 {pct:.0f}%"
        return "高", f"好评率 {pct:.0f}%"
    if label:
        if "极好" in label:
            return "低", label
        if "良好" in label or label.endswith("好"):
            return "中", label
        return "高", label
    if seller and seller.positive_count is not None and seller.total_count:
        pct = seller.positive_count / seller.total_count * 100
        if pct >= 98:
            return "低", f"好评 {seller.positive_count}/{seller.total_count}"
        if pct >= 90:
            return "中", f"好评 {seller.positive_count}/{seller.total_count}"
        return "高", f"好评 {seller.positive_count}/{seller.total_count}"
    return "未知", "卖家数据不足"


class SellerService:
    def __init__(self, session_factory, adapter=None):
        self._session_factory = session_factory
        self.adapter = adapter

    def get(self, platform: str, seller_uid: str) -> Optional[Seller]:
        with self._session_factory() as session:
            return (
                session.query(Seller)
                .filter_by(platform=platform, seller_uid=seller_uid)
                .first()
            )

    def ensure_fresh(self, platform: str, seller_uid: str, nickname: Optional[str] = None) -> Optional[Seller]:
        seller = self.get(platform, seller_uid)
        stale = (
            seller is None
            or seller.last_fetched_at is None
            or datetime.now() - seller.last_fetched_at > timedelta(days=CACHE_DAYS)
        )
        if not stale or self.adapter is None:
            return seller
        try:
            data = self.adapter.fetch_seller(seller_uid)
        except Exception as exc:
            logger.warning("卖家 %s 数据抓取失败: %s", seller_uid, exc)
            return seller
        with self._session_factory() as session:
            seller = (
                session.query(Seller)
                .filter_by(platform=platform, seller_uid=seller_uid)
                .first()
            )
            if seller is None:
                seller = Seller(platform=platform, seller_uid=seller_uid)
                session.add(seller)
            if data.nickname:
                seller.nickname = data.nickname
            elif nickname:
                seller.nickname = nickname
            seller.positive_count = data.positive_count
            seller.total_count = data.total_count
            seller.tags = data.tags
            if data.positive_count is not None and data.total_count:
                seller.positive_rate = data.positive_count / data.total_count
            seller.last_fetched_at = datetime.now()
            session.commit()
            session.refresh(seller)
            return seller
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_seller_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 卖家缓存服务与风险分级"
```

## Task 6: 流水线接入阶段三 + 展示

**Files:**
- Modify: `goodprice/services/crawl_service.py`, `goodprice/main.py`, `goodprice/web/templates/progress.html`, `goodprice/web/templates/listings_grid.html`
- Test: `tests/test_crawl_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_crawl_service.py` 追加：

```python
from goodprice.crawler.base import SellerData
from goodprice.services.seller_service import SellerService


class SellerFakeAdapter(FakeAdapter):
    def __init__(self, items=None, seller_data=None, seller_error=None):
        super().__init__(items=items)
        self.seller_data = seller_data or SellerData(
            seller_uid="2672367114", positive_count=133, total_count=194, tags=["沟通愉快 13"]
        )
        self.seller_error = seller_error
        self.seller_calls = 0

    def fetch_detail(self, url):
        from goodprice.crawler.base import ListingDetail

        return ListingDetail(
            description="屏幕完好",
            image_urls=["https://x/d.jpg"],
            seller_uid="2672367114",
            seller_name="饼住呼吸",
            credit_label="卖家信用极好",
            positive_rate=100.0,
            sold_count=264,
        )

    def fetch_seller(self, user_id):
        self.seller_calls += 1
        if self.seller_error:
            raise self.seller_error
        return self.seller_data


def _service_with_seller(session_factory, base_settings, adapter, **kwargs):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = FakeNotifier()
    seller_service = SellerService(session_factory, adapter=adapter)
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=kwargs.get("llm") or FakeLLM(),
        vision=kwargs.get("vision") if "vision" in kwargs else FakeVision(),
        notifiers=[("log", notifier)],
        settings_service=settings_service,
        seller_service=seller_service,
    )
    return crawl, notifier


def test_seller_advisory_in_notification_and_cache(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    assert "卖家" in notifier.messages[0].content
    assert "低" in notifier.messages[0].content
    crawl.run_task(task.id)
    assert adapter.seller_calls == 1  # 缓存命中
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_uid == "2672367114"
        assert listing.seller_risk["risk_level"] == "低"


def test_seller_fetch_failure_does_not_block(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()], seller_error=RuntimeError("主页超时"))
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_risk["risk_level"] == "低"  # 详情页好评率 100% 仍可用


def test_no_seller_uid_skips_seller_stage(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = FakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_risk is None
```

`_service` 辅助函数补 `seller_service=None` 参数并传入 `CrawlService`。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/services/crawl_service.py`：

- `__init__` 增加 `seller_service=None` 参数。
- `_fetch_detail` 末尾追加卖家字段落库：

```python
        if detail.seller_uid:
            listing.seller_uid = detail.seller_uid
            listing.seller_name = detail.seller_name or listing.seller_name
            listing.seller_risk = {
                "credit_label": detail.credit_label,
                "positive_rate": detail.positive_rate,
                "sold_count": detail.sold_count,
            }
```

- 新增 `_seller_check`：

```python
    def _seller_check(self, session, listing: Listing, task: WatchTask) -> None:
        if not listing.seller_uid or self.seller_service is None:
            return
        seller = self.seller_service.ensure_fresh(
            task.platform, listing.seller_uid, nickname=listing.seller_name
        )
        from goodprice.services.seller_service import compute_risk

        raw = dict(listing.seller_risk or {})
        level, reason = compute_risk(
            seller,
            credit_label=raw.get("credit_label"),
            detail_rate=raw.get("positive_rate"),
        )
        raw["risk_level"] = level
        raw["risk_reason"] = reason
        if seller is not None:
            raw["positive_count"] = seller.positive_count
            raw["total_count"] = seller.total_count
            raw["tags"] = seller.tags or []
        raw["nickname"] = listing.seller_name or (seller.nickname if seller else None)
        listing.seller_risk = raw
```

- `is_new` 分支中品相门槛判断之后、通知之前调用 `self._seller_check(session, listing, task)`。
- `_notify` 内容追加卖家行：

```python
        seller_line = ""
        if listing.seller_risk:
            risk = listing.seller_risk
            name = risk.get("nickname") or "卖家"
            level = risk.get("risk_level")
            reason = risk.get("risk_reason") or ""
            rate = risk.get("positive_rate")
            rate_txt = f"好评率 {rate:.0f}%" if isinstance(rate, (int, float)) else ""
            seller_line = f"卖家：{name} {rate_txt} · 风险{level}（{reason}）\n"
```

`goodprice/main.py`：

```python
from goodprice.services.seller_service import SellerService
...
    seller_service = SellerService(session_factory, adapter=adapter)
    ...
    return CrawlService(
        ...,
        seller_service=seller_service,
    )
```

`goodprice/web/templates/progress.html` 与 `goodprice/web/templates/listings_grid.html` 的商品行追加风险徽标：

```html
{% if item.seller_risk and item.seller_risk.risk_level %}
  {% set lv = item.seller_risk.risk_level %}
  <span class="px-1.5 py-0.5 rounded text-xs {% if lv == '低' %}bg-green-100 text-green-700{% elif lv == '中' %}bg-yellow-100 text-yellow-700{% elif lv == '高' %}bg-red-100 text-red-700{% else %}bg-gray-200 text-gray-600{% endif %}">卖家{{ lv }}</span>
{% endif %}
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 流水线接入卖家信用阶段（提示不拦截）+ 展示"
```

## Task 7: README 与全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README**

补充：群机器人 webhook 配置步骤、卖家信用/评价说明（数据来源、7 天缓存、风险分级规则、只提示不拦截）。

- [ ] **Step 2: 全量验证**

Run: `conda run -n good-price pytest -v`
Expected: 全部通过，0 failed。

- [ ] **Step 3: 启动冒烟**

Run: `conda run -n good-price python -c "from goodprice.main import app; print(app.title)"`
Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "docs: 第三轮 README（群机器人、卖家信用）"
```

---

## 验收清单

- [ ] 群机器人通道测试全绿（成功/93000/93004/未配置禁用）
- [ ] 详情页卖家区块解析与卖家主页解析测试全绿
- [ ] 缓存（7 天）与风险分级测试全绿；抓取失败不拦截
- [ ] 全量 `pytest` 通过；`python -m goodprice` 可启动
