"""Momentum strategy: 52-week breakouts with volume confirmation."""
from __future__ import annotations
import pandas as pd


def momentum_score(df: pd.DataFrame) -> dict:
    """Compute momentum score for a single ticker.

    Signals:
    - Price within 5% of 52-week high (breakout candidate)
    - 20D avg volume > 1.2x 50D avg volume
    - 3-month return > 0
    """
    if len(df) < 60:
        return {"score": 0, "signal": "insufficient_data"}

    close = df["Close"]
    vol = df["Volume"]
    hi_52w = close.tail(252).max() if len(close) >= 252 else close.max()
    last = close.iloc[-1]
    pct_from_high = (last / hi_52w - 1) * 100

    vol20 = vol.tail(20).mean()
    vol50 = vol.tail(50).mean()
    vol_ratio = vol20 / vol50 if vol50 else 0

    ret_3m = (last / close.iloc[-min(63, len(close))] - 1) * 100

    score = 0
    if pct_from_high > -5:
        score += 40
    elif pct_from_high > -10:
        score += 20
    if vol_ratio > 1.2:
        score += 30
    elif vol_ratio > 1.0:
        score += 15
    if ret_3m > 10:
        score += 30
    elif ret_3m > 0:
        score += 15

    signal = "BUY" if score >= 70 else "WATCH" if score >= 40 else "HOLD"

    return {
        "score": score,
        "signal": signal,
        "last_price": round(last, 2),
        "pct_from_52w_high": round(pct_from_high, 2),
        "volume_ratio": round(vol_ratio, 2),
        "return_3m_pct": round(ret_3m, 2),
    }


def screen(price_data: dict[str, pd.DataFrame], top_n: int = 10) -> pd.DataFrame:
    """Rank universe by momentum score."""
    rows = []
    for ticker, df in price_data.items():
        m = momentum_score(df)
        m["ticker"] = ticker
        rows.append(m)
    res = pd.DataFrame(rows).sort_values("score", ascending=False)
    return res.head(top_n)


if __name__ == "__main__":
    from src.ingestion.prices import fetch_prices
    from src.config import NIFTY_50
    data = fetch_prices(NIFTY_50[:10], period="1y")
    print(screen(data))
