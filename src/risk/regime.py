"""Market-regime detection — gate equity exposure to the prevailing trend.

The single biggest driver of long-only results is *how much* you are invested when
the market falls. This module reads the benchmark index (and, optionally, market
breadth across the universe) and returns a regime label plus a recommended equity
exposure in [0, 1] that the portfolio layer scales gross positions by. Buying full
size in a confirmed down-trend is the most common way retail books blow up.
"""
from __future__ import annotations

import pandas as pd

from src.strategies import indicators as ind

# Regime -> default gross equity exposure (fraction of capital deployed long).
EXPOSURE = {"RISK_ON": 1.0, "NEUTRAL": 0.6, "RISK_OFF": 0.25}


def _breadth_above_ma(price_data: dict[str, pd.DataFrame] | None, window: int = 200) -> float | None:
    if not price_data:
        return None
    above = 0
    total = 0
    for df in price_data.values():
        close = df["Close"].dropna()
        win = window if len(close) >= window else 100
        if len(close) < 60:
            continue
        ma = ind.sma(close, win).iloc[-1]
        if pd.isna(ma):
            continue
        total += 1
        if close.iloc[-1] > ma:
            above += 1
    if total == 0:
        return None
    return round(above / total * 100, 1)


def detect_regime(
    benchmark: pd.DataFrame | pd.Series | None,
    price_data: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Classify the market regime from index trend + (optional) breadth.

    Returns a dict with ``regime`` (RISK_ON/NEUTRAL/RISK_OFF), ``equity_exposure``
    (0-1), a 0-100 ``risk_score``, the underlying signals, and a human ``rationale``.
    """
    signals: dict[str, object] = {}
    score = 0  # higher = more risk-on
    rationale: list[str] = []

    breadth = _breadth_above_ma(price_data, 200)
    if breadth is not None:
        signals["breadth_above_200dma_pct"] = breadth
        if breadth >= 60:
            score += 25
            rationale.append(f"Broad participation ({breadth}% above 200DMA)")
        elif breadth >= 40:
            score += 12
        else:
            rationale.append(f"Weak breadth ({breadth}% above 200DMA)")

    if benchmark is not None:
        close = (benchmark["Close"] if isinstance(benchmark, pd.DataFrame) else benchmark).dropna()
        if len(close) >= 60:
            last = float(close.iloc[-1])
            sma50 = ind.sma(close, 50).iloc[-1]
            sma200 = ind.sma(close, 200 if len(close) >= 200 else 100).iloc[-1]
            dist_200 = (last / float(sma200) - 1) * 100 if pd.notna(sma200) and sma200 else 0.0
            ret_3m = ind.lookback_return(close, 63) or 0.0
            dd = ind.max_drawdown(close.tail(252)) * 100

            signals.update(
                {
                    "index_above_50dma": bool(pd.notna(sma50) and last > sma50),
                    "index_above_200dma": bool(pd.notna(sma200) and last > sma200),
                    "golden_cross": bool(pd.notna(sma50) and pd.notna(sma200) and sma50 > sma200),
                    "dist_from_200dma_pct": round(dist_200, 2),
                    "index_return_3m_pct": round(ret_3m, 2),
                    "index_drawdown_1y_pct": round(dd, 2),
                }
            )

            if signals["index_above_200dma"]:
                score += 25
                rationale.append("Index above 200DMA")
            else:
                rationale.append("Index below 200DMA (structural caution)")
            if signals["golden_cross"]:
                score += 15
                rationale.append("50DMA above 200DMA (golden cross)")
            if ret_3m > 3:
                score += 15
            elif ret_3m < -5:
                rationale.append(f"Index down {ret_3m:.1f}% over 3M")
            if dd < -15:
                score -= 10
                rationale.append(f"Index in {dd:.0f}% drawdown")
    else:
        # No benchmark: lean on breadth only, default toward neutral.
        score += 12

    score = int(max(0, min(100, score)))
    if score >= 60:
        regime = "RISK_ON"
    elif score >= 35:
        regime = "NEUTRAL"
    else:
        regime = "RISK_OFF"

    return {
        "regime": regime,
        "risk_score": score,
        "equity_exposure": EXPOSURE[regime],
        "signals": signals,
        "rationale": rationale or ["Insufficient data; defaulting toward neutral exposure."],
    }


def regime_exposure_multiplier(regime: str) -> float:
    """Map a regime label to its gross-exposure multiplier (default 0.6)."""
    return EXPOSURE.get(regime, 0.6)


if __name__ == "__main__":
    from src.config import NIFTY_50
    from src.ingestion.prices import fetch_prices

    bench = fetch_prices(["^NSEI"], period="2y").get("^NSEI")
    universe = fetch_prices(NIFTY_50[:20], period="2y")
    import json

    print(json.dumps(detect_regime(bench, universe), indent=2, default=str))
