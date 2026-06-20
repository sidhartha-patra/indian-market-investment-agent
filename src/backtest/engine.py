"""Strategy backtester — lookahead-free, cost- and slippage-aware.

Evaluates a cross-sectional ranking strategy by periodically rebalancing into the
top-N names. Critically:
- The ``ranker`` only ever sees prices up to and including the rebalance date, so
  there is no look-ahead.
- Transaction cost **and** slippage are charged on turnover at each rebalance.
- Results are reported against a benchmark with realistic, deflated metrics.

A backtest is a *hypothesis test on the past*, not a forecast. Treat strong numbers
with suspicion — see ``metrics.deflated_sharpe`` and the walk-forward note in README.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

from src.backtest import metrics as M

logger = logging.getLogger(__name__)

# A ranker maps a price history (dates x symbols closes) to target weights.
Ranker = Callable[[pd.DataFrame], dict]


def prices_panel(price_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build an aligned wide close-price panel (dates x symbols) from OHLCV dicts."""
    closes = {
        s: df["Close"].astype(float)
        for s, df in price_data.items()
        if df is not None and not df.empty and "Close" in df.columns
    }
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index()


def _rebalance_dates(index: pd.DatetimeIndex, freq: str) -> list[pd.Timestamp]:
    """Last trading day of each period ('M' month, 'W' week, 'Q' quarter)."""
    s = pd.Series(index, index=index)
    grouped = s.groupby(index.to_period(freq))
    return [g.iloc[-1] for _, g in grouped]


def _weights_vector(weights: dict, columns: pd.Index) -> np.ndarray:
    return np.array([weights.get(c, 0.0) for c in columns], dtype=float)


def backtest_strategy(
    price_data: dict[str, pd.DataFrame] | pd.DataFrame,
    ranker: Ranker,
    rebalance: str = "M",
    cost_bps: float = 10.0,
    slippage_bps: float = 5.0,
    benchmark: pd.Series | pd.DataFrame | None = None,
    warmup: int = 252,
    risk_free: float = 0.06,
    n_trials: int = 1,
) -> dict:
    """Run a periodic-rebalance backtest of ``ranker`` over ``price_data``.

    Returns metrics, the equity curve, per-rebalance holdings, and turnover/cost
    diagnostics. ``n_trials`` (number of strategies you tried) enables a deflated
    Sharpe so you can discount data-snooping.
    """
    panel = price_data if isinstance(price_data, pd.DataFrame) else prices_panel(price_data)
    if panel.empty or len(panel) <= warmup:
        return {"error": "insufficient_data"}

    daily_ret = panel.pct_change()
    rebal_dates = [d for d in _rebalance_dates(panel.index, rebalance) if d >= panel.index[warmup]]
    if not rebal_dates:
        return {"error": "no_rebalance_dates"}

    cols = panel.columns
    port_ret = pd.Series(0.0, index=panel.index)
    prev_w = np.zeros(len(cols))
    cost_rate = (cost_bps + slippage_bps) / 1e4
    holdings: list[dict] = []
    total_cost = 0.0

    for i, rdate in enumerate(rebal_dates):
        hist = panel.loc[:rdate]
        try:
            target = ranker(hist) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ranker failed at %s: %s", rdate.date(), exc)
            target = {}
        w = _weights_vector(target, cols)
        if w.sum() > 0:
            w = w / w.sum()

        turnover = float(np.abs(w - prev_w).sum()) / 2.0
        cost = turnover * cost_rate
        total_cost += cost

        end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else panel.index[-1]
        window = daily_ret.loc[(daily_ret.index > rdate) & (daily_ret.index <= end)]
        if not window.empty:
            seg = window.fillna(0.0).values @ w
            seg_ret = pd.Series(seg, index=window.index)
            # Charge the rebalance cost on the first day of the holding window.
            seg_ret.iloc[0] -= cost
            port_ret.loc[seg_ret.index] = seg_ret

        holdings.append(
            {
                "date": str(rdate.date()),
                "n_holdings": int((w > 0).sum()),
                "turnover": round(turnover, 3),
                "top": sorted(target.items(), key=lambda kv: kv[1], reverse=True)[:5],
            }
        )
        prev_w = w

    port_ret = port_ret.loc[rebal_dates[0]:]
    bench_ret = None
    if benchmark is not None:
        bser = benchmark["Close"] if isinstance(benchmark, pd.DataFrame) else benchmark
        bench_ret = bser.reindex(panel.index).pct_change().loc[port_ret.index]

    result = M.performance_metrics(
        port_ret, benchmark=bench_ret, risk_free=risk_free, n_trials=n_trials
    )
    curve = M.equity_curve(port_ret)
    return {
        "metrics": result,
        "equity_curve": {str(k.date()): round(float(v), 4) for k, v in curve.items()},
        "rebalances": holdings,
        "n_rebalances": len(rebal_dates),
        "avg_turnover": round(float(np.mean([h["turnover"] for h in holdings])), 3),
        "total_cost_drag_pct": round(total_cost * 100, 2),
        "config": {
            "rebalance": rebalance,
            "cost_bps": cost_bps,
            "slippage_bps": slippage_bps,
            "warmup": warmup,
        },
    }


# --------------------------------------------------------------------------- #
# Close-only ranker factories (self-contained; need no High/Low/Volume).
# --------------------------------------------------------------------------- #
def _select_weights(scores: pd.Series, top_n: int, weighting: str, hist: pd.DataFrame) -> dict:
    picks = scores.dropna().sort_values(ascending=False).head(top_n)
    if picks.empty:
        return {}
    if weighting == "score":
        shifted = picks - picks.min() + 1e-6
        w = shifted / shifted.sum()
    elif weighting == "inverse_vol":
        vol = hist[picks.index].pct_change().tail(63).std()
        inv = (1.0 / vol.replace(0.0, np.nan)).fillna(0.0)
        w = inv / inv.sum() if inv.sum() > 0 else pd.Series(1.0 / len(picks), index=picks.index)
    else:  # equal
        w = pd.Series(1.0 / len(picks), index=picks.index)
    return w.to_dict()


def momentum_ranker(top_n: int = 10, lookback: int = 252, skip: int = 21,
                    weighting: str = "equal") -> Ranker:
    """Cross-sectional 12-1 style momentum ranker."""
    def rank(hist: pd.DataFrame) -> dict:
        if len(hist) < lookback + 1:
            return {}
        past = hist.iloc[-lookback]
        recent = hist.iloc[-skip - 1] if skip else hist.iloc[-1]
        scores = (recent / past - 1)
        return _select_weights(scores, top_n, weighting, hist)

    return rank


def low_vol_ranker(top_n: int = 10, lookback: int = 126, weighting: str = "inverse_vol") -> Ranker:
    """Low-volatility factor ranker (lowest trailing vol scores highest)."""
    def rank(hist: pd.DataFrame) -> dict:
        if len(hist) < lookback + 1:
            return {}
        vol = hist.pct_change().tail(lookback).std()
        scores = -vol  # lower vol => higher score
        return _select_weights(scores, top_n, weighting, hist)

    return rank


def composite_ranker(top_n: int = 10, weighting: str = "equal") -> Ranker:
    """Close-only multi-factor blend: momentum + low-vol + trend (z-scored)."""
    def rank(hist: pd.DataFrame) -> dict:
        if len(hist) < 200:
            return {}
        mom = (hist.iloc[-21] / hist.iloc[-252] - 1) if len(hist) >= 252 else (
            hist.iloc[-1] / hist.iloc[-126] - 1
        )
        vol = hist.pct_change().tail(126).std()
        sma200 = hist.tail(200).mean()
        trend = hist.iloc[-1] / sma200 - 1

        def z(s):
            s = s.astype(float)
            sd = s.std(ddof=0)
            return (s - s.mean()) / sd if sd > 0 else pd.Series(0.0, index=s.index)

        scores = 0.4 * z(mom) - 0.3 * z(vol) + 0.3 * z(trend)
        return _select_weights(scores, top_n, weighting, hist)

    return rank


def equal_weight_buy_hold() -> Ranker:
    """Benchmark ranker: hold every name equally (no selection)."""
    def rank(hist: pd.DataFrame) -> dict:
        cols = [c for c in hist.columns if hist[c].notna().iloc[-1]]
        return {c: 1.0 / len(cols) for c in cols} if cols else {}

    return rank


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from src.config import NIFTY_50
    from src.ingestion.prices import fetch_prices

    data = fetch_prices(NIFTY_50, period="3y")
    res = backtest_strategy(data, momentum_ranker(top_n=10), rebalance="M", n_trials=4)
    import json

    print(json.dumps(res["metrics"], indent=2))
