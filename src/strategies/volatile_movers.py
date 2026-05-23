"""Volatile movers — high-beta swing trade screener.

Scores stocks on:
- Realized volatility (annualized) — must be elevated (>30%) to be a "mover"
- ATR% (Average True Range as % of price) — recent intraday range
- Short-term momentum (5D & 20D return) — directional bias
- Volume surge (current vs 30D avg) — institutional interest
- Distance from 20D high (breakout proximity)

Higher score = stronger swing trade candidate. NOT for long-term investing.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.tail(period).mean()
    return float(atr / close.iloc[-1] * 100)


def volatile_score(df: pd.DataFrame) -> dict:
    """Score a single ticker as a volatile-mover candidate."""
    if len(df) < 60:
        return {"score": 0, "signal": "insufficient_data"}

    close = df["Close"]
    vol = df["Volume"]
    rets = close.pct_change().dropna()

    ann_vol = rets.std() * np.sqrt(252) * 100
    atr_pct = _atr_pct(df)
    ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0
    ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
    vol_today = vol.iloc[-1]
    vol_30d_avg = vol.tail(30).mean()
    vol_surge = vol_today / vol_30d_avg if vol_30d_avg else 0
    hi_20d = close.tail(20).max()
    pct_from_20d_high = (close.iloc[-1] / hi_20d - 1) * 100

    score = 0
    # Volatility filter — we WANT volatile names
    if 30 <= ann_vol <= 80:
        score += 25
    elif 20 <= ann_vol < 30 or 80 < ann_vol <= 100:
        score += 10
    # ATR% — intraday opportunity
    if atr_pct > 3:
        score += 20
    elif atr_pct > 2:
        score += 10
    # Short-term momentum
    if ret_5d > 5:
        score += 20
    elif ret_5d > 2:
        score += 10
    elif ret_5d < -5:
        score += 15  # potential bounce setup
    # Volume surge
    if vol_surge > 2:
        score += 20
    elif vol_surge > 1.5:
        score += 10
    # Breakout proximity
    if pct_from_20d_high > -2:
        score += 15
    elif pct_from_20d_high > -5:
        score += 8

    direction = "LONG" if ret_5d > 0 and ret_20d > 0 else \
                "SHORT" if ret_5d < -3 and ret_20d < 0 else "WATCH"
    signal = "STRONG_BUY" if score >= 75 and direction == "LONG" else \
             "BUY" if score >= 55 and direction == "LONG" else \
             "SHORT" if score >= 60 and direction == "SHORT" else "WATCH"

    return {
        "score": score,
        "signal": signal,
        "direction": direction,
        "last_price": round(float(close.iloc[-1]), 2),
        "ann_vol_pct": round(ann_vol, 1),
        "atr_pct": round(atr_pct, 2),
        "return_5d_pct": round(ret_5d, 2),
        "return_20d_pct": round(ret_20d, 2),
        "volume_surge": round(vol_surge, 2),
        "pct_from_20d_high": round(pct_from_20d_high, 2),
    }


def screen(price_data: dict[str, pd.DataFrame], top_n: int = 15) -> pd.DataFrame:
    """Rank universe by volatile-mover score (desc)."""
    rows = []
    for ticker, df in price_data.items():
        r = volatile_score(df)
        r["ticker"] = ticker
        rows.append(r)
    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    return df.head(top_n)


if __name__ == "__main__":
    from src.ingestion.prices import fetch_prices
    from src.universe import HIGH_VOL_UNIVERSE
    data = fetch_prices(HIGH_VOL_UNIVERSE[:15], period="6mo")
    print(screen(data))
