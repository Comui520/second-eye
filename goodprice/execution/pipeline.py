"""Production stage graph. Workers return data; the coordinator applies writes."""
import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from goodprice.analysis.media_analysis import analysis_key
from goodprice.crawler.base import ListingData
from goodprice.crawler.xianyu import XianyuAdapter
from goodprice.execution.browser import current_browser
from goodprice.execution.coordinator import Coordinator
from goodprice.execution.delivery import DeliveryService
from goodprice.execution.limits import ResourceLimits
from goodprice.execution.media_registry import MediaRegistry
from goodprice.execution.model_provider import ModelProvider
from goodprice.execution.protection import ResourceGuard
from goodprice.execution.store import ExecutionStore, naive
from goodprice.execution.types import Stage, WorkResult, thaw
from goodprice.execution.workers import WorkerPools
from goodprice.media.selection import select_media
from goodprice.media.types import MediaCandidate, MediaEvidence, MediaSelection
from goodprice.models import (
    AnalysisCache,
    Listing,
    ListingOffer,
    ManualQuote,
    MediaAsset,
    NotificationDelivery,
    OfferEvaluationRecord,
    OfferObservation,
    Seller,
    TaskRun,
    WatchTask,
    WorkItem,
)
from goodprice.offers.evaluation import assess_offer
from goodprice.offers.pricing import decide_price, utc
from goodprice.offers.service import OfferService, decode_offer, encode_offer
from goodprice.offers.types import OfferCandidate, PriceClaim, QuoteInput
from goodprice.services.crawl_service import CrawlService
from goodprice.services.offer_pipeline import OfferPipeline, StaleEvaluation
from goodprice.services.satisfaction import compute_satisfaction

PENDING = {"requirement", "media", "visual", "value_pending", "value_running", "aggregate", "refreshing"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def node(stage, identity, payload):
    return {"stage": stage.value, "fingerprint": digest(identity), "payload": payload}


class Pipeline:
    def __init__(self, factory, settings, adapter_factory=None, provider=None,
                 notifier_factory=None, processor_factory=None, clock=time.time):
        self.factory, self.settings, self.clock = factory, settings, clock
        self.adapter_factory = adapter_factory
        self.provider = provider or ModelProvider()
        self.registry = MediaRegistry(processor_factory)
        self.notifier_factory = notifier_factory or (lambda: {})
        self.delivery = DeliveryService(factory, self.notifier_factory,
            vision_enabled=lambda: self.provider.enabled("visual", self.settings.get()))
        self.upserter = CrawlService(factory, None, None, None, [], settings)
        self.store = None
        self.guard = ResourceGuard()

    def now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def config_key(self, runtime):
        from goodprice.analysis import prompts
        values = runtime.model_dump(mode="json")
        return digest([{k: v for k, v in values.items() if k.startswith(("llm_", "jev_"))},
            {k: v for k, v in vars(prompts).items() if k.isupper() and isinstance(v, str)}, "typed-contract-1"])

    def model_config(self, role, runtime):
        if role == "visual":
            from goodprice.analysis.media_analysis import PROMPT_VERSION, SYSTEM
            fields = {"vision_enabled", "vision_base_url", "vision_api_key", "vision_model", "vision_api_format",
                "video_analysis_enabled", "media_image_count", "media_video_count", "media_frames_per_video",
                "media_image_quota", "media_frame_quota", "media_total_limit", "media_model_limit"}
            return digest([{k: v for k, v in runtime.model_dump(mode="json").items() if k in fields}, PROMPT_VERSION, SYSTEM])
        return self.config_key(runtime)

    def provider_gate(self, role, runtime):
        from urllib.parse import urlsplit
        if role == "visual":
            url, key = runtime.vision_base_url, runtime.vision_api_key
        elif runtime.jev_enabled and runtime.jev_backend == "typesafe" and not role.endswith("fallback"):
            url, key = "https://api.typesafe.ai", runtime.jev_api_key
        else:
            url, key = runtime.llm_base_url, runtime.llm_api_key
        try:
            parsed = urlsplit(url)
            host = (parsed.scheme, parsed.hostname, parsed.port)
        except ValueError:
            host = "invalid"
        return digest([host, digest(key)])

    def _adapter(self, runtime):
        factory = self.adapter_factory or (lambda: XianyuAdapter(runtime.xianyu_cookie, runtime.proxy).session())
        return current_browser(digest([runtime.xianyu_cookie, runtime.proxy]), factory)

    def _context(self, item):
        with self.factory() as session:
            task = session.get(WatchTask, item.token.task_id)
            if not task or not task.enabled or task.version != item.token.task_version:
                raise StaleEvaluation("任务条件已变化")
            task_data = {c.name: getattr(task, c.name) for c in WatchTask.__table__.columns}
            listing = session.get(Listing, item.payload.get("listing_id")) if item.payload.get("listing_id") else None
            if listing is not None and listing.task_id != item.token.task_id:
                raise StaleEvaluation("商品引用已不属于当前任务")
            listing_data = {c.name: getattr(listing, c.name) for c in Listing.__table__.columns} if listing else None
        return task_data, listing_data

    def _owned(self, session, row, run):
        if row is None or row.task_version != run.task_version or row.payload.get("run_id") != run.id:
            return False
        offer = session.get(ListingOffer, row.offer_id)
        listing = session.get(Listing, offer.listing_id) if offer else None
        return bool(listing and listing.task_id == run.task_id)

    def platform_io(self, item):
        if item.stage == Stage.SEARCH:
            return not item.payload.get("target_id")
        if item.stage == Stage.DETAIL:
            with self.factory() as session:
                task = session.get(WatchTask, item.token.task_id)
                return bool(task and task.fetch_detail)
        return False

    def cached(self, item):
        if item.stage == Stage.DETAIL and item.payload.get("role") == "seller":
            with self.factory() as session:
                seller = session.query(Seller).filter_by(platform="xianyu", seller_uid=item.payload["seller_uid"]).first()
                if seller and seller.last_fetched_at and self.now() - utc(seller.last_fetched_at) < timedelta(days=7):
                    return WorkResult(item.token, item.stage, item.input_fingerprint, {"seller_cached": True})
        if item.stage != Stage.ANALYZE:
            return None
        runtime = self.settings.get()
        if item.payload.get("model_cfg") != self.model_config(item.payload["role"], runtime):
            return WorkResult(item.token, item.stage, item.input_fingerprint, {"configuration_changed": True, "no_http": True})
        if item.payload.get("force"):
            return None
        if item.payload["role"] == "value":
            scores = self._cached_values(item.payload)
            if len(scores) == len(item.payload["items"]):
                return WorkResult(item.token, item.stage, item.input_fingerprint,
                    {"verdict": {"scores": scores, "best": max(scores, key=scores.get)}, "cache_hit": True})
            return None
        with self.factory() as session:
            cached = session.query(WorkItem).filter(WorkItem.stage == Stage.ANALYZE.value,
                WorkItem.state == "done", WorkItem.payload["cache_key"].as_string() == item.payload["cache_key"],
                WorkItem.output["verdict"].as_string().is_not(None)).order_by(WorkItem.id.desc()).first()
        if cached and "verdict" in (cached.output or {}):
            return WorkResult(item.token, item.stage, item.input_fingerprint,
                              {"verdict": cached.output["verdict"], "cache_hit": True})
        return None

    def _cached_values(self, payload):
        if payload.get("force"):
            return {}
        keys = payload.get("item_cache_keys", {})
        with self.factory() as session:
            rows = {r.key: r.payload for r in session.query(AnalysisCache).filter(AnalysisCache.key.in_(list(keys.values())))}
        return {i: rows[key]["score"] for i, key in keys.items() if key in rows}

    def worker(self, item):
        task, listing = self._context(item)
        runtime = self.settings.get()
        payload = thaw(item.payload)
        if item.stage == Stage.SEARCH:
            if payload.get("target_id"):
                return {"manual_ids": [payload["target_id"]]}
            data = self._adapter(runtime).search(task["keyword"], max_items=200, attempts=1)
            return {"items": [{**asdict(row), "published_at": row.published_at.isoformat() if row.published_at else None}
                              for row in data[:200]], "source_truncated": max(0, len(data) - 200)}
        if item.stage == Stage.DETAIL:
            if not listing:
                return {"missing": True}
            if payload.get("role") == "seller":
                try:
                    return {"seller": asdict(self._adapter(runtime).fetch_seller(payload["seller_uid"]))}
                except Exception:
                    return {"seller_unavailable": True}
            if not task["fetch_detail"]:
                return {"disabled": True}
            detail = self._adapter(runtime).fetch_detail(listing["url"])
            media = detail.media or [MediaCandidate(digest(["adapter-image", url]), "image", url,
                source_locator="adapter:detail-image") for url in detail.image_urls[:32]]
            return {"detail": {"description": detail.description, "image_urls": detail.image_urls,
                "variants": detail.variants, "offers": [encode_offer(o) for o in detail.offers],
                "media": [asdict(m) for m in media], "seller_uid": detail.seller_uid,
                "seller_name": detail.seller_name, "credit_label": detail.credit_label,
                "positive_rate": detail.positive_rate, "sold_count": detail.sold_count}}
        if item.stage == Stage.MEDIA:
            runtime = self.guard.media_settings(runtime)
            with self.factory() as session:
                sources = session.query(MediaAsset).filter(MediaAsset.listing_id == payload["listing_id"],
                    MediaAsset.source_url != "", MediaAsset.status != "superseded").order_by(MediaAsset.id).all()
            candidates = [MediaCandidate(r.key, r.kind, r.source_url, r.offer_key, r.source_locator,
                                         status="unsupported" if r.status == "unsupported" else "pending") for r in sources]
            return self.registry.prepare(payload["group"], candidates, runtime)
        if item.stage == Stage.ANALYZE:
            if payload.get("model_cfg") != self.model_config(payload["role"], runtime):
                return {"configuration_changed": True, "no_http": True}
            role = payload["role"]
            if role == "value":
                cached = self._cached_values(payload)
                missing = [r for r in payload["items"] if r["external_id"] not in cached]
                verdict = self.provider.value(missing, runtime, payload.get("fallback", False)) if missing else {"scores": {}, "best": None}
                if not verdict.get("fallback"):
                    ids = {row["external_id"] for row in missing}
                    scores = verdict.get("scores")
                    if (not isinstance(scores, dict) or set(scores) != ids or
                        any(type(v) not in (int, float) or not 0 <= v <= 10 for v in scores.values()) or
                        verdict.get("best") not in ids | {None}):
                        raise ValueError("模型评分必须覆盖本次商品且只能引用本次商品")
                    verdict = {**verdict, "scores": {**cached, **scores}}
                return {"verdict": verdict}
            with self.factory() as session:
                row = session.get(OfferEvaluationRecord, payload["eval_id"])
                observation = session.get(OfferObservation, row.payload["observation_id"])
                candidate = decode_offer(observation.payload)
            data = {"title": listing["title"], "description": f"{listing['description'] or ''}\n规格：{dict(candidate.attributes)}",
                    "requirement": task["condition_requirement"]}
            if role.startswith("requirement"):
                return {"verdict": self.provider.requirement(data, runtime, role.endswith("fallback"))}
            with self.registry.pin(payload["group"]):
                selection, scoped_candidate = self._selection(payload, candidate, runtime)
                data["description"] = f"{listing['title']}\n{listing['description'] or ''}\n规格：{dict(scoped_candidate.attributes)}"
                verdict = self.provider.visual(scoped_candidate, selection, data, runtime)
            return {"verdict": verdict}
        return {}

    def _selection(self, payload, candidate, runtime):
        from pathlib import Path
        raw = payload["evidence"]
        scoped = [e for e in raw if e["offer_key"] == candidate.key]
        shared = not scoped and payload.get("multiple", False)
        chosen = scoped or [e for e in raw if e["offer_key"] is None]
        root = runtime.data_root / "media-cache"
        evidence = [MediaEvidence(e["id"], e["kind"], e["hash"], root / Path(e["path"]),
            e["offer_key"], e["video_id"], e["timestamp_ms"]) for e in chosen]
        selection = select_media([e for e in evidence if e.kind == "image"],
            [e for e in evidence if e.kind == "frame"], runtime.media_image_quota, runtime.media_frame_quota, runtime.media_total_limit)
        selection = MediaSelection(selection.items, selection.omitted_ids,
            tuple(dict.fromkeys(selection.limitations + tuple(payload.get("limitations", [])))))
        return selection, OfferCandidate("shared", {}) if shared else candidate

    def apply(self, result, session):
        work = session.query(WorkItem).filter_by(run_id=result.token.run_id, lease_id=result.token.lease_id).one()
        run = session.get(TaskRun, result.token.run_id)
        task = session.get(WatchTask, run.task_id)
        runtime = self.settings.get()
        data, payload = thaw(result.output), work.payload
        if data.get("configuration_changed") or (result.stage == Stage.ANALYZE and
                payload.get("model_cfg") != self.model_config(payload["role"], runtime)):
            run.cursor = {**(run.cursor or {}), "_draining": True, "_drain_reason": "configuration"}
            session.query(WorkItem).filter(WorkItem.run_id == run.id, WorkItem.state.in_(("pending", "retry")),
                WorkItem.stage.in_(("search", "detail", "media", "analyze"))).update({"state": "deferred"})
            return {}
        if payload.get("listing_id"):
            listing = session.get(Listing, payload["listing_id"])
            if listing is not None and listing.task_id != run.task_id:
                return {}
        if result.stage == Stage.SEARCH:
            return self._search(session, run, task, data, runtime)
        if result.stage == Stage.DETAIL:
            return self._detail(session, run, task, payload, data)
        if result.stage == Stage.PARSE:
            return self._parse(session, run, task, payload, runtime)
        if result.stage == Stage.MEDIA:
            return self._media(session, run, payload, data, runtime, result.input_fingerprint)
        if result.stage == Stage.ANALYZE:
            return self._analyzed(session, run, task, payload, data["verdict"], runtime)
        if result.stage == Stage.AGGREGATE:
            return self._aggregate(session, run, task, payload, runtime)
        return {}

    def _search(self, session, run, task, data, runtime):
        seen = []
        for item in data.get("items", []):
            if self.upserter._excluded(task, item["title"]):
                continue
            item = dict(item)
            item["published_at"] = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None
            listing, _ = self.upserter._upsert_listing(session, task, ListingData(**item))
            listing.status, listing.missed_count = "active", 0
            seen.append(listing.id)
        priorities = data.get("manual_ids") or list((run.cursor or {}).get("remaining_ids", []))
        ids = list(dict.fromkeys(priorities + seen))[:200]
        ids = [i for i in ids if (session.get(Listing, i) is not None and session.get(Listing, i).task_id == task.id)]
        run.cursor = {**(run.cursor or {}), "remaining_ids": ids, "seen_ids": seen,
                      "source_truncated": data.get("source_truncated", 0), "planned_ids": ids[:runtime.execution_run_details]}
        task.last_run_at, task.last_run_count, task.last_error = naive(self.now()), (task.last_run_count or 0) + 1, None
        if run.kind == "crawl":
            for listing in session.query(Listing).filter_by(task_id=task.id):
                if listing.id not in seen:
                    listing.missed_count += 1
                    if listing.missed_count >= 3:
                        listing.status = "unseen"
        return {"next": [node(Stage.DETAIL, ["detail", i], {"listing_id": i}) for i in ids[:runtime.execution_run_details]]}

    def _detail(self, session, run, task, payload, data):
        listing = session.get(Listing, payload["listing_id"])
        if listing is None or data.get("missing"):
            self._listing_done(session, run, payload["listing_id"])
            return {}
        if payload.get("role") == "seller":
            from goodprice.services.seller_service import compute_risk
            seller = session.query(Seller).filter_by(platform=task.platform, seller_uid=payload["seller_uid"]).first()
            captured = data.get("seller")
            if captured:
                if seller is None:
                    seller = Seller(platform=task.platform, seller_uid=payload["seller_uid"])
                    session.add(seller)
                seller.nickname, seller.tags = captured["nickname"], captured["tags"]
                seller.positive_count, seller.total_count = captured["positive_count"], captured["total_count"]
                seller.positive_rate = captured["positive_count"] / captured["total_count"] if captured["positive_count"] is not None and captured["total_count"] else None
                seller.last_fetched_at = naive(self.now())
            risk = listing.seller_risk or {}
            if seller and seller.blocked:
                listing.blocked = True
            level, reason = compute_risk(seller, risk.get("credit_label"), risk.get("positive_rate"))
            listing.seller_risk = {**risk, "risk_level": level, "reason": reason}
            return {"next": [node(Stage.PARSE, ["parse", listing.id, payload["eval_ids"]],
                {"listing_id": listing.id, "eval_ids": payload["eval_ids"]})]}
        detail = data.get("detail", {})
        if payload.get("price_refresh") and detail:
            candidates = [decode_offer(o) for o in detail.get("offers", [])]
            if candidates and self._source_key(session, listing, detail) == payload["source_key"]:
                for candidate in candidates:
                    OfferService(self.factory).record_observation(listing.id, candidate, session=session, now=self.now())
                refreshed = []
                for i in payload["eval_ids"]:
                    row = session.get(OfferEvaluationRecord, i)
                    observation = session.query(OfferObservation).filter_by(offer_id=row.offer_id).order_by(OfferObservation.id.desc()).first()
                    row.payload = {**row.payload, "observation_id": observation.id, "status": "parse"}
                    row.payload = {**row.payload, **self._price_context(session, row)}
                    refreshed.append(observation.id)
                return {"next": [node(Stage.PARSE, ["price_refreshed", payload["eval_ids"], refreshed],
                    {"listing_id": listing.id, "eval_ids": payload["eval_ids"], "reuse_inputs": True})]}
            for i in payload["eval_ids"]:
                row = session.get(OfferEvaluationRecord, i)
                if row:
                    row.payload = {**row.payload, "status": "superseded", "eligible": False}
        if detail:
            listing.offer_verified_until, listing.value_score, listing.best_of_batch = None, None, False
            listing.satisfaction = 0
            listing.description, listing.image_urls = detail["description"], detail["image_urls"][:32]
            listing.variants = detail["variants"]
            listing.seller_uid, listing.seller_name = detail["seller_uid"], detail["seller_name"]
            listing.seller_risk = {"credit_label": detail["credit_label"], "positive_rate": detail["positive_rate"], "sold_count": detail["sold_count"]}
            from goodprice.services.seller_service import compute_risk
            level, reason = compute_risk(None, detail["credit_label"], detail["positive_rate"])
            listing.seller_risk = {**listing.seller_risk, "risk_level": level, "reason": reason}
            listing.status, listing.missed_count = "active", 0
            sources = {r.key: r for r in session.query(MediaAsset).filter(MediaAsset.listing_id == listing.id, MediaAsset.source_url != "")}
            for old in sources.values():
                old.status = "superseded"
            for candidate in detail["media"]:
                row = sources.get(candidate["key"])
                if row is None:
                    row = MediaAsset(listing_id=listing.id, key=candidate["key"], kind=candidate["kind"])
                    session.add(row)
                row.source_url, row.source_locator, row.offer_key = candidate["url"], candidate["source_locator"], candidate["offer_key"]
                row.status, row.relative_path, row.content_hash = candidate["status"], None, None
        candidates = [decode_offer(o) for o in detail.get("offers", [])]
        if not candidates:
            candidates = [OfferCandidate("default", {}, claims=(PriceClaim("dom", "unknown",
                "详情未核验" if not data.get("disabled") else "详情抓取关闭", self.now(), "detail:missing"),))]
        ids = OfferPipeline(self.factory, self.provider.enabled("visual", self.settings.get())).evaluate_listing(
            listing.id, task.id, candidates, self.now(), session=session, expected_version=task.version,
            ready=False, per_offer_results={})
        included = {session.get(OfferEvaluationRecord, i).offer_id for i in ids}
        for old in session.query(ListingOffer).filter_by(listing_id=listing.id):
            if old.id not in included:
                old.seen_in_latest_detail = False
                old.effective_price_state, old.effective_price_cents, old.price_valid_until = "unknown", None, None
        group = digest([run.id, listing.id, ids])
        for i in ids:
            row = session.get(OfferEvaluationRecord, i)
            row.payload = {**row.payload, "run_id": run.id, "group": group, "multiple": len(ids) > 1, "status": "parse"}
        if listing.seller_uid and detail:
            return {"next": [node(Stage.DETAIL, ["seller", listing.id, ids],
                {"role": "seller", "listing_id": listing.id, "seller_uid": listing.seller_uid, "eval_ids": ids})]}
        return {"next": [node(Stage.PARSE, ["parse", listing.id, ids], {"listing_id": listing.id, "eval_ids": ids})]}

    def _parse(self, session, run, task, payload, runtime):
        following = []
        for i in payload["eval_ids"]:
            row = session.get(OfferEvaluationRecord, i)
            offer = session.get(ListingOffer, row.offer_id)
            base = {"listing_id": payload["listing_id"], "eval_id": i, "group": row.payload["group"], "force": run.kind == "reanalyze"}
            gates = self._assessment(session, row, runtime)
            if gates["state"] == "exact" and not gates["price_eligible"]:
                row.payload = {**row.payload, "status": "aggregate"}
                following.append(node(Stage.AGGREGATE, ["aggregate", i, row.payload["observation_id"]], base))
                continue
            if payload.get("reuse_inputs") and row.payload.get("requirement_done"):
                following.extend(self._after_requirement(session, row, base, runtime))
                continue
            if row.payload["match"] != "matched" or not task.fetch_detail:
                row.payload = {**row.payload, "status": "aggregate", "requirement_match": None}
                following.append(node(Stage.AGGREGATE, ["aggregate", i], base))
            elif task.condition_requirement and self.provider.enabled("requirement", runtime):
                listing = session.get(Listing, payload["listing_id"])
                key = digest(["requirement", listing.id, offer.offer_key, listing.title, listing.description,
                              task.condition_requirement, task.target_variant, self.config_key(runtime)])
                row.payload = {**row.payload, "status": "requirement"}
                following.append(node(Stage.ANALYZE, ["requirement", i, row.payload["observation_id"], key], {**base, "role": "requirement", "cache_key": key, "http": True,
                    "model_cfg": self.model_config("requirement", runtime), "provider_gate": self.provider_gate("requirement", runtime)}))
            else:
                row.payload = {**row.payload, "requirement_match": None, "requirement_done": True}
                following.extend(self._after_requirement(session, row, base, runtime))
        return {"next": following}

    def _after_requirement(self, session, row, base, runtime):
        if row.payload.get("media_done"):
            return self._ready_for_value(session, row, base, runtime)
        if row.payload.get("requirement_match") is False or not self.provider.enabled("visual", runtime):
            row.payload = {**row.payload, "condition_score": None, "visual_scope": None}
            return self._ready_for_value(session, row, base, runtime)
        row.payload = {**row.payload, "status": "media"}
        return [node(Stage.MEDIA, ["media", row.id, row.payload["observation_id"]], {**base, "role": "media"})]

    def _media(self, session, run, payload, data, runtime, generation):
        row = session.get(OfferEvaluationRecord, payload["eval_id"])
        if not self._owned(session, row, run) or row.payload.get("status") in {"completed", "superseded", "refreshing"}:
            return {}
        assets = {r.key: r for r in session.query(MediaAsset).filter_by(listing_id=payload["listing_id"])}
        for e in data["evidence"]:
            asset_key = f"evidence:{e['id']}"
            asset = assets.get(asset_key)
            if asset is None:
                asset = MediaAsset(listing_id=payload["listing_id"], key=asset_key, kind=e["kind"])
                session.add(asset)
                assets[asset_key] = asset
            asset.offer_key, asset.content_hash, asset.relative_path = e["offer_key"], e["hash"], e["path"]
            asset.parent_video_key, asset.timestamp_ms, asset.status = e["video_id"], e["timestamp_ms"], "ready"
        base = {**payload, **data, "role": "visual", "multiple": row.payload["multiple"]}
        offer = decode_offer(session.get(OfferObservation, row.payload["observation_id"]).payload)
        selection, scoped = self._selection(base, offer, runtime)
        if not selection.items:
            row.payload = {**row.payload, "condition_score": None, "visual_scope": None,
                           "media": {"limitations": list(selection.limitations) + ["no_visual_input"]}}
            return {"next": self._ready_for_value(session, row, base, runtime)}
        listing = session.get(Listing, payload["listing_id"])
        client_identity = SimpleNamespace(model=runtime.vision_model, base_url=runtime.vision_base_url, api_format=runtime.vision_api_format)
        seller_text = f"{listing.title}\n{listing.description or ''}\n规格：{dict(scoped.attributes)}"
        key = digest([listing.id, digest(runtime.vision_api_key), analysis_key(scoped, selection, client_identity,
            session.get(WatchTask, run.task_id).condition_requirement, runtime, seller_text)])
        row.payload = {**row.payload, "status": "visual", "media_cache_key": key}
        return {"next": [node(Stage.ANALYZE, ["visual", row.id, key, generation], {**base, "cache_key": key, "http": True,
            "model_cfg": self.model_config("visual", runtime), "provider_gate": self.provider_gate("visual", runtime)})]}

    def _analyzed(self, session, run, task, payload, verdict, runtime):
        if payload["role"] == "value":
            if verdict.get("fallback"):
                if self.provider.enabled("value_fallback", runtime):
                    key = digest([payload["cache_key"], "fallback"])
                    return {"next": [node(Stage.ANALYZE, ["value_fallback", payload["eval_ids"], key],
                        {**payload, "cache_key": key, "fallback": True,
                         "provider_gate": self.provider_gate("value_fallback", runtime)})]}
                for i in payload["eval_ids"]:
                    row = session.get(OfferEvaluationRecord, i)
                    row.payload = {**row.payload, "status": "unverified", "eligible": False,
                                   "reason": "评分置信度不足，文本回落模型尚未配置"}
                return {}
            following = []
            for item_id, score in verdict["scores"].items():
                key = payload["item_cache_keys"][item_id]
                cached = session.get(AnalysisCache, key)
                if cached is None:
                    session.add(AnalysisCache(key=key, payload={"score": score}))
                else:
                    cached.payload = {"score": score}
            for i in payload["eval_ids"]:
                row = session.get(OfferEvaluationRecord, i)
                if not self._owned(session, row, run) or row.payload.get("status") in {"completed", "superseded", "refreshing"}:
                    continue
                if self._expired(session, row):
                    following.extend(self._refresh_price(session, run, payload["listing_ids"][str(i)]))
                    continue
                current = self._price_context(session, row)
                if current != payload.get("price_contexts", {}).get(str(i)):
                    row.payload = {**row.payload, **current, "value_score": None,
                                   "status": "value_pending", "value_ready_at": self.now().isoformat()}
                    continue
                value = verdict.get("scores", {}).get(f"offer:{row.offer_id}")
                row.payload = {**row.payload, "value_score": value, "value_price_context": current, "status": "aggregate"}
                following.append(node(Stage.AGGREGATE, ["aggregate", i, payload["cache_key"]], {"listing_id": payload["listing_ids"][str(i)], "eval_id": i}))
            return {"next": following}
        row = session.get(OfferEvaluationRecord, payload["eval_id"])
        if not self._owned(session, row, run) or row.payload.get("status") in {"completed", "superseded", "refreshing"}:
            return {}
        if payload["role"].startswith("requirement"):
            if verdict.get("fallback"):
                role = "requirement_fallback"
                if not self.provider.enabled(role, runtime):
                    row.payload = {**row.payload, "status": "unverified", "eligible": False,
                                   "reason": "判断置信度不足，文本回落模型尚未配置"}
                    return {}
                key = digest([payload["cache_key"], role])
                return {"next": [node(Stage.ANALYZE, [role, row.id, key], {**payload, "role": role, "cache_key": key,
                    "model_cfg": self.model_config(role, runtime), "provider_gate": self.provider_gate(role, runtime)})]}
            row.payload = {**row.payload, "requirement_match": verdict.get("matched"), "requirement_reason": verdict.get("reason", ""), "requirement_done": True}
            return {"next": self._after_requirement(session, row, payload, runtime)}
        offer = session.get(ListingOffer, row.offer_id)
        from goodprice.analysis.media_analysis import validate_evidence_references
        candidate = decode_offer(session.get(OfferObservation, row.payload["observation_id"]).payload)
        selection, scoped = self._selection(payload, candidate, runtime)
        clean = validate_evidence_references(verdict, {e.id for e in selection.items})
        limitations = list(dict.fromkeys(list(selection.limitations) + list(verdict.get("limitations", []))))
        if scoped.key == "shared":
            clean["condition_score"] = None
            limitations.append("shared_media")
        if clean.get("conflicts"):
            clean["condition_score"] = None
            limitations.append("conflicting_evidence")
        clean.update(limitations=list(dict.fromkeys(limitations)), evidence_ids=[e.id for e in selection.items],
                     media_cache_key=payload["cache_key"], omitted_evidence_ids=list(selection.omitted_ids))
        row.payload = {**row.payload, "media": clean, "condition_score": clean.get("condition_score"),
            "visual_scope": offer.offer_key if clean.get("condition_score") is not None else None, "media_done": True}
        return {"next": self._ready_for_value(session, row, payload, runtime)}

    def _ready_for_value(self, session, row, payload, runtime):
        if not self._assessment(session, row, runtime)["eligible"] or not self.provider.enabled("value", runtime):
            row.payload = {**row.payload, "value_score": None, "status": "aggregate"}
            return [node(Stage.AGGREGATE, ["aggregate", row.id], {"listing_id": payload["listing_id"], "eval_id": row.id})]
        row.payload = {**row.payload, "status": "value_pending", "value_ready_at": self.now().isoformat()}
        return []

    def _aggregate(self, session, run, task, payload, runtime):
        original = session.get(OfferEvaluationRecord, payload["eval_id"])
        if not self._owned(session, original, run) or original.payload.get("status") == "completed":
            return {}
        if (run.cursor or {}).get("_drain_reason") == "configuration":
            original.payload = {**original.payload, "status": "deferred", "eligible": False}
            return {}
        if self._expired(session, original):
            return {"next": self._refresh_price(session, run, payload["listing_id"])}
        current_price = self._price_context(session, original)
        if original.payload.get("value_score") is not None and original.payload.get("value_price_context") != current_price:
            original.payload = {**original.payload, **current_price, "value_score": None,
                "status": "value_pending", "value_ready_at": self.now().isoformat()}
            return {}
        listing = session.get(Listing, payload["listing_id"])
        offer = session.get(ListingOffer, original.offer_id)
        if not offer.seen_in_latest_detail:
            original.payload = {**original.payload, "status": "superseded", "eligible": False}
            self._listing_done(session, run, listing.id)
            return {}
        if listing is None or offer is None:
            return {}
        observation = session.query(OfferObservation).filter_by(offer_id=offer.id).order_by(OfferObservation.id.desc()).first()
        candidate = decode_offer(observation.payload)
        values = dict(original.payload)
        ids = OfferPipeline(self.factory, self.provider.enabled("visual", runtime)).evaluate_listing(
            listing.id, task.id, [candidate], self.now(), session=session, expected_version=task.version,
            per_offer_results={candidate.key: values}, record_observations=False)
        final = session.get(OfferEvaluationRecord, ids[0])
        final.payload = {**final.payload, "run_id": run.id, "status": "ready", "media": values.get("media"),
                         "value_score": values.get("value_score"), "media_cache_key": values.get("media_cache_key")}
        original.payload = {**original.payload, "status": "completed", "final_id": final.id}
        if final.payload["eligible"]:
            score = compute_satisfaction(SimpleNamespace(requirement_match=values.get("requirement_match"),
                condition_score=values.get("condition_score"), value_score=values.get("value_score"), seller_risk=listing.seller_risk),
                vision_enabled=self.provider.enabled("visual", runtime))
            final.payload = {**final.payload, "satisfaction": score}
            if listing.offer_task_version != task.version or listing.offer_price_cents is None or score >= listing.satisfaction:
                listing.condition_score, listing.condition_detail = values.get("condition_score"), values.get("media")
                listing.requirement_match, listing.requirement_reason = values.get("requirement_match"), values.get("requirement_reason")
                listing.satisfaction, listing.value_score = score, values.get("value_score")
                listing.offer_price_cents, listing.offer_task_version = final.payload["amount_cents"], task.version
                listing.offer_verified_until = datetime.fromisoformat(final.payload["fresh_until"])
            session.flush()
            self.rank_ready(session, run)
            if run.kind != "reanalyze":
                self._event(session, task, listing, offer, final, score, values)
        elif (run.kind != "reanalyze" and task.notify_pending_price and task.fetch_detail
              and final.payload["state"] != "exact" and final.payload["match"] != "mismatched"
              and offer.availability != "unavailable" and not listing.blocked and self.notifier_factory()):
            conditions = digest({name: getattr(task, name) for name in ("keyword", "target_variant", "condition_requirement", "min_condition_score", "min_price", "max_price")})
            key = digest(["pending", task.id, listing.platform, listing.external_id, offer.offer_key,
                conditions, offer.id, final.payload["fingerprint"]])
            self.delivery.enqueue(task.id, task.version, listing.id, offer.id, key,
                {"title": f"[待询价] {listing.title}", "content": "当前售价尚未核实，不参与正常性价比排名。请自行联系卖家核实具体规格和实际价格。",
                 "url": listing.url, "pending": True, "fingerprint": final.payload["fingerprint"],
                 "source": None, "amount_cents": None, "condition_score": values.get("condition_score"),
                 "media_cache_key": values.get("media_cache_key"), "offer_evaluation": True}, session=session)
        self._listing_done(session, run, listing.id)
        return {}

    def _event(self, session, task, listing, offer, final, score, values):
        if not self.notifier_factory():
            return
        price = final.payload
        if offer.last_notified_source == "legacy_baseline":
            offer.last_notified_score, offer.last_notified_amount = score, price["amount_cents"]
            offer.last_notified_source, offer.last_notified_task_version = price["source"], task.version
            return
        changed_source = offer.last_notified_source != price["source"]
        reduced = (not changed_source and offer.last_notified_amount is not None and price["amount_cents"] < offer.last_notified_amount)
        if offer.last_notified_task_version in {None, task.version} and offer.last_notified_score is not None and score <= offer.last_notified_score and not changed_source and not reduced:
            return
        conditions = digest({name: getattr(task, name) for name in ("keyword", "platform", "target_variant", "min_price", "max_price", "exclude_words", "condition_requirement", "min_condition_score")})
        key = digest([task.id, listing.platform, listing.external_id, offer.offer_key,
            conditions, offer.id, price["fingerprint"], price["source"], price["amount_cents"], score, values.get("media_cache_key")])
        self.delivery.enqueue(task.id, task.version, listing.id, offer.id, key,
            {"title": f"[{task.keyword}] {listing.title}", "content": f"规格：{offer.name or dict(offer.attributes) or '默认商品'}\n"
                f"价格：{price['amount_cents'] / 100:g}元（{'人工报价' if price['source'] == 'manual' else '页面确价'}）\n"
                f"品相：{values.get('condition_score') if values.get('condition_score') is not None else '未核验'}\n"
                f"性价比：{values.get('value_score') if values.get('value_score') is not None else '未评估'}",
             "url": listing.url, "fingerprint": price["fingerprint"], "source": price["source"],
             "amount_cents": price["amount_cents"], "condition_score": values.get("condition_score"),
             "media_cache_key": values.get("media_cache_key"), "satisfaction": score, "offer_evaluation": True}, session=session)

    def rank_ready(self, session, run):
        rows = session.query(OfferEvaluationRecord).filter(OfferEvaluationRecord.payload["run_id"].as_integer() == run.id,
            OfferEvaluationRecord.payload["status"].as_string() == "ready").all()
        offer_ids = [r.offer_id for r in rows]
        offers = {o.id: o for o in session.query(ListingOffer).filter(ListingOffer.id.in_(offer_ids))}
        ready = [r for r in rows if r.payload.get("eligible") and r.offer_id in offers and
            offers[r.offer_id].seen_in_latest_detail and offers[r.offer_id].price_valid_until is not None and
            offers[r.offer_id].price_valid_until > naive(self.now()) and
            offers[r.offer_id].price_task_version == run.task_version and
            r.payload.get("amount_cents") == offers[r.offer_id].effective_price_cents and
            r.payload.get("source") == offers[r.offer_id].effective_price_source and
            r.payload.get("fingerprint") == offers[r.offer_id].price_evidence_fingerprint]
        valued = [r for r in ready if r.payload.get("value_score") is not None]
        best = max(valued, key=lambda r: (r.payload.get("satisfaction", 0), -r.payload["amount_cents"], -r.offer_id)) if len(valued) > 1 else None
        listings = {p.id: p for p in session.query(Listing).filter(Listing.id.in_({o.listing_id for o in offers.values()}))}
        for row in ready:
            row.payload = {**row.payload, "best_of_batch": row is best, "batch_scope_count": len(valued)}
        for listing in listings.values():
            current = [r for r in ready if offers[r.offer_id].listing_id == listing.id]
            if not current:
                listing.offer_verified_until, listing.value_score, listing.best_of_batch = None, None, False
                listing.satisfaction = 0
                continue
            chosen = max(current, key=lambda r: r.payload.get("satisfaction", 0))
            listing.satisfaction, listing.value_score = chosen.payload.get("satisfaction", 0), chosen.payload.get("value_score")
            listing.offer_price_cents = min(r.payload["amount_cents"] for r in current)
            listing.offer_verified_until = min(datetime.fromisoformat(r.payload["fresh_until"]) for r in current)
            listing.offer_task_version = run.task_version
            listing.best_of_batch = bool(best and offers[best.offer_id].listing_id == listing.id)
            listing.value_batch_at = naive(self.now())

    def _listing_done(self, session, run, listing_id):
        rows = session.query(OfferEvaluationRecord).join(ListingOffer).filter(ListingOffer.listing_id == listing_id,
            OfferEvaluationRecord.payload["run_id"].as_integer() == run.id).all()
        if any((row.payload or {}).get("status") in PENDING | {"parse"} for row in rows):
            return
        run.cursor = {**(run.cursor or {}), "remaining_ids": [i for i in (run.cursor or {}).get("remaining_ids", []) if i != listing_id]}

    def can_finish(self, session, run):
        return session.query(OfferEvaluationRecord.id).filter(OfferEvaluationRecord.payload["run_id"].as_integer() == run.id,
            OfferEvaluationRecord.payload["status"].as_string().in_(PENDING | {"parse"})).first() is None

    def maintain(self, store, now):
        self._restore_media(store)
        with store.lock, self.factory() as session:
            runs = session.query(TaskRun).filter(TaskRun.state.in_(("active", "waiting_capacity"))).all()
            needed = set()
            for run in runs:
                rows = session.query(OfferEvaluationRecord).filter(OfferEvaluationRecord.payload["run_id"].as_integer() == run.id).all()
                if (run.cursor or {}).get("_draining"):
                    running = session.query(WorkItem).filter_by(run_id=run.id, state="running").all()
                    active_ids = {i for work in running for i in (work.payload.get("eval_ids") or [work.payload.get("eval_id")]) if i}
                    for row in rows:
                        if row.id not in active_ids and row.payload.get("status") in {"requirement", "media", "visual", "value_pending", "value_running"}:
                            row.payload = {**row.payload, "status": "deferred", "eligible": False,
                                           "reason": "本轮模型预算已用尽，未完成部分等待后续执行"}
                for row in rows:
                    if row.payload.get("status") in {"requirement", "media", "visual", "refreshing"}:
                        needed.add(row.payload["group"])
                pending = [row for row in rows if row.payload.get("status") == "value_pending"]
                if pending and not (run.cursor or {}).get("_draining"):
                    other = any(row.payload.get("status") in {"parse", "requirement", "media", "visual"} for row in rows)
                    oldest = min(datetime.fromisoformat(row.payload["value_ready_at"]) for row in pending)
                    runtime = self.settings.get()
                    if len(pending) >= 5 or not other or (now - oldest).total_seconds() >= 30:
                        batch = pending[:1 if runtime.jev_enabled else 30]
                        self._value_batch(session, store, run, batch, runtime)
            session.commit()
        self.registry.retain(needed)
        return [run.id for run in runs]

    def resources(self, limits):
        return self.guard.evaluate(limits)

    def _restore_media(self, store):
        with self.factory() as session:
            pending = session.query(WorkItem).filter(WorkItem.stage == Stage.ANALYZE.value,
                WorkItem.state.in_(("pending", "retry")), WorkItem.payload["role"].as_string() == "visual").all()
        runtime = self.settings.get()
        for work in pending:
            key = work.payload["group"]
            if self.registry.has(key) or self.registry.restore(key, work.payload, runtime):
                continue
            with store.lock, self.factory() as session:
                current = session.get(WorkItem, work.id)
                if current.state not in {"pending", "retry"}:
                    continue
                current.state = "superseded"
                session.flush()
                if not store._room(session, work.task_id):
                    current.state = "pending"
                    session.commit()
                    continue
                session.add(WorkItem(run_id=work.run_id, task_id=work.task_id, stage=Stage.MEDIA.value,
                    input_fingerprint=digest(["restore_media", work.id]),
                    payload={"listing_id": work.payload["listing_id"], "eval_id": work.payload["eval_id"],
                             "group": key, "force": work.payload.get("force", False), "role": "media"}))
                row = session.get(OfferEvaluationRecord, work.payload["eval_id"])
                row.payload = {**row.payload, "status": "media"}
                session.commit()

    def failed(self, session, work):
        ids = work.payload.get("eval_ids") or [work.payload.get("eval_id")]
        for i in ids:
            row = session.get(OfferEvaluationRecord, i) if i else None
            if row and row.payload.get("status") != "completed":
                row.payload = {**row.payload, "status": "unverified", "eligible": False,
                               "reason": "本轮步骤失败，保留证据并等待后续重试"}
        task = session.get(WatchTask, work.task_id)
        if task:
            task.last_error = f"{work.stage} 阶段未完成，未完成项将保留重试"

    def delivery_events(self, now):
        from sqlalchemy import or_
        with self.factory() as session:
            rows = session.query(NotificationDelivery).filter(
                NotificationDelivery.state.in_(("pending", "failed", "awaiting_revalidation", "retry_requested")),
                or_(NotificationDelivery.not_before.is_(None), NotificationDelivery.not_before <= naive(now)),
                or_(NotificationDelivery.attempt < 3, NotificationDelivery.state == "retry_requested")
            ).order_by(NotificationDelivery.id).limit(20).all()
        return list(dict.fromkeys(r.event_id for r in rows))

    def _value_batch(self, session, store, run, rows, runtime):
        if not store._room(session, run.task_id):
            if not session.query(WorkItem.id).filter(WorkItem.run_id == run.id,
                    WorkItem.state.in_(("pending", "running", "retry"))).first():
                run.state = "waiting_capacity"
            return
        items, listing_ids, contexts = [], {}, {}
        for row in rows:
            if row.payload.get("status") != "value_pending":
                continue
            current = self._price_context(session, row)
            row.payload = {**row.payload, **current}
            if current["state"] != "exact":
                if self._expired(session, row):
                    for next_node in self._refresh_price(session, run, session.get(ListingOffer, row.offer_id).listing_id):
                        if store._room(session, run.task_id) and not session.query(WorkItem.id).filter_by(
                                run_id=run.id, stage=next_node["stage"], input_fingerprint=next_node["fingerprint"]).first():
                            session.add(WorkItem(run_id=run.id, task_id=run.task_id, stage=next_node["stage"],
                                input_fingerprint=next_node["fingerprint"], payload=next_node["payload"]))
                            session.flush()
                    continue
                row.payload = {**row.payload, "status": "unverified", "eligible": False,
                               "reason": "价格或规格证据已过期，等待重新抓取"}
                continue
            offer = session.get(ListingOffer, row.offer_id)
            listing = session.get(Listing, offer.listing_id)
            task = session.get(WatchTask, run.task_id)
            items.append({"external_id": f"offer:{offer.id}", "title": f"{listing.title} {dict(offer.attributes)}",
                "price": row.payload["amount_cents"] / 100, "condition_score": row.payload.get("condition_score"),
                "defects": (row.payload.get("media") or {}).get("observations", []),
                "seller_risk": (listing.seller_risk or {}).get("risk_level"), "requirement": task.condition_requirement})
            listing_ids[str(row.id)] = listing.id
            contexts[str(row.id)] = current
        rows = [row for row in rows if str(row.id) in contexts]
        if not rows:
            return
        key = digest(["value", items, self.config_key(runtime)])
        item_keys = {item["external_id"]: digest(["independent-value-1", item, self.config_key(runtime)]) for item in items}
        identity = digest(["value", [r.id for r in rows], [r.payload["observation_id"] for r in rows], key, contexts])
        if not session.query(WorkItem.id).filter_by(run_id=run.id, stage=Stage.ANALYZE.value, input_fingerprint=identity).first():
            session.add(WorkItem(run_id=run.id, task_id=run.task_id, stage=Stage.ANALYZE.value, input_fingerprint=identity,
                payload={"role": "value", "listing_id": next(iter(listing_ids.values())), "listing_ids": listing_ids,
                         "eval_ids": [r.id for r in rows], "items": items, "cache_key": key, "http": True,
                         "item_cache_keys": item_keys,
                         "price_contexts": contexts,
                         "model_cfg": self.model_config("value", runtime), "provider_gate": self.provider_gate("value", runtime),
                         "force": run.kind == "reanalyze"}))
            for row in rows:
                row.payload = {**row.payload, "status": "value_running"}
            session.flush()

    def _price_context(self, session, row):
        offer = session.get(ListingOffer, row.offer_id)
        listing = session.get(Listing, offer.listing_id)
        task = session.get(WatchTask, listing.task_id)
        observation = session.query(OfferObservation).filter_by(offer_id=offer.id).order_by(OfferObservation.id.desc()).first()
        candidate = decode_offer(observation.payload)
        quote = session.query(ManualQuote).filter_by(offer_id=offer.id, revoked=False).order_by(ManualQuote.id.desc()).first()
        value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents, utc(quote.confirmed_at),
            utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
        decision = decide_price(candidate, value, self.now(), task.interval_minutes * 60 if task and task.fetch_detail else 0)
        return {"state": decision.state, "source": decision.source, "amount_cents": decision.amount_cents,
                "fingerprint": observation.fingerprint}

    def _expired(self, session, row):
        observation = session.get(OfferObservation, row.payload["observation_id"])
        offer = session.get(ListingOffer, row.offer_id)
        listing = session.get(Listing, offer.listing_id)
        task = session.get(WatchTask, listing.task_id)
        return bool(task and task.fetch_detail and
            (self.now() - utc(observation.observed_at)).total_seconds() >= task.interval_minutes * 60)

    def _assessment(self, session, row, runtime):
        offer = session.get(ListingOffer, row.offer_id)
        listing = session.get(Listing, offer.listing_id)
        task = session.get(WatchTask, listing.task_id)
        observation = session.query(OfferObservation).filter_by(offer_id=offer.id).order_by(OfferObservation.id.desc()).first()
        candidate = decode_offer(observation.payload)
        quote = session.query(ManualQuote).filter_by(offer_id=offer.id, revoked=False).order_by(ManualQuote.id.desc()).first()
        value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents, utc(quote.confirmed_at),
            utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
        return assess_offer(task, listing, candidate, value, self.now(), vision_enabled=self.provider.enabled("visual", runtime),
            condition_score=row.payload.get("condition_score"), requirement_match=row.payload.get("requirement_match"),
            visual_valid=row.payload.get("visual_scope") == candidate.key)

    def _source_key(self, session, listing, detail=None):
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        def canonical(url):
            try:
                parts = urlsplit(url)
                ignored = {"sig", "sign", "signature", "token", "expires", "ossaccesskeyid", "x-oss-signature", "x-oss-security-token", "x-oss-credential", "x-oss-date", "x-oss-expires"}
                query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in ignored])
                return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
            except ValueError:
                return "unsupported"
        if detail is None:
            description = listing.description
            media = [(r.kind, canonical(r.source_url), r.offer_key) for r in session.query(MediaAsset).filter(
                MediaAsset.listing_id == listing.id, MediaAsset.source_url != "", MediaAsset.status != "superseded")]
            offers = [(r.offer_key, r.attributes) for r in session.query(ListingOffer).filter_by(listing_id=listing.id)]
        else:
            description = detail["description"]
            media = [(r["kind"], canonical(r["url"]), r["offer_key"]) for r in detail["media"]]
            offers = [(o["key"], o["attributes"]) for o in detail["offers"]]
        return digest([listing.title, description, sorted(media, key=str), sorted(offers, key=str)])

    def _refresh_price(self, session, run, listing_id):
        listing = session.get(Listing, listing_id)
        rows = session.query(OfferEvaluationRecord).join(ListingOffer).filter(
            ListingOffer.listing_id == listing_id, OfferEvaluationRecord.payload["run_id"].as_integer() == run.id).all()
        rows = [r for r in rows if r.payload.get("status") not in {"ready", "completed", "superseded"}]
        observations = [r.payload["observation_id"] for r in rows]
        for row in rows:
            row.payload = {**row.payload, "status": "refreshing", "eligible": False}
        return [node(Stage.DETAIL, ["price_refresh", listing_id, observations], {
            "listing_id": listing_id, "price_refresh": True, "eval_ids": [r.id for r in rows],
            "source_key": self._source_key(session, listing)})]


def build_pipeline(factory, settings_service, *, adapter_factory=None, provider=None,
                   notifier_factory=None, processor_factory=None, clock=time.time):
    if notifier_factory is None:
        def notifier_factory():
            from goodprice.notify.log import LogNotifier
            from goodprice.notify.registry import build_enabled_notifiers
            return dict([("log", LogNotifier()), *build_enabled_notifiers(settings_service.get())])
    pipeline = Pipeline(factory, settings_service, adapter_factory, provider, notifier_factory, processor_factory, clock)
    limits = ResourceLimits.from_settings(settings_service.get())
    store = ExecutionStore(factory, global_limit=limits.global_items, task_limit=limits.task_items,
        active_limit=limits.active_runs, run_minutes=limits.run_minutes)
    store.finish_guard = pipeline.can_finish
    store.completion_state = lambda session, run: "deferred" if (run.cursor or {}).get("remaining_ids") else "completed"
    pipeline.store = store
    controller = Coordinator(store, limits, WorkerPools(limits, {stage: pipeline.worker for stage in Stage if stage != Stage.NOTIFY}),
                             clock=clock, apply=pipeline.apply)
    controller.pipeline = pipeline
    return controller
