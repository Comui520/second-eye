import logging
from datetime import datetime, timedelta
from typing import Optional

from goodprice.models import Seller

logger = logging.getLogger(__name__)
CACHE_DAYS = 7


def compute_risk(
    seller: Optional[Seller],
    credit_label: Optional[str] = None,
    detail_rate: Optional[float] = None,
):
    """返回 (风险等级, 一句话理由)。只提示不拦截。"""
    rate = detail_rate if detail_rate is not None else (seller.positive_rate if seller else None)
    label = credit_label or (seller.credit_label if seller else "")
    if rate is not None:
        pct = rate * 100
        if rate >= 0.98:
            return "低", f"好评率 {pct:.0f}%"
        if rate >= 0.90:
            return "中", f"好评率 {pct:.0f}%"
        return "高", f"好评率 {pct:.0f}%"
    if label:
        if "极好" in label:
            return "低", label
        if "良好" in label or label.endswith("好"):
            return "中", label
        return "高", label
    if seller and seller.positive_count is not None and seller.total_count:
        pct = seller.positive_count / seller.total_count * 100
        if pct >= 98:
            return "低", f"好评 {seller.positive_count}/{seller.total_count}"
        if pct >= 90:
            return "中", f"好评 {seller.positive_count}/{seller.total_count}"
        return "高", f"好评 {seller.positive_count}/{seller.total_count}"
    return "未知", "卖家数据不足"


class SellerService:
    def __init__(self, session_factory, adapter=None):
        self._session_factory = session_factory
        self.adapter = adapter

    def get(self, platform: str, seller_uid: str) -> Optional[Seller]:
        with self._session_factory() as session:
            return (
                session.query(Seller)
                .filter_by(platform=platform, seller_uid=seller_uid)
                .first()
            )

    def ensure_fresh(
        self, platform: str, seller_uid: str, nickname: Optional[str] = None
    ) -> Optional[Seller]:
        seller = self.get(platform, seller_uid)
        stale = (
            seller is None
            or seller.last_fetched_at is None
            or datetime.now() - seller.last_fetched_at > timedelta(days=CACHE_DAYS)
        )
        if not stale or self.adapter is None:
            return seller
        try:
            data = self.adapter.fetch_seller(seller_uid)
        except Exception as exc:
            logger.warning("卖家 %s 数据抓取失败: %s", seller_uid, exc)
            return seller
        with self._session_factory() as session:
            seller = (
                session.query(Seller)
                .filter_by(platform=platform, seller_uid=seller_uid)
                .first()
            )
            if seller is None:
                seller = Seller(platform=platform, seller_uid=seller_uid)
                session.add(seller)
            if data.nickname:
                seller.nickname = data.nickname
            elif nickname:
                seller.nickname = nickname
            seller.positive_count = data.positive_count
            seller.total_count = data.total_count
            seller.tags = data.tags
            if data.positive_count is not None and data.total_count:
                seller.positive_rate = data.positive_count / data.total_count
            seller.last_fetched_at = datetime.now()
            session.commit()
            session.refresh(seller)
            return seller
