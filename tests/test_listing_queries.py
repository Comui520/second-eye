from sqlalchemy import event

from goodprice.models import Listing
from goodprice.services.offer_pipeline import present_listings


def test_list_presentation_uses_bounded_queries_not_per_listing(session_factory):
    with session_factory() as session:
        rows = [Listing(platform="x", external_id=str(i), title="fixture", url="x", price=1) for i in range(25)]
        session.add_all(rows)
        session.commit()
    queries = []
    engine = session_factory.kw["bind"]
    def count(*args): queries.append(1)
    event.listen(engine, "before_cursor_execute", count)
    try:
        views = present_listings(rows, session_factory)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(views) == 25 and not any(row.price_confirmed for row in views)
    assert len(queries) <= 8, f"25-row read used {len(queries)} SQL queries"
