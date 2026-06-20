"""Vectorised technical-indicator primitives shared across strategies.

Dependency-light (numpy/pandas only) so every strategy, the risk engine and the
backtester compute signals the same consistent way. All functions accept a price
Series or an OHLCV DataFrame and return pandas objects aligned to the input index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=max(2, window // 2)).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=max(2, span // 2)).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (0-100). Lower = oversold, higher = overbought."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # When there are no losses RSI is 100; when no gains it is 0.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain != 0, out.where(avg_loss == 0, 0.0))
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift()
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing), in price units."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    a = atr(df, period)
    last_close = float(df["Close"].iloc[-1])
    if not np.isfinite(a.iloc[-1]) or last_close == 0:
        return 0.0
    return float(a.iloc[-1] / last_close * 100)


def bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    """Return mid/upper/lower bands and %B (0 at lower band, 1 at upper)."""
    mid = sma(close, window)
    std = close.rolling(window, min_periods=max(2, window // 2)).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower).replace(0.0, np.nan)
    pct_b = (close - lower) / width
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b, "bandwidth": width / mid}
    )


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index with +DI/-DI. ADX > 25 ~ trending market."""
    high, low = df["High"], df["Low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    tr = true_range(df)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    atr_safe = atr_.replace(0.0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_safe

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_ = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Supertrend trailing-stop indicator.

    Returns columns: ``supertrend`` (line), ``trend`` (1 uptrend / -1 downtrend).
    """
    hl2 = (df["High"] + df["Low"]) / 2
    atr_ = atr(df, period)
    upper = hl2 + multiplier * atr_
    lower = hl2 - multiplier * atr_
    close = df["Close"]

    n = len(df)
    final_upper = upper.copy()
    final_lower = lower.copy()
    for i in range(1, n):
        if close.iloc[i - 1] <= final_upper.iloc[i - 1]:
            final_upper.iloc[i] = min(upper.iloc[i], final_upper.iloc[i - 1])
        if close.iloc[i - 1] >= final_lower.iloc[i - 1]:
            final_lower.iloc[i] = max(lower.iloc[i], final_lower.iloc[i - 1])

    trend = np.ones(n, dtype=int)
    line = np.full(n, np.nan)
    for i in range(1, n):
        if close.iloc[i] > final_upper.iloc[i - 1]:
            trend[i] = 1
        elif close.iloc[i] < final_lower.iloc[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
        line[i] = final_lower.iloc[i] if trend[i] == 1 else final_upper.iloc[i]

    return pd.DataFrame({"supertrend": line, "trend": trend}, index=df.index)


def annualized_return(close: pd.Series, periods: int = TRADING_DAYS) -> float:
    rets = close.pct_change().dropna()
    if rets.empty:
        return 0.0
    return float((1 + rets.mean()) ** periods - 1)


def annualized_volatility(close: pd.Series, periods: int = TRADING_DAYS) -> float:
    rets = close.pct_change().dropna()
    if rets.empty:
        return 0.0
    return float(rets.std() * np.sqrt(periods))


def sharpe_ratio(close: pd.Series, risk_free: float = 0.06, periods: int = TRADING_DAYS) -> float:
    ann_ret = annualized_return(close, periods)
    ann_vol = annualized_volatility(close, periods)
    if ann_vol == 0:
        return 0.0
    return float((ann_ret - risk_free) / ann_vol)


def max_drawdown(close: pd.Series) -> float:
    """Largest peak-to-trough decline as a negative fraction (e.g. -0.32)."""
    if close.empty:
        return 0.0
    cummax = close.cummax()
    dd = close / cummax - 1
    return float(dd.min())


def lookback_return(close: pd.Series, days: int) -> float | None:
    """Simple return over the last ``days`` sessions, in percent."""
    if len(close) <= days:
        return None
    return float((close.iloc[-1] / close.iloc[-1 - days] - 1) * 100)


def momentum_12_1(close: pd.Series) -> float | None:
    """Academic 12-1 momentum: 12-month return excluding the most recent month."""
    if len(close) < TRADING_DAYS + 1:
        return None
    start = close.iloc[-TRADING_DAYS]
    end = close.iloc[-21]  # skip last ~1 month to avoid short-term reversal
    if start == 0:
        return None
    return float((end / start - 1) * 100)
