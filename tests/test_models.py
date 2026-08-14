import pytest
from sqlalchemy.exc import IntegrityError

from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask
from goodprice.models import Seller


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


def test_listing_unique_per_task(session_factory):
    with session_factory() as session:
        t1 = WatchTask(keyword="k1")
        t2 = WatchTask(keyword="k2")
        session.add_all([t1, t2])
        session.flush()
        session.add(Listing(platform="xianyu", external_id="1001", title="a", price=1, url="u", task_id=t1.id))
        session.add(Listing(platform="xianyu", external_id="1001", title="b", price=2, url="v", task_id=t2.id))
        session.commit()  # 不同任务允许同一外部 ID
    with session_factory() as session:
        t = WatchTask(keyword="k")
        session.add(t)
        session.flush()
        session.add(Listing(platform="xianyu", external_id="1001", title="c", price=3, url="w", task_id=t.id))
        session.flush()
        session.add(Listing(platform="xianyu", external_id="1001", title="d", price=4, url="x", task_id=t.id))
        with pytest.raises(IntegrityError):
            session.commit()  # 同一任务重复外部 ID 仍拦截


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


def test_round2_model_columns(session_factory):
    with session_factory() as session:
        task = WatchTask(keyword="k")
        session.add(task)
        session.flush()
        listing = Listing(
            platform="xianyu", external_id="1", title="t", price=1.0, url="u", description="d"
        )
        session.add(listing)
        session.commit()
        assert task.fetch_detail is True
        assert listing.description == "d"
        assert listing.requirement_match is None
        assert listing.requirement_reason is None


def test_seller_crud_and_listing_columns(session_factory):
    with session_factory() as session:
        seller = Seller(platform="xianyu", seller_uid="2672367114", positive_count=133, total_count=194)
        session.add(seller)
        session.flush()
        listing = Listing(
            platform="xianyu",
            external_id="3001",
            title="t",
            price=1.0,
            url="u",
            seller_uid="2672367114",
            seller_name="饼住呼吸",
            seller_risk={"risk_level": "低", "risk_reason": "好评率 100%"},
        )
        session.add(listing)
        session.commit()
        assert seller.positive_count == 133
        assert listing.seller_name == "饼住呼吸"
        assert listing.seller_risk["risk_level"] == "低"


def test_blocked_flags(session_factory):
    with session_factory() as session:
        l = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u", blocked=True)
        s = Seller(platform="xianyu", seller_uid="u1", blocked=True)
        session.add_all([l, s])
        session.commit()
        assert l.blocked is True
        assert s.blocked is True


def test_round7_listing_columns(session_factory):
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u")
        session.add(listing)
        session.commit()
        assert listing.status == "active"
        assert listing.missed_count == 0
        assert listing.variants == []
        assert listing.value_score is None
        assert listing.value_batch_at is None
        assert listing.best_of_batch is False
        assert listing.last_notified_satisfaction is None


def test_round8_task_columns(session_factory):
    with session_factory() as session:
        task = WatchTask(keyword="k")
        session.add(task)
        session.commit()
        assert task.min_price == 0.0
        assert task.exclude_words == ""
