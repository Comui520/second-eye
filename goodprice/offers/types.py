from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Mapping

PriceState = Literal["exact", "range_or_from", "inquiry", "conflict", "unknown"]
MatchState = Literal["matched", "mismatched", "uncertain"]


@dataclass(frozen=True)
class PriceClaim:
    source: str
    kind: PriceState
    text: str
    observed_at: datetime
    evidence_id: str
    amount_cents: int | None = None
    min_cents: int | None = None
    max_cents: int | None = None
    scope_key: str | None = None


@dataclass(frozen=True)
class OfferCandidate:
    key: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    availability: Literal["available", "unavailable", "unknown"] = "unknown"
    claims: tuple[PriceClaim, ...] = ()
    name: str = ""

    def __post_init__(self):
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "claims", tuple(self.claims))


@dataclass(frozen=True)
class QuoteInput:
    offer_key: str
    amount_cents: int
    confirmed_at: datetime
    expires_at: datetime
    evidence_fingerprint: str
    revoked: bool = False


@dataclass(frozen=True)
class PriceDecision:
    state: PriceState
    amount_cents: int | None = None
    source: str | None = None
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    fresh_until: datetime | None = None


@dataclass(frozen=True)
class OfferEvaluation:
    offer_key: str
    match: MatchState
    price: PriceDecision
    eligible: bool
    reason: str = ""
