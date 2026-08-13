from types import SimpleNamespace

from goodprice.models import Listing
from goodprice.services.satisfaction import backfill_satisfaction, compute_satisfaction


def _listing(**kw):
    defaults = dict(
        requirement_match=True,
        condition_score=8,
        value_score=8,
        seller_risk={"risk_level": "低"},
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_compute_satisfaction_four_dimensions():
    assert compute_satisfaction(_listing()) == 90.0  # 40 + 24 + 16 + 10
    assert (
        compute_satisfaction(
            _listing(requirement_match=None, condition_score=None, value_score=None, seller_risk=None)
        )
        == 30.0  # 20 + 0 + 10 + 0
    )
    assert (
        compute_satisfaction(
            _listing(requirement_match=False, condition_score=3, value_score=None, seller_risk={"risk_level": "高"})
        )
        == 19.0  # 0 + 9 + 10 + 0
    )


def test_value_score_missing_half_credit():
    assert compute_satisfaction(_listing(value_score=None)) == 84.0  # 40 + 24 + 10 + 10
    assert (
        compute_satisfaction(_listing(value_score=None), vision_enabled=False) == 85.0  # 50 + 15 + 20
    )


def test_value_score_bounds():
    assert compute_satisfaction(_listing(value_score=10)) == 94.0  # 40 + 24 + 20 + 10
    assert compute_satisfaction(_listing(value_score=1)) == 76.0  # 40 + 24 + 2 + 10


def test_compute_satisfaction_vision_off():
    assert compute_satisfaction(_listing(), vision_enabled=False) == 94.0  # 50 + 24 + 20
    assert (
        compute_satisfaction(
            _listing(requirement_match=None, condition_score=None, value_score=None, seller_risk={"risk_level": "高"}),
            vision_enabled=False,
        )
        == 40.0  # 25 + 15 + 0
    )


def test_backfill(session_factory):
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
                value_score=8,
                seller_risk={"risk_level": "低"},
            )
        )
        session.commit()
    assert backfill_satisfaction(session_factory) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.satisfaction == 90.0
