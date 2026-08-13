# 第四轮：Bug 修复 + 任务/拉黑/跳转/排序 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复好评率单位错乱与品相分析占位图问题；补齐任务编辑、拉黑（商品+卖家）、任务→商品跳转、命中列表排序（默认满足程度组合评分）。

**Architecture:** 沿用单进程 FastAPI + SQLite；模型加列（blocked/task_id/satisfaction），迁移 + 启动回填；流水线内嵌满足程度评分与拉黑跳过；前端加编辑页、拉黑按钮、筛选排序条。

**Tech Stack:** 沿用 Python 3.11（conda `good-price`）、FastAPI、SQLAlchemy、pytest。

---

## Task 1: 好评率单位 + 视觉图片过滤/错误显示

**Files:**
- Modify: `goodprice/crawler/parser.py`, `goodprice/services/crawl_service.py`
- Test: `tests/test_crawler_parser.py`, `tests/test_crawl_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_crawler_parser.py`：`test_parse_detail_html` 的断言改为：

```python
    assert detail.positive_rate == 1.0  # 好评率 100% 存为小数
```

并新增：

```python
from goodprice.crawler.parser import is_product_image


def test_is_product_image_filters_placeholder():
    assert is_product_image("https://img.alicdn.com/bao/uploaded/i2/x.jpg") is True
    assert is_product_image("https://img.alicdn.com/imgextra/i4/xxx-2-tps-2-2.png") is False
    assert is_product_image("https://img.alicdn.com/imgextra/i1/xxx-tps-480-144.png") is False
```

`tests/fixtures/xianyu_detail.html` 的图片改为 bao/uploaded 真实图，并追加一张占位图：

```html
    <img class="ant-image-img css-ab" src="//img.alicdn.com/bao/uploaded/d1.jpg">
    <img class="ant-image-img css-ab" src="//img.alicdn.com/bao/uploaded/d2.jpg">
    <img class="ant-image-img css-ab" src="https://img.alicdn.com/imgextra/i4/xx-2-tps-2-2.png">
```

（原 `https://img.alicdn.com/d1.jpg` 一行删除。）

`tests/test_crawl_service.py`：

- `_item()` 的 `image_urls` 改为 `[f"https://img.alicdn.com/bao/uploaded/{external_id}.jpg"]`
- `SellerFakeAdapter.fetch_detail` 的图片改为 `["https://img.alicdn.com/bao/uploaded/d.jpg"]`，`positive_rate` 默认改为 `1.0`
- `test_seller_advisory_in_notification_and_cache` 追加 `assert "好评率 100%" in notifier.messages[0].content`

新增：

```python
def test_condition_analysis_retries_once_and_records_error(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    vision = FakeVision(error=RuntimeError("模型超时"))
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=vision)
    crawl.run_task(task.id)
    assert len(vision.calls) == 2  # 重试一次
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_detail["error"] == "模型超时"
        assert listing.condition_score is None


def test_no_valid_image_skips_vision(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    vision = FakeVision()

    class NoImgAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(items=[_item()])

        def fetch_detail(self, url):
            from goodprice.crawler.base import ListingDetail

            return ListingDetail(description="无图", image_urls=["https://img.alicdn.com/imgextra/xx-2-tps-2-2.png"])

    crawl, _, _ = _service(session_factory, base_settings, adapter=NoImgAdapter(), vision=vision)
    crawl.run_task(task.id)
    assert vision.calls == []
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_detail["error"] == "无有效商品图"
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawl_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/crawler/parser.py`：

```python
def is_product_image(url: str) -> bool:
    """真实商品图判定：alicdn 产品图路径含 bao/uploaded；占位图/图标（tps-）一律排除。"""
    return "bao/uploaded" in (url or "")
```

`parse_detail_html` 的图片收集改为：

```python
    images: list[str] = []
    for img in soup.select(sel.DETAIL_IMAGE):
        src = img.get("src")
        if src and is_product_image(src):
            url = _absolute(src)
            if url not in images:
                images.append(url)
```

`_parse_seller_block` 改为存小数：

```python
    if m:
        positive_rate = float(m.group(1)) / 100
```

`goodprice/services/crawl_service.py`：

- 顶部导入 `from goodprice.crawler.parser import is_product_image`
- `_fetch_detail` 合并图片时过滤：

```python
        merged = [u for u in (listing.image_urls or []) if is_product_image(u)]
        for url in detail.image_urls:
            if url not in merged and is_product_image(url):
                merged.append(url)
        listing.image_urls = merged[:8]
```

- `_condition_analysis` 改为过滤 + 重试 + 记错误：

```python
    def _condition_analysis(self, session, listing: Listing, task: WatchTask) -> None:
        if not self.vision.enabled:
            return
        valid = [u for u in (listing.image_urls or []) if is_product_image(u)]
        if not valid:
            listing.condition_detail = {"error": "无有效商品图"}
            return
        last_exc = None
        for _attempt in range(2):
            try:
                verdict = self.vision.analyze_condition(
                    title=listing.title,
                    price=listing.price,
                    description=listing.description or "",
                    requirement=task.condition_requirement or "",
                    image_urls=valid,
                )
            except Exception as exc:
                last_exc = exc
                continue
            listing.condition_score = verdict["condition_score"]
            listing.condition_detail = verdict
            return
        listing.condition_detail = {"error": str(last_exc)[:200]}
```

- `_notify` 的评分行改为带原因：

```python
        if listing.condition_score is not None:
            score_line = f"品相分：{listing.condition_score}\n"
        elif self.vision.enabled:
            err = ""
            if isinstance(listing.condition_detail, dict):
                err = listing.condition_detail.get("error") or ""
            score_line = f"品相分：未评估（{err or '分析失败'}）\n"
        else:
            score_line = "品相分：未配置视觉模型，未评估\n"
```

- `_notify` 的好评率展示改小数：

```python
            rate_txt = f"好评率 {rate * 100:.0f}%" if isinstance(rate, (int, float)) else ""
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_crawler_parser.py tests/test_crawl_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "fix: 好评率小数化 + 视觉只送真实商品图 + 失败重试并记录原因"
```

## Task 2: 任务编辑

**Files:**
- Modify: `goodprice/services/task_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/tasks.html`
- Create: `goodprice/web/templates/tasks_edit.html`
- Test: `tests/test_task_service.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_task_service.py` 追加：

```python
def test_update_task(session_factory):
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "a"})
    updated = service.update_task(task.id, {"keyword": "b", "max_price": "500", "condition_requirement": "屏幕完好"})
    assert updated.keyword == "b"
    assert updated.max_price == 500.0
    assert updated.condition_requirement == "屏幕完好"
    assert service.update_task(999, {"keyword": "x"}) is None
```

`tests/test_api.py` 追加：

```python
def test_edit_task_api_and_page(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "旧词"}).json()
    response = client.put(f"/api/tasks/{task['id']}", json={"keyword": "新词", "max_price": 300})
    assert response.status_code == 200
    assert response.json()["keyword"] == "新词"
    page = client.get(f"/tasks/{task['id']}/edit")
    assert page.status_code == 200
    assert "新词" in page.text
    calls = []
    client.app.state.sync_scheduler = lambda: calls.append(1)
    resp = client.post(
        f"/tasks/{task['id']}/edit",
        data={
            "keyword": "改后", "name": "", "max_price": "100", "condition_requirement": "",
            "min_condition_score": "0", "interval_minutes": "30", "fetch_detail": "1", "enabled": "1",
        },
    )
    assert resp.status_code == 303
    assert client.get("/api/tasks").json()[0]["keyword"] == "改后"
    assert calls == [1]
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_task_service.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/services/task_service.py` 追加：

```python
    def update_task(self, task_id: int, data: dict) -> Optional[WatchTask]:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if not task:
                return None
            if data.get("keyword"):
                task.keyword = data["keyword"].strip()
            if "name" in data:
                task.name = data.get("name", "").strip()
            if "max_price" in data:
                task.max_price = float(data.get("max_price") or 0)
            if "condition_requirement" in data:
                task.condition_requirement = data.get("condition_requirement", "")
            if "min_condition_score" in data:
                task.min_condition_score = int(data.get("min_condition_score") or 0)
            if "interval_minutes" in data:
                task.interval_minutes = int(data.get("interval_minutes") or 20)
            if "fetch_detail" in data:
                task.fetch_detail = bool(data.get("fetch_detail"))
            if "enabled" in data:
                task.enabled = bool(data.get("enabled"))
            session.commit()
            session.refresh(task)
            return task
```

`goodprice/web/routes.py`：

```python
@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_page(request: Request, task_id: int):
    task_service, _ = _services(request)
    task = task_service.get_task(task_id)
    if task is None:
        return RedirectResponse("/tasks", status_code=303)
    return templates.TemplateResponse(request, "tasks_edit.html", {"task": task, "active": "tasks"})


@router.post("/tasks/{task_id}/edit")
def edit_task_form(
    request: Request,
    task_id: int,
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
    task_service.update_task(
        task_id,
        {
            "keyword": keyword.strip(),
            "name": name.strip(),
            "max_price": max_price,
            "condition_requirement": condition_requirement,
            "min_condition_score": min_condition_score,
            "interval_minutes": interval_minutes,
            "fetch_detail": bool(fetch_detail),
            "enabled": bool(enabled),
        },
    )
    request.app.state.sync_scheduler()
    return RedirectResponse("/tasks", status_code=303)


@router.put("/api/tasks/{task_id}")
def api_update_task(request: Request, task_id: int, data: TaskCreate):
    task_service, _ = _services(request)
    task = task_service.update_task(task_id, data.model_dump())
    if task is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="任务不存在")
    request.app.state.sync_scheduler()
    return _task_dict(task)
```

`goodprice/web/templates/tasks_edit.html`：复制创建表单，`action="/tasks/{{ task.id }}/edit"`，各字段预填 `value="{{ task.xxx }}"`，复选框按 `task.fetch_detail/enabled` 勾选。

`goodprice/web/templates/tasks.html` 每行加编辑链接：

```html
    <a href="/tasks/{{ task.id }}/edit" class="border rounded px-3 py-1">编辑</a>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_task_service.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 任务编辑（页面 + API）"
```

## Task 3: 拉黑（商品+卖家）

**Files:**
- Modify: `goodprice/models.py`, `goodprice/db.py`, `goodprice/services/crawl_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/listings.html`, `goodprice/web/templates/listings_grid.html`
- Test: `tests/test_models.py`, `tests/test_db.py`, `tests/test_crawl_service.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py` 追加：

```python
def test_blocked_flags(session_factory):
    with session_factory() as session:
        l = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u", blocked=True)
        s = Seller(platform="xianyu", seller_uid="u1", blocked=True)
        session.add_all([l, s])
        session.commit()
        assert l.blocked is True
        assert s.blocked is True
```

`tests/test_db.py` 追加：

```python
def test_migrate_adds_block_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE sellers (id INTEGER PRIMARY KEY)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        lc = {r[1] for r in session.execute(text("PRAGMA table_info(listings)"))}
        sc = {r[1] for r in session.execute(text("PRAGMA table_info(sellers)"))}
    assert "blocked" in lc and "blocked" in sc
```

`tests/test_crawl_service.py` 追加：

```python
def test_blocked_listing_and_seller_skipped(session_factory, base_settings):
    from goodprice.models import Seller

    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1

    with session_factory() as session:
        listing = session.query(Listing).one()
        listing.blocked = True
        session.commit()
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1  # 已拉黑不再处理

    with session_factory() as session:
        session.query(Listing).update({Listing.blocked: False})
        session.add(Seller(platform="xianyu", seller_uid="2672367114", blocked=True))
        session.commit()
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1  # 卖家已拉黑不再通知
```

`tests/test_api.py` 追加：

```python
def test_block_unblock_listing(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        from goodprice.models import Listing

        session.add(Listing(platform="xianyu", external_id="1", title="t", price=1, url="u"))
        session.commit()
        listing_id = session.query(Listing).one().id
    resp = client.post(f"/listings/{listing_id}/block")
    assert resp.status_code == 303
    assert client.get("/listings?show=blocked").text.count("已拉黑") >= 1
    client.post(f"/listings/{listing_id}/unblock")
    assert client.get("/listings?show=blocked").text.count("已拉黑") == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py`：`Listing` 加 `blocked: Mapped[bool] = mapped_column(default=False)`；`Seller` 加 `blocked: Mapped[bool] = mapped_column(default=False)`。

`goodprice/db.py` 迁移追加：

```python
        "listings": [
            ...,
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
        ],
        "sellers": [
            ("credit_label", "credit_label TEXT"),
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
        ],
```

`goodprice/services/crawl_service.py`：`_run_impl` 循环内 `_upsert_listing` 之后加：

```python
                    if self._is_blocked(session, listing):
                        session.commit()
                        continue
```

并新增：

```python
    def _is_blocked(self, session, listing: Listing) -> bool:
        if listing.blocked:
            return True
        if not listing.seller_uid:
            return False
        from goodprice.models import Seller

        seller = (
            session.query(Seller)
            .filter_by(platform=listing.platform, seller_uid=listing.seller_uid)
            .first()
        )
        if seller and seller.blocked:
            listing.blocked = True
            return True
        return False
```

`goodprice/web/routes.py`：

```python
@router.post("/listings/{listing_id}/block")
def block_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=False)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/unblock")
def unblock_listing(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=False)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/block-seller")
def block_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, True, seller=True)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/unblock-seller")
def unblock_seller(request: Request, listing_id: int):
    _set_blocked(request, listing_id, False, seller=True)
    return RedirectResponse("/listings", status_code=303)


def _set_blocked(request: Request, listing_id: int, blocked: bool, seller: bool) -> None:
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Seller

        listing = session.get(Listing, listing_id)
        if listing is None:
            return
        if seller and listing.seller_uid:
            s = (
                session.query(Seller)
                .filter_by(platform=listing.platform, seller_uid=listing.seller_uid)
                .first()
            )
            if s is None:
                s = Seller(platform=listing.platform, seller_uid=listing.seller_uid)
                session.add(s)
            s.blocked = blocked
            session.query(Listing).filter(
                Listing.platform == listing.platform,
                Listing.seller_uid == listing.seller_uid,
            ).update({Listing.blocked: blocked})
        else:
            listing.blocked = blocked
        session.commit()
```

`listings_page` 增加参数并过滤：

```python
def listings_page(
    request: Request,
    partial: int = 0,
    task_id: Optional[int] = None,
    sort: str = "satisfaction",
    show: str = "active",
):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        query = session.query(Listing)
        if task_id:
            query = query.filter(Listing.task_id == task_id)
        if show == "active":
            query = query.filter(Listing.blocked.is_(False))
        elif show == "blocked":
            query = query.filter(Listing.blocked.is_(True))
        if sort == "price_asc":
            query = query.order_by(Listing.price.asc())
        elif sort == "price_desc":
            query = query.order_by(Listing.price.desc())
        elif sort == "newest":
            query = query.order_by(Listing.first_seen_at.desc())
        else:
            query = query.order_by(Listing.satisfaction.desc(), Listing.first_seen_at.desc())
        listings = query.limit(100).all()
    partial_url = f"/listings?partial=1&task_id={task_id or ''}&sort={sort}&show={show}"
    if partial:
        return templates.TemplateResponse(request, "listings_grid.html", {"listings": listings})
    running_ids = request.app.state.guard.running_ids()
    return templates.TemplateResponse(
        request,
        "listings.html",
        {
            "listings": listings,
            "running_ids": running_ids,
            "task_id": task_id,
            "sort": sort,
            "show": show,
            "partial_url": partial_url,
            "active": "listings",
        },
    )
```

`api_list_listings` 同步支持 `task_id/sort/show`。

`goodprice/web/templates/listings.html`：轮询 URL 用 `partial_url`；顶部加筛选排序条：

```html
<form method="get" action="/listings" class="flex items-center gap-3 mb-4">
  <select name="sort" class="border rounded px-3 py-2">
    <option value="satisfaction" {{ 'selected' if sort == 'satisfaction' }}>按满足程度</option>
    <option value="price_asc" {{ 'selected' if sort == 'price_asc' }}>价格升序</option>
    <option value="price_desc" {{ 'selected' if sort == 'price_desc' }}>价格降序</option>
    <option value="newest" {{ 'selected' if sort == 'newest' }}>最新</option>
  </select>
  <select name="show" class="border rounded px-3 py-2">
    <option value="active" {{ 'selected' if show == 'active' }}>未拉黑</option>
    <option value="blocked" {{ 'selected' if show == 'blocked' }}>已拉黑</option>
    <option value="all" {{ 'selected' if show == 'all' }}>全部</option>
  </select>
  <input type="hidden" name="task_id" value="{{ task_id or '' }}">
  <button class="bg-blue-600 text-white rounded px-4 py-2">筛选</button>
</form>
```

`goodprice/web/templates/listings_grid.html` 每张卡片追加操作按钮与拉黑标记：

```html
    <div class="mt-2 flex items-center gap-1 flex-wrap">
      {% if item.blocked %}<span class="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-600">已拉黑</span>{% endif %}
      <form method="post" action="/listings/{{ item.id }}/block" class="inline"><button class="border rounded px-2 py-0.5 text-xs text-red-600">拉黑商品</button></form>
      {% if item.seller_uid %}<form method="post" action="/listings/{{ item.id }}/block-seller" class="inline"><button class="border rounded px-2 py-0.5 text-xs text-orange-600">拉黑卖家</button></form>{% endif %}
      {% if item.blocked %}<form method="post" action="/listings/{{ item.id }}/unblock" class="inline"><button class="border rounded px-2 py-0.5 text-xs">恢复</button></form>{% endif %}
    </div>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_models.py tests/test_db.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 拉黑商品/卖家，流水线跳过，列表筛选"
```

## Task 4: 任务→商品跳转

**Files:**
- Modify: `goodprice/models.py`, `goodprice/db.py`, `goodprice/services/crawl_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/tasks.html`
- Test: `tests/test_db.py`, `tests/test_crawl_service.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db.py` 的 `test_migrate_adds_block_columns` 断言追加 `"task_id" in lc`。

`tests/test_crawl_service.py` 的 `test_happy_path_and_dedup` 追加：

```python
        assert listing.task_id == task.id
```

`tests/test_api.py` 追加：

```python
def test_listings_filter_by_task(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    with session_factory() as session:
        from goodprice.models import Listing

        session.add(Listing(platform="xianyu", external_id="1", title="甲", price=1, url="u", task_id=task["id"]))
        session.add(Listing(platform="xianyu", external_id="2", title="乙", price=1, url="v", task_id=999))
        session.commit()
    data = client.get(f"/api/listings?task_id={task['id']}").json()
    assert [d["title"] for d in data] == ["甲"]
    page = client.get("/tasks")
    assert f"/listings?task_id={task['id']}" in page.text
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_db.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py`：`Listing` 加 `task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("watch_tasks.id", ondelete="SET NULL"), nullable=True)`。

`goodprice/db.py` 迁移 `listings` 追加 `("task_id", "task_id INTEGER")`。

`goodprice/services/crawl_service.py` 的 `_upsert_listing` 新建时设置 `task_id=task.id`。

`goodprice/web/routes.py` 的 `api_list_listings` 支持 `task_id`：

```python
@router.get("/api/listings")
def api_list_listings(request: Request, task_id: Optional[int] = None, sort: str = "satisfaction", show: str = "active"):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        query = session.query(Listing)
        if task_id:
            query = query.filter(Listing.task_id == task_id)
        if show == "active":
            query = query.filter(Listing.blocked.is_(False))
        elif show == "blocked":
            query = query.filter(Listing.blocked.is_(True))
        if sort == "price_asc":
            query = query.order_by(Listing.price.asc())
        elif sort == "price_desc":
            query = query.order_by(Listing.price.desc())
        elif sort == "newest":
            query = query.order_by(Listing.first_seen_at.desc())
        else:
            query = query.order_by(Listing.satisfaction.desc(), Listing.first_seen_at.desc())
        rows = query.limit(100).all()
    return [...同现状...]
```

`goodprice/web/templates/tasks.html`：任务名/关键词改为链接：

```html
<a href="/listings?task_id={{ task.id }}" class="font-medium hover:text-blue-600">{{ task.keyword }}</a>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_db.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 任务→商品跳转（task_id 关联 + 筛选）"
```

## Task 5: 满足程度评分与排序

**Files:**
- Create: `goodprice/services/satisfaction.py`
- Modify: `goodprice/models.py`, `goodprice/db.py`, `goodprice/services/crawl_service.py`, `goodprice/main.py`
- Test: `tests/test_satisfaction.py`, `tests/test_crawl_service.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_satisfaction.py`：

```python
from types import SimpleNamespace

from goodprice.services.satisfaction import backfill_satisfaction, compute_satisfaction


def _listing(**kw):
    defaults = dict(requirement_match=True, condition_score=8, seller_risk={"risk_level": "低"})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_compute_satisfaction():
    assert compute_satisfaction(_listing()) == 92.0  # 50 + 32 + 10
    assert compute_satisfaction(_listing(requirement_match=None, condition_score=None, seller_risk=None)) == 25.0
    assert compute_satisfaction(_listing(requirement_match=False, condition_score=3, seller_risk={"risk_level": "高"})) == 12.0


def test_backfill(session_factory):
    from goodprice.models import Listing

    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1", title="t", price=1, url="u",
                            requirement_match=True, condition_score=8, seller_risk={"risk_level": "低"}))
        session.commit()
    assert backfill_satisfaction(session_factory) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.satisfaction == 92.0
```

`tests/test_crawl_service.py` 的 `test_happy_path_and_dedup` 追加：

```python
        assert listing.satisfaction == 92.0
```

`tests/test_api.py` 追加：

```python
def test_listings_sort(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        from goodprice.models import Listing

        session.add(Listing(platform="xianyu", external_id="1", title="a", price=100, url="u", satisfaction=20))
        session.add(Listing(platform="xianyu", external_id="2", title="b", price=10, url="v", satisfaction=90))
        session.commit()
    assert [d["title"] for d in client.get("/api/listings?sort=satisfaction").json()] == ["b", "a"]
    assert [d["title"] for d in client.get("/api/listings?sort=price_asc").json()] == ["b", "a"]
    assert [d["title"] for d in client.get("/api/listings?sort=price_desc").json()] == ["a", "b"]
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_satisfaction.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py`：`Listing` 加 `satisfaction: Mapped[float] = mapped_column(Float, default=0.0)`。

`goodprice/db.py` 迁移 `listings` 追加 `("satisfaction", "satisfaction FLOAT DEFAULT 0")`。

`goodprice/services/satisfaction.py`：

```python
def compute_satisfaction(listing) -> float:
    score = 0.0
    if listing.requirement_match is True:
        score += 50
    elif listing.requirement_match is None:
        score += 25
    if listing.condition_score is not None:
        score += min(40.0, listing.condition_score * 4)
    risk = None
    if isinstance(listing.seller_risk, dict):
        risk = listing.seller_risk.get("risk_level")
    score += {"低": 10.0, "中": 5.0, "高": 0.0}.get(risk, 0.0)
    return score


def backfill_satisfaction(session_factory) -> int:
    from goodprice.models import Listing

    count = 0
    with session_factory() as session:
        for listing in session.query(Listing).filter(Listing.satisfaction == 0).all():
            listing.satisfaction = compute_satisfaction(listing)
            count += 1
        session.commit()
    return count
```

`goodprice/services/crawl_service.py`：顶部导入 `from goodprice.services.satisfaction import compute_satisfaction`；`_run_impl` 每个商品处理末尾（commit 前）加：

```python
                    listing.satisfaction = compute_satisfaction(listing)
```

（新建分支与回填分支各一次，或统一放在循环体末尾。）

`goodprice/main.py`：`migrate_schema(session_factory)` 之后调用：

```python
    from goodprice.services.satisfaction import backfill_satisfaction

    backfill_satisfaction(session_factory)
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_satisfaction.py tests/test_crawl_service.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 满足程度组合评分与排序（含存量回填）"
```

## Task 6: README 与全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README**

补充：任务编辑、拉黑（商品/卖家）、任务→商品跳转、排序方式与满足程度评分构成（需求 50 + 品相 40 + 卖家风险 10）。

- [ ] **Step 2: 全量验证**

Run: `conda run -n good-price pytest -v`
Expected: 全部通过，0 failed。

- [ ] **Step 3: 启动冒烟**

Run: `conda run -n good-price python -c "from goodprice.main import app; print(app.title)"`
Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "docs: 第四轮 README"
```

---

## 验收清单

- [ ] 好评率显示为百分比且无 9900% 类错误；视觉只送真实商品图、失败重试并显示原因
- [ ] 任务编辑（页面 + API）可用
- [ ] 拉黑商品/卖家后不再通知，列表可筛选已拉黑
- [ ] 任务名点击跳到对应商品；列表可按满足程度/价格/最新排序
- [ ] 全量 `pytest` 通过；`python -m goodprice` 可启动
