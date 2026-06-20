"""Position sizing & risk control — turn a signal into a *risk-bounded* trade.

Good entries are worthless without sizing discipline. These helpers convert a price
chart and an account size into concrete share counts using the two industry-standard
rules: (1) ATR-based stops so the exit adapts to each name's volatility, and (2)
fixed-fractional sizing so no single trade risks more than a set %% of capital. Also
included: volatility-target weighting and a (half-)Kelly fraction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies import indicators as ind


def atr_stop_loss(
    df: pd.DataFrame, atr_mult: float = 2.5, period: int = 14, side: str = "long"
) -> dict:
    """Compute an ATR trailing-stop and target for the latest bar.

    A 2.5x ATR stop is wide enough to ride normal noise yet caps the loss per name.
    """
    if len(df) < period + 1:
        return {"error": "insufficient_data"}
    entry = float(df["Close"].iloc[-1])
    a = float(ind.atr(df, period).iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return {"error": "invalid_atr"}
    if side == "long":
        stop = entry - atr_mult * a
        target = entry + 2 * atr_mult * a  # 2:1 reward:risk
    else:
        stop = entry + atr_mult * a
        target = entry - 2 * atr_mult * a
    stop_pct = (stop / entry - 1) * 100
    return {
        "entry": round(entry, 2),
        "atr": round(a, 2),
        "atr_mult": atr_mult,
        "stop": round(stop, 2),
        "stop_pct": round(stop_pct, 2),
        "target": round(target, 2),
        "reward_risk": 2.0,
        "side": side,
    }


def position_size(
    capital: float,
    entry: float,
    stop: float,
    risk_per_trade_pct: float = 1.0,
    max_position_pct: float = 20.0,
) -> dict:
    """Fixed-fractional sizing: risk at most ``risk_per_trade_pct``%% of capital.

    Shares are capped so the notional never exceeds ``max_position_pct``%% of capital,
    which keeps a single low-volatility name from dominating the book.
    """
    if entry <= 0 or capital <= 0:
        return {"error": "invalid_inputs"}
    risk_per_share = abs(entry - stop)
    risk_amount = capital * risk_per_trade_pct / 100
    if risk_per_share <= 0:
        return {"error": "zero_risk_per_share"}

    shares = int(risk_amount // risk_per_share)
    max_notional = capital * max_position_pct / 100
    if shares * entry > max_notional:
        shares = int(max_notional // entry)

    notional = shares * entry
    return {
        "shares": shares,
        "notional": round(notional, 2),
        "position_pct": round(notional / capital * 100, 2) if capital else 0.0,
        "risk_amount": round(min(risk_amount, shares * risk_per_share), 2),
        "risk_per_share": round(risk_per_share, 2),
        "risk_per_trade_pct": risk_per_trade_pct,
    }


def volatility_target_weight(
    returns: pd.Series, target_vol: float = 0.15, max_weight: float = 1.0
) -> float:
    """Scale a single position so its annualised vol contribution ~= ``target_vol``."""
    rets = returns.dropna()
    if len(rets) < 20:
        return 0.0
    ann_vol = float(rets.std() * np.sqrt(ind.TRADING_DAYS))
    if ann_vol <= 0:
        return max_weight
    return float(min(max_weight, target_vol / ann_vol))


def kelly_fraction(win_rate: float, win_loss_ratio: float, fraction: float = 0.5) -> float:
    """Half-Kelly bet fraction. ``win_loss_ratio`` = avg win / avg loss.

    Full Kelly is too aggressive in practice; the default halves it and floors at 0.
    """
    if win_loss_ratio <= 0:
        return 0.0
    kelly = win_rate - (1 - win_rate) / win_loss_ratio
    return float(max(0.0, min(1.0, kelly * fraction)))


def size_from_chart(
    df: pd.DataFrame,
    capital: float,
    risk_per_trade_pct: float = 1.0,
    atr_mult: float = 2.5,
    max_position_pct: float = 20.0,
) -> dict:
    """Convenience: derive ATR stop from the chart, then size the position."""
    stop_info = atr_stop_loss(df, atr_mult=atr_mult)
    if "error" in stop_info:
        return stop_info
    sizing = position_size(
        capital,
        stop_info["entry"],
        stop_info["stop"],
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )
    return {**stop_info, **sizing}
