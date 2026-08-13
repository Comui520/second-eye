from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from goodprice.crawler.base import SellerData
from goodprice.models import Seller
from goodprice.services.seller_service import SellerService, compute_risk


class FakeSellerAdapter:
    def __init__(self, data=None, error=None):
        self.data = data or SellerData(
            seller_uid="1", positive_count=133, total_count=194, tags=["沟通愉快 13"]
        )
        self.error = error
        self.calls = 0

    def fetch_seller(self, user_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.data


def test_fetch_and_cache(session_factory):
    adapter = FakeSellerAdapter()
    service = SellerService(session_factory, adapter=adapter)
    seller = service.ensure_fresh("xianyu", "1", nickname="饼住呼吸")
    assert seller.positive_count == 133
    assert seller.positive_rate == pytest.approx(133 / 194)
    seller2 = service.ensure_fresh("xianyu", "1")
    assert adapter.calls == 1  # 7 天内不重复抓
    assert seller2 is not None


def test_refetch_after_cache_expiry(session_factory):
    adapter = FakeSellerAdapter()
    service = SellerService(session_factory, adapter=adapter)
    service.ensure_fresh("xianyu", "1")
    with session_factory() as session:
        seller = session.query(Seller).one()
        seller.last_fetched_at = datetime.now() - timedelta(days=8)
        session.commit()
    service.ensure_fresh("xianyu", "1")
    assert adapter.calls == 2


def test_fetch_failure_returns_existing(session_factory):
    adapter = FakeSellerAdapter(error=RuntimeError("网络错误"))
    service = SellerService(session_factory, adapter=adapter)
    assert service.ensure_fresh("xianyu", "1") is None
    with session_factory() as session:
        assert session.query(Seller).count() == 0


def test_compute_risk_rules():
    low = SimpleNamespace(positive_rate=0.99, positive_count=99, total_count=100, credit_label="")
    mid = SimpleNamespace(positive_rate=0.92, positive_count=92, total_count=100, credit_label="")
    high = SimpleNamespace(positive_rate=0.8, positive_count=80, total_count=100, credit_label="")
    assert compute_risk(low)[0] == "低"
    assert compute_risk(mid)[0] == "中"
    assert compute_risk(high)[0] == "高"
    assert compute_risk(None, credit_label="卖家信用极好")[0] == "低"
    assert compute_risk(None)[0] == "未知"


def test_compute_risk_prefers_detail_rate():
    seller = SimpleNamespace(positive_rate=0.68, positive_count=133, total_count=194, credit_label="")
    assert compute_risk(seller, detail_rate=100.0)[0] == "低"


def test_compute_risk_real_seller_without_label(session_factory):
    with session_factory() as session:
        session.add(Seller(platform="xianyu", seller_uid="9", positive_count=133, total_count=194))
        session.commit()
    with session_factory() as session:
        seller = session.query(Seller).one()
    level, reason = compute_risk(seller, credit_label=None)
    assert level == "高"
    assert "133/194" in reason


def test_ensure_fresh_stores_credit_label(session_factory):
    adapter = FakeSellerAdapter()
    service = SellerService(session_factory, adapter=adapter)
    service.ensure_fresh("xianyu", "1", credit_label="卖家信用极好")
    with session_factory() as session:
        seller = session.query(Seller).one()
    assert seller.credit_label == "卖家信用极好"
