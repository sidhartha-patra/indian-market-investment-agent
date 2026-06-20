"""Shared synthetic-data fixtures for the test suite (no network access)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(
    n: int = 400,
    seed: int = 0,
    drift: float = 0.0006,
    vol: float = 0.012,
    start: str = "2022-01-03",
) -> pd.DataFrame:
    """Geometric-random-walk OHLCV frame with a configurable drift/volatility."""
    rng = np.random.RandomState(seed)
    rets = rng.randn(n) * vol + drift
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.date_range(start, periods=n, freq="B")
    high = close * (1 + np.abs(rng.randn(n)) * 0.006)
    low = close * (1 - np.abs(rng.randn(n)) * 0.006)
    volume = rng.randint(100_000, 1_000_000, n)
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx
    )


@pytest.fixture
def uptrend() -> pd.DataFrame:
    return make_ohlcv(n=400, seed=1, drift=0.0012, vol=0.011)


@pytest.fixture
def downtrend() -> pd.DataFrame:
    return make_ohlcv(n=400, seed=2, drift=-0.0012, vol=0.013)


@pytest.fixture
def flat() -> pd.DataFrame:
    return make_ohlcv(n=400, seed=3, drift=0.0, vol=0.009)


@pytest.fixture
def universe() -> dict[str, pd.DataFrame]:
    """A 10-name universe with a spread of drifts/vols for cross-sectional tests."""
    out = {}
    for k in range(10):
        out[f"S{k}.NS"] = make_ohlcv(
            n=500, seed=10 + k, drift=0.0002 + 0.0003 * k / 10, vol=0.008 + 0.006 * (k % 3)
        )
    return out


@pytest.fixture
def benchmark() -> pd.DataFrame:
    return make_ohlcv(n=500, seed=99, drift=0.0005, vol=0.009)
