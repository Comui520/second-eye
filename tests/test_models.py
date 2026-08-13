import pytest
from sqlalchemy.exc import IntegrityError

from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask


def test_watch_task_crud(session_factory):
    with session_factory() as session:
        task = WatchTask(keyword="iPhone 13", max_price=3000, min_condition_score=6)
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    with session_factory() as session:
        loaded = session.get(WatchTask, task_id)
        assert loaded.keyword == "iPhone 13"
        assert loaded.enabled is True
        assert loaded.last_error is None


def test_listing_unique_platform_external(session_factory):
    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1001", title="a", price=1.0, url="u"))
        session.commit()
    with session_factory() as session:
        session.add(Listing(platform="xianyu", external_id="1001", title="b", price=2.0, url="v"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_listing_relations(session_factory):
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="2001", title="t", price=9.9, url="u")
        session.add(listing)
        session.flush()
        session.add(PriceSnapshot(listing_id=listing.id, price=9.9))
        session.add(Notification(listing_id=listing.id, channel="log", status="sent"))
        session.commit()
        session.refresh(listing)
        assert len(listing.snapshots) == 1
        assert len(listing.notifications) == 1
