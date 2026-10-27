import pytest

from goodprice.media.budget import BudgetExceeded, MediaBudget


def test_concurrent_reservations_include_temporary_outputs(tmp_path):
    budget = MediaBudget(tmp_path, 256 * 1024**2, 86400, safety_bytes=0)
    with budget.reserve("first", 200 * 1024**2):
        other = MediaBudget(tmp_path, 256 * 1024**2, 86400, safety_bytes=0)
        with pytest.raises(BudgetExceeded):
            other.reserve("second", 200 * 1024**2)
    with budget.reserve("second", 200 * 1024**2):
        pass
    assert budget.usage_bytes == 0


def test_active_inputs_survive_cleanup_and_output_overflow_fails(tmp_path):
    budget = MediaBudget(tmp_path, 32, 0, safety_bytes=0)
    with budget.reserve("one", 16) as token:
        path = token.directory / "image.jpg"
        path.write_bytes(b"12345678")
        budget.commit(token.token, [path])
        path = token.committed_paths[0]
        with budget.pin([path]):
            with pytest.raises(BudgetExceeded):
                budget.reserve("second", 32)
            assert path.exists()
    with budget.reserve("oversized", 8) as token:
        path = token.directory / "large.jpg"
        path.write_bytes(b"x" * 9)
        with pytest.raises(BudgetExceeded):
            budget.commit(token.token, [path])
    assert not path.exists()


def test_reject_paths_outside_root(tmp_path):
    budget = MediaBudget(tmp_path / "cache", 100, 1, safety_bytes=0)
    outside = tmp_path / "secret"
    outside.write_text("secret")
    with budget.reserve("one", 30) as token:
        with pytest.raises(ValueError):
            budget.commit(token.token, [outside])
    assert outside.read_text() == "secret"


def test_impossible_reservation_does_not_wait(tmp_path):
    budget = MediaBudget(tmp_path, 32, 1, safety_bytes=0)
    with pytest.raises(BudgetExceeded):
        budget.reserve("one", 33)
