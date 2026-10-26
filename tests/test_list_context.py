import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from goodprice.main import build_app
from goodprice.web.list_context import ListContext, safe_return_to


def test_roundtrip_preserves_filters_and_loaded_range():
    context = ListContext.from_query(QueryParams("task_id=7&show=blocked&sort=price_asc&price_state=pending&loaded=200"))
    assert ListContext.from_query(QueryParams(context.url().split("?", 1)[1])) == context
    assert context.loaded == 200


@pytest.mark.parametrize("raw", ["//example.com/listings", "https://evil.example/listings", "//[", "/listings%0d%0aLocation:evil", "/%252f%252fevil", "/listings\\evil", "/listings?return_to=https://evil"])
def test_return_to_rejects_external_or_ambiguous_input(raw):
    assert safe_return_to(raw, "/listings") == "/listings"


def test_safe_query_and_invalid_duplicate_parameters():
    assert safe_return_to("/listings?task_id=7&show=active", "/listings") == "/listings?task_id=7&show=active"
    context = ListContext.from_query(QueryParams("task_id=1&task_id=2&loaded=999999&sort=unsafe"))
    assert context.task_id is None and context.loaded <= 1000 and context.sort == "satisfaction"
    assert ListContext.from_query({"task_id": "²", "loaded": "²"}).task_id is None


def test_detail_links_and_actions_keep_return_context(session_factory, base_settings):
    from goodprice.models import Listing
    from goodprice.services.task_service import TaskService
    task = TaskService(session_factory).create_task({"keyword": "test"})
    with session_factory() as session:
        row = Listing(platform="x", external_id="1", task_id=task.id, title="test", price=1, url="x")
        session.add(row)
        session.commit()
    client = TestClient(build_app(base_settings, session_factory, with_scheduler=False))
    context = f"/listings?task_id={task.id}&show=all&sort=newest&loaded=200"
    html = client.get(context).text
    assert "return_to=" in html and "loaded%3D200" in html
    detail = client.get(f"/listings/{row.id}", params={"return_to": context}).text
    assert f'task_id={task.id}&amp;show=all' in detail
    response = client.post(f"/listings/{row.id}/block", params={"return_to": context}, follow_redirects=False)
    assert "show=all" in response.headers["location"] and "loaded=200" in response.headers["location"]
