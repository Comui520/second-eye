import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from goodprice.offers.types import OfferCandidate, PriceDecision, QuoteInput


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_money_cents(raw: str) -> int:
    text = str(raw).strip().removeprefix("￥").removeprefix("¥").removesuffix("元").strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", text):
        raise ValueError("价格必须是非负人民币金额，最多两位小数")
    try:
        cents = Decimal(text.replace(",", "")) * 100
        if not cents.is_finite() or cents < 0 or cents > 10**12:
            raise ValueError("价格超出支持范围")
        return int(cents)
    except InvalidOperation as exc:
        raise ValueError("无效价格") from exc


def offer_fingerprint(offer: OfferCandidate) -> str:
    # Capture time is deliberately excluded: renewing an unchanged observation
    # must not invalidate a quote or cause another paid analysis/notification.
    claims = sorted((c.source, c.kind, c.text, c.amount_cents, c.min_cents, c.max_cents,
                     c.scope_key or "") for c in offer.claims)
    raw = {"key": offer.key, "attributes": dict(offer.attributes),
           "availability": offer.availability, "claims": claims}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decide_price(offer: OfferCandidate, quote: QuoteInput | None,
                 now: datetime, ttl_seconds: int) -> PriceDecision:
    now = utc(now)
    claims = [c for c in offer.claims if c.scope_key in (None, "", offer.key)]
    ids = tuple(c.evidence_id for c in claims)

    def result(state, reason, amount=None, source=None, until=None):
        return PriceDecision(state, amount, source, ids, reason, until)

    if ttl_seconds <= 0 or not claims:
        return result("unknown", "无有效价格证据")
    if offer.availability == "unavailable":
        return result("unknown", "该规格不可购买")
    until = min(utc(c.observed_at) for c in claims) + timedelta(seconds=ttl_seconds)
    if any(utc(c.observed_at) > now + timedelta(seconds=5) for c in claims) or until <= now:
        return result("unknown", "价格或规格证据已过期，等待重新核验")
    if quote and not quote.revoked and utc(quote.expires_at) > now:
        if quote.offer_key != offer.key or quote.evidence_fingerprint != offer_fingerprint(offer):
            return result("conflict", "页面证据已变化，请复核人工报价")
        if utc(quote.confirmed_at) <= now and type(quote.amount_cents) is int and quote.amount_cents > 0:
            return result("exact", "人工确认报价", quote.amount_cents, "manual",
                          min(until, utc(quote.expires_at)))
        return result("unknown", "无效的人工报价")
    for state in ("conflict", "inquiry"):
        if any(c.kind == state for c in claims):
            return result(state, "价格声明存在冲突" if state == "conflict" else "需联系卖家询价")
    exact = [c for c in claims if c.kind == "exact" and c.source in {"sku", "dom", "description"}
             and type(c.amount_cents) is int and 0 < c.amount_cents <= 10**12]
    amounts = {c.amount_cents for c in exact}
    if len(amounts) > 1:
        return result("conflict", "同一规格有多个不一致价格")
    if len(amounts) == 1:
        return result("exact", "该规格有明确页面价格", exact[0].amount_cents, exact[0].source, until)
    if any(c.kind == "range_or_from" for c in claims):
        return result("range_or_from", "只有起售价或价格区间")
    return result("unknown", "缺少可核验的明确价格")


def is_comparable_drop(previous: PriceDecision, current: PriceDecision, same_offer: bool) -> bool:
    return bool(same_offer and previous.state == current.state == "exact"
                and previous.source == current.source
                and previous.amount_cents is not None and current.amount_cents is not None
                and 0 < current.amount_cents < previous.amount_cents)
