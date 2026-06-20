"""Tests for the portfolio optimisers."""
from src.portfolio import optimizer as opt


def _check_weights(weights, max_weight, expected_sum=1.0):
    assert weights
    assert all(w >= -1e-9 for w in weights.values())
    assert all(w <= max_weight + 1e-6 for w in weights.values())
    assert abs(sum(weights.values()) - expected_sum) < 1e-2


def test_returns_frame_shape(universe):
    rf = opt.returns_frame(universe)
    assert not rf.empty
    assert set(rf.columns) == set(universe.keys())


def test_equal_weight(universe):
    w = opt.equal_weight(list(universe.keys()), max_weight=0.25)
    _check_weights(w, 0.25)


def test_inverse_vol_prefers_low_vol(universe):
    rf = opt.returns_frame(universe)
    w = opt.inverse_volatility(rf, max_weight=0.5)
    _check_weights(w, 0.5)
    vols = rf.std()
    lowest, highest = vols.idxmin(), vols.idxmax()
    assert w[lowest] >= w[highest]


def test_min_variance(universe):
    w = opt.min_variance(opt.returns_frame(universe), max_weight=0.25)
    _check_weights(w, 0.25)


def test_max_sharpe(universe):
    w = opt.max_sharpe(opt.returns_frame(universe), max_weight=0.25)
    _check_weights(w, 0.25)


def test_risk_parity(universe):
    w = opt.risk_parity(opt.returns_frame(universe), max_weight=0.25)
    _check_weights(w, 0.25)


def test_build_portfolio_applies_exposure(universe):
    scores = {s: 50 + i * 5 for i, s in enumerate(universe)}
    port = opt.build_portfolio(universe, method="inverse_vol", scores=scores,
                               exposure=0.6, capital=1_000_000, max_weight=0.3)
    assert abs(sum(port["weights"].values()) - 0.6) < 1e-2  # rest is cash
    assert abs(port["cash_pct"] - 40.0) < 1.0
    assert port["allocations"][0]["allocation"] > 0


def test_build_portfolio_unknown_method_raises(universe):
    import pytest

    with pytest.raises(ValueError):
        opt.build_portfolio(universe, method="nope")
