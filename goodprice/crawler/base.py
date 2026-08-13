from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ListingData:
    external_id: str
    title: str
    price: float
    url: str
    image_urls: list[str] = field(default_factory=list)
    seller: Optional[str] = None
    location: Optional[str] = None
    published_at: Optional[datetime] = None


@dataclass
class ListingDetail:
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)
    seller_uid: Optional[str] = None
    seller_name: Optional[str] = None
    credit_label: Optional[str] = None
    positive_rate: Optional[float] = None
    sold_count: Optional[int] = None


@dataclass
class SellerData:
    seller_uid: str
    nickname: str = ""
    positive_count: Optional[int] = None
    total_count: Optional[int] = None
    tags: list[str] = field(default_factory=list)


class CrawlerAuthError(RuntimeError):
    """登录态失效或需要登录。"""
