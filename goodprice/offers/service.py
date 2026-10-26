from contextlib import nullcontext
from datetime import datetime, timezone

from goodprice.models import (
    Listing,
    ListingOffer,
    ManualQuote,
    Notification,
    OfferObservation,
    WatchTask,
)
from goodprice.offers.pricing import decide_price, offer_fingerprint, utc
from goodprice.offers.types import OfferCandidate, PriceClaim, QuoteInput


def encode_offer(offer: OfferCandidate) -> dict:
    return {"key": offer.key, "attributes": dict(offer.attributes), "name": offer.name,
            "availability": offer.availability,
            "claims": [{**vars(c), "observed_at": utc(c.observed_at).isoformat()} for c in offer.claims]}


def decode_offer(payload: dict) -> OfferCandidate:
    return OfferCandidate(payload["key"], payload["attributes"], payload["availability"],
                          tuple(PriceClaim(**{**c, "observed_at": datetime.fromisoformat(c["observed_at"])})
                                for c in payload["claims"]), payload.get("name", ""))


class OfferService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def _refresh_price_index(self, session, row, listing, candidate, quote=None):
        task = session.get(WatchTask, listing.task_id) if listing.task_id else None
        if quote is None:
            quote = session.query(ManualQuote).filter_by(offer_id=row.id, revoked=False).order_by(ManualQuote.id.desc()).first()
        value = None if quote is None else QuoteInput(row.offer_key, quote.amount_cents,
            utc(quote.confirmed_at), utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
        ttl = task.interval_minutes * 60 if task and task.fetch_detail else 0
        decision = decide_price(candidate, value, datetime.now(timezone.utc), ttl)
        row.effective_price_state, row.effective_price_cents = decision.state, decision.amount_cents
        row.price_valid_until = utc(decision.fresh_until).replace(tzinfo=None) if decision.fresh_until else None
        row.price_task_version = task.version if task else None

    def record_observation(self, listing_id: int, offer: OfferCandidate, *, session=None) -> int:
        if not offer.key or not offer.claims:
            raise ValueError("规格或证据不能为空")
        own = session is None
        with self._session_factory() if own else nullcontext(session) as session:
            listing = session.get(Listing, listing_id)
            if listing is None:
                raise LookupError("商品不存在")
            row = session.query(ListingOffer).filter_by(listing_id=listing_id, offer_key=offer.key).first()
            if row is None:
                row = ListingOffer(listing_id=listing_id, offer_key=offer.key)
                legacy_sent = session.query(Notification).filter_by(
                    listing_id=listing_id, offer_id=None, status="sent").first()
                if legacy_sent and offer.key == "default":
                    row.last_notified_source = "legacy_baseline"
                session.add(row)
                session.flush()
            row.name, row.attributes, row.availability = offer.name, dict(offer.attributes), offer.availability
            session.add(OfferObservation(offer_id=row.id, fingerprint=offer_fingerprint(offer),
                                         payload=encode_offer(offer),
                                         observed_at=min(utc(c.observed_at) for c in offer.claims)))
            session.flush()
            self._refresh_price_index(session, row, listing, offer)
            if own:
                session.commit()
            return row.id

    def confirm_quote(self, offer_id: int, quote: QuoteInput) -> int:
        now = datetime.now(timezone.utc)
        if (type(quote.amount_cents) is not int or not 0 < quote.amount_cents <= 10**12
                or utc(quote.expires_at) <= now or utc(quote.confirmed_at) > now
                or utc(quote.expires_at) <= utc(quote.confirmed_at) or quote.revoked):
            raise ValueError("报价金额、确认时间或有效期无效")
        with self._session_factory() as session:
            offer = session.get(ListingOffer, offer_id)
            if offer is None:
                raise LookupError("规格不存在")
            latest = session.query(OfferObservation).filter_by(offer_id=offer_id).order_by(
                OfferObservation.observed_at.desc(), OfferObservation.id.desc()).first()
            listing = session.get(Listing, offer.listing_id)
            candidate = decode_offer(latest.payload) if latest else None
            if candidate and any(c.evidence_id in ("duplicate", "truncated") for c in candidate.claims):
                raise ValueError("规格身份不明确或获取不完整，不能用报价代替核验")
            task = session.get(WatchTask, listing.task_id) if listing.task_id else None
            ttl = (task.interval_minutes if task else 20) * 60
            if (latest is None or latest.fingerprint != quote.evidence_fingerprint
                    or offer.offer_key != quote.offer_key or offer.availability == "unavailable"
                    or (now - utc(latest.observed_at)).total_seconds() >= ttl):
                raise ValueError("证据已变化或过期，请刷新后重新确认")
            for old in session.query(ManualQuote).filter_by(offer_id=offer_id, revoked=False):
                old.revoked = True
            row = ManualQuote(offer_id=offer_id, amount_cents=quote.amount_cents,
                              confirmed_at=utc(quote.confirmed_at), expires_at=utc(quote.expires_at),
                              evidence_fingerprint=quote.evidence_fingerprint, revoked=False)
            session.add(row)
            listing.offer_verified_until = None
            self._refresh_price_index(session, offer, listing, candidate, quote=row)
            session.commit()
            return row.id

    def revoke_quote(self, quote_id: int) -> None:
        with self._session_factory() as session:
            quote = session.get(ManualQuote, quote_id)
            if quote is None:
                raise LookupError("报价不存在")
            quote.revoked = True
            offer = session.get(ListingOffer, quote.offer_id)
            listing = session.get(Listing, offer.listing_id)
            listing.offer_verified_until = None
            session.flush()
            observation = session.query(OfferObservation).filter_by(offer_id=offer.id).order_by(OfferObservation.id.desc()).first()
            if observation:
                self._refresh_price_index(session, offer, listing, decode_offer(observation.payload))
            session.commit()
