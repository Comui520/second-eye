from datetime import datetime
from typing import Any, Optional

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
    min_price: Mapped[float] = mapped_column(Float, default=0.0)
    exclude_words: Mapped[str] = mapped_column(Text, default="")
    condition_requirement: Mapped[str] = mapped_column(Text, default="")
    min_condition_score: Mapped[int] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(50), default="xianyu")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(default=True)
    fetch_detail: Mapped[bool] = mapped_column(default=True)
    target_variant: Mapped[str] = mapped_column(Text, default="")
    notify_pending_price: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_run_count: Mapped[int] = mapped_column(Integer, default=0)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", "task_id", name="uq_listing_task_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[float] = mapped_column(Float)
    offer_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    offer_verified_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    offer_task_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(Text)
    image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    seller: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    condition_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    condition_detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requirement_match: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    requirement_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seller_uid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    seller_risk: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    blocked: Mapped[bool] = mapped_column(default=False)
    satisfaction: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    missed_count: Mapped[int] = mapped_column(Integer, default=0)
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    value_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    value_batch_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    best_of_batch: Mapped[bool] = mapped_column(default=False)
    last_notified_satisfaction: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("watch_tasks.id", ondelete="SET NULL"), nullable=True
    )

    snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )
    offers: Mapped[list["ListingOffer"]] = relationship(cascade="all, delete-orphan")


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
    offer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    event_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("watch_tasks.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), default="log")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    listing: Mapped[Optional[Listing]] = relationship(back_populates="notifications")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    legacy_install: Mapped[bool] = mapped_column(Boolean, default=False)


class ListingOffer(Base):
    __tablename__ = "listing_offers"
    __table_args__ = (UniqueConstraint("listing_id", "offer_key", name="uq_listing_offer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    offer_key: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    availability: Mapped[str] = mapped_column(String(20), default="unknown")
    last_notified_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_notified_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_notified_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    effective_price_state: Mapped[str] = mapped_column(String(20), default="unknown")
    effective_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    price_task_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observations: Mapped[list["OfferObservation"]] = relationship(cascade="all, delete-orphan")
    quotes: Mapped[list["ManualQuote"]] = relationship(cascade="all, delete-orphan")
    evaluations: Mapped[list["OfferEvaluationRecord"]] = relationship(cascade="all, delete-orphan")


class OfferObservation(Base):
    __tablename__ = "offer_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("listing_offers.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ManualQuote(Base):
    __tablename__ = "manual_quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("listing_offers.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OfferEvaluationRecord(Base):
    __tablename__ = "offer_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("listing_offers.id"), index=True)
    task_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Seller(Base):
    __tablename__ = "sellers"
    __table_args__ = (
        UniqueConstraint("platform", "seller_uid", name="uq_seller_platform_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    seller_uid: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    credit_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    positive_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    positive_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    blocked: Mapped[bool] = mapped_column(default=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))
    offer_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_locator: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    parent_video_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timestamp_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    relative_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    failure: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaskRun(Base):
    __tablename__ = "task_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30), default="crawl")
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    followup: Mapped[bool] = mapped_column(default=False)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (UniqueConstraint("run_id", "stage", "input_fingerprint", name="uq_work_input"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("task_runs.id"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    stage: Mapped[str] = mapped_column(String(20), index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    not_before: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    business_key: Mapped[str] = mapped_column(String(128), unique=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_version: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[int] = mapped_column(Integer)
    listing_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "channel", name="uq_delivery_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("notification_events.id"), index=True)
    channel: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    not_before: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
