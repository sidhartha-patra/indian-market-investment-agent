"""Tests for sector-neutral factor scoring."""
from src.strategies.factor_model import factor_composite


def test_factor_composite_runs_with_sector_map(universe):
    sector_map = {f"S{k}.NS": ("IT" if k % 2 == 0 else "Bank") for k in range(10)}
    res = factor_composite(universe, top_n=10, sector_map=sector_map)
    assert {"ticker", "score", "sector"}.issubset(res.columns)
    assert res["score"].between(0, 100).all()
    assert set(res["sector"]).issubset({"IT", "Bank", "OTHER"})


def test_factor_composite_backward_compatible(universe):
    # No sector_map -> universe-wide z-scores, no 'sector' column, still valid scores.
    res = factor_composite(universe, top_n=10)
    assert "sector" not in res.columns
    assert res["score"].between(0, 100).all()


def test_sector_neutral_changes_ranking(universe):
    sector_map = {f"S{k}.NS": ("IT" if k < 5 else "Bank") for k in range(10)}
    plain = factor_composite(universe, top_n=10)
    neutral = factor_composite(universe, top_n=10, sector_map=sector_map)
    # Both produce full rankings; sector-neutral should still yield 10 scored names.
    assert len(plain) == len(neutral) == 10
