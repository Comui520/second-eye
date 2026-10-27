from goodprice.execution.limits import RateGate, ResourceLimits


def test_rate_gate_and_retry_after_never_increase_rate():
    gate = RateGate(interval_seconds=6)
    assert gate.reserve("model", 100) == 100
    assert gate.reserve("model", 100) == 106
    gate.postpone("model", 150)
    assert gate.reserve("model", 110) == 150
    assert gate.reserve("another", 110) == 110


def test_limits_reject_unbounded_settings():
    import pytest
    with pytest.raises(ValueError):
        ResourceLimits(model_workers=9)
    with pytest.raises(ValueError):
        ResourceLimits(platform_interval_seconds=0)
