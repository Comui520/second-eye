"""Conservative, bounded offer parsing. Unsupported platform state stays unknown.

The data-* fixture format and Schema.org Product/Offer are supported adapters,
not a claim that every current Goofish SKU widget exposes these attributes.
"""
import json
import re
from dataclasses import replace
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from goodprice.crawler import selectors as sel
from goodprice.offers.matching import stable_offer_key
from goodprice.offers.pricing import parse_money_cents
from goodprice.offers.types import OfferCandidate, PriceClaim

_INQUIRY = re.compile(r"(?:标价.{0,5}(?:非|不代表|不为).{0,5}(?:售|价)|(?:价格|报价).{0,8}(?:私聊|咨询|面议)|(?:私聊|咨询).{0,8}(?:价格|报价))")


def _claim(raw, source, now, evidence_id, scope=None):
    text = str(raw or "").strip()[:1000]
    kind, amount, low, high = "unknown", None, None, None
    if _INQUIRY.search(text):
        kind = "inquiry"
    elif "起" in text or re.search(r"\d\s*[-~至]\s*\d", text):
        kind = "range_or_from"
        numbers = re.findall(r"\d+(?:\.\d{1,2})?", text)
        if numbers:
            low = parse_money_cents(numbers[0])
            high = parse_money_cents(numbers[1]) if len(numbers) > 1 else None
    else:
        try:
            amount = parse_money_cents(text)
            kind = "exact" if amount > 0 else "unknown"
        except ValueError:
            pass
    return PriceClaim(source, kind, text, now, evidence_id, amount, low, high, scope)


def parse_offer_candidates(html: str) -> list[OfferCandidate]:
    now = datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("[data-item-detail], main, [class*='detail-container--']")
    result = []
    if root:
        description = root.select_one("[data-item-description], " + sel.DETAIL_DESC)
        disclaimer = description.get_text(" ", strip=True) if description else ""
        global_claims = ((_claim(disclaimer, "description", now, "description"),)
                         if _INQUIRY.search(disclaimer) else ())
        nodes = root.select("[data-sku-id]")
        for i, node in enumerate(nodes[:21]):
            if i == 20:
                result = [replace(o, claims=(*o.claims, PriceClaim(
                    "dom", "unknown", "规格获取不完整", now, "truncated"))) for o in result]
                break
            try:
                attrs = json.loads(node.get("data-attributes", "{}"))
                if not isinstance(attrs, dict) or any(not isinstance(v, str) for v in attrs.values()):
                    continue
                key = stable_offer_key(node.get("data-sku-id"), attrs)
                if not key:
                    continue
                claim = _claim(node.get("data-price"), "sku", now, f"{key}:price", key)
                available = "unavailable" if node.get("data-available") == "false" else "unknown"
                if node.get("data-available") == "true":
                    available = "available"
                result.append(OfferCandidate(key, attrs, available, (claim, *global_claims),
                                             node.get("data-name", "")))
            except (ValueError, TypeError):
                continue
        if not nodes:
            price = root.select_one("[data-item-price], " + sel.DETAIL_PRICE_RANGE)
            if price or global_claims:
                result.append(OfferCandidate("default", {}, "unknown", (
                    _claim(price.get_text(" ", strip=True) if price else "", "dom", now, "detail:price"),
                    *global_claims), "默认商品"))
    if not result:
        products = []
        for script in soup.select('script[type="application/ld+json"]')[:10]:
            try:
                data = json.loads(script.string or "")
            except (ValueError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            products.extend(d for d in items if isinstance(d, dict) and d.get("@type") == "Product")
        if len(products) == 1:
            product = products[0]
            offers = product.get("offers", [])
            offers = [offers] if isinstance(offers, dict) else offers
            if isinstance(offers, list):
                for i, data in enumerate(offers[:20]):
                    if not isinstance(data, dict) or data.get("priceCurrency", "CNY") != "CNY":
                        continue
                    attrs = data.get("itemOffered", {}).get("additionalProperty", []) if isinstance(data.get("itemOffered", {}), dict) else []
                    attrs = {str(a["name"]): str(a["value"]) for a in attrs
                             if isinstance(a, dict) and "name" in a and "value" in a} if isinstance(attrs, list) else {}
                    key = stable_offer_key(data.get("sku"), attrs) or ("default" if len(offers) == 1 else None)
                    if key:
                        claim = _claim(data.get("price", ""), "sku", now, f"jsonld:{i}", key)
                        if len(offers) > 20:
                            claim = replace(claim, kind="unknown", amount_cents=None, text="规格获取不完整")
                        available = "unavailable" if "OutOfStock" in str(data.get("availability")) else "unknown"
                        if "InStock" in str(data.get("availability")):
                            available = "available"
                        description = str(product.get("description", ""))
                        disclaimers = (_claim(description, "description", now, "jsonld:description"),) if _INQUIRY.search(description) else ()
                        result.append(OfferCandidate(key, attrs, available, (claim, *disclaimers), str(data.get("name", ""))))
    if not result:
        result = [OfferCandidate("default", claims=(PriceClaim(
            "dom", "unknown", "未找到可核验的规格价格", now, "missing"),))]
    merged = {}
    for offer in result:
        if offer.key in merged:
            old = merged[offer.key]
            merged[offer.key] = replace(old, claims=(*old.claims, *offer.claims, PriceClaim(
                "dom", "conflict", "相同规格身份存在重复记录", now, "duplicate")))
        else:
            merged[offer.key] = offer
    return list(merged.values())
