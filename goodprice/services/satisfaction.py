def compute_satisfaction(listing) -> float:
    """组合评分：需求匹配 50 + 品相分 40 + 卖家风险 10，满分 100。"""
    score = 0.0
    if listing.requirement_match is True:
        score += 50
    elif listing.requirement_match is None:
        score += 25
    if listing.condition_score is not None:
        score += min(40.0, listing.condition_score * 4)
    risk = None
    if isinstance(listing.seller_risk, dict):
        risk = listing.seller_risk.get("risk_level")
    score += {"低": 10.0, "中": 5.0, "高": 0.0}.get(risk, 0.0)
    return score


def backfill_satisfaction(session_factory) -> int:
    from goodprice.models import Listing

    count = 0
    with session_factory() as session:
        for listing in session.query(Listing).filter(Listing.satisfaction == 0).all():
            listing.satisfaction = compute_satisfaction(listing)
            count += 1
        session.commit()
    return count
