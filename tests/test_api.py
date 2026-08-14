import time

from datetime import datetime

from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.models import Listing


def _client(base_settings, session_factory):
    app = build_app(settings=base_settings, session_factory=session_factory, with_scheduler=False)
    app.state.run_job = lambda task_id: None
    return TestClient(app, follow_redirects=False)


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


def test_run_redirect_includes_feedback_param(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    client.app.state.run_job = lambda task_id: None
    response = client.post(f"/tasks/{task['id']}/run")
    assert response.status_code == 303
    assert f"/tasks?run={task['id']}" in response.headers["location"]


def test_tasks_page_shows_run_banner(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post("/api/tasks", json={"keyword": "k"})
    response = client.get("/tasks?run=1")
    assert response.status_code == 200
    assert "已开始执行" in response.text


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
    task = client.post(
        "/api/tasks", json={"keyword": "iPhone 13", "condition_requirement": "屏幕完好"}
    ).json()
    client.app.state.guard.try_start(task["id"])
    response = client.get("/tasks")
    assert response.status_code == 200
    assert "屏幕完好" in response.text
    assert "运行中" in response.text
    assert "hx-get" in response.text  # HTMX 局部轮询替代整页刷新
    client.app.state.guard.finish(task["id"])


def test_edit_task_api_and_page(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "旧词"}).json()
    response = client.put(f"/api/tasks/{task['id']}", json={"keyword": "新词", "max_price": 300})
    assert response.status_code == 200
    assert response.json()["keyword"] == "新词"
    resp = client.get(f"/tasks/{task['id']}/edit")
    assert resp.status_code == 303
    assert f"/tasks/{task['id']}" in resp.headers["location"]
    page = client.get(f"/tasks/{task['id']}")
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


def test_tasks_progress_fragment(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    from goodprice.models import WatchTask

    with session_factory() as session:
        row = session.get(WatchTask, task["id"])
        row.last_run_at = datetime.now()
        session.add(
            Listing(
                platform="xianyu",
                external_id="9001",
                title="本次运行新商品",
                price=88.0,
                url="https://x/9001",
                first_seen_at=datetime.now(),
            )
        )
        session.commit()
    client.app.state.guard.try_start(task["id"])
    response = client.get("/tasks/progress")
    assert response.status_code == 200
    assert "本次运行新商品" in response.text
    assert "执行中" in response.text
    client.app.state.guard.finish(task["id"])


def test_listings_partial_and_poll(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        session.add(
            Listing(
                platform="xianyu",
                external_id="9002",
                title="列表页商品",
                price=66.0,
                url="https://x/9002",
            )
        )
        session.commit()
    response = client.get("/listings?partial=1")
    assert response.status_code == 200
    assert "列表页商品" in response.text
    assert 'id="listings-grid"' in response.text

    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    client.app.state.guard.try_start(task["id"])
    page = client.get("/listings")
    assert "hx-get" in page.text
    client.app.state.guard.finish(task["id"])


def test_block_unblock_listing(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        from goodprice.models import Listing

        session.add(Listing(platform="xianyu", external_id="1", title="t", price=1, url="u"))
        session.commit()
        listing_id = session.query(Listing).one().id
    resp = client.post(f"/listings/{listing_id}/block")
    assert resp.status_code == 303
    assert [d["title"] for d in client.get("/api/listings?show=blocked").json()] == ["t"]
    client.post(f"/listings/{listing_id}/unblock")
    assert client.get("/api/listings?show=blocked").json() == []


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
    assert f"/tasks/{task['id']}" in page.text
    assert f"/listings?task_id={task['id']}" in page.text


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
    assert len(client.get("/api/notifications").json()) == 1
    with session_factory() as session:
        session.add(Notification(channel="log", status="sent", title="x", content="y"))
        session.add(Notification(channel="log", status="sent", title="y", content="z"))
        session.commit()
        ids2 = [r.id for r in session.query(Notification).all()]
    client.post("/notifications/delete-batch", data={"ids": ids2})
    assert client.get("/api/notifications").json() == []


def test_listings_page_has_task_filter(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "镜头"}).json()
    page = client.get("/listings")
    assert f'value="{task["id"]}"' in page.text
    assert "镜头" in page.text


def test_listings_filter_empty_task_id_ok(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    resp = client.get("/listings?task_id=&sort=price_asc&show=active")
    assert resp.status_code == 200
    resp2 = client.get("/listings?partial=1&task_id=&sort=satisfaction&show=active")
    assert resp2.status_code == 200
    assert client.get("/api/listings?task_id=&sort=satisfaction").status_code == 200


def test_listings_show_gone_filter(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1", title="在售", price=1, url="u", status="active"))
        session.add(Listing(platform="xianyu", external_id="2", title="下架", price=1, url="v", status="gone"))
        session.add(Listing(platform="xianyu", external_id="3", title="拉黑", price=1, url="w", blocked=True))
        session.commit()
    assert [d["title"] for d in client.get("/api/listings?show=active").json()] == ["在售"]
    assert [d["title"] for d in client.get("/api/listings?show=gone").json()] == ["下架"]
    assert [d["title"] for d in client.get("/api/listings?show=blocked").json()] == ["拉黑"]
    assert len(client.get("/api/listings?show=all").json()) == 3
    page = client.get("/listings?show=gone")
    assert "已下架" in page.text
    assert 'value="gone"' in page.text


def test_listings_actions_redirect_with_toast(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        row = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u")
        session.add(row)
        session.commit()
        listing_id = row.id
    resp = client.post(f"/listings/{listing_id}/block")
    assert resp.status_code == 303
    assert "toast" in resp.headers["location"]
    resp = client.post(f"/listings/{listing_id}/delete")
    assert resp.status_code == 303
    assert "toast" in resp.headers["location"]
    assert "toast" in client.get("/listings").text  # 基础模板含 toast JS


def test_listing_detail_page_shows_analysis_blocks(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        from goodprice.models import Notification, PriceSnapshot

        listing = Listing(
            platform="xianyu",
            external_id="1",
            title="尼康16-85 镜头",
            price=580.0,
            url="https://www.goofish.com/item?id=1",
            description="镜片无霉无划痕，功能正常",
            requirement_match=True,
            requirement_reason="符合买家要求",
            condition_score=8,
            condition_detail={"defects": ["轻微使用痕迹"], "reason": "成色不错"},
            value_score=8,
            best_of_batch=True,
            seller_risk={"nickname": "兵哥哥", "risk_level": "低", "positive_rate": 0.99},
            variants=[{"name": "最低价", "price": 580.0}, {"name": "最高价", "price": 1299.0}],
        )
        session.add(listing)
        session.flush()
        session.add_all(
            [
                PriceSnapshot(listing_id=listing.id, price=699.0),
                PriceSnapshot(listing_id=listing.id, price=580.0),
            ]
        )
        session.add(
            Notification(listing_id=listing.id, channel="serverchan", status="sent", title="历史通知标题", content="内容")
        )
        session.commit()
        listing_id = listing.id
    page = client.get(f"/listings/{listing_id}")
    assert page.status_code == 200
    assert "尼康16-85 镜头" in page.text
    assert "描述原文" in page.text and "镜片无霉无划痕" in page.text
    assert "需求匹配" in page.text and "符合买家要求" in page.text
    assert "品相" in page.text and "轻微使用痕迹" in page.text
    assert "性价比" in page.text and "8/10" in page.text
    assert "本批最优" in page.text
    assert "兵哥哥" in page.text and "风险低" in page.text
    assert "规格" in page.text and "最低价" in page.text and "580.0" in page.text
    assert "首见价" in page.text and "降幅" in page.text
    assert "跳转商品页" in page.text
    assert "重新分析" in page.text and f"/listings/{listing_id}/reanalyze" in page.text
    assert "历史通知标题" in page.text


def test_listing_detail_shows_missing_analysis_reasons(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        listing = Listing(
            platform="xianyu",
            external_id="2",
            title="缺分析商品",
            price=100,
            url="u",
            requirement_match=None,
            requirement_reason="需求分析失败，未过滤（网络错误）",
            condition_detail={"error": "模型超时"},
        )
        session.add(listing)
        session.commit()
        listing_id = listing.id
    page = client.get(f"/listings/{listing_id}")
    assert "需求分析失败，未过滤（网络错误）" in page.text
    assert "模型超时" in page.text


def test_reanalyze_runs_in_background(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="3", title="t", price=1, url="u")
        session.add(listing)
        session.commit()
        listing_id = listing.id
    calls = []
    client.app.state.run_reanalyze = lambda lid: calls.append(lid)
    resp = client.post(f"/listings/{listing_id}/reanalyze")
    assert resp.status_code == 303
    assert "toast" in resp.headers["location"]
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    assert calls == [listing_id]


def test_listings_offset_pagination(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        for i in range(5):
            session.add(
                Listing(platform="xianyu", external_id=str(i), title=f"商品{i}", price=i, url=f"u{i}")
            )
        session.commit()
    assert len(client.get("/api/listings?offset=0").json()) == 5
    assert len(client.get("/api/listings?offset=3").json()) == 2
    assert client.get("/api/listings?offset=10").json() == []
    fragment = client.get("/listings/more?offset=0")
    assert fragment.status_code == 200
    assert "商品0" in fragment.text
    assert 'id="listings-grid"' not in fragment.text


def test_listing_actions_preserve_filters(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u")
        session.add(listing)
        session.commit()
        listing_id = listing.id
    resp = client.post(
        f"/listings/{listing_id}/block",
        headers={"referer": "/listings?task_id=7&sort=price_asc&show=active"},
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "task_id=7" in location and "sort=price_asc" in location and "show=active" in location
    assert "toast=" in location


def test_settings_page_glm_hints(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    page = client.get("/settings")
    assert "glm-4.7-flash" in page.text
    assert "open.bigmodel.cn" in page.text
    assert "小写" in page.text


def test_settings_login_route_and_status(base_settings, session_factory):
    client = _client(base_settings, session_factory)

    class FakeLogin:
        def __init__(self):
            self.started = 0

        def start(self):
            self.started += 1

        def status(self):
            return ("running", "正在打开浏览器窗口…")

    client.app.state.login_session = FakeLogin()
    resp = client.post("/settings/login")
    assert resp.status_code == 303
    assert "toast" in resp.headers["location"]
    page = client.get("/settings")
    assert "一键登录" in page.text
    frag = client.get("/settings/login-status")
    assert frag.status_code == 200
    assert "正在打开浏览器窗口" in frag.text


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


def test_settings_page_layout(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "消息通知" in page.text
    assert "max-w-2xl mx-auto" in page.text


def test_settings_save(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    response = client.post(
        "/settings",
        data={**_settings_form(), "xianyu_cookie": "a=1",
              "default_crawl_interval_minutes": "30",
              "default_crawl_jitter_minutes": "5"},
    )
    assert response.status_code == 303
    settings = client.app.state.settings_service.get()
    assert settings.xianyu_cookie == "a=1"
    assert settings.default_crawl_interval_minutes == 30


def test_settings_secret_empty_keeps_old_value(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post("/settings", data={**_settings_form(), "llm_api_key": "secret1"})
    client.post("/settings", data={**_settings_form(), "llm_api_key": ""})
    assert client.app.state.settings_service.get().llm_api_key == "secret1"


def test_settings_save_toggles(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    client.post(
        "/settings",
        data={**_settings_form(), "serverchan_enabled": "", "vision_enabled": ""},
    )
    settings = client.app.state.settings_service.get()
    assert settings.serverchan_enabled is False
    assert settings.vision_enabled is False
    assert settings.wecom_robot_enabled is True


def test_settings_save_recomputes_satisfaction(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    with session_factory() as session:
        session.add(
            Listing(
                platform="xianyu", external_id="1", title="t", price=1, url="u",
                requirement_match=True, condition_score=8, value_score=8,
                seller_risk={"risk_level": "低"}, satisfaction=99,
            )
        )
        session.commit()
    client.post("/settings", data={**_settings_form(), "xianyu_cookie": "a=1"})
    with session_factory() as session:
        assert session.query(Listing).one().satisfaction == 90.0


def test_task_api_accepts_round8_fields(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post(
        "/api/tasks",
        json={"keyword": "闪光灯", "min_price": 100, "max_price": 1000, "exclude_words": "配件 电池"},
    ).json()
    assert task["min_price"] == 100
    assert task["exclude_words"] == "配件 电池"
    updated = client.put(
        f"/api/tasks/{task['id']}",
        json={"keyword": "闪光灯", "min_price": 150, "exclude_words": "配件"},
    ).json()
    assert updated["min_price"] == 150
    assert updated["exclude_words"] == "配件"


def test_task_detail_page_shows_params_stats_and_recent(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post(
        "/api/tasks",
        json={"keyword": "闪光灯", "min_price": 100, "max_price": 1000, "exclude_words": "配件"},
    ).json()
    with session_factory() as session:
        listing = Listing(
            platform="xianyu", external_id="9001", title="任务详情页商品", price=200, url="u", task_id=task["id"]
        )
        session.add(listing)
        session.commit()
    page = client.get(f"/tasks/{task['id']}")
    assert page.status_code == 200
    assert "价格下限" in page.text
    assert "排除词" in page.text
    assert "配件" in page.text
    assert "任务详情页商品" in page.text
    assert f"/listings/{listing.id}" in page.text


def test_edit_page_redirects_to_detail(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    resp = client.get(f"/tasks/{task['id']}/edit")
    assert resp.status_code == 303
    assert f"/tasks/{task['id']}" in resp.headers["location"]


def test_task_detail_form_saves_round8_fields(base_settings, session_factory):
    client = _client(base_settings, session_factory)
    task = client.post("/api/tasks", json={"keyword": "k"}).json()
    resp = client.post(
        f"/tasks/{task['id']}/edit",
        data={
            "keyword": "闪光灯", "name": "", "max_price": "1000", "min_price": "100",
            "exclude_words": "配件 遮光罩", "condition_requirement": "",
            "min_condition_score": "0", "interval_minutes": "20",
            "fetch_detail": "1", "enabled": "1",
        },
    )
    assert resp.status_code == 303
    saved = client.get("/api/tasks").json()[0]
    assert saved["min_price"] == 100
    assert saved["exclude_words"] == "配件 遮光罩"


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
        "wecom_webhook": "",
        "serverchan_enabled": "1",
        "wecom_robot_enabled": "1",
        "vision_enabled": "1",
    }
