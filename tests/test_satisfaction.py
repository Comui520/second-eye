from types import SimpleNamespace

from goodprice.services.satisfaction import backfill_satisfaction, compute_satisfaction


def _listing(**kw):
    defaults = dict(requirement_match=True, condition_score=8, seller_risk={"risk_level": "低"})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_compute_satisfaction():
    assert compute_satisfaction(_listing()) == 92.0  # 50 + 32 + 10
    assert compute_satisfaction(_listing(requirement_match=None, condition_score=None, seller_risk=None)) == 25.0
    assert compute_satisfaction(_listing(requirement_match=False, condition_score=3, seller_risk={"risk_level": "高"})) == 12.0


def test_backfill(session_factory):
    from goodprice.models import Listing

    with session_factory() as session:
        session.add(
            Listing(
                platform="xianyu",
                external_id="1",
                title="t",
                price=1,
                url="u",
                requirement_match=True,
                condition_score=8,
                seller_risk={"risk_level": "低"},
            )
        )
        session.commit()
    assert backfill_satisfaction(session_factory) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.satisfaction == 92.0
