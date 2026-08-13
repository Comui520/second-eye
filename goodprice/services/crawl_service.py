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
from goodprice.services.satisfaction import compute_satisfaction

logger = logging.getLogger(__name__)

GONE_THRESHOLD = 3
MAX_BATCH_VALUE_ITEMS = 30


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
        stats = {
            "found": 0,
            "new": 0,
            "notified": 0,
            "backfilled": 0,
            "reevaluated": 0,
            "gone": 0,
        }
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
        batch_rows: list[dict] = []
        pending: list[tuple] = []  # (task, listing, old_price, is_renotify)
        seen_ids: set[int] = set()
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            try:
                for data in items:
                    if task.max_price and data.price > task.max_price:
                        continue
                    old_price: Optional[float] = None
                    existing = (
                        session.query(Listing)
                        .filter(
                            Listing.platform == task.platform,
                            Listing.external_id == data.external_id,
                        )
                        .first()
                    )
                    if existing is not None and abs(existing.price - data.price) > 0.001:
                        old_price = existing.price
                    listing, is_new = self._upsert_listing(session, task, data)
                    seen_ids.add(listing.id)
                    if self._is_blocked(session, listing):
                        session.commit()
                        continue
                    listing.status = "active"
                    listing.missed_count = 0
                    if is_new:
                        stats["new"] += 1
                        if task.fetch_detail:
                            self._fetch_detail(session, listing)
                        if not self._requirement_pass(session, listing, task):
                            session.commit()
                            continue
                        self._condition_analysis(session, listing, task)
                        if self._condition_gate_fails(task, listing):
                            session.commit()
                            continue
                        self._seller_check(session, listing, task)
                        batch_rows.append(self._batch_row(listing))
                        pending.append((task, listing, old_price, False))
                    else:
                        changed = old_price is not None or listing.status == "gone"
                        if changed:
                            stats["reevaluated"] += 1
                            if not self._requirement_pass(session, listing, task):
                                session.commit()
                                continue
                            self._condition_analysis(session, listing, task)
                            if self._condition_gate_fails(task, listing):
                                session.commit()
                                continue
                            self._seller_check(session, listing, task)
                            batch_rows.append(self._batch_row(listing))
                            pending.append((task, listing, old_price, True))
                        else:
                            if self._backfill(session, listing, task):
                                stats["backfilled"] += 1
                    session.commit()
                # 下架软标记：本轮未出现的该任务商品
                for other in (
                    session.query(Listing)
                    .filter(Listing.task_id == task.id)
                    .filter(~Listing.id.in_(seen_ids))
                ):
                    other.missed_count = (other.missed_count or 0) + 1
                    if other.missed_count >= GONE_THRESHOLD and other.status != "gone":
                        other.status = "gone"
                        stats["gone"] += 1
                session.commit()
                # 批性价比：本批通过筛选的商品统一横向对比
                if batch_rows and self._value_client() is not None:
                    self._batch_value(session, batch_rows)
                    session.commit()
                # 通知：新品在批性价比后统一发出；重评仅满意度提高时发出
                for task, listing, old_price, is_renotify in pending:
                    satisfaction = compute_satisfaction(
                        listing, vision_enabled=self.vision.enabled
                    )
                    listing.satisfaction = satisfaction
                    if (
                        is_renotify
                        and listing.last_notified_satisfaction is not None
                        and satisfaction <= listing.last_notified_satisfaction
                    ):
                        continue
                    self._notify(
                        session,
                        task,
                        listing,
                        satisfaction,
                        old_price=old_price,
                        is_renotify=is_renotify,
                    )
                    stats["notified"] += 1
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
                task_id=task.id,
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

    def _is_blocked(self, session, listing: Listing) -> bool:
        if listing.blocked:
            return True
        if not listing.seller_uid:
            return False
        from goodprice.models import Seller

        seller = (
            session.query(Seller)
            .filter_by(platform=listing.platform, seller_uid=listing.seller_uid)
            .first()
        )
        if seller and seller.blocked:
            listing.blocked = True
            return True
        return False

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
        if detail.variants:
            listing.variants = detail.variants[:6]
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
            listing.requirement_reason = f"需求分析失败，未过滤（{exc}）"[:500]
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

    def _condition_gate_fails(self, task: WatchTask, listing: Listing) -> bool:
        return bool(
            task.min_condition_score
            and listing.condition_score is not None
            and listing.condition_score < task.min_condition_score
        )

    def _batch_row(self, listing: Listing) -> dict:
        defects = []
        if isinstance(listing.condition_detail, dict):
            defects = listing.condition_detail.get("defects") or []
        risk = None
        if isinstance(listing.seller_risk, dict):
            risk = listing.seller_risk.get("risk_level")
        return {
            "listing_id": listing.id,
            "external_id": listing.external_id,
            "title": listing.title,
            "price": listing.price,
            "condition_score": listing.condition_score,
            "defects": defects,
            "seller_risk": risk,
        }

    def _value_client(self):
        """批量性价比只依赖文字，优先用文本 LLM，未配置时退回视觉模型。"""
        for client in (self.llm, self.vision):
            if getattr(client, "enabled", False):
                return client
        return None

    def _batch_value(self, session, rows: list[dict]) -> None:
        client = self._value_client()
        if client is None:
            return
        try:
            result = client.analyze_batch_value(rows[:MAX_BATCH_VALUE_ITEMS])
        except Exception as exc:
            logger.warning("批量性价比分析失败，跳过: %s", exc)
            return
        now = datetime.now()
        scores = result.get("scores") or {}
        best = result.get("best")
        for row in rows:
            listing = session.get(Listing, row["listing_id"])
            if listing is None:
                continue
            score = scores.get(row["external_id"])
            if score is not None:
                listing.value_score = score
                listing.value_batch_at = now
                listing.best_of_batch = best == row["external_id"]

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

    def _notify(
        self,
        session,
        task: WatchTask,
        listing: Listing,
        satisfaction: float,
        old_price: Optional[float] = None,
        is_renotify: bool = False,
    ) -> None:
        def _fmt(value: float) -> str:
            return format(value, "g")

        lines = [f"价格：{_fmt(listing.price)} 元"]
        if is_renotify and old_price is not None:
            lines.append(f"价格更新重推：{_fmt(old_price)} → {_fmt(listing.price)} 元")
        if listing.requirement_match is not None:
            status = "是" if listing.requirement_match else "否"
            reason = listing.requirement_reason or ""
            line = f"需求匹配：{status}"
            if reason:
                line += f"（{reason}）"
            lines.append(line)
        if listing.condition_score is not None:
            lines.append(f"品相分：{listing.condition_score}")
        elif self.vision.enabled:
            err = ""
            if isinstance(listing.condition_detail, dict):
                err = listing.condition_detail.get("error") or ""
            lines.append(f"品相分：未评估（{err or '分析失败'}）")
        else:
            lines.append("品相分：视觉模型未启用，未评估")
        if listing.value_score is not None:
            lines.append(f"性价比：{listing.value_score}/10")
        else:
            lines.append("性价比：未评估")
        if listing.best_of_batch:
            lines.append("本批最优")
        if listing.variants:
            parts = " · ".join(
                f"{v.get('name')} {_fmt(v.get('price'))} 元" for v in listing.variants[:6]
            )
            lines.append(f"规格：{parts}")
        if listing.seller_risk:
            risk = listing.seller_risk
            name = risk.get("nickname") or "卖家"
            level = risk.get("risk_level")
            reason = risk.get("risk_reason") or ""
            rate = risk.get("positive_rate")
            rate_txt = f"好评率 {rate * 100:.0f}%" if isinstance(rate, (int, float)) else ""
            lines.append(f"卖家：{name} {rate_txt} · 风险{level}（{reason}）")
        if isinstance(listing.condition_detail, dict):
            extra = listing.condition_detail.get("reason", "")
            if extra:
                lines.append(extra)
        message = NotificationMessage(
            title=f"[{task.keyword}] {listing.title}",
            content="\n".join(lines),
            url=listing.url,
        )
        for channel, notifier in self.notifiers:
            try:
                notifier.send(message)
                session.add(
                    Notification(
                        listing_id=listing.id,
                        task_id=task.id,
                        channel=channel,
                        status="sent",
                        title=message.title,
                        content=message.content,
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
                        title=message.title,
                        content=message.content,
                    )
                )
        listing.notified_at = datetime.now()
        listing.last_notified_satisfaction = satisfaction

    def _record_error(self, task_id: int, message: str) -> None:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.last_error = message[:1000]
                session.commit()
