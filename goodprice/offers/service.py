from datetime import datetime, timezone

from goodprice.models import Listing, ListingOffer, ManualQuote, OfferObservation, WatchTask
from goodprice.offers.pricing import offer_fingerprint, utc
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

    def record_observation(self, listing_id: int, offer: OfferCandidate) -> int:
        if not offer.key or not offer.claims:
            raise ValueError("规格或证据不能为空")
        with self._session_factory() as session:
            if session.get(Listing, listing_id) is None:
                raise LookupError("商品不存在")
            row = session.query(ListingOffer).filter_by(listing_id=listing_id, offer_key=offer.key).first()
            if row is None:
                row = ListingOffer(listing_id=listing_id, offer_key=offer.key)
                session.add(row)
                session.flush()
            row.name, row.attributes, row.availability = offer.name, dict(offer.attributes), offer.availability
            session.add(OfferObservation(offer_id=row.id, fingerprint=offer_fingerprint(offer),
                                         payload=encode_offer(offer),
                                         observed_at=min(utc(c.observed_at) for c in offer.claims)))
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
            session.commit()
            return row.id

    def revoke_quote(self, quote_id: int) -> None:
        with self._session_factory() as session:
            quote = session.get(ManualQuote, quote_id)
            if quote is None:
                raise LookupError("报价不存在")
            quote.revoked = True
            session.commit()
