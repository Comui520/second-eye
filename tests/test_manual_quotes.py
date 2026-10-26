from datetime import datetime, timedelta, timezone

import pytest

from goodprice.models import Listing, ListingOffer, ManualQuote, OfferObservation
from goodprice.offers.pricing import offer_fingerprint
from goodprice.offers.service import OfferService
from goodprice.offers.types import OfferCandidate, PriceClaim, QuoteInput


def test_quote_validation_before_commit_and_independent_storage(session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="1", title="t", price=1, url="u")
        session.add(listing)
        session.commit()
        listing_id = listing.id
    service = OfferService(session_factory)
    offer = OfferCandidate("sku:1", {"容量": "256GB"}, "available", (
        PriceClaim("sku", "exact", "1500元", now, "p", amount_cents=150000),))
    offer_id = service.record_observation(listing_id, offer)
    bad = QuoteInput("sku:1", -1, now, now + timedelta(days=7), offer_fingerprint(offer))
    with pytest.raises(ValueError):
        service.confirm_quote(offer_id, bad)
    with session_factory() as session:
        assert session.query(ManualQuote).count() == 0
    quote = QuoteInput("sku:1", 140000, now, now + timedelta(days=7), offer_fingerprint(offer))
    quote_id = service.confirm_quote(offer_id, quote)
    service.record_observation(listing_id, offer)
    with session_factory() as session:
        assert session.get(ManualQuote, quote_id).amount_cents == 140000
        assert session.query(ListingOffer).count() == 1
    service.revoke_quote(quote_id)
    with session_factory() as session:
        assert session.get(ManualQuote, quote_id).revoked is True
        assert session.query(OfferObservation).count() >= 1


def test_stale_confirmation_cannot_be_saved(session_factory):
    now = datetime.now(timezone.utc)
    service = OfferService(session_factory)
    with pytest.raises((ValueError, LookupError)):
        service.confirm_quote(999, QuoteInput("x", 1, now, now, "old"))
