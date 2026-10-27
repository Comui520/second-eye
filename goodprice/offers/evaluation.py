"""Pure offer gates shared by scheduling and final publication."""
from dataclasses import asdict

from goodprice.offers.matching import match_variant
from goodprice.offers.pricing import decide_price


def assess_offer(task, listing, candidate, quote, now, *, vision_enabled,
                 condition_score=None, requirement_match=None, visual_valid=False, ready=True):
    ttl = task.interval_minutes * 60 if task.fetch_detail else 0
    decision = decide_price(candidate, quote, now, ttl)
    match = match_variant(task.target_variant, candidate.attributes, listing.title)
    eligible = match == "matched" and decision.state == "exact" and candidate.availability != "unavailable"
    reason = decision.reason if not eligible else ""
    if decision.amount_cents is not None:
        if decision.amount_cents < round(task.min_price * 100) or (task.max_price and decision.amount_cents > round(task.max_price * 100)):
            eligible, reason = False, "该规格价格不在预算内"
    if match != "matched":
        eligible, reason = False, "目标规格不匹配" if match == "mismatched" else "规格待核验"
    if not task.enabled or listing.blocked:
        eligible, reason = False, "任务已停用或商品已拉黑"
    price_eligible = eligible
    if not vision_enabled or not visual_valid or type(condition_score) is not int or not 0 <= condition_score <= 10:
        condition_score = None
    if task.min_condition_score and (not vision_enabled or not visual_valid or condition_score is None
                                     or condition_score < task.min_condition_score):
        eligible, reason = False, "品相未核验或低于门槛"
    if requirement_match is False:
        eligible, reason = False, "需求不匹配"
    if not ready:
        eligible, reason = False, "分析尚未完成"
    return {**asdict(decision), "fresh_until": decision.fresh_until.isoformat() if decision.fresh_until else None,
            "match": match, "eligible": eligible, "price_eligible": price_eligible, "reason": reason,
            "condition_score": condition_score, "requirement_match": requirement_match}
