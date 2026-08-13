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


class CrawlerAuthError(RuntimeError):
    """登录态失效或需要登录。"""
