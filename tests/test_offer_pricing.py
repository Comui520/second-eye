from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from goodprice.offers.pricing import (
    decide_price,
    is_comparable_drop,
    offer_fingerprint,
    parse_money_cents,
)
from goodprice.offers.types import OfferCandidate, PriceClaim, QuoteInput

NOW = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)


def candidate(kind="exact", cents=150000, text="256GB 1500元", source="sku"):
    return OfferCandidate("sku:256", {"容量": "256GB"}, "available", (
        PriceClaim(source, kind, text, NOW, "price-1", amount_cents=cents),))


def test_money_uses_integer_cents():
    assert parse_money_cents("1500.00") == 150000
    assert parse_money_cents("￥1,500.50元") == 150050


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-1", "1.001", "USD 3", "3起", "1e3"])
def test_invalid_money_rejected(raw):
    with pytest.raises(ValueError):
        parse_money_cents(raw)


@pytest.mark.parametrize("kind", ["range_or_from", "inquiry", "conflict", "unknown"])
def test_non_exact_claim_is_never_usable_price(kind):
    result = decide_price(candidate(kind), None, NOW, 1200)
    assert result.state == kind
    assert result.amount_cents is None


def test_exact_price_and_freshness_boundary():
    result = decide_price(candidate(), None, NOW, 1200)
    assert (result.state, result.amount_cents) == ("exact", 150000)
    assert decide_price(candidate(), None, NOW + timedelta(seconds=1200), 1200).state == "unknown"


def test_unsupported_evidence_cannot_assert_price():
    assert decide_price(candidate(source="llm"), None, NOW, 1200).amount_cents is None
    assert decide_price(replace(candidate(), availability="unavailable"), None, NOW, 1200).amount_cents is None


def test_global_disclaimer_overrides_sku_and_different_prices_conflict():
    offer = candidate()
    disclaimer = PriceClaim("description", "inquiry", "所有标价非售价", NOW, "desc")
    assert decide_price(replace(offer, claims=(*offer.claims, disclaimer)), None, NOW, 1200).state == "inquiry"
    other = replace(offer.claims[0], amount_cents=160000, evidence_id="price-2")
    assert decide_price(replace(offer, claims=(*offer.claims, other)), None, NOW, 1200).state == "conflict"


def test_manual_quote_priority_expiry_change_and_source_switch():
    offer = candidate()
    quote = QuoteInput(offer.key, 140000, NOW, NOW + timedelta(days=7), offer_fingerprint(offer))
    manual = decide_price(offer, quote, NOW, 1200)
    assert manual.amount_cents == 140000
    assert manual.source == "manual"
    page = decide_price(offer, replace(quote, revoked=True), NOW, 1200)
    assert not is_comparable_drop(page, manual, same_offer=True)
    assert decide_price(candidate(cents=160000), quote, NOW, 1200).state == "conflict"
    refreshed = replace(offer, claims=(replace(offer.claims[0], observed_at=NOW + timedelta(days=8)),))
    assert offer_fingerprint(refreshed) == offer_fingerprint(offer)
    assert decide_price(refreshed, quote, NOW + timedelta(days=8), 1200).source == "sku"


def test_refresh_failure_does_not_extend_quote_recommendation():
    offer = candidate()
    quote = QuoteInput(offer.key, 140000, NOW, NOW + timedelta(days=7), offer_fingerprint(offer))
    assert decide_price(offer, quote, NOW + timedelta(hours=1), 1200).state == "unknown"


def test_same_source_and_same_offer_required_for_drop():
    previous = decide_price(candidate(), None, NOW, 1200)
    current = decide_price(candidate(cents=130000), None, NOW, 1200)
    assert is_comparable_drop(previous, current, same_offer=True)
    assert not is_comparable_drop(previous, current, same_offer=False)


def test_incomplete_evidence_never_becomes_exact():
    offer = candidate()
    incomplete = PriceClaim("dom", "unknown", "规格获取不完整", NOW, "truncated")
    result = decide_price(replace(offer, claims=(*offer.claims, incomplete)), None, NOW, 1200)
    assert result.state == "unknown"
    assert result.amount_cents is None
