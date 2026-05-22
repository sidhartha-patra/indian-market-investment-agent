"""Mutual fund ranking by rolling returns and risk metrics."""
from __future__ import annotations
import numpy as np
import pandas as pd
from src.ingestion.mutual_funds import fetch_scheme_history


def analyze_scheme(scheme_code: str) -> dict:
    """Compute 1Y / 3Y / 5Y CAGR + Sharpe for a scheme."""
    df = fetch_scheme_history(scheme_code)
    if df.empty or len(df) < 252:
        return {"scheme_code": scheme_code, "error": "insufficient_history"}

    df = df.set_index("date")
    today = df.index.max()
    nav_now = df["nav"].iloc[-1]

    def cagr(years: int) -> float | None:
        cutoff = today - pd.DateOffset(years=years)
        past = df[df.index <= cutoff]
        if past.empty:
            return None
        nav_then = past["nav"].iloc[-1]
        return ((nav_now / nav_then) ** (1 / years) - 1) * 100

    daily_ret = df["nav"].pct_change().dropna()
    ann_vol = daily_ret.std() * np.sqrt(252) * 100
    sharpe = (daily_ret.mean() * 252) / (daily_ret.std() * np.sqrt(252)) if daily_ret.std() else 0

    return {
        "scheme_code": scheme_code,
        "cagr_1y_pct": round(cagr(1), 2) if cagr(1) is not None else None,
        "cagr_3y_pct": round(cagr(3), 2) if cagr(3) is not None else None,
        "cagr_5y_pct": round(cagr(5), 2) if cagr(5) is not None else None,
        "ann_volatility_pct": round(ann_vol, 2),
        "sharpe": round(sharpe, 2),
        "latest_nav": round(nav_now, 4),
    }


def rank_schemes(scheme_codes: list[str]) -> pd.DataFrame:
    rows = [analyze_scheme(c) for c in scheme_codes]
    df = pd.DataFrame([r for r in rows if "error" not in r])
    if df.empty:
        return df
    return df.sort_values("cagr_3y_pct", ascending=False, na_position="last")


if __name__ == "__main__":
    # Sample: Parag Parikh Flexi Cap, Mirae Asset Large Cap, Axis Bluechip
    codes = ["122639", "118989", "120465"]
    print(rank_schemes(codes))
