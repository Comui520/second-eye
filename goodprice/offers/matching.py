import hashlib
import json
import re
import unicodedata
from typing import Mapping

from goodprice.offers.types import MatchState


def _normal(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def stable_offer_key(platform_sku_id: str | None, attributes: Mapping[str, str]) -> str | None:
    if platform_sku_id:
        return f"sku:{platform_sku_id.strip()}"
    normalized = {_normal(k): _normal(v) for k, v in attributes.items() if k.strip() and v.strip()}
    if not normalized:
        return None
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
    return "attrs:" + hashlib.sha256(raw).hexdigest()[:32]


def match_variant(target: str, attributes: Mapping[str, str], title: str) -> MatchState:
    """Only attest explicit attributes, not broad listing titles or guessed negatives."""
    if not target.strip():
        return "matched"
    values = {_normal(v) for v in attributes.values()}
    if not values:
        return "uncertain"
    uncertain = False
    for requirement in re.split(r"[/,，;；]+", target):
        term = _normal(requirement)
        if not term:
            continue
        if term.startswith(("非", "不要", "不含", "无")):
            uncertain = True
        elif term in values:
            continue
        elif re.fullmatch(r"\d+(?:\.\d+)?(?:gb|tb)", term):
            if any(re.fullmatch(r"\d+(?:\.\d+)?(?:gb|tb)", v) for v in values):
                return "mismatched"
            uncertain = True
        elif any(k in ("型号", "model") for k in attributes):
            model = next((v for k, v in attributes.items() if k in ("型号", "model")), "")
            if _normal(model) != term:
                return "mismatched"
        else:
            uncertain = True
    return "uncertain" if uncertain else "matched"
