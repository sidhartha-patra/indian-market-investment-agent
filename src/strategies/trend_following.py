"""Trend-following strategy — ride established up-trends, cut down-trends.

Combines three classic, complementary trend filters so a name only scores high
when price structure, trend strength and momentum all agree:
- Supertrend direction (ATR trailing stop) — the primary regime flag + exit level.
- EMA stack (20 > 50 > 200) — multi-timeframe trend alignment.
- ADX > 25 with +DI > -DI — confirms the trend is strong, not choppy.
- MACD histogram > 0 — momentum tailwind.

Trend-following typically wins by limiting drawdowns: the Supertrend line doubles
as a trailing stop, surfaced as ``trailing_stop``.
"""
from __future__ import annotations

import pandas as pd

from src.strategies import indicators as ind


def trend_score(df: pd.DataFrame) -> dict:
    """Score a single ticker as a trend-following long (0-100)."""
    if len(df) < 60:
        return {"score": 0, "signal": "insufficient_data"}

    close = df["Close"]
    last = float(close.iloc[-1])

    st = ind.supertrend(df, period=10, multiplier=3.0)
    st_trend = int(st["trend"].iloc[-1])
    st_line = st["supertrend"].iloc[-1]

    ema20 = ind.ema(close, 20).iloc[-1]
    ema50 = ind.ema(close, 50).iloc[-1]
    ema200 = ind.ema(close, 200 if len(close) >= 200 else 100).iloc[-1]

    adx_df = ind.adx(df, 14)
    adx_val = adx_df["adx"].iloc[-1]
    plus_di = adx_df["plus_di"].iloc[-1]
    minus_di = adx_df["minus_di"].iloc[-1]

    macd_df = ind.macd(close)
    macd_hist = macd_df["hist"].iloc[-1]

    score = 0
    if st_trend == 1:
        score += 30

    stack_up = pd.notna(ema20) and pd.notna(ema50) and pd.notna(ema200)
    if stack_up and ema20 > ema50 > ema200:
        score += 25
    elif stack_up and ema20 > ema50:
        score += 12

    if pd.notna(adx_val):
        strong = adx_val > 25 and pd.notna(plus_di) and pd.notna(minus_di) and plus_di > minus_di
        moderate = adx_val > 20 and pd.notna(plus_di) and pd.notna(minus_di) and plus_di > minus_di
        if adx_val > 35 and strong:
            score += 25
        elif strong:
            score += 18
        elif moderate:
            score += 8

    if pd.notna(macd_hist) and macd_hist > 0:
        score += 20
    elif pd.notna(macd_hist) and macd_hist > -abs(last) * 0.001:
        score += 8

    signal = "BUY" if score >= 70 else "WATCH" if score >= 45 else "HOLD"

    return {
        "score": int(score),
        "signal": signal,
        "last_price": round(last, 2),
        "supertrend_dir": "UP" if st_trend == 1 else "DOWN",
        "trailing_stop": round(float(st_line), 2) if pd.notna(st_line) else None,
        "adx": round(float(adx_val), 1) if pd.notna(adx_val) else None,
        "ema_stack_aligned": bool(stack_up and ema20 > ema50 > ema200),
        "macd_hist": round(float(macd_hist), 3) if pd.notna(macd_hist) else None,
    }


def screen(price_data: dict[str, pd.DataFrame], top_n: int = 10) -> pd.DataFrame:
    """Rank a universe by trend-following score."""
    rows = []
    for ticker, df in price_data.items():
        r = trend_score(df)
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
