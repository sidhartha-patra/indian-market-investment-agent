"""Mean-reversion strategy — buy short-term oversold dips inside a long uptrend.

Connors-style RSI(2) approach: only fade pullbacks in names that are still in a
structural uptrend (price above its long moving average). This complements the
trend/momentum books, which buy strength, by buying weakness selectively. It is a
short-horizon, tactical signal — pair every entry with the suggested ATR stop.
"""
from __future__ import annotations

import pandas as pd

from src.strategies import indicators as ind


def mean_reversion_score(df: pd.DataFrame, rsi_period: int = 2) -> dict:
    """Score a single ticker as an oversold-bounce candidate (0-100).

    Signals:
    - Long-term uptrend intact (Close > SMA200, or SMA100 for shorter history).
    - Deeply oversold short-term RSI(2) (< 10 ideal, < 25 acceptable).
    - Price near/below the lower Bollinger band (%B low).
    - Pulled back a few percent from the recent swing high (room to revert).
    - Not in free-fall: still above the 50DMA keeps it a dip, not a breakdown.
    """
    if len(df) < 60:
        return {"score": 0, "signal": "insufficient_data"}

    close = df["Close"]
    last = float(close.iloc[-1])

    long_win = 200 if len(close) >= 200 else 100
    sma_long = ind.sma(close, long_win).iloc[-1]
    sma50 = ind.sma(close, 50).iloc[-1]
    rsi2 = ind.rsi(close, rsi_period).iloc[-1]
    bands = ind.bollinger(close, 20, 2.0)
    pct_b = bands["pct_b"].iloc[-1]
    hi_20 = close.tail(20).max()
    pullback = (last / hi_20 - 1) * 100 if hi_20 else 0.0

    in_uptrend = bool(pd.notna(sma_long) and last > sma_long)
    above_50 = bool(pd.notna(sma50) and last > sma50)

    score = 0
    # Trend gate — a mean-reversion long only makes sense inside an uptrend.
    if in_uptrend:
        score += 30
    if above_50:
        score += 10

    if pd.notna(rsi2):
        if rsi2 < 5:
            score += 30
        elif rsi2 < 10:
            score += 24
        elif rsi2 < 20:
            score += 15
        elif rsi2 < 30:
            score += 8

    if pd.notna(pct_b):
        if pct_b < 0:
            score += 15
        elif pct_b < 0.2:
            score += 10
        elif pct_b < 0.35:
            score += 5

    if -12 < pullback < -2:
        score += 15
    elif -2 <= pullback <= 0:
        score += 5

    # A long uptrend filter is mandatory: without it, suppress the signal.
    if not in_uptrend:
        score = min(score, 25)

    signal = "BUY" if score >= 70 else "WATCH" if score >= 45 else "HOLD"

    return {
        "score": int(score),
        "signal": signal,
        "last_price": round(last, 2),
        "rsi2": round(float(rsi2), 1) if pd.notna(rsi2) else None,
        "pct_b": round(float(pct_b), 2) if pd.notna(pct_b) else None,
        "pullback_from_20d_high_pct": round(pullback, 2),
        "in_uptrend": in_uptrend,
        "entry": round(last, 2),
        "atr_stop": _atr_stop(df, last),
    }


def _atr_stop(df: pd.DataFrame, last: float, mult: float = 2.0) -> float | None:
    a = ind.atr(df, 14).iloc[-1]
    if pd.isna(a):
        return None
    return round(last - mult * float(a), 2)


def screen(price_data: dict[str, pd.DataFrame], top_n: int = 10) -> pd.DataFrame:
    """Rank a universe by mean-reversion (oversold-in-uptrend) score."""
    rows = []
    for ticker, df in price_data.items():
        r = mean_reversion_score(df)
        r["ticker"] = ticker
        rows.append(r)
    if not rows:
        return pd.DataFrame(columns=["ticker", "score", "signal"])
    return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n)


if __name__ == "__main__":
    from src.config import NIFTY_50
    from src.ingestion.prices import fetch_prices

    data = fetch_prices(NIFTY_50[:10], period="1y")
    print(screen(data))
