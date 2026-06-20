"""Multi-factor composite model — the agent's flagship cross-sectional ranker.

Real quant equity models do not bet on one signal; they blend several lowly-correlated
return factors so the combined score is far more stable than any single one. This module
builds a transparent composite from price (and, when available, fundamentals):

- **Momentum**  — 12-1 month return (trend persistence).
- **Low volatility** — the low-volatility anomaly (lower realised vol scores higher).
- **Trend**     — distance above the 200-day moving average (structural up-trend).
- **Quality (price)** — risk-adjusted consistency (Sharpe).
- **Value***    — earnings yield (1 / P/E); needs fundamentals.
- **Quality (fundamental)*** — ROCE; needs fundamentals.

Each factor is winsorised and z-scored across the universe, multiplied by its weight,
summed, and converted to a 0-100 percentile ``score`` so it slots into the pipeline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies import indicators as ind

_PRICE_WEIGHTS = {"momentum": 0.30, "low_vol": 0.20, "trend": 0.25, "quality_px": 0.25}
_FULL_WEIGHTS = {
    "momentum": 0.24,
    "low_vol": 0.16,
    "trend": 0.16,
    "quality_px": 0.14,
    "value": 0.15,
    "quality_fund": 0.15,
}


def _num(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        f = float(value)
        return f if np.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _price_factors(df: pd.DataFrame) -> dict | None:
    if len(df) < 70:
        return None
    close = df["Close"].dropna()
    last = float(close.iloc[-1])
    long_win = 200 if len(close) >= 200 else 100
    sma_long = ind.sma(close, long_win).iloc[-1]
    trend = (last / float(sma_long) - 1) * 100 if pd.notna(sma_long) and sma_long else 0.0
    mom = ind.momentum_12_1(close)
    if mom is None:
        mom = ind.lookback_return(close, min(126, len(close) - 1))
    return {
        "momentum": mom,
        "low_vol": -ind.annualized_volatility(close) * 100,
        "trend": trend,
        "quality_px": ind.sharpe_ratio(close),
    }


def _winsor_zscore(s: pd.Series, limit: float = 3.0) -> pd.Series:
    s = s.astype(float)
    mean = s.mean()
    std = s.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    z = (s - mean) / std
    return z.clip(-limit, limit).fillna(0.0)


def _sector_neutral_zscore(s: pd.Series, sectors: pd.Series, limit: float = 3.0) -> pd.Series:
    """Winsorised z-score computed *within each sector*.

    Comparing a bank's P/E to an IT firm's creates hidden sector bets; scoring each
    metric against sector peers removes them. Falls back to a universe-wide z-score
    for any sector with too few names to standardise.
    """
    out = pd.Series(0.0, index=s.index, dtype=float)
    sectors = sectors.reindex(s.index).fillna("OTHER")
    for sector in sectors.unique():
        mask = sectors == sector
        sub = s[mask]
        out.loc[mask] = _winsor_zscore(sub, limit) if mask.sum() >= 3 else _winsor_zscore(s, limit)[mask]
    return out


def compute_factors(
    price_data: dict[str, pd.DataFrame],
    fundamentals: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """Return a per-ticker table of raw factor values (pre z-score)."""
    rows = []
    for ticker, df in price_data.items():
        pf = _price_factors(df)
        if pf is None:
            continue
        row = {"ticker": ticker, **pf}
        fund = (fundamentals or {}).get(ticker) or (fundamentals or {}).get(
            ticker.replace(".NS", "").replace(".BO", "")
        )
        if fund:
            pe = _num(fund.get("Stock P/E") or fund.get("P/E"))
            row["value"] = (1.0 / pe * 100) if pe and pe > 0 else None
            row["quality_fund"] = _num(fund.get("ROCE"))
        rows.append(row)
    return pd.DataFrame(rows)


def factor_composite(
    price_data: dict[str, pd.DataFrame],
    fundamentals: dict[str, dict] | None = None,
    weights: dict[str, float] | None = None,
    top_n: int = 10,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Rank a universe by the weighted multi-factor composite score (0-100).

    When ``sector_map`` (ticker -> sector) is supplied, every factor is z-scored
    *within its sector* (sector-neutral), which removes hidden sector bets and is the
    recommended mode for cross-sector universes.
    """
    factors = compute_factors(price_data, fundamentals)
    if factors.empty:
        return pd.DataFrame(columns=["ticker", "score"])

    has_value = "value" in factors.columns and factors["value"].notna().any()
    weights = weights or (_FULL_WEIGHTS if has_value else _PRICE_WEIGHTS)
    weights = {k: v for k, v in weights.items() if k in factors.columns}
    norm = sum(weights.values()) or 1.0

    sectors = None
    if sector_map:
        sectors = factors["ticker"].map(lambda t: sector_map.get(t) or sector_map.get(
            str(t).replace(".NS", "").replace(".BO", "")) or "OTHER")

    composite = pd.Series(0.0, index=factors.index)
    for factor, weight in weights.items():
        if sectors is not None:
            z = _sector_neutral_zscore(factors[factor], sectors)
        else:
            z = _winsor_zscore(factors[factor])
        factors[f"z_{factor}"] = z.round(2)
        composite += (weight / norm) * z

    factors["composite_z"] = composite.round(3)
    factors["score"] = (composite.rank(pct=True) * 100).round(1)
    factors["signal"] = np.where(
        factors["score"] >= 80,
        "STRONG_BUY",
        np.where(factors["score"] >= 60, "BUY", np.where(factors["score"] >= 40, "HOLD", "AVOID")),
    )
    if sectors is not None:
        factors["sector"] = sectors.values
    for col in ("momentum", "low_vol", "trend", "quality_px", "value", "quality_fund"):
        if col in factors.columns:
            factors[col] = factors[col].round(2)
    return factors.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def screen(
    price_data: dict[str, pd.DataFrame],
    top_n: int = 10,
    fundamentals: dict[str, dict] | None = None,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Screener-style wrapper for the recommendation pipeline."""
    return factor_composite(price_data, fundamentals=fundamentals, top_n=top_n, sector_map=sector_map)


if __name__ == "__main__":
    from src.config import NIFTY_50
    from src.ingestion.prices import fetch_prices

    data = fetch_prices(NIFTY_50[:20], period="1y")
    print(factor_composite(data, top_n=10))
