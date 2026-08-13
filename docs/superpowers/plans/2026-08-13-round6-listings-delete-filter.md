# 第六轮：命中列表删除、筛选修复、设置页微调 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 命中列表支持单删/全选/批量删除；修复 `task_id=` 空串导致筛选与 HTMX 轮询 422 的问题；设置页 Server酱 SendKey 移入「消息通知」区、表单卡片居中。

**Architecture:** 沿用单进程 FastAPI + SQLite；`task_id` 查询参数改为字符串解析；删除 Listing 走 ORM 级联（快照/通知一并删除）。

**Tech Stack:** 沿用 Python 3.11（conda `good-price`）、FastAPI、SQLAlchemy、pytest。

---

## Task 1: 修复命中列表筛选（空 task_id 422）

**Files:**
- Modify: `goodprice/web/routes.py`, `goodprice/web/templates/listings.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api.py` 追加：

```python
def test_listings_filter_empty_task_id_ok(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    resp = client.get("/listings?task_id=&sort=price_asc&show=active")
    assert resp.status_code == 200
    resp2 = client.get("/listings?partial=1&task_id=&sort=satisfaction&show=active")
    assert resp2.status_code == 200
    assert client.get("/api/listings?task_id=&sort=satisfaction").status_code == 200
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py::test_listings_filter_empty_task_id_ok -q`
Expected: FAIL（422）

- [ ] **Step 3: 实现**

`goodprice/web/routes.py`：`from fastapi import Query`；`listings_page` 与 `api_list_listings` 的 `task_id: Optional[int] = None` 改为 `task_id: str = Query("")`，内部转换：

```python
    task_id_int = int(task_id) if task_id else None
    ...
        if task_id_int:
            query = query.filter(Listing.task_id == task_id_int)
```

`goodprice/web/templates/listings.html` 的任务下拉选中判断改为字符串比较：

```html
    {% for t in tasks %}<option value="{{ t.id }}" {{ 'selected' if (task_id|string) == (t.id|string) }}>{{ t.keyword }}</option>{% endfor %}
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "fix: 命中列表空 task_id 不再 422，筛选与轮询可用"
```

## Task 2: 命中列表删除（单删/全选/批量）

**Files:**
- Modify: `goodprice/web/routes.py`, `goodprice/web/templates/listings.html`, `goodprice/web/templates/listings_grid.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api.py` 追加：

```python
def test_listings_delete_single_and_batch(base_settings, session_factory):
    from goodprice.models import Listing, Notification

    client = _client(base_settings, session_factory)
    with session_factory() as session:
        l1 = Listing(platform="xianyu", external_id="1", title="甲", price=1, url="u")
        l2 = Listing(platform="xianyu", external_id="2", title="乙", price=2, url="v")
        session.add_all([l1, l2])
        session.flush()
        session.add(Notification(listing_id=l1.id, channel="log", status="sent", title="t"))
        session.commit()
        ids = [l1.id, l2.id]
    resp = client.post(f"/listings/{ids[0]}/delete")
    assert resp.status_code == 303
    assert len(client.get("/api/listings").json()) == 1
    with session_factory() as session:
        assert session.query(Notification).count() == 0  # 级联删除
    client.post("/listings/delete-batch", data={"ids": [ids[1]]})
    assert client.get("/api/listings").json() == []
    page = client.get("/listings")
    assert "listings-select-all" in page.text
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py::test_listings_delete_single_and_batch -q`
Expected: FAIL（404）

- [ ] **Step 3: 实现**

`goodprice/web/routes.py`（在参数化 `/listings/{...}` 路由之前声明批量删除）：

```python
@router.post("/listings/delete-batch")
def delete_listings_batch(request: Request, ids: list[int] = Form(...)):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        for row in session.query(Listing).filter(Listing.id.in_(ids)).all():
            session.delete(row)
        session.commit()
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/delete")
def delete_listing(request: Request, listing_id: int):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing

        row = session.get(Listing, listing_id)
        if row:
            session.delete(row)
            session.commit()
    return RedirectResponse("/listings", status_code=303)
```

`goodprice/web/templates/listings.html`：把网格包进批量删除表单，顶部加全选与批量删除：

```html
<form method="post" action="/listings/delete-batch" onsubmit="return confirm('确认删除选中的商品？')">
  <div class="flex items-center gap-4 mb-3">
    <label class="flex items-center gap-2"><input type="checkbox" id="listings-select-all" onchange="document.querySelectorAll('.row-check').forEach(c => c.checked = this.checked)"> 全选</label>
    <button class="bg-red-600 text-white rounded px-4 py-1">批量删除</button>
  </div>
  {% if running_ids %}
  <div hx-get="{{ partial_url }}" hx-trigger="every 2s" hx-target="#listings-grid" hx-swap="outerHTML"></div>
  {% endif %}
  {% include "listings_grid.html" %}
</form>
```

`goodprice/web/templates/listings_grid.html` 每张卡片操作区加复选框与删除：

```html
      <label class="flex items-center gap-1"><input type="checkbox" class="row-check" name="ids" value="{{ item.id }}"> 选</label>
      <form method="post" action="/listings/{{ item.id }}/delete" class="inline" onsubmit="return confirm('确认删除该商品？')"><button class="border rounded px-2 py-0.5 text-xs text-gray-600">删除</button></form>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 命中列表删除（单删/全选/批量，级联清理通知与快照）"
```

## Task 3: 设置页微调（SendKey 归位 + 卡片居中）

**Files:**
- Modify: `goodprice/web/templates/settings.html`
- Test: `tests/test_api.py`（页面渲染回归）

- [ ] **Step 1: 写失败测试**

`tests/test_api.py` 追加：

```python
def test_settings_page_layout(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "消息通知" in page.text
    assert "mx-auto" in page.text
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py::test_settings_page_layout -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/web/templates/settings.html`：
- 表单 class 加 `mx-auto`（`class="bg-white rounded shadow p-4 max-w-2xl mx-auto space-y-4"`）
- LLM 网格中删除 Server酱 SendKey 输入，移入「消息通知」区块（放在 serverchan 开关下方）：

```html
    <div class="mt-3">
      <label class="block text-sm font-medium mb-1">Server酱 SendKey</label>
      <input name="serverchan_sendkey" type="password" class="w-full border rounded px-3 py-2" value="" placeholder="已配置（留空保持不变）">
      <p class="text-xs text-gray-500 mt-1">https://sct.ftqq.com 获取；关闭开关则不发。</p>
    </div>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 设置页 SendKey 归入消息通知、卡片居中"
```

## Task 4: 全量验证

**Files:**
- Test: 全量

- [ ] **Step 1: 全量测试**

Run: `conda run -n good-price pytest -q`
Expected: 全部通过。

- [ ] **Step 2: 冒烟**

Run: `conda run -n good-price python -c "from goodprice.main import app; print(app.title)"`
Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "docs: 第六轮收尾"
```

---

## 验收清单

- [ ] `/listings?task_id=&...` 不再 422；筛选与轮询可用
- [ ] 命中列表单删/全选/批量删除可用，删除级联清理通知与价格快照
- [ ] 设置页 Server酱 SendKey 在消息通知区，卡片居中
- [ ] 全量 `pytest` 通过
