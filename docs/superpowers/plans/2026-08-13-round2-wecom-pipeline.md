# 第二轮：企业微信通知 + 分阶段筛选链路 + 稳定性修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增企业微信应用消息通知通道；筛选改为"需求匹配（文本）→ 视觉品相（看图）"两阶段流水线；修复页面与执行稳定性问题。

**Architecture:** 在现有单进程 FastAPI + APScheduler + SQLite 架构内扩展：notify 新增 WeCom 通道；CrawlService 拆分需求/品相两阶段并引入防重入守卫；新增详情页抓取与幂等迁移。

**Tech Stack:** 沿用 Python 3.11（conda `good-price`）、FastAPI、SQLAlchemy、APScheduler、Playwright、httpx、pytest。

---

## Task 1: 配置与设置扩展

**Files:**
- Modify: `goodprice/config.py`, `goodprice/services/settings_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/settings.html`, `.env.example`
- Test: `tests/test_config.py`, `tests/test_settings_service.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py` 追加：

```python
def test_round2_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.vision_model == "qwen-vl-max"
    assert settings.vision_base_url == ""
    assert settings.wecom_touser == "@all"
    assert settings.wecom_corpid == ""


def test_round2_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("WECOM_CORPID", "ww123")
    monkeypatch.setenv("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    settings = Settings(_env_file=None)
    assert settings.wecom_corpid == "ww123"
    assert settings.vision_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

`tests/test_settings_service.py` 追加：

```python
def test_round2_settings_persist(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"wecom_corpid": "ww123", "wecom_touser": "@all", "vision_model": "glm-4v-flash"})
    settings = service.get()
    assert settings.wecom_corpid == "ww123"
    assert settings.vision_model == "glm-4v-flash"
    assert settings.wecom_touser == "@all"
```

`tests/test_api.py` 追加：

```python
def test_settings_secret_empty_keeps_old_value(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post("/settings", data={**_settings_form(), "llm_api_key": "secret1"})
    client.post("/settings", data={**_settings_form(), "llm_api_key": ""})
    assert client.app.state.settings_service.get().llm_api_key == "secret1"


def test_settings_save_wecom(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post("/settings", data={**_settings_form(), "wecom_corpid": "ww123", "wecom_secret": "sec"})
    settings = client.app.state.settings_service.get()
    assert settings.wecom_corpid == "ww123"
    assert settings.wecom_secret == "sec"


def _settings_form():
    return {
        "xianyu_cookie": "",
        "llm_base_url": "",
        "llm_api_key": "",
        "llm_model": "qwen-vl-max",
        "serverchan_sendkey": "",
        "proxy": "",
        "default_crawl_interval_minutes": "20",
        "default_crawl_jitter_minutes": "10",
        "vision_base_url": "",
        "vision_api_key": "",
        "vision_model": "",
        "wecom_corpid": "",
        "wecom_agentid": "",
        "wecom_secret": "",
        "wecom_touser": "@all",
    }
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_config.py tests/test_settings_service.py tests/test_api.py -v`
Expected: 新测试 FAIL（字段不存在）

- [ ] **Step 3: 实现**

`goodprice/config.py` 的 `Settings` 追加字段：

```python
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "qwen-vl-max"
    wecom_corpid: str = ""
    wecom_agentid: str = ""
    wecom_secret: str = ""
    wecom_touser: str = "@all"
```

`goodprice/services/settings_service.py` 的 `RuntimeSettings` 追加字段：

```python
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    wecom_corpid: str = ""
    wecom_agentid: str = ""
    wecom_secret: str = ""
    wecom_touser: str = "@all"
```

`goodprice/web/routes.py` 的 `save_settings` 改为：

```python
@router.post("/settings")
def save_settings(
    request: Request,
    xianyu_cookie: str = Form(""),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    serverchan_sendkey: str = Form(""),
    proxy: str = Form(""),
    default_crawl_interval_minutes: int = Form(20),
    default_crawl_jitter_minutes: int = Form(10),
    vision_base_url: str = Form(""),
    vision_api_key: str = Form(""),
    vision_model: str = Form(""),
    wecom_corpid: str = Form(""),
    wecom_agentid: str = Form(""),
    wecom_secret: str = Form(""),
    wecom_touser: str = Form("@all"),
):
    _, settings_service = _services(request)
    values = {
        "xianyu_cookie": xianyu_cookie,
        "llm_base_url": llm_base_url,
        "llm_api_key": llm_api_key,
        "llm_model": llm_model,
        "serverchan_sendkey": serverchan_sendkey,
        "proxy": proxy,
        "default_crawl_interval_minutes": str(default_crawl_interval_minutes),
        "default_crawl_jitter_minutes": str(default_crawl_jitter_minutes),
        "vision_base_url": vision_base_url,
        "vision_api_key": vision_api_key,
        "vision_model": vision_model,
        "wecom_corpid": wecom_corpid,
        "wecom_agentid": wecom_agentid,
        "wecom_secret": wecom_secret,
        "wecom_touser": wecom_touser,
    }
    for key in ("llm_api_key", "serverchan_sendkey", "vision_api_key", "wecom_secret"):
        if values.get(key) == "":
            values.pop(key)  # 留空 = 保持原值
    settings_service.set_many(values)
    return RedirectResponse("/settings", status_code=303)
```

`goodprice/web/templates/settings.html` 在 LLM 区块后追加视觉模型与企微区块，密钥输入改为 `value="" placeholder="已配置（留空保持不变）"`：

```html
  <div class="border-t pt-4">
    <h3 class="text-md font-semibold mb-3">视觉模型（阶段二看图，必填才做品相分析）</h3>
    <div class="grid grid-cols-2 gap-3">
      <div><label class="block text-sm font-medium mb-1">Vision Base URL</label>
        <input name="vision_base_url" class="w-full border rounded px-3 py-2" value="{{ settings.vision_base_url }}" placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1"></div>
      <div><label class="block text-sm font-medium mb-1">Vision API Key</label>
        <input name="vision_api_key" type="password" class="w-full border rounded px-3 py-2" value="" placeholder="已配置（留空保持不变）"></div>
      <div><label class="block text-sm font-medium mb-1">Vision 模型</label>
        <input name="vision_model" class="w-full border rounded px-3 py-2" value="{{ settings.vision_model }}" placeholder="如 qwen-vl-max / glm-4v-flash"></div>
    </div>
  </div>
  <div class="border-t pt-4">
    <h3 class="text-md font-semibold mb-3">企业微信（应用消息推送）</h3>
    <div class="grid grid-cols-2 gap-3">
      <div><label class="block text-sm font-medium mb-1">CorpID</label>
        <input name="wecom_corpid" class="w-full border rounded px-3 py-2" value="{{ settings.wecom_corpid }}"></div>
      <div><label class="block text-sm font-medium mb-1">AgentID</label>
        <input name="wecom_agentid" class="w-full border rounded px-3 py-2" value="{{ settings.wecom_agentid }}"></div>
      <div><label class="block text-sm font-medium mb-1">Secret</label>
        <input name="wecom_secret" type="password" class="w-full border rounded px-3 py-2" value="" placeholder="已配置（留空保持不变）"></div>
      <div><label class="block text-sm font-medium mb-1">接收人 Touser</label>
        <input name="wecom_touser" class="w-full border rounded px-3 py-2" value="{{ settings.wecom_touser }}" placeholder="@all 或成员 userid"></div>
    </div>
    <p class="text-xs text-gray-500 mt-1">应用消息需在企业微信后台把本机出口 IP 加入"企业可信 IP"。</p>
  </div>
```

同时把 `settings.html` 中 `llm_api_key`、`serverchan_sendkey` 的 `value="{{ ... }}"` 改为 `value=""` 并加同样 placeholder。

`.env.example` 追加：

```dotenv
# 视觉模型（阶段二看图；不填则跳过品相分析）
VISION_BASE_URL=
VISION_API_KEY=
VISION_MODEL=qwen-vl-max

# 企业微信应用消息（https://work.weixin.qq.com）
WECOM_CORPID=
WECOM_AGENTID=
WECOM_SECRET=
WECOM_TOUSER=@all
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_config.py tests/test_settings_service.py tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 视觉模型与企业微信配置项 + 密钥留空保持原值"
```

## Task 2: 数据模型新列与幂等迁移

**Files:**
- Modify: `goodprice/models.py`, `goodprice/db.py`, `goodprice/main.py`
- Test: `tests/test_models.py`, `tests/test_db.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py` 追加：

```python
def test_round2_model_columns(session_factory):
    with session_factory() as session:
        task = WatchTask(keyword="k")
        session.add(task)
        session.flush()
        listing = Listing(platform="xianyu", external_id="1", title="t", price=1.0, url="u", description="d")
        session.add(listing)
        session.commit()
        assert task.fetch_detail is True
        assert listing.description == "d"
        assert listing.requirement_match is None
        assert listing.requirement_reason is None
```

新建 `tests/test_db.py`：

```python
from sqlalchemy import create_engine, text

from goodprice.db import make_session_factory, migrate_schema


def test_migrate_adds_new_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE watch_tasks (id INTEGER PRIMARY KEY, keyword TEXT)"))
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT, title TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        task_cols = {row[1] for row in session.execute(text("PRAGMA table_info(watch_tasks)"))}
        listing_cols = {row[1] for row in session.execute(text("PRAGMA table_info(listings)"))}
    assert "fetch_detail" in task_cols
    assert {"description", "requirement_match", "requirement_reason"} <= listing_cols


def test_migrate_is_idempotent(session_factory):
    migrate_schema(session_factory)
    migrate_schema(session_factory)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py`：`WatchTask` 追加 `fetch_detail: Mapped[bool] = mapped_column(default=True)`；`Listing` 追加：

```python
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requirement_match: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    requirement_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

（`from sqlalchemy import Boolean` 加入导入。）

`goodprice/db.py` 追加：

```python
from sqlalchemy import text


def migrate_schema(session_factory) -> None:
    """幂等迁移：为已有数据库补齐新列。"""
    columns = {
        "watch_tasks": [("fetch_detail", "fetch_detail BOOLEAN DEFAULT 1")],
        "listings": [
            ("description", "description TEXT"),
            ("requirement_match", "requirement_match BOOLEAN"),
            ("requirement_reason", "requirement_reason TEXT"),
        ],
    }
    with session_factory() as session:
        for table, cols in columns.items():
            existing = {row[1] for row in session.execute(text(f"PRAGMA table_info({table})"))}
            for col, ddl in cols:
                if col not in existing:
                    session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        session.commit()
```

`goodprice/main.py` 的 `build_app` 中 `Base.metadata.create_all(...)` 之后调用 `migrate_schema(session_factory)`。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 详情/需求/详情开关字段 + 幂等数据库迁移"
```

## Task 3: 详情页抓取

**Files:**
- Modify: `goodprice/crawler/base.py`, `goodprice/crawler/selectors.py`, `goodprice/crawler/parser.py`, `goodprice/crawler/xianyu.py`
- Test: `tests/test_crawler_parser.py`, `tests/test_crawler_xianyu.py`, 新增 `tests/fixtures/xianyu_detail.html`

- [ ] **Step 1: 写失败测试与 fixture**

`tests/fixtures/xianyu_detail.html`：

```html
<!DOCTYPE html>
<html>
<body>
  <div class="detail-container--abc">
    <span class="desc--GaIUKUQY">
      <span><span>iPhone 13 128G 蓝色</span></span><br>
      <span><span>屏幕完好 电池健康 无拆修</span></span><br>
      <span><span>带原装盒 配件齐全</span></span>
    </span>
    <img class="ant-image-img css-ab" src="//img.alicdn.com/d1.jpg">
    <img class="ant-image-img css-ab" src="//img.alicdn.com/d2.jpg">
    <img class="ant-image-img css-ab" src="https://img.alicdn.com/d1.jpg">
    <div class="price-desc--hxYyq3i3">10人想要</div>
    <img class="feeds-image--TDRC4fV1" src="//img.alicdn.com/rel.jpg">
  </div>
</body>
</html>
```

`tests/test_crawler_parser.py` 追加：

```python
from goodprice.crawler.parser import parse_detail_html

DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail.html"


def test_parse_detail_html():
    detail = parse_detail_html(DETAIL_FIXTURE.read_text(encoding="utf-8"))
    assert "屏幕完好" in detail.description
    assert "带原装盒" in detail.description
    assert detail.image_urls == ["https://img.alicdn.com/d1.jpg", "https://img.alicdn.com/d2.jpg"]
```

`tests/test_crawler_xianyu.py` 追加：

```python
from goodprice.crawler.parser import parse_detail_html  # noqa: F401

DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail.html"


def test_fetch_detail_parses_page():
    html = DETAIL_FIXTURE.read_text(encoding="utf-8")
    adapter, playwright = _adapter(FakePage(html))
    detail = adapter.fetch_detail("https://www.goofish.com/item?id=1001")
    assert "屏幕完好" in detail.description
    assert len(detail.image_urls) == 2
    assert playwright.browser.context.cookies
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawler_xianyu.py -v`
Expected: FAIL（parse_detail_html 不存在）

- [ ] **Step 3: 实现**

`goodprice/crawler/base.py` 追加：

```python
@dataclass
class ListingDetail:
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
```

`goodprice/crawler/selectors.py` 追加：

```python
# 商品详情页（实测：描述 span[class*='desc--']，主图 img.ant-image-img）
DETAIL_DESC = "span[class*='desc--']"
DETAIL_IMAGE = "img[class*='ant-image-img']"
```

`goodprice/crawler/parser.py` 追加：

```python
from goodprice.crawler.base import ListingDetail


def parse_detail_html(html: str) -> ListingDetail:
    soup = BeautifulSoup(html, "html.parser")
    desc = ""
    desc_el = soup.select_one(sel.DETAIL_DESC)
    if desc_el:
        desc = desc_el.get_text(" ", strip=True)
    if not desc:
        for el in soup.select("[class*='desc--']"):
            text = el.get_text(" ", strip=True)
            if len(text) > len(desc) and "想要" not in text and not text.startswith("¥"):
                desc = text
    images: list[str] = []
    for img in soup.select(sel.DETAIL_IMAGE):
        src = img.get("src")
        if src:
            url = _absolute(src)
            if url not in images:
                images.append(url)
    return ListingDetail(description=desc[:2000], image_urls=images[:8])
```

`goodprice/crawler/xianyu.py` 追加：

```python
from goodprice.crawler.base import CrawlerAuthError, ListingData, ListingDetail
from goodprice.crawler.parser import parse_search_html, parse_detail_html


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
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawler_xianyu.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 闲鱼商品详情页抓取（描述+主图）"
```

## Task 4: LLM 客户端拆分

**Files:**
- Modify: `goodprice/analysis/prompts.py`, `goodprice/analysis/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: 写失败测试**

`tests/test_llm.py` 更新与追加：

```python
def test_analyze_condition_returns_verdict():  # 原 test_analyze_listing_returns_verdict 改名
    ...

def test_analyze_condition_disabled_without_config():
    client = LLMClient(base_url="", api_key="", model="")
    assert client.enabled is False
    with pytest.raises(RuntimeError):
        client.analyze_condition("t", 1)


def test_analyze_requirement_returns_verdict():
    def handler(request):
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        assert all(item.get("type") == "text" for item in content)  # 纯文本
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"matched": true, "reason": "屏幕完好，符合要求"}'}}]},
        )

    verdict = _client(handler).analyze_requirement("iPhone 13", "屏幕完好", "屏幕完好")
    assert verdict == {"matched": True, "reason": "屏幕完好，符合要求"}


def test_parse_requirement_requires_bool():
    from goodprice.analysis.llm import parse_requirement_json

    with pytest.raises(ValueError):
        parse_requirement_json('{"matched": "yes", "reason": "x"}')


def test_vision_no_text_fallback_when_disabled():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "unknown variant `image_url`"}})

    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        transport=httpx.MockTransport(handler),
        allow_image_fallback=False,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert len(calls) == 1  # 视觉强依赖：不做纯文本降级
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_llm.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/analysis/prompts.py`：现有 `SYSTEM_PROMPT` 改名 `CONDITION_SYSTEM_PROMPT`、`USER_PROMPT_TEMPLATE` 改名 `CONDITION_USER_TEMPLATE`，并追加：

```python
REQUIREMENT_SYSTEM_PROMPT = (
    "你是二手商品筛选助手。用户给出商品标题、卖家描述和买家需求，"
    "请判断商品是否满足买家的硬性需求。只输出 JSON："
    '{"matched": true或false, "reason": "一句话理由"}'
)

REQUIREMENT_USER_TEMPLATE = (
    "商品标题：{title}\n"
    "卖家描述：{description}\n"
    "买家需求：{requirement}\n"
    "请给出 JSON 结论。"
)
```

`goodprice/analysis/llm.py` 重构：

```python
from goodprice.analysis.prompts import (
    CONDITION_SYSTEM_PROMPT,
    CONDITION_USER_TEMPLATE,
    REQUIREMENT_SYSTEM_PROMPT,
    REQUIREMENT_USER_TEMPLATE,
)


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中没有 JSON: {raw!r}")
    return json.loads(text[start : end + 1])


def parse_analysis_json(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    score = max(1, min(10, int(data.get("condition_score", 0))))
    defects = [str(d) for d in data.get("defects", [])][:10]
    return {
        "condition_score": score,
        "defects": defects,
        "recommended": bool(data.get("recommended", False)),
        "reason": str(data.get("reason", ""))[:500],
    }


def parse_requirement_json(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    matched = data.get("matched")
    if not isinstance(matched, bool):
        raise ValueError(f"需求判断输出缺少布尔 matched: {raw!r}")
    return {"matched": matched, "reason": str(data.get("reason", ""))[:500]}
```

`LLMClient`：

```python
    def __init__(self, base_url, api_key, model, timeout=60.0, transport=None, allow_image_fallback=True):
        ...
        self.allow_image_fallback = allow_image_fallback

    def analyze_requirement(self, title, description="", requirement="") -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        text = REQUIREMENT_USER_TEMPLATE.format(
            title=title, description=description or "无", requirement=requirement or "无"
        )
        return self._complete(
            self._payload([{"type": "text", "text": text}], system=REQUIREMENT_SYSTEM_PROMPT),
            parser=parse_requirement_json,
        )

    def analyze_condition(
        self, title, price, description="", requirement="", image_urls=None
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        image_urls = image_urls or []
        text = CONDITION_USER_TEMPLATE.format(
            title=title,
            price=price,
            description=description or "无",
            requirement=requirement or "无",
            image_count=len(image_urls),
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls[:4])
        try:
            return self._complete(self._payload(content))
        except httpx.HTTPStatusError as exc:
            error_text = (exc.response.text or "").lower()
            image_rejected = any(marker in error_text for marker in _IMAGE_ERROR_MARKERS)
            if (
                image_urls
                and image_rejected
                and exc.response.status_code in (400, 422)
                and self.allow_image_fallback
            ):
                logger.info("模型不支持图片输入，降级为纯文本分析（%s）", exc.response.status_code)
                fallback_text = f"{text}\n（注：当前模型不支持图片输入，本次仅依据文字信息判断品相）"
                return self._complete(self._payload([{"type": "text", "text": fallback_text}]))
            raise

    def _payload(self, content, system=CONDITION_SYSTEM_PROMPT) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }

    def _complete(self, payload, parser=parse_analysis_json) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = httpx.Client(transport=self._transport, timeout=self.timeout)
        response = client.post(
            f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return parser(raw)
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: LLM 客户端拆分需求匹配/品相分析，视觉强依赖开关"
```

## Task 5: 企业微信应用消息通知通道

**Files:**
- Create: `goodprice/notify/wecom.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: 写失败测试**

`tests/test_notify.py` 追加：

```python
from goodprice.notify.wecom import WeComNotifier


def _wecom(handler, **kwargs):
    return WeComNotifier(
        corpid="ww123", agentid="1000002", secret="sec", touser="@all",
        transport=httpx.MockTransport(handler), **kwargs,
    )


def _route(request):
    if "/gettoken" in str(request.url):
        return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
    return httpx.Response(200, json={"errcode": 0})


def test_wecom_send_success():
    captured = {}

    def handler(request):
        if "/gettoken" in str(request.url):
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    notifier = _wecom(handler)
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["body"]["touser"] == "@all"
    assert captured["body"]["agentid"] == 1000002
    assert captured["body"]["msgtype"] == "text"
    assert "标题" in captured["body"]["text"]["content"]


def test_wecom_refreshes_token_on_40014():
    token_calls = []
    send_calls = []

    def handler(request):
        if "/gettoken" in str(request.url):
            token_calls.append(1)
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        send_calls.append(1)
        if len(send_calls) == 1:
            return httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid token"})
        return httpx.Response(200, json={"errcode": 0})

    _wecom(handler).send(NotificationMessage(title="t", content="c"))
    assert len(token_calls) == 2
    assert len(send_calls) == 2


def test_wecom_60020_raises_clear_error():
    def handler(request):
        if "/gettoken" in str(request.url):
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 60020, "errmsg": "not allow to access from your ip"})

    with pytest.raises(RuntimeError, match="可信 IP"):
        _wecom(handler).send(NotificationMessage(title="t", content="c"))


def test_wecom_disabled_without_config():
    assert WeComNotifier(corpid="", agentid="", secret="").enabled is False
```

`tests/test_notify.py` 顶部补 `import json`。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_notify.py -v`
Expected: FAIL（ModuleNotFoundError: goodprice.notify.wecom）

- [ ] **Step 3: 实现**

`goodprice/notify/wecom.py`：

```python
import logging
import threading
import time
from typing import Optional

import httpx

from goodprice.notify.base import NotificationMessage, Notifier

logger = logging.getLogger(__name__)

GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


class WeComNotifier(Notifier):
    channel = "wecom"

    def __init__(
        self,
        corpid: str = "",
        agentid: str = "",
        secret: str = "",
        touser: str = "@all",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.corpid = corpid
        self.agentid = agentid
        self.secret = secret
        self.touser = touser or "@all"
        self._transport = transport
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.corpid and self.agentid and self.secret)

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self.timeout)

    def _fetch_token(self) -> tuple[str, int]:
        response = self._client().get(
            GET_TOKEN_URL, params={"corpid": self.corpid, "corpsecret": self.secret}
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"企业微信获取 access_token 失败: {data}")
        return data["access_token"], int(data.get("expires_in", 7200))

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            token, expires = self._fetch_token()
            self._token = token
            self._token_expires_at = time.time() + expires
            return token

    def _send_once(self, token: str, content: str) -> dict:
        try:
            agentid = int(self.agentid)
        except (TypeError, ValueError):
            raise RuntimeError("企业微信 agentid 必须为数字")
        response = self._client().post(
            SEND_URL,
            params={"access_token": token},
            json={
                "touser": self.touser,
                "msgtype": "text",
                "agentid": agentid,
                "text": {"content": content},
                "safe": 0,
            },
        )
        response.raise_for_status()
        return response.json()

    def send(self, message: NotificationMessage) -> None:
        if not self.enabled:
            raise RuntimeError("企业微信未配置 corpid/agentid/secret")
        content = f"{message.title}\n{message.content}\n{message.url}"
        token = self._get_token()
        data = self._send_once(token, content)
        if data.get("errcode") in (40014, 42001):
            with self._lock:
                self._token = None
            token = self._get_token()
            data = self._send_once(token, content)
        errcode = data.get("errcode", -1)
        if errcode == 60020:
            raise RuntimeError(
                "企业微信报错：IP 不在可信 IP 列表中，请在企业微信后台把本机出口 IP 加入企业可信 IP"
            )
        if errcode != 0:
            raise RuntimeError(f"企业微信发送失败: {data}")
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_notify.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 企业微信应用消息通知通道"
```

## Task 6: 两阶段流水线 + 防重入 + 回填

**Files:**
- Modify: `goodprice/services/crawl_service.py`, `goodprice/main.py`
- Test: `tests/test_crawl_service.py`

- [ ] **Step 1: 重写失败测试**

`tests/test_crawl_service.py` 全文替换为：

```python
import pytest

from goodprice.crawler.base import CrawlerAuthError, ListingData
from goodprice.models import Listing
from goodprice.services.crawl_service import CrawlService, TaskRunGuard
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


class FakeAdapter:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.fetch_calls = []

    def search(self, keyword):
        if self.error:
            raise self.error
        return self.items

    def fetch_detail(self, url):
        self.fetch_calls.append(url)
        from goodprice.crawler.base import ListingDetail

        return ListingDetail(description="屏幕完好 电池健康", image_urls=["https://x/d.jpg"])


class FakeLLM:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {"matched": True, "reason": "符合需求"}
        self.error = error
        self.calls = []

    def analyze_requirement(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.verdict


class FakeVision:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {
            "condition_score": 8,
            "defects": [],
            "recommended": True,
            "reason": "ok",
        }
        self.error = error
        self.calls = []

    def analyze_condition(self, **kwargs):
        self.calls.append(kwargs)
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


def _service(session_factory, base_settings, adapter=None, llm=None, vision=None, notifier=None):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = notifier or FakeNotifier()
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter or FakeAdapter(),
        llm=llm or FakeLLM(),
        vision=vision if vision is not None else FakeVision(),
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
    assert len(notifier.messages) == 1

    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8
        assert listing.requirement_match is True
        assert listing.description == "屏幕完好 电池健康"
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


def test_requirement_mismatch_blocks_and_skips_vision(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    llm = FakeLLM(verdict={"matched": False, "reason": "描述说后盖碎了"})
    vision = FakeVision()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm, vision=vision)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0
    assert vision.calls == []
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is False
        assert listing.condition_score is None


def test_requirement_empty_skips_stage1(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    crawl.run_task(task.id)
    assert llm.calls == []
    assert len(notifier.messages) == 1


def test_vision_disabled_skips_stage2(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=FakeVision(enabled=False))
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score is None


def test_condition_gate_blocks_low_score(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "min_condition_score": "6"})
    vision = FakeVision(verdict={"condition_score": 3, "defects": ["碎屏"], "recommended": False, "reason": "太差"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=vision)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0


def test_requirement_failure_fails_open(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    llm = FakeLLM(error=RuntimeError("网络错误"))
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is None


def test_fetch_detail_off_skips_call(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "fetch_detail": False})
    adapter = FakeAdapter([_item()])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    assert adapter.fetch_calls == []


def test_fetch_detail_failure_falls_back(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})

    class BrokenAdapter(FakeAdapter):
        def fetch_detail(self, url):
            raise RuntimeError("详情页超时")

    crawl, notifier, _ = _service(session_factory, base_settings, adapter=BrokenAdapter([_item()]))
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1


def test_backfill_fills_missing_analysis_without_renotify(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = FakeAdapter([_item()])
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=adapter, vision=FakeVision(enabled=False))
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1

    crawl2, notifier2, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=FakeVision())
    stats = crawl2.run_task(task.id)
    assert stats["backfilled"] == 1
    assert len(notifier2.messages) == 0
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8


def test_guard_prevents_concurrent_run(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    guard = TaskRunGuard()
    assert guard.try_start(task.id) is True
    assert guard.try_start(task.id) is False
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.guard = guard
    stats = crawl.run_task(task.id)
    assert stats.get("skipped") == "already_running"
    guard.finish(task.id)


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
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -v`
Expected: FAIL（vision/guard 等不存在）

- [ ] **Step 3: 实现**

`goodprice/services/crawl_service.py` 全文替换：

```python
import logging
import random
import threading
import time
from datetime import datetime
from typing import Any, Optional

from goodprice.crawler.base import ListingData
from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask
from goodprice.notify.base import NotificationMessage

logger = logging.getLogger(__name__)


class TaskRunGuard:
    """进程内任务防重入守卫。"""

    def __init__(self):
        self._running: set[int] = set()
        self._lock = threading.Lock()

    def try_start(self, task_id: int) -> bool:
        with self._lock:
            if task_id in self._running:
                return False
            self._running.add(task_id)
            return True

    def finish(self, task_id: int) -> None:
        with self._lock:
            self._running.discard(task_id)

    def running_ids(self) -> set[int]:
        with self._lock:
            return set(self._running)


class CrawlService:
    def __init__(self, session_factory, adapter, llm, vision, notifiers, settings_service, guard=None):
        self._session_factory = session_factory
        self.adapter = adapter
        self.llm = llm
        self.vision = vision
        self.notifiers = notifiers
        self.settings_service = settings_service
        self.guard = guard or TaskRunGuard()

    def run_task(self, task_id: int) -> dict[str, Any]:
        if not self.guard.try_start(task_id):
            return {"found": 0, "new": 0, "notified": 0, "skipped": "already_running"}
        try:
            return self._run_impl(task_id)
        finally:
            self.guard.finish(task_id)

    def _run_impl(self, task_id: int) -> dict[str, Any]:
        stats = {"found": 0, "new": 0, "notified": 0, "backfilled": 0}
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
                listing, is_new = self._upsert_listing(session, task, data)
                if is_new:
                    if task.fetch_detail:
                        self._fetch_detail(session, listing)
                    if not self._requirement_pass(session, listing, task):
                        continue
                    self._condition_analysis(session, listing, task)
                    if (
                        task.min_condition_score
                        and listing.condition_score is not None
                        and listing.condition_score < task.min_condition_score
                    ):
                        continue
                    if listing.notified_at is None:
                        self._notify(session, task, listing)
                        stats["notified"] += 1
                else:
                    if self._backfill(session, listing, task):
                        stats["backfilled"] += 1
            session.commit()
        return stats

    def _upsert_listing(self, session, task: WatchTask, data: ListingData):
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
            return listing, True
        if abs(listing.price - data.price) > 0.001:
            listing.price = data.price
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
        listing.last_seen_at = datetime.now()
        return listing, False

    def _fetch_detail(self, session, listing: Listing) -> None:
        if not listing.url or listing.description:
            return
        try:
            detail = self.adapter.fetch_detail(listing.url)
        except Exception as exc:
            logger.warning("详情抓取失败，退回标题判断: %s", exc)
            return
        if detail.description:
            listing.description = detail.description
        merged = list(listing.image_urls or [])
        for url in detail.image_urls:
            if url not in merged:
                merged.append(url)
        listing.image_urls = merged[:8]

    def _requirement_pass(self, session, listing: Listing, task: WatchTask) -> bool:
        requirement = (task.condition_requirement or "").strip()
        if not requirement or not self.llm.enabled:
            return True
        try:
            verdict = self.llm.analyze_requirement(
                title=listing.title,
                description=listing.description or "",
                requirement=requirement,
            )
        except Exception as exc:
            logger.warning("需求分析失败，不拦截: %s", exc)
            listing.requirement_match = None
            listing.requirement_reason = "需求分析失败，未过滤"
            return True
        listing.requirement_match = verdict["matched"]
        listing.requirement_reason = verdict["reason"]
        return bool(verdict["matched"])

    def _condition_analysis(self, session, listing: Listing, task: WatchTask) -> None:
        if not self.vision.enabled:
            return
        try:
            verdict = self.vision.analyze_condition(
                title=listing.title,
                price=listing.price,
                description=listing.description or "",
                requirement=task.condition_requirement or "",
                image_urls=listing.image_urls,
            )
        except Exception as exc:
            logger.warning("品相分析失败: %s", exc)
            return
        listing.condition_score = verdict["condition_score"]
        listing.condition_detail = verdict

    def _backfill(self, session, listing: Listing, task: WatchTask) -> bool:
        changed = False
        requirement = (task.condition_requirement or "").strip()
        if requirement and self.llm.enabled and listing.requirement_match is None:
            try:
                verdict = self.llm.analyze_requirement(
                    title=listing.title,
                    description=listing.description or "",
                    requirement=requirement,
                )
                listing.requirement_match = verdict["matched"]
                listing.requirement_reason = verdict["reason"]
                changed = True
            except Exception as exc:
                logger.warning("回填需求分析失败: %s", exc)
        if self.vision.enabled and listing.condition_score is None:
            try:
                verdict = self.vision.analyze_condition(
                    title=listing.title,
                    price=listing.price,
                    description=listing.description or "",
                    requirement=requirement,
                    image_urls=listing.image_urls,
                )
                listing.condition_score = verdict["condition_score"]
                listing.condition_detail = verdict
                changed = True
            except Exception as exc:
                logger.warning("回填品相分析失败: %s", exc)
        return changed

    def _notify(self, session, task: WatchTask, listing: Listing) -> None:
        requirement_line = ""
        if listing.requirement_match is not None:
            status = "是" if listing.requirement_match else "否"
            reason = listing.requirement_reason or ""
            requirement_line = f"需求匹配：{status}"
            if reason:
                requirement_line += f"（{reason}）"
            requirement_line += "\n"
        if listing.condition_score is not None:
            score_line = f"品相分：{listing.condition_score}\n"
        elif self.vision.enabled:
            score_line = "品相分：分析失败\n"
        else:
            score_line = "品相分：未配置视觉模型，未评估\n"
        extra = ""
        if listing.condition_detail:
            extra = listing.condition_detail.get("reason", "")
        message = NotificationMessage(
            title=f"[{task.keyword}] {listing.title}",
            content=f"价格：{listing.price} 元\n{requirement_line}{score_line}{extra}",
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

`goodprice/main.py` 的 `_make_crawl_service` 与 `build_app` 相应更新（Task 7 一并给出）。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawl_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 两阶段筛选流水线、防重入守卫、缺失分析回填"
```

## Task 7: Web 路由/模板/后台执行/调度即时同步

**Files:**
- Modify: `goodprice/web/routes.py`, `goodprice/main.py`, `goodprice/web/templates/tasks.html`, `goodprice/web/templates/settings.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api.py` 追加：

```python
import threading
import time


def test_run_task_executes_in_background(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    calls = []
    client.app.state.run_job = lambda task_id: calls.append(task_id)
    response = client.post(f"/tasks/{task['id']}/run")
    assert response.status_code == 303
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    assert calls == [task["id"]]


def test_task_change_triggers_scheduler_sync(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    calls = []
    client.app.state.sync_scheduler = lambda: calls.append(1)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    client.post(f"/tasks/{task['id']}/toggle")
    client.post(f"/tasks/{task['id']}/delete")
    assert len(calls) >= 3


def test_tasks_page_shows_requirement_and_running(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post("/api/tasks", json={"keyword": "iPhone 13", "condition_requirement": "屏幕完好"})
    response = client.get("/tasks")
    assert response.status_code == 200
    assert "屏幕完好" in response.text
    assert "运行中" in response.text
```

（原 `test_run_task_uses_run_job` 删除，由 `test_run_task_executes_in_background` 替代。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/web/routes.py`：

```python
import threading
...

@router.post("/tasks")
def create_task_form(
    request: Request,
    keyword: str = Form(...),
    name: str = Form(""),
    max_price: float = Form(0),
    condition_requirement: str = Form(""),
    min_condition_score: int = Form(0),
    interval_minutes: int = Form(20),
    fetch_detail: Optional[int] = Form(None),
    enabled: Optional[int] = Form(None),
):
    task_service, _ = _services(request)
    task_service.create_task(
        {
            "keyword": keyword.strip(),
            "name": name.strip(),
            "max_price": max_price,
            "condition_requirement": condition_requirement,
            "min_condition_score": min_condition_score,
            "interval_minutes": interval_minutes,
            "fetch_detail": bool(fetch_detail),
            "enabled": bool(enabled),
        }
    )
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.toggle_task(task_id)
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/run")
def run_task(request: Request, task_id: int):
    threading.Thread(
        target=request.app.state.run_job, args=(task_id,), daemon=True
    ).start()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task(request: Request, task_id: int):
    task_service, _ = _services(request)
    task_service.delete_task(task_id)
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)
```

`tasks_page` 改为：

```python
@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request):
    task_service, _ = _services(request)
    tasks = task_service.list_tasks()
    running_ids = request.app.state.guard.running_ids()
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {"tasks": tasks, "running_ids": running_ids, "active": "tasks"},
    )
```

`TaskCreate` 增加 `fetch_detail: bool = True`，`api_create_task` 不变（`model_dump` 自动带出）。

`goodprice/main.py`：

```python
from goodprice.db import init_db, migrate_schema
from goodprice.services.crawl_service import CrawlService, TaskRunGuard
...


def _make_crawl_service(session_factory, settings_service, guard):
    runtime = settings_service.get()
    from goodprice.analysis.llm import LLMClient
    from goodprice.crawler.xianyu import XianyuAdapter
    from goodprice.notify.log import LogNotifier
    from goodprice.notify.serverchan import ServerChanNotifier
    from goodprice.notify.wecom import WeComNotifier

    adapter = XianyuAdapter(cookie=runtime.xianyu_cookie, proxy=runtime.proxy)
    llm = LLMClient(
        base_url=runtime.llm_base_url,
        api_key=runtime.llm_api_key,
        model=runtime.llm_model,
    )
    vision = LLMClient(
        base_url=runtime.vision_base_url,
        api_key=runtime.vision_api_key,
        model=runtime.vision_model,
        allow_image_fallback=False,
    )
    notifiers = [("log", LogNotifier())]
    serverchan = ServerChanNotifier(sendkey=runtime.serverchan_sendkey)
    if serverchan.enabled:
        notifiers.append(("serverchan", serverchan))
    wecom = WeComNotifier(
        corpid=runtime.wecom_corpid,
        agentid=runtime.wecom_agentid,
        secret=runtime.wecom_secret,
        touser=runtime.wecom_touser,
    )
    if wecom.enabled:
        notifiers.append(("wecom", wecom))
    return CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=llm,
        vision=vision,
        notifiers=notifiers,
        settings_service=settings_service,
        guard=guard,
    )
```

`build_app`：

```python
    guard = TaskRunGuard()

    def run_job(task_id: int) -> None:
        try:
            _make_crawl_service(session_factory, settings_service, guard).run_task(task_id)
        except Exception:
            logger.exception("任务 %s 执行失败", task_id)

    ...
    Base.metadata.create_all(session_factory().get_bind())
    migrate_schema(session_factory)
    ...
    scheduler = build_scheduler(session_factory, run_job, task_service) if with_scheduler else None

    def sync_scheduler() -> None:
        if scheduler is not None:
            _sync_tasks(session_factory, run_job, task_service, scheduler)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if scheduler is not None:
            app.state.scheduler = scheduler
            scheduler.start()
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.settings_service = settings_service
    app.state.task_service = task_service
    app.state.run_job = run_job
    app.state.guard = guard
    app.state.sync_scheduler = sync_scheduler
    app.include_router(router)
    return app
```

`goodprice/web/templates/tasks.html`：任务卡片中把

```html
<div class="text-sm text-gray-500">
  最高价 {{ task.max_price }} · 品相分 ≥ {{ task.min_condition_score }} · 间隔 {{ task.interval_minutes }} 分钟
  ...
</div>
```

改为：

```html
<div class="text-sm text-gray-500">
  需求：{{ task.condition_requirement or '无' }} · 最高价 {{ task.max_price }} · 品相分 ≥ {{ task.min_condition_score }} · 间隔 {{ task.interval_minutes }} 分钟{% if task.fetch_detail %} · 抓详情{% endif %}
  {% if task.last_run_at %} · 上次运行 {{ task.last_run_at.strftime('%m-%d %H:%M') }}{% endif %}
</div>
{% if task.id in running_ids %}<span class="px-2 py-1 rounded text-xs bg-blue-100 text-blue-700">运行中</span>{% endif %}
```

新建任务表单加"抓详情"复选框（默认勾选）：

```html
<label class="flex items-center gap-2"><input type="checkbox" name="fetch_detail" value="1" checked> 抓详情描述</label>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 立即执行后台化、调度即时同步、任务需求展示"
```

## Task 8: 文档与全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README**

补充：两阶段筛选说明（需求匹配 → 视觉品相）、视觉模型配置、企业微信应用消息接入步骤（注册企业、创建自建应用、可信 IP、微信插件）。

- [ ] **Step 2: 全量验证**

Run: `conda run -n good-price pytest -v`
Expected: 全部通过，0 failed。

- [ ] **Step 3: 启动冒烟**

Run: `conda run -n good-price python -c "from goodprice.main import app; print(app.title)"`
Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "docs: 第二轮 README（两阶段筛选、视觉模型、企业微信配置）"
```

---

## 验收清单

- [ ] WeCom 通道测试全绿（token 缓存/刷新/60020/未配置禁用）
- [ ] 两阶段筛选测试全绿（不匹配不通知、视觉未配置跳过、回填不重发、防重入）
- [ ] 详情抓取解析测试全绿；真实商品详情页可抓到描述与主图
- [ ] 全量 `pytest` 通过；`python -m goodprice` 可启动
