"""Leak-safe feature engineering for the price-prediction models.

Design rules (a quant's non-negotiables):
- **Point-in-time only.** Every feature at date *t* uses data up to and including *t*.
  Rolling/EWM windows are backward-looking by construction; we never peek forward.
- **Predict returns, not price levels.** The target is the forward *h*-day return, which
  is far closer to stationary than raw price and avoids trivially "predicting" a trend.
- **Drop the last h rows when training** (their forward return is unknown), but keep the
  final row for live inference.

The point prediction is later converted back to a price via ``last_close * (1 + ret)``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies import indicators as ind

FEATURE_COLUMNS = [
    "ret_1", "ret_5", "ret_10", "ret_21", "ret_63",
    "rsi_14", "rsi_2",
    "vol_21", "vol_63",
    "macd_hist_norm",
    "dist_sma20", "dist_sma50", "dist_sma200",
    "bb_pctb", "bb_bandwidth",
    "atr_pct",
    "volume_z",
    "mom_12_1",
]


def _safe_pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a / b.replace(0.0, np.nan) - 1) * 100


def compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the full backward-looking feature matrix (one row per date)."""
    close = df["Close"].astype(float)
    out = pd.DataFrame(index=df.index)

    out["ret_1"] = close.pct_change(1) * 100
    out["ret_5"] = close.pct_change(5) * 100
    out["ret_10"] = close.pct_change(10) * 100
    out["ret_21"] = close.pct_change(21) * 100
    out["ret_63"] = close.pct_change(63) * 100

    out["rsi_14"] = ind.rsi(close, 14)
    out["rsi_2"] = ind.rsi(close, 2)

    daily = close.pct_change()
    out["vol_21"] = daily.rolling(21).std() * np.sqrt(ind.TRADING_DAYS) * 100
    out["vol_63"] = daily.rolling(63).std() * np.sqrt(ind.TRADING_DAYS) * 100

    macd = ind.macd(close)
    out["macd_hist_norm"] = macd["hist"] / close * 100

    out["dist_sma20"] = _safe_pct(close, ind.sma(close, 20))
    out["dist_sma50"] = _safe_pct(close, ind.sma(close, 50))
    out["dist_sma200"] = _safe_pct(close, ind.sma(close, 200))

    bb = ind.bollinger(close, 20, 2.0)
    out["bb_pctb"] = bb["pct_b"]
    out["bb_bandwidth"] = bb["bandwidth"] * 100

    if {"High", "Low"}.issubset(df.columns):
        out["atr_pct"] = ind.atr(df, 14) / close * 100
    else:
        out["atr_pct"] = daily.abs().rolling(14).mean() * 100

    if "Volume" in df.columns:
        vol = df["Volume"].astype(float)
        vmean = vol.rolling(20).mean()
        vstd = vol.rolling(20).std().replace(0.0, np.nan)
        out["volume_z"] = (vol - vmean) / vstd
    else:
        out["volume_z"] = 0.0

    out["mom_12_1"] = (close.shift(21) / close.shift(252) - 1) * 100
    return out[FEATURE_COLUMNS]


def make_dataset(
    df: pd.DataFrame, horizon: int = 5
) -> tuple[pd.DataFrame, pd.Series]:
    """Return (X, y) where y is the forward ``horizon``-day return in percent.

    Rows whose forward return is unknown (the last ``horizon`` rows) and rows with
    missing features are dropped — so this is a clean, leakage-free training set.
    """
    feats = compute_feature_frame(df)
    close = df["Close"].astype(float)
    target = (close.shift(-horizon) / close - 1) * 100
    target.name = f"fwd_ret_{horizon}d_pct"

    data = feats.copy()
    data[target.name] = target
    data = data.replace([np.inf, -np.inf], np.nan).dropna()
    if data.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=float)
    return data[FEATURE_COLUMNS], data[target.name]


def latest_features(df: pd.DataFrame) -> pd.DataFrame | None:
    """The most recent fully-populated feature row, for live inference."""
    feats = compute_feature_frame(df).replace([np.inf, -np.inf], np.nan).dropna()
    if feats.empty:
        return None
    return feats.iloc[[-1]]
