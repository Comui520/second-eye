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


def test_update_task(session_factory):
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "a"})
    updated = service.update_task(task.id, {"keyword": "b", "max_price": "500", "condition_requirement": "屏幕完好"})
    assert updated.keyword == "b"
    assert updated.max_price == 500.0
    assert updated.condition_requirement == "屏幕完好"
    assert service.update_task(999, {"keyword": "x"}) is None
