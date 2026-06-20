"""Tests for the backtesting metrics and engine."""
import numpy as np
import pandas as pd

from src.backtest import metrics as M
from src.backtest.engine import (
    backtest_strategy,
    composite_ranker,
    low_vol_ranker,
    momentum_ranker,
    prices_panel,
)


def test_metrics_positive_returns():
    r = pd.Series([0.001] * 252, index=pd.date_range("2023-01-02", periods=252, freq="B"))
    m = M.performance_metrics(r)
    assert m["cagr_pct"] > 0
    assert m["max_drawdown_pct"] == 0.0
    assert m["sharpe"] > 0


def test_max_drawdown_detects_crash():
    r = pd.Series([0.01] * 50 + [-0.5] + [0.0] * 20)
    assert M.max_drawdown(r) < -0.4


def test_equity_curve_grows():
    r = pd.Series([0.01] * 10)
    curve = M.equity_curve(r)
    assert curve.iloc[-1] > curve.iloc[0]


def test_backtest_runs_and_reports(universe):
    panel = prices_panel(universe)
    bench = panel.mean(axis=1)
    res = backtest_strategy(universe, momentum_ranker(top_n=3), rebalance="M",
                            benchmark=bench, warmup=252, n_trials=4)
    assert "metrics" in res and res["n_rebalances"] > 0
    for key in ("cagr_pct", "sharpe", "max_drawdown_pct", "sortino"):
        assert key in res["metrics"]
    assert "deflated_sharpe_prob" in res["metrics"]  # n_trials>1
    assert res["equity_curve"]


def test_backtest_alternative_rankers(universe):
    for ranker in (composite_ranker(top_n=3), low_vol_ranker(top_n=3)):
        res = backtest_strategy(universe, ranker, rebalance="M", warmup=252)
        assert "error" not in res


def test_backtest_has_no_lookahead(universe):
    """The ranker must only ever see prices up to the rebalance date."""
    seen_max_dates = []

    def spy_ranker(hist: pd.DataFrame) -> dict:
        seen_max_dates.append(hist.index.max())
        cols = list(hist.columns)
        return {c: 1.0 / len(cols) for c in cols}

    res = backtest_strategy(universe, spy_ranker, rebalance="M", warmup=252)
    assert "error" not in res
    # Dates handed to the ranker must be strictly increasing (monotonic rebalances).
    assert seen_max_dates == sorted(seen_max_dates)
    # And the engine's portfolio returns all start strictly after the first rebalance.
    first_curve_date = pd.Timestamp(next(iter(res["equity_curve"])))
    assert first_curve_date >= seen_max_dates[0]


def test_insufficient_data_returns_error():
    tiny = {"A.NS": pd.DataFrame({"Close": np.arange(50.0)},
                                 index=pd.date_range("2023-01-02", periods=50, freq="B"))}
    res = backtest_strategy(tiny, momentum_ranker(), warmup=252)
    assert res.get("error") == "insufficient_data"
