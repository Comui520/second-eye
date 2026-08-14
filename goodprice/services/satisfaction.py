def _drop_bonus(pct: float) -> float:
    """降价加成：较首见降幅 ≥8% 加 1 档，≥20% 加 2 档（折算进性价比维度）。"""
    if pct >= 0.20:
        return 2.0
    if pct >= 0.08:
        return 1.0
    return 0.0


def compute_satisfaction(
    listing, vision_enabled: bool = True, price_drop_pct: float = 0.0
) -> float:
    """组合评分：视觉开启时 需求40 + 品相30 + 性价比20 + 卖家10；关闭时 需求50 + 性价比30 + 卖家20。
    品相/需求/性价比缺失按半值；卖家未知或无数据按半值；降价加成折算进性价比，总分封顶 100。"""
    if vision_enabled:
        req_w, cond_w, value_w, seller_w = 40.0, 30.0, 20.0, 10.0
    else:
        req_w, cond_w, value_w, seller_w = 50.0, 0.0, 30.0, 20.0
    score = 0.0
    if listing.requirement_match is True:
        score += req_w
    elif listing.requirement_match is None:
        score += req_w / 2
    if cond_w:
        if listing.condition_score is not None:
            score += min(cond_w, listing.condition_score * cond_w / 10)
        else:
            score += cond_w / 2
    value_base = listing.value_score if listing.value_score is not None else 5.0
    value_eff = min(10.0, value_base + _drop_bonus(price_drop_pct))
    score += min(value_w, value_eff * value_w / 10)
    risk = None
    if isinstance(listing.seller_risk, dict):
        risk = listing.seller_risk.get("risk_level")
    score += {"低": seller_w, "中": seller_w / 2, "高": 0.0}.get(risk, seller_w / 2)
    return score


def drop_pct_from_snapshots(listing) -> float:
    snaps = sorted(listing.snapshots or [], key=lambda s: (s.seen_at, s.id))
    if not snaps:
        return 0.0
    first_price = snaps[0].price
    if first_price and first_price > 0:
        return (first_price - listing.price) / first_price
    return 0.0


def backfill_satisfaction(session_factory, vision_enabled: bool = True) -> int:
    from goodprice.models import Listing

    count = 0
    with session_factory() as session:
        for listing in session.query(Listing).all():
            listing.satisfaction = compute_satisfaction(
                listing,
                vision_enabled=vision_enabled,
                price_drop_pct=drop_pct_from_snapshots(listing),
            )
            count += 1
        session.commit()
    return count
