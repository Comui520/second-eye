from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.services.task_service import TaskService


def test_missing_or_paused_task_does_not_report_started(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    calls = []
    app.state.run_job = lambda task_id: calls.append(task_id)
    client = TestClient(app)
    assert client.post("/tasks/9999/run").status_code == 404
    task = TaskService(session_factory).create_task({"keyword": "offline", "enabled": False})
    assert client.post(f"/tasks/{task.id}/run").status_code == 409
    assert not calls
