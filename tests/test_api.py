import time

from fastapi.testclient import TestClient

from goodprice.main import build_app


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
    client.app.state.guard.finish(task["id"])


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
