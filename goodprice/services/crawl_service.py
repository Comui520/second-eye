import logging
import threading
from datetime import datetime
from typing import Any

from goodprice.crawler.base import ListingData, ListingDetail
from goodprice.crawler.parser import is_product_image
from goodprice.models import Listing, PriceSnapshot, WatchTask

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
        media_service=None,
    ):
        self._session_factory = session_factory
        self.adapter = adapter
        self.llm = llm
        self.vision = vision
        self.notifiers = notifiers
        self.settings_service = settings_service
        self.guard = guard or TaskRunGuard()
        self.seller_service = seller_service
        self.media_service = media_service
        self._judger = None

    def run_task(self, task_id: int) -> dict[str, Any]:
        if not self.guard.try_start(task_id):
            logger.info("任务 %s 已在运行，忽略重复触发", task_id)
            return {"found": 0, "new": 0, "notified": 0, "skipped": "already_running"}
        try:
            from goodprice.services.offer_crawl import OfferCrawlRunner

            return OfferCrawlRunner(self).run(task_id)
        except Exception as exc:
            self._record_error(task_id, f"处理商品时出错: {exc}"[:1000])
            raise
        finally:
            self.guard.finish(task_id)

    def _upsert_listing(self, session, task: WatchTask, data: ListingData):
        listing = (
            session.query(Listing)
            .filter(
                Listing.platform == task.platform,
                Listing.external_id == data.external_id,
                Listing.task_id == task.id,
            )
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

    def _fetch_detail(self, session, listing: Listing, force: bool = False) -> ListingDetail | None:
        if not listing.url:
            return
        if not force and listing.description:
            return
        try:
            detail = self.adapter.fetch_detail(listing.url)
        except Exception as exc:
            logger.warning("详情抓取失败，退回标题判断: %s", exc)
            return
        listing.description = detail.description
        if self.media_service:
            self.media_service.record(listing.id, detail.media)
        merged = []
        for url in detail.image_urls:
            if url not in merged and is_product_image(url):
                merged.append(url)
        listing.image_urls = merged[:8]
        listing.variants = detail.variants[:6]
        if detail.seller_uid:
            listing.seller_uid = detail.seller_uid
            listing.seller_name = detail.seller_name or listing.seller_name
            listing.seller_risk = {
                **(listing.seller_risk or {}),
                "credit_label": detail.credit_label,
                "positive_rate": detail.positive_rate,
                "sold_count": detail.sold_count,
            }
        return detail

    def reanalyze_listing(self, listing_id: int) -> dict[str, Any]:
        """Revalidate offer evidence before analysis, without sending notifications."""
        from goodprice.services.offer_crawl import OfferCrawlRunner

        return OfferCrawlRunner(self).reanalyze(listing_id)

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
        verdict = self._requirement_verdict(listing, requirement)
        if verdict is not None:
            matched, reason = verdict
            listing.requirement_match = matched
            listing.requirement_reason = reason
            return bool(matched)
        try:
            result = self.llm.analyze_requirement(
                title=listing.title,
                description=listing.description or "",
                requirement=requirement,
            )
        except Exception as exc:
            logger.warning("需求分析失败，不拦截: %s", exc)
            listing.requirement_match = None
            listing.requirement_reason = f"需求分析失败，未过滤（{exc}）"[:500]
            return True
        listing.requirement_match = result["matched"]
        listing.requirement_reason = result["reason"]
        return bool(result["matched"])

    def _requirement_verdict(self, listing: Listing, requirement: str):
        """Jev 判断层的需求匹配结论；返回 (matched, reason) 或 None（回落原路径）。"""
        judger = self._judger
        if judger is None or not judger.enabled:
            return None
        try:
            verdict = judger.analyze_requirement(
                title=listing.title,
                description=listing.description or "",
                requirement=requirement,
            )
        except Exception as exc:
            logger.warning("Jev 需求判断失败，回落原模型: %s", exc)
            return None
        if verdict.confidence < judger.auto_threshold:
            logger.info(
                "Jev 需求判断置信度 %.2f 低于阈值 %.2f，回落原模型",
                verdict.confidence,
                judger.auto_threshold,
            )
            return None
        return verdict.matched, f"{verdict.reason}（置信度 {verdict.confidence:.2f}）"[:500]

    def _build_judger(self, settings):
        from goodprice.analysis.judge import build_judger

        judger = build_judger(
            llm=self.llm,
            jev_enabled=bool(getattr(settings, "jev_enabled", False)),
            auto_threshold=getattr(settings, "jev_auto_threshold", 0.85),
            backend=getattr(settings, "jev_backend", "adapter") or "adapter",
            api_key=getattr(settings, "jev_api_key", "") or "",
        )
        if judger is not None and not judger.enabled:
            backend_name = getattr(settings, "jev_backend", "adapter") or "adapter"
            reason = "缺少 TypeSafe API Key" if backend_name == "typesafe" else "LLM 未配置"
            logger.warning(
                "Jev 判断层已开启但后端不可用（backend=%s：%s），将完全走原模型路径",
                backend_name,
                reason,
            )
        return judger

    def _condition_analysis(self, session, listing: Listing, task: WatchTask,
                            price: float | None = None, offer=None, force=False) -> None:
        if not self.vision.enabled:
            return
        listing.condition_score = None
        if self.media_service:
            from goodprice.offers.types import OfferCandidate

            try:
                verdict = self.media_service.analyze(listing.id, offer or OfferCandidate("default", {}),
                    self.vision, task.condition_requirement or "", self.settings_service.get(), force=force)
                listing.condition_score = verdict.get("condition_score")
                listing.condition_detail = verdict
            except Exception:
                listing.condition_detail = {"error": "媒体分析失败或超出预算，品相待核验"}
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
                    price=price if price is not None else listing.price,
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

    def _excluded(self, task: WatchTask, title: str) -> bool:
        """排除词按空白/中英文逗号拆分，标题命中任一即跳过。"""
        words = (
            (task.exclude_words or "")
            .replace("，", " ")
            .replace(",", " ")
            .split()
        )
        return any(word and word in (title or "") for word in words)

    def _batch_row(self, listing: Listing, requirement: str = "") -> dict:
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
            "requirement": requirement,
        }

    def _value_client(self):
        """批量性价比只依赖文字，优先用文本 LLM，未配置时退回视觉模型。"""
        for client in (self.llm, self.vision):
            if getattr(client, "enabled", False):
                return client
        return None

    def _record_error(self, task_id: int, message: str) -> None:
        with self._session_factory() as session:
            task = session.get(WatchTask, task_id)
            if task:
                task.last_error = message[:1000]
                session.commit()
