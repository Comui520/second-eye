from datetime import datetime

from goodprice.analysis.media_analysis import analysis_key, analyze_media
from goodprice.media.selection import select_media
from goodprice.media.types import MediaCandidate, MediaSelection
from goodprice.models import Listing, MediaAsset


class MediaService:
    def __init__(self, factory, processor):
        self.factory = factory
        self.processor = processor

    def record(self, listing_id, candidates):
        with self.factory() as session:
            rows = session.query(MediaAsset).filter_by(listing_id=listing_id).all()
            sources = {row.key: row for row in rows if row.kind != "frame" and row.source_url}
            for row in sources.values():
                row.status = "superseded"
            for candidate in candidates:
                row = sources.get(candidate.key)
                if row is None:
                    row = MediaAsset(listing_id=listing_id, key=candidate.key, kind=candidate.kind)
                    session.add(row)
                row.source_url, row.source_locator = candidate.url, candidate.source_locator
                row.relative_path, row.content_hash = None, None
                row.offer_key, row.status = candidate.offer_key, candidate.status
                row.observed_at = datetime.now()
            session.commit()

    def _candidates(self, listing_id):
        with self.factory() as session:
            rows = session.query(MediaAsset).filter(MediaAsset.listing_id == listing_id,
                MediaAsset.source_url != "", MediaAsset.status != "superseded").order_by(MediaAsset.id).all()
            return [MediaCandidate(r.key, r.kind, r.source_url, r.offer_key, r.source_locator,
                                   status="unsupported" if r.status == "unsupported" else "pending") for r in rows]

    def analyze(self, listing_id, offer, client, requirement, settings, *, force=False):
        candidates = self._candidates(listing_id)
        with self.factory() as session:
            listing = session.get(Listing, listing_id)
            seller_text = f"{listing.title}\n{listing.description or ''}" if listing else ""
        with self.processor.session(candidates, settings) as (evidence, failures):
            # A media explicitly bound to a different SKU is not this offer's evidence.
            relevant = [e for e in evidence if e.offer_key is None or e.offer_key == offer.key]
            selection = select_media([e for e in relevant if e.kind == "image"],
                [e for e in relevant if e.kind == "frame"], settings.media_image_quota,
                settings.media_frame_quota, settings.media_total_limit)
            selection = MediaSelection(selection.items, selection.omitted_ids,
                                       tuple(dict.fromkeys(selection.limitations + failures)))
            # Price is excluded: this is a visual judgement, not a price verdict.
            key = analysis_key(offer, selection, client, requirement, settings, seller_text)
            with self.factory() as session:
                listing = session.get(Listing, listing_id)
                previous = listing.condition_detail or {} if listing else {}
                for e in evidence:
                    row = session.query(MediaAsset).filter_by(listing_id=listing_id,
                        key=f"evidence:{e.id}").first()
                    if row is None:
                        row = MediaAsset(listing_id=listing_id, key=f"evidence:{e.id}", kind=e.kind)
                        session.add(row)
                    row.content_hash, row.offer_key = e.content_hash, e.offer_key
                    row.relative_path = e.local_path.relative_to(self.processor.budget.root).as_posix()
                    row.timestamp_ms, row.parent_video_key = e.timestamp_ms, e.video_id
                    row.status = "ready"
                session.commit()
            if not force and previous.get("media_cache_key") == key and selection.items:
                return previous
            result = analyze_media(offer, selection, client, requirement, settings.media_request_max_mib * 1024**2, seller_text)
            result["media_cache_key"] = key
            result["omitted_evidence_ids"] = list(selection.omitted_ids)
            result["defects"] = [o["text"] for o in result.get("observations", [])]
            return result

    def save_result(self, listing_id, result):
        with self.factory() as session:
            listing = session.get(Listing, listing_id)
            if listing:
                listing.condition_score = result.get("condition_score")
                listing.condition_detail = result
                session.commit()

    def public_assets(self, listing_id):
        with self.factory() as session:
            rows = session.query(MediaAsset).filter_by(listing_id=listing_id).order_by(MediaAsset.id).all()
            result = []
            for row in rows:
                if row.status == "superseded":
                    continue
                available = False
                if row.relative_path:
                    try:
                        path = self.processor.budget.checked(self.processor.budget.root / row.relative_path)
                        available = path.is_file()
                    except ValueError:
                        pass
                viewable = available or (row.kind == "image" and bool(row.source_url) and row.status != "unsupported")
                result.append({"id": row.id, "evidence_id": row.key.removeprefix("evidence:"),
                    "kind": row.kind, "offer_key": row.offer_key, "timestamp_ms": row.timestamp_ms,
                    "content_hash": row.content_hash, "status": row.status,
                    "available": available, "viewable": viewable,
                    "url": f"/media/{row.id}" if viewable else None})
            return result
