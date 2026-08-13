import logging
import random
import time
from datetime import datetime
from typing import Any, Optional

from goodprice.crawler.base import ListingData
from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask
from goodprice.notify.base import NotificationMessage

logger = logging.getLogger(__name__)


class CrawlService:
    def __init__(self, session_factory, adapter, llm, notifiers, settings_service):
        self._session_factory = session_factory
        self.adapter = adapter
        self.llm = llm
        self.notifiers = notifiers  # [(channel_name, notifier), ...]
        self.settings_service = settings_service

    def run_task(self, task_id: int) -> dict[str, int]:
        stats = {"found": 0, "new": 0, "notified": 0}
        settings = self.settings_service.get()
        jitter = int(settings.default_crawl_jitter_minutes)
        if jitter:
            time.sleep(random.uniform(0, jitter * 60))
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task is None:
                raise RuntimeError(f"任务 {task_id} 不存在")
            task.last_run_at = datetime.now()
            task.last_error = None
            session.commit()
        try:
            items = self.adapter.search(task.keyword)
        except Exception as exc:
            self._record_error(task_id, f"抓取失败: {exc}")
            raise
        stats["found"] = len(items)
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            for data in items:
                if task.max_price and data.price > task.max_price:
                    continue
                listing = self._upsert_listing(session, task, data)
                if listing is None:
                    continue
                stats["new"] += 1
                verdict = self._analyze(session, listing, task)
                if verdict is not None and verdict["condition_score"] < task.min_condition_score:
                    continue
                if listing.notified_at is None:
                    self._notify(session, task, listing)
                    stats["notified"] += 1
            session.commit()
        return stats

    def _upsert_listing(
        self, session, task: WatchTask, data: ListingData
    ) -> Optional[Listing]:
        listing = (
            session.query(Listing)
            .filter(Listing.platform == task.platform, Listing.external_id == data.external_id)
            .first()
        )
        if listing is None:
            listing = Listing(
                platform=task.platform,
                external_id=data.external_id,
                title=data.title,
                price=data.price,
                url=data.url,
                image_urls=data.image_urls,
                seller=data.seller,
                location=data.location,
                published_at=data.published_at,
            )
            session.add(listing)
            session.flush()
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
            return listing
        if abs(listing.price - data.price) > 0.001:
            listing.price = data.price
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
        listing.last_seen_at = datetime.now()
        return None

    def _analyze(self, session, listing: Listing, task: WatchTask) -> Optional[dict[str, Any]]:
        if not self.llm.enabled:
            return None
        try:
            verdict = self.llm.analyze_listing(
                title=listing.title,
                price=listing.price,
                description=task.condition_requirement,
                requirement=task.condition_requirement,
                image_urls=listing.image_urls,
            )
        except Exception as exc:
            logger.warning("LLM 分析失败，降级为仅价格命中: %s", exc)
            return None
        listing.condition_score = verdict["condition_score"]
        listing.condition_detail = verdict
        return verdict

    def _notify(self, session, task: WatchTask, listing: Listing) -> None:
        reason = ""
        if listing.condition_detail:
            reason = listing.condition_detail.get("reason", "")
        message = NotificationMessage(
            title=f"[{task.keyword}] {listing.title}",
            content=(
                f"价格：{listing.price} 元\n"
                f"品相分：{listing.condition_score or '未评估'}\n{reason}"
            ),
            url=listing.url,
        )
        for channel, notifier in self.notifiers:
            try:
                notifier.send(message)
                session.add(
                    Notification(
                        listing_id=listing.id, task_id=task.id, channel=channel, status="sent"
                    )
                )
            except Exception as exc:
                logger.warning("通知[%s]失败: %s", channel, exc)
                session.add(
                    Notification(
                        listing_id=listing.id,
                        task_id=task.id,
                        channel=channel,
                        status="failed",
                        detail=str(exc),
                    )
                )
        listing.notified_at = datetime.now()

    def _record_error(self, task_id: int, message: str) -> None:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.last_error = message[:1000]
                session.commit()
