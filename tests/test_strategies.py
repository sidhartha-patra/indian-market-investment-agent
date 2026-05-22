"""Basic tests for strategy scorers."""
import numpy as np
import pandas as pd
from src.strategies.momentum import momentum_score
from src.strategies.quality import quality_score


def _synthetic_uptrend(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.linspace(100, 200, n) + np.random.RandomState(42).randn(n) * 2
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.random.RandomState(0).randint(1e5, 1e6, n)
    }, index=idx)


def test_momentum_uptrend_high_score():
    df = _synthetic_uptrend()
    r = momentum_score(df)
    assert r["score"] >= 40
    assert r["signal"] in {"BUY", "WATCH", "HOLD"}


def test_quality_returns_keys():
    df = _synthetic_uptrend()
    r = quality_score(df)
    assert "sharpe" in r and "signal" in r


def test_insufficient_data():
    df = _synthetic_uptrend(n=10)
    assert momentum_score(df)["signal"] == "insufficient_data"
