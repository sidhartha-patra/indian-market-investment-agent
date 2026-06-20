"""Relative-strength strategy — cross-sectional, risk-adjusted momentum vs benchmark.

Relative strength (a stock's return ranked against its peers and the index) is one
of the most robust documented return factors. This module ranks the whole universe
on a blend of multi-horizon momentum, divides by realised volatility (so the score
rewards *clean* trends rather than lottery tickets), and subtracts the benchmark's
return so only genuine outperformers float to the top.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies import indicators as ind

# (lookback_days, weight) — longer horizons get more weight (slow momentum persists).
_LOOKBACKS = [(21, 0.15), (63, 0.25), (126, 0.30), (252, 0.30)]


def _benchmark_returns(benchmark: pd.DataFrame | pd.Series | None) -> dict[int, float]:
    out: dict[int, float] = {days: 0.0 for days, _ in _LOOKBACKS}
    if benchmark is None:
        return out
    close = benchmark["Close"] if isinstance(benchmark, pd.DataFrame) else benchmark
    close = close.dropna()
    for days, _ in _LOOKBACKS:
        r = ind.lookback_return(close, days)
        out[days] = r if r is not None else 0.0
    return out


def _raw_rs(df: pd.DataFrame, bench_rets: dict[int, float]) -> dict | None:
    if len(df) < 70:
        return None
    close = df["Close"].dropna()
    ann_vol = ind.annualized_volatility(close) or 0.20

    weighted_excess = 0.0
    weight_used = 0.0
    horizon_returns: dict[str, float | None] = {}
    for days, weight in _LOOKBACKS:
        r = ind.lookback_return(close, days)
        horizon_returns[f"ret_{days}d_pct"] = round(r, 2) if r is not None else None
        if r is None:
            continue
        weighted_excess += weight * (r - bench_rets.get(days, 0.0))
        weight_used += weight
    if weight_used == 0:
        return None

    rel_strength = weighted_excess / weight_used
    risk_adjusted = rel_strength / (ann_vol * 100)  # excess-return per unit of vol
    return {
        "rel_strength": rel_strength,
        "risk_adjusted_rs": risk_adjusted,
        "ann_vol_pct": round(ann_vol * 100, 1),
        **horizon_returns,
    }


def relative_strength(
    price_data: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame | pd.Series | None = None,
    top_n: int = 10,
) -> pd.DataFrame:
    """Rank a universe by risk-adjusted relative strength vs ``benchmark``.

    ``score`` is the cross-sectional percentile (0-100) of the risk-adjusted
    relative-strength metric, so it is directly comparable to the other screeners.
    """
    bench_rets = _benchmark_returns(benchmark)
    rows = []
    for ticker, df in price_data.items():
        raw = _raw_rs(df, bench_rets)
        if raw is None:
            continue
        raw["ticker"] = ticker
        rows.append(raw)

    if not rows:
        return pd.DataFrame(columns=["ticker", "score"])

    out = pd.DataFrame(rows)
    out["score"] = (out["risk_adjusted_rs"].rank(pct=True) * 100).round(1)
    out["rel_strength"] = out["rel_strength"].round(2)
    out["risk_adjusted_rs"] = out["risk_adjusted_rs"].round(3)
    out["signal"] = np.where(
        out["score"] >= 80, "STRONG", np.where(out["score"] >= 60, "LEADER", "LAGGARD")
    )
    return out.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def screen(
    price_data: dict[str, pd.DataFrame],
    top_n: int = 10,
    benchmark: pd.DataFrame | pd.Series | None = None,
) -> pd.DataFrame:
    """Screener-style wrapper so this plugs into the recommendation pipeline."""
    return relative_strength(price_data, benchmark=benchmark, top_n=top_n)


if __name__ == "__main__":
    from src.config import NIFTY_50
    from src.ingestion.prices import fetch_prices

    data = fetch_prices(NIFTY_50[:15], period="1y")
    bench = fetch_prices(["^NSEI"], period="1y").get("^NSEI")
    print(relative_strength(data, benchmark=bench))
