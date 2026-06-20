"""Tests for the new strategy screeners."""
import pandas as pd

from src.strategies import mean_reversion, trend_following
from src.strategies.factor_model import factor_composite
from src.strategies.relative_strength import relative_strength
from tests.conftest import make_ohlcv


def test_mean_reversion_keys_and_bounds(uptrend):
    r = mean_reversion.mean_reversion_score(uptrend)
    assert 0 <= r["score"] <= 100
    assert r["signal"] in {"BUY", "WATCH", "HOLD"}
    assert "atr_stop" in r and "in_uptrend" in r


def test_mean_reversion_insufficient_data():
    short = make_ohlcv(n=30, seed=1)
    assert mean_reversion.mean_reversion_score(short)["signal"] == "insufficient_data"


def test_trend_following_uptrend_scores_well(uptrend):
    r = trend_following.trend_score(uptrend)
    assert 0 <= r["score"] <= 100
    assert r["supertrend_dir"] in {"UP", "DOWN"}
    assert r["score"] >= 40  # a clean uptrend should register


def test_screen_returns_sorted_frame(universe):
    df = trend_following.screen(universe, top_n=5)
    assert {"ticker", "score"}.issubset(df.columns)
    assert len(df) <= 5
    assert df["score"].is_monotonic_decreasing


def test_relative_strength_scores_are_percentiles(universe, benchmark):
    rs = relative_strength(universe, benchmark=benchmark, top_n=10)
    assert {"ticker", "score"}.issubset(rs.columns)
    assert rs["score"].between(0, 100).all()
    assert rs["score"].is_monotonic_decreasing


def test_factor_composite_price_only(universe):
    fc = factor_composite(universe, top_n=10)
    assert {"ticker", "score", "signal", "composite_z"}.issubset(fc.columns)
    assert fc["score"].between(0, 100).all()


def test_factor_composite_with_fundamentals(universe):
    funds = {
        "S0.NS": {"Stock P/E": 18, "ROCE": 25},
        "S1.NS": {"Stock P/E": 70, "ROCE": 8},
    }
    fc = factor_composite(universe, fundamentals=funds, top_n=10)
    assert "value" in fc.columns  # value factor activated by fundamentals
