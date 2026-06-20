"""Tests for shared technical indicators."""
import numpy as np
import pandas as pd

from src.strategies import indicators as ind
from tests.conftest import make_ohlcv


def test_rsi_bounds():
    df = make_ohlcv(seed=4)
    r = ind.rsi(df["Close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))  # strictly rising
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(up, 14).iloc[-1] > 90
    assert ind.rsi(down, 14).iloc[-1] < 10


def test_atr_positive():
    df = make_ohlcv(seed=5)
    a = ind.atr(df, 14).dropna()
    assert (a > 0).all()
    assert ind.atr_pct(df) > 0


def test_bollinger_columns_and_pctb():
    df = make_ohlcv(seed=6)
    bb = ind.bollinger(df["Close"], 20, 2.0)
    assert set(["mid", "upper", "lower", "pct_b", "bandwidth"]).issubset(bb.columns)
    assert (bb["upper"].dropna() >= bb["lower"].dropna()).all()


def test_adx_bounds():
    df = make_ohlcv(seed=7)
    a = ind.adx(df, 14)["adx"].dropna()
    assert ((a >= 0) & (a <= 100)).all()


def test_supertrend_uptrend_is_up():
    df = make_ohlcv(n=300, seed=8, drift=0.002, vol=0.008)
    st = ind.supertrend(df)
    assert int(st["trend"].iloc[-1]) == 1


def test_macd_shape():
    df = make_ohlcv(seed=9)
    m = ind.macd(df["Close"])
    assert set(["macd", "signal", "hist"]).issubset(m.columns)


def test_risk_helpers_finite():
    df = make_ohlcv(seed=10)
    assert np.isfinite(ind.sharpe_ratio(df["Close"]))
    assert ind.max_drawdown(df["Close"]) <= 0
    assert ind.annualized_volatility(df["Close"]) >= 0


def test_momentum_12_1_handles_short_series():
    short = make_ohlcv(n=100, seed=11)
    assert ind.momentum_12_1(short["Close"]) is None
    long = make_ohlcv(n=300, seed=11)
    assert isinstance(ind.momentum_12_1(long["Close"]), float)
