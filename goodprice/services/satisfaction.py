def compute_satisfaction(listing, vision_enabled: bool = True) -> float:
    """组合评分：视觉开启时 需求 50 + 品相 40 + 卖家 10；关闭时 需求 70 + 卖家 30。"""
    if vision_enabled:
        req_w, cond_w, seller_w = 50.0, 40.0, 10.0
    else:
        req_w, cond_w, seller_w = 70.0, 0.0, 30.0
    score = 0.0
    if listing.requirement_match is True:
        score += req_w
    elif listing.requirement_match is None:
        score += req_w / 2
    if cond_w and listing.condition_score is not None:
        score += min(cond_w, listing.condition_score * cond_w / 10)
    risk = None
    if isinstance(listing.seller_risk, dict):
        risk = listing.seller_risk.get("risk_level")
    score += {"低": seller_w, "中": seller_w / 2, "高": 0.0}.get(risk, 0.0)
    return score


def backfill_satisfaction(session_factory, vision_enabled: bool = True) -> int:
    from goodprice.models import Listing

    count = 0
    with session_factory() as session:
        for listing in session.query(Listing).filter(Listing.satisfaction == 0).all():
            listing.satisfaction = compute_satisfaction(listing, vision_enabled=vision_enabled)
            count += 1
        session.commit()
    return count
