from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from goodprice.db import Base


def _now() -> datetime:
    return datetime.now()


class WatchTask(Base):
    __tablename__ = "watch_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    keyword: Mapped[str] = mapped_column(String(200))
    max_price: Mapped[float] = mapped_column(Float, default=0.0)
    condition_requirement: Mapped[str] = mapped_column(Text, default="")
    min_condition_score: Mapped[int] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(50), default="xianyu")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(default=True)
    fetch_detail: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_run_count: Mapped[int] = mapped_column(Integer, default=0)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_listing_platform_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[float] = mapped_column(Float)
    url: Mapped[str] = mapped_column(Text)
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    seller: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    condition_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    condition_detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requirement_match: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    requirement_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    price: Mapped[float] = mapped_column(Float)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("watch_tasks.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), default="log")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    listing: Mapped[Optional[Listing]] = relationship(back_populates="notifications")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
