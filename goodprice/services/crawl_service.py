import logging
import random
import threading
import time
from datetime import datetime
from typing import Any, Optional

from goodprice.crawler.base import ListingData
from goodprice.crawler.parser import is_product_image
from goodprice.models import Listing, Notification, PriceSnapshot, WatchTask
from goodprice.notify.base import NotificationMessage

logger = logging.getLogger(__name__)


class TaskRunGuard:
    """进程内任务防重入守卫。"""

    def __init__(self):
        self._running: set[int] = set()
        self._lock = threading.Lock()

    def try_start(self, task_id: int) -> bool:
        with self._lock:
            if task_id in self._running:
                return False
            self._running.add(task_id)
            return True

    def finish(self, task_id: int) -> None:
        with self._lock:
            self._running.discard(task_id)

    def running_ids(self) -> set[int]:
        with self._lock:
            return set(self._running)


class CrawlService:
    def __init__(
        self,
        session_factory,
        adapter,
        llm,
        vision,
        notifiers,
        settings_service,
        guard=None,
        seller_service=None,
    ):
        self._session_factory = session_factory
        self.adapter = adapter
        self.llm = llm
        self.vision = vision
        self.notifiers = notifiers
        self.settings_service = settings_service
        self.guard = guard or TaskRunGuard()
        self.seller_service = seller_service

    def run_task(self, task_id: int) -> dict[str, Any]:
        if not self.guard.try_start(task_id):
            return {"found": 0, "new": 0, "notified": 0, "skipped": "already_running"}
        try:
            return self._run_impl(task_id)
        finally:
            self.guard.finish(task_id)

    def _run_impl(self, task_id: int) -> dict[str, Any]:
        stats = {"found": 0, "new": 0, "notified": 0, "backfilled": 0}
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
            try:
                for data in items:
                    if task.max_price and data.price > task.max_price:
                        continue
                    listing, is_new = self._upsert_listing(session, task, data)
                    if is_new:
                        stats["new"] += 1
                        if task.fetch_detail:
                            self._fetch_detail(session, listing)
                        if not self._requirement_pass(session, listing, task):
                            session.commit()
                            continue
                        self._condition_analysis(session, listing, task)
                        if (
                            task.min_condition_score
                            and listing.condition_score is not None
                            and listing.condition_score < task.min_condition_score
                        ):
                            session.commit()
                            continue
                        self._seller_check(session, listing, task)
                        if listing.notified_at is None:
                            self._notify(session, task, listing)
                            stats["notified"] += 1
                    else:
                        if self._backfill(session, listing, task):
                            stats["backfilled"] += 1
                    session.commit()
            except Exception as exc:
                task.last_error = f"处理商品时出错: {exc}"[:1000]
                session.commit()
                raise
            session.commit()
        return stats

    def _upsert_listing(self, session, task: WatchTask, data: ListingData):
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
            return listing, True
        if abs(listing.price - data.price) > 0.001:
            listing.price = data.price
            session.add(PriceSnapshot(listing_id=listing.id, price=data.price))
        listing.last_seen_at = datetime.now()
        return listing, False

    def _fetch_detail(self, session, listing: Listing) -> None:
        if not listing.url or listing.description:
            return
        try:
            detail = self.adapter.fetch_detail(listing.url)
        except Exception as exc:
            logger.warning("详情抓取失败，退回标题判断: %s", exc)
            return
        if detail.description:
            listing.description = detail.description
        merged = [u for u in (listing.image_urls or []) if is_product_image(u)]
        for url in detail.image_urls:
            if url not in merged and is_product_image(url):
                merged.append(url)
        listing.image_urls = merged[:8]
        if detail.seller_uid:
            listing.seller_uid = detail.seller_uid
            listing.seller_name = detail.seller_name or listing.seller_name
            listing.seller_risk = {
                "credit_label": detail.credit_label,
                "positive_rate": detail.positive_rate,
                "sold_count": detail.sold_count,
            }

    def _seller_check(self, session, listing: Listing, task: WatchTask) -> None:
        if not listing.seller_uid or self.seller_service is None:
            return
        raw = dict(listing.seller_risk or {})
        seller = self.seller_service.ensure_fresh(
            task.platform,
            listing.seller_uid,
            nickname=listing.seller_name,
            credit_label=raw.get("credit_label"),
            session=session,
        )
        from goodprice.services.seller_service import compute_risk

        level, reason = compute_risk(
            seller,
            credit_label=raw.get("credit_label"),
            detail_rate=raw.get("positive_rate"),
        )
        raw["risk_level"] = level
        raw["risk_reason"] = reason
        if seller is not None:
            raw["positive_count"] = seller.positive_count
            raw["total_count"] = seller.total_count
            raw["tags"] = seller.tags or []
        raw["nickname"] = listing.seller_name or (seller.nickname if seller else None)
        listing.seller_risk = raw

    def _requirement_pass(self, session, listing: Listing, task: WatchTask) -> bool:
        requirement = (task.condition_requirement or "").strip()
        if not requirement or not self.llm.enabled:
            return True
        try:
            verdict = self.llm.analyze_requirement(
                title=listing.title,
                description=listing.description or "",
                requirement=requirement,
            )
        except Exception as exc:
            logger.warning("需求分析失败，不拦截: %s", exc)
            listing.requirement_match = None
            listing.requirement_reason = "需求分析失败，未过滤"
            return True
        listing.requirement_match = verdict["matched"]
        listing.requirement_reason = verdict["reason"]
        return bool(verdict["matched"])

    def _condition_analysis(self, session, listing: Listing, task: WatchTask) -> None:
        if not self.vision.enabled:
            return
        valid = [u for u in (listing.image_urls or []) if is_product_image(u)]
        if not valid:
            listing.condition_detail = {"error": "无有效商品图"}
            return
        last_exc = None
        for _attempt in range(2):
            try:
                verdict = self.vision.analyze_condition(
                    title=listing.title,
                    price=listing.price,
                    description=listing.description or "",
                    requirement=task.condition_requirement or "",
                    image_urls=valid,
                )
            except Exception as exc:
                last_exc = exc
                continue
            listing.condition_score = verdict["condition_score"]
            listing.condition_detail = verdict
            return
        listing.condition_detail = {"error": str(last_exc)[:200]}

    def _backfill(self, session, listing: Listing, task: WatchTask) -> bool:
        changed = False
        requirement = (task.condition_requirement or "").strip()
        if requirement and self.llm.enabled and listing.requirement_match is None:
            try:
                verdict = self.llm.analyze_requirement(
                    title=listing.title,
                    description=listing.description or "",
                    requirement=requirement,
                )
                listing.requirement_match = verdict["matched"]
                listing.requirement_reason = verdict["reason"]
                changed = True
            except Exception as exc:
                logger.warning("回填需求分析失败: %s", exc)
        if self.vision.enabled and listing.condition_score is None:
            try:
                verdict = self.vision.analyze_condition(
                    title=listing.title,
                    price=listing.price,
                    description=listing.description or "",
                    requirement=requirement,
                    image_urls=listing.image_urls,
                )
                listing.condition_score = verdict["condition_score"]
                listing.condition_detail = verdict
                changed = True
            except Exception as exc:
                logger.warning("回填品相分析失败: %s", exc)
        return changed

    def _notify(self, session, task: WatchTask, listing: Listing) -> None:
        requirement_line = ""
        if listing.requirement_match is not None:
            status = "是" if listing.requirement_match else "否"
            reason = listing.requirement_reason or ""
            requirement_line = f"需求匹配：{status}"
            if reason:
                requirement_line += f"（{reason}）"
            requirement_line += "\n"
        if listing.condition_score is not None:
            score_line = f"品相分：{listing.condition_score}\n"
        elif self.vision.enabled:
            err = ""
            if isinstance(listing.condition_detail, dict):
                err = listing.condition_detail.get("error") or ""
            score_line = f"品相分：未评估（{err or '分析失败'}）\n"
        else:
            score_line = "品相分：未配置视觉模型，未评估\n"
        extra = ""
        if listing.condition_detail:
            extra = listing.condition_detail.get("reason", "")
        seller_line = ""
        if listing.seller_risk:
            risk = listing.seller_risk
            name = risk.get("nickname") or "卖家"
            level = risk.get("risk_level")
            reason = risk.get("risk_reason") or ""
            rate = risk.get("positive_rate")
            rate_txt = f"好评率 {rate * 100:.0f}%" if isinstance(rate, (int, float)) else ""
            seller_line = f"卖家：{name} {rate_txt} · 风险{level}（{reason}）\n"
        message = NotificationMessage(
            title=f"[{task.keyword}] {listing.title}",
            content=f"价格：{listing.price} 元\n{requirement_line}{score_line}{seller_line}{extra}",
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
