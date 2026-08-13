# 第五轮：消息通知页与删除、设置开关重构、移除企微应用消息 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「消息通知」页（单删/全选/批量删除）；命中列表支持按任务筛选；设置页把 Server酱与企微 webhook 归入「消息通知」并加开关、视觉模型加开关；评分与通知文案适配视觉关闭；删除企业微信应用消息模块（corpid/agentid/secret/touser）。

**Architecture:** 沿用单进程 FastAPI + SQLite；`Notification` 增 title/content 列；设置项增加三个布尔开关；`compute_satisfaction` 支持视觉开关权重；移除 `notify/wecom.py`。

**Tech Stack:** 沿用 Python 3.11（conda `good-price`）、FastAPI、SQLAlchemy、pytest。

---

## Task 1: 消息通知页 + 删除（单删/批量/全选）

**Files:**
- Modify: `goodprice/models.py`, `goodprice/db.py`, `goodprice/services/crawl_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/base.html`
- Create: `goodprice/web/templates/notifications.html`
- Test: `tests/test_db.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db.py` 追加：

```python
def test_migrate_adds_notification_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE notifications (id INTEGER PRIMARY KEY)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {r[1] for r in session.execute(text("PRAGMA table_info(notifications)"))}
    assert {"title", "content"} <= cols
```

`tests/test_api.py` 追加：

```python
def test_notifications_page_and_delete(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    from goodprice.models import Notification

    with session_factory() as session:
        session.add(Notification(channel="log", status="sent", title="消息甲", content="内容甲"))
        session.add(Notification(channel="serverchan", status="failed", title="消息乙", content="内容乙"))
        session.commit()
        ids = [r.id for r in session.query(Notification).order_by(Notification.id).all()]
    page = client.get("/notifications")
    assert page.status_code == 200
    assert "消息甲" in page.text and "消息乙" in page.text
    assert "select-all" in page.text  # 全选
    resp = client.post(f"/notifications/{ids[0]}/delete")
    assert resp.status_code == 303
    assert client.get("/api/notifications").json() == []
    with session_factory() as session:
        session.add(Notification(channel="log", status="sent", title="x", content="y"))
        session.add(Notification(channel="log", status="sent", title="y", content="z"))
        session.commit()
        ids2 = [r.id for r in session.query(Notification).all()]
    client.post("/notifications/delete-batch", data={"ids": ids2})
    assert client.get("/api/notifications").json() == []
```

（需要新增 `GET /api/notifications` 返回列表。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_db.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/models.py` 的 `Notification` 追加：

```python
    title: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")
```

`goodprice/db.py` 迁移追加：

```python
        "notifications": [
            ("title", "title TEXT"),
            ("content", "content TEXT"),
        ],
```

`goodprice/services/crawl_service.py` 的 `_notify` 中两处 `Notification(...)` 均加上 `title=message.title, content=message.content`。

`goodprice/web/routes.py`：

```python
@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, Notification

        rows = (
            session.query(Notification, Listing)
            .outerjoin(Listing, Listing.id == Notification.listing_id)
            .order_by(Notification.id.desc())
            .limit(200)
            .all()
        )
    return templates.TemplateResponse(
        request, "notifications.html", {"rows": rows, "active": "notifications"}
    )


@router.post("/notifications/delete-batch")
def delete_notifications_batch(request: Request, ids: list[int] = Form(...)):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        session.query(Notification).filter(Notification.id.in_(ids)).delete(
            synchronize_session=False
        )
        session.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/{notification_id}/delete")
def delete_notification(request: Request, notification_id: int):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        row = session.get(Notification, notification_id)
        if row:
            session.delete(row)
            session.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.get("/api/notifications")
def api_list_notifications(request: Request):
    with request.app.state.session_factory() as session:
        from goodprice.models import Notification

        rows = session.query(Notification).order_by(Notification.id.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "channel": r.channel,
            "status": r.status,
            "title": r.title,
            "content": r.content,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
```

`goodprice/web/templates/notifications.html`：表格 + 全选 JS + 批量删除表单 + 单条删除：

```html
{% extends "base.html" %}
{% block title %}消息通知 - 闲鱼盯价助手{% endblock %}
{% block content %}
<div class="bg-white rounded shadow">
  <form method="post" action="/notifications/delete-batch" onsubmit="return confirm('确认删除选中的消息？')">
    <div class="p-3 flex items-center gap-4">
      <label class="flex items-center gap-2"><input type="checkbox" id="select-all" onchange="document.querySelectorAll('.row-check').forEach(c => c.checked = this.checked)"> 全选</label>
      <button class="bg-red-600 text-white rounded px-4 py-1">批量删除</button>
    </div>
    <table class="w-full text-sm">
      <thead><tr class="text-left text-gray-500 border-b">
        <th class="p-2"></th><th class="p-2">通道</th><th class="p-2">状态</th><th class="p-2">内容</th><th class="p-2">时间</th><th class="p-2">操作</th>
      </tr></thead>
      <tbody>
        {% for n, listing in rows %}
        <tr class="border-b">
          <td class="p-2"><input type="checkbox" class="row-check" name="ids" value="{{ n.id }}"></td>
          <td class="p-2">{{ n.channel }}</td>
          <td class="p-2">{{ n.status }}</td>
          <td class="p-2">
            {% if listing %}<a href="{{ listing.url }}" target="_blank" class="hover:text-blue-600">{{ n.title }}</a>
            {% else %}{{ n.title }}{% endif %}
            <div class="text-gray-500 text-xs">{{ n.content }}</div>
          </td>
          <td class="p-2 text-gray-500">{{ n.created_at.strftime('%m-%d %H:%M') }}</td>
          <td class="p-2">
            <form method="post" action="/notifications/{{ n.id }}/delete" class="inline" onsubmit="return confirm('确认删除该消息？')">
              <button class="text-red-600">删除</button>
            </form>
          </td>
        </tr>
        {% else %}
        <tr><td class="p-4 text-gray-500" colspan="6">暂无消息。</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </form>
</div>
{% endblock %}
```

`goodprice/web/templates/base.html` 导航追加 `消息通知` 链接。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_db.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 消息通知页（单删/全选/批量删除）+ 通知标题内容落库"
```

## Task 2: 命中列表按任务分类

**Files:**
- Modify: `goodprice/web/routes.py`, `goodprice/web/templates/listings.html`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api.py` 追加：

```python
def test_listings_page_has_task_filter(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "镜头"}).json()
    page = client.get("/listings")
    assert f'value="{task["id"]}"' in page.text
    assert "镜头" in page.text
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_api.py::test_listings_page_has_task_filter -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/web/routes.py` 的 `listings_page` 查询任务列表并传入模板：

```python
    with request.app.state.session_factory() as session:
        from goodprice.models import Listing, WatchTask

        tasks = session.query(WatchTask).order_by(WatchTask.id).all()
        ...
    return templates.TemplateResponse(
        ...,
        {"listings": listings, "tasks": tasks, "task_id": task_id, ...},
    )
```

`goodprice/web/templates/listings.html` 筛选条最前面加任务下拉：

```html
  <select name="task_id" class="border rounded px-3 py-2">
    <option value="">全部任务</option>
    {% for t in tasks %}<option value="{{ t.id }}" {{ 'selected' if task_id == t.id }}>{{ t.keyword }}</option>{% endfor %}
  </select>
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 命中列表任务筛选下拉"
```

## Task 3: 设置重构（消息通知/视觉开关）+ 移除企微应用消息

**Files:**
- Modify: `goodprice/config.py`, `goodprice/services/settings_service.py`, `goodprice/web/routes.py`, `goodprice/web/templates/settings.html`, `.env.example`, `goodprice/main.py`
- Delete: `goodprice/notify/wecom.py`
- Test: `tests/test_config.py`, `tests/test_settings_service.py`, `tests/test_notify.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py` 删除/替换引用 `wecom_corpid` 的用例，追加：

```python
def test_round5_toggle_defaults():
    settings = Settings(_env_file=None)
    assert settings.serverchan_enabled is True
    assert settings.wecom_robot_enabled is True
    assert settings.vision_enabled is True
```

`tests/test_settings_service.py` 的 `test_round2_settings_persist` 改为不引用 wecom_corpid，并追加：

```python
def test_round5_toggle_persist(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"serverchan_enabled": "0", "vision_enabled": "0"})
    settings = service.get()
    assert settings.serverchan_enabled is False
    assert settings.vision_enabled is False
    assert settings.wecom_robot_enabled is True
```

`tests/test_notify.py` 删除 `WeComNotifier` 相关 import 与用例（应用消息模块移除）。

`tests/test_api.py` 的 `_settings_form()` 删除 wecom_corpid/agentid/secret/touser，替换为开关字段，并调整 `test_settings_save_wecom` 为 `test_settings_save_toggles`。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_config.py tests/test_settings_service.py tests/test_notify.py tests/test_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/config.py` 的 `Settings`：删除 `wecom_corpid/wecom_agentid/wecom_secret/wecom_touser`，追加：

```python
    serverchan_enabled: bool = True
    wecom_robot_enabled: bool = True
    vision_enabled: bool = True
```

`goodprice/services/settings_service.py` 的 `RuntimeSettings` 同样调整，并加布尔强制转换：

```python
    _BOOL_FIELDS = {"serverchan_enabled", "wecom_robot_enabled", "vision_enabled"}
    ...
        for key in cls._BOOL_FIELDS:
            if values.get(key) not in ("", None):
                values[key] = str(values[key]).lower() in ("1", "true", "yes", "on")
```

`goodprice/web/routes.py` 的 `save_settings`：删除四个企微字段，追加三个开关：

```python
    serverchan_enabled: Optional[int] = Form(None),
    wecom_robot_enabled: Optional[int] = Form(None),
    vision_enabled: Optional[int] = Form(None),
```

`values` 中写入 `"serverchan_enabled": "1" if serverchan_enabled else "0"`（其余同理）；`wecom_webhook` 仍进"留空保持原值"列表。

`goodprice/web/templates/settings.html`：删除企微 corpid/agentid/secret/touser 输入，改为「消息通知」区块（Server酱开关 + 群机器人 Webhook 开关 + Webhook 输入）与「视觉模型」区块（启用开关 + base_url/key/model）。

`.env.example`：删除 `WECOM_CORPID/WECOM_AGENTID/WECOM_SECRET/WECOM_TOUSER`，追加：

```dotenv
SERVERCHAN_ENABLED=true
WECOM_ROBOT_ENABLED=true
VISION_ENABLED=true
```

`goodprice/main.py` 的 `_make_crawl_service`：删除 `WeComNotifier` 导入与装配；serverchan 需 `runtime.serverchan_enabled` 且 key 非空；webhook 需 `runtime.wecom_robot_enabled` 且 webhook 非空；vision 客户端仅当 `runtime.vision_enabled` 时构建，否则传空配置（disabled）。

删除 `goodprice/notify/wecom.py`。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_config.py tests/test_settings_service.py tests/test_notify.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 消息通知/视觉开关，移除企业微信应用消息模块"
```

## Task 4: 评分与通知文案适配视觉开关

**Files:**
- Modify: `goodprice/services/satisfaction.py`, `goodprice/services/crawl_service.py`, `goodprice/main.py`
- Test: `tests/test_satisfaction.py`, `tests/test_crawl_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_satisfaction.py` 追加：

```python
def test_compute_satisfaction_vision_off():
    assert compute_satisfaction(_listing(), vision_enabled=False) == 100.0  # 70 + 30
    assert compute_satisfaction(_listing(requirement_match=None, condition_score=None, seller_risk={"risk_level": "高"}), vision_enabled=False) == 35.0
```

`tests/test_crawl_service.py` 的 `test_vision_disabled_skips_stage2` 追加：

```python
    assert "视觉模型未启用" in notifier.messages[0].content
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n good-price pytest tests/test_satisfaction.py tests/test_crawl_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`goodprice/services/satisfaction.py`：

```python
def compute_satisfaction(listing, vision_enabled: bool = True) -> float:
    if vision_enabled:
        req_w, cond_w, seller_w = 50.0, 40.0, 10.0
    else:
        req_w, cond_w, seller_w = 70.0, 0.0, 30.0
    score = 0.0
    if listing.requirement_match is True:
        score += req_w
    elif listing.requirement_match is None:
        score += req_w / 2
    if cond_w and listing.condition_score is not None:
        score += min(cond_w, listing.condition_score * cond_w / 10)
    risk = None
    if isinstance(listing.seller_risk, dict):
        risk = listing.seller_risk.get("risk_level")
    score += {"低": seller_w, "中": seller_w / 2, "高": 0.0}.get(risk, 0.0)
    return score


def backfill_satisfaction(session_factory, vision_enabled: bool = True) -> int:
    ...
            listing.satisfaction = compute_satisfaction(listing, vision_enabled=vision_enabled)
```

`goodprice/services/crawl_service.py`：调用处改为 `compute_satisfaction(listing, vision_enabled=self.vision.enabled)`；`_notify` 中视觉未启用文案改为 `"品相分：视觉模型未启用，未评估\n"`。

`goodprice/main.py`：`backfill_satisfaction(session_factory, vision_enabled=settings_service.get().vision_enabled)`。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n good-price pytest tests/test_satisfaction.py tests/test_crawl_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat: 视觉关闭时评分权重调整（需求70+卖家30）与通知文案"
```

## Task 5: README 与全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README**

补充：消息通知页与删除、任务筛选、消息通知/视觉开关、企微仅保留群机器人。

- [ ] **Step 2: 全量验证**

Run: `conda run -n good-price pytest -v`
Expected: 全部通过，0 failed。

- [ ] **Step 3: 启动冒烟**

Run: `conda run -n good-price python -c "from goodprice.main import app; print(app.title)"`
Expected: 输出 `闲鱼盯价助手`。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "docs: 第五轮 README"
```

---

## 验收清单

- [ ] 消息通知页可单删/全选/批量删除；通知带标题内容
- [ ] 命中列表可下拉按任务筛选
- [ ] Server酱/群机器人/视觉模型三个开关生效；移除企微应用消息
- [ ] 视觉关闭时评分按 70/30 且文案注明未启用
- [ ] 全量 `pytest` 通过；`python -m goodprice` 可启动
