"""Quality screen: low volatility + consistent uptrend + drawdown control."""
from __future__ import annotations
import numpy as np
import pandas as pd


def quality_score(df: pd.DataFrame) -> dict:
    """Quality proxy from price action.

    For real fundamentals (ROE/ROCE/Debt) integrate Screener.in or NSE financials.
    Here we approximate with:
    - Low realized volatility (annualized)
    - High Sharpe-like ratio
    - Limited max drawdown
    """
    if len(df) < 120:
        return {"score": 0, "signal": "insufficient_data"}

    close = df["Close"]
    rets = close.pct_change().dropna()

    ann_ret = (1 + rets.mean()) ** 252 - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol else 0

    cummax = close.cummax()
    dd = (close / cummax - 1).min()

    score = 0
    if sharpe > 1.5:
        score += 40
    elif sharpe > 1.0:
        score += 25
    elif sharpe > 0.5:
        score += 10
    if ann_vol < 0.20:
        score += 30
    elif ann_vol < 0.30:
        score += 15
    if dd > -0.15:
        score += 30
    elif dd > -0.25:
        score += 15

    signal = "BUY" if score >= 70 else "WATCH" if score >= 40 else "HOLD"

    return {
        "score": score,
        "signal": signal,
        "sharpe": round(sharpe, 2),
        "ann_volatility_pct": round(ann_vol * 100, 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
    }


def screen(price_data: dict[str, pd.DataFrame], top_n: int = 10) -> pd.DataFrame:
    rows = []
    for ticker, df in price_data.items():
        q = quality_score(df)
        q["ticker"] = ticker
        rows.append(q)
    return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n)
