"""Portfolio optimisers — convert a basket of picks into sensible weights.

Provides the workhorse long-only schemes used in practice:
- ``equal_weight``        — robust 1/N baseline, hard to beat out-of-sample.
- ``inverse_volatility``  — risk-balanced; lower-vol names get more capital.
- ``min_variance``        — minimise portfolio variance (SLSQP).
- ``max_sharpe``          — tangency portfolio on the efficient frontier (SLSQP).
- ``risk_parity``         — equalise each holding's risk contribution.

Every optimiser is long-only (weights >= 0, sum = 1), honours a per-name cap, and
can be tilted by a per-symbol conviction ``score``. ``build_portfolio`` wires the
whole thing to raw prices and applies the regime ``exposure`` overlay (rest = cash).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def returns_frame(price_data: dict[str, pd.DataFrame], lookback: int = 252) -> pd.DataFrame:
    """Build an aligned daily-returns DataFrame (dates x symbols)."""
    closes = {}
    for sym, df in price_data.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        closes[sym] = df["Close"].astype(float)
    if not closes:
        return pd.DataFrame()
    px = pd.DataFrame(closes).sort_index().tail(lookback + 1)
    return px.pct_change().dropna(how="all")


def _normalise(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    """Clip to [0, max_weight] and renormalise to sum 1 (iteratively to respect caps)."""
    w = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(w.values())
    if total <= 0:
        n = len(w)
        return {k: round(1.0 / n, 4) for k in w} if n else {}
    w = {k: v / total for k, v in w.items()}

    for _ in range(50):
        over = {k: v for k, v in w.items() if v > max_weight + 1e-9}
        if not over:
            break
        excess = sum(v - max_weight for v in over.values())
        for k in over:
            w[k] = max_weight
        room = {k: v for k, v in w.items() if v < max_weight - 1e-9}
        room_total = sum(room.values())
        if room_total <= 0:
            break
        for k in room:
            w[k] += excess * (w[k] / room_total)
    return {k: round(v, 4) for k, v in w.items()}


def equal_weight(symbols: list[str], max_weight: float = 1.0) -> dict[str, float]:
    if not symbols:
        return {}
    return _normalise({s: 1.0 for s in symbols}, max_weight)


def inverse_volatility(returns: pd.DataFrame, max_weight: float = 0.25) -> dict[str, float]:
    if returns.empty:
        return {}
    vol = returns.std()
    inv = (1.0 / vol.replace(0.0, np.nan)).fillna(0.0)
    return _normalise(inv.to_dict(), max_weight)


def _solve(returns: pd.DataFrame, objective, max_weight: float) -> dict[str, float]:
    from scipy.optimize import minimize

    cols = list(returns.columns)
    n = len(cols)
    if n == 0:
        return {}
    if n == 1:
        return {cols[0]: 1.0}

    cov = returns.cov().values * 252
    mean = returns.mean().values * 252
    x0 = np.full(n, 1.0 / n)
    bounds = [(0.0, max(max_weight, 1.0 / n)) for _ in range(n)]
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    result = minimize(
        objective,
        x0,
        args=(mean, cov),
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-9},
    )
    weights = result.x if result.success else x0
    return _normalise(dict(zip(cols, weights)), max_weight)


def min_variance(returns: pd.DataFrame, max_weight: float = 0.25) -> dict[str, float]:
    def obj(w, _mean, cov):
        return float(w @ cov @ w)

    return _solve(returns, obj, max_weight)


def max_sharpe(
    returns: pd.DataFrame, risk_free: float = 0.06, max_weight: float = 0.25
) -> dict[str, float]:
    def obj(w, mean, cov):
        port_ret = float(w @ mean) - risk_free
        port_vol = float(np.sqrt(max(w @ cov @ w, 1e-12)))
        return -port_ret / port_vol  # minimise negative Sharpe

    return _solve(returns, obj, max_weight)


def risk_parity(returns: pd.DataFrame, max_weight: float = 0.25) -> dict[str, float]:
    """Equalise marginal risk contributions (SLSQP on the RC dispersion)."""
    from scipy.optimize import minimize

    cols = list(returns.columns)
    n = len(cols)
    if n == 0:
        return {}
    if n == 1:
        return {cols[0]: 1.0}
    cov = returns.cov().values * 252

    def obj(w):
        port_var = w @ cov @ w
        if port_var <= 0:
            return 0.0
        rc = w * (cov @ w)
        target = port_var / n
        return float(np.sum((rc - target) ** 2))

    x0 = np.full(n, 1.0 / n)
    bounds = [(1e-4, max(max_weight, 1.0 / n)) for _ in range(n)]
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    result = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=constraints,
                      options={"maxiter": 500, "ftol": 1e-10})
    weights = result.x if result.success else x0
    return _normalise(dict(zip(cols, weights)), max_weight)


def _score_tilt(weights: dict[str, float], scores: dict[str, float], strength: float,
                max_weight: float) -> dict[str, float]:
    """Tilt optimiser weights toward higher-conviction names (geometric blend)."""
    if not scores:
        return weights
    vals = [scores.get(s, 0.0) for s in weights]
    lo, hi = min(vals), max(vals)
    tilted = {}
    for sym, w in weights.items():
        norm = (scores.get(sym, 0.0) - lo) / (hi - lo) if hi > lo else 0.5
        multiplier = 1.0 + strength * (norm - 0.5) * 2  # in [1-strength, 1+strength]
        tilted[sym] = w * max(0.1, multiplier)
    return _normalise(tilted, max_weight)


_METHODS = {
    "equal": lambda r, mw: equal_weight(list(r.columns), mw),
    "inverse_vol": inverse_volatility,
    "min_variance": min_variance,
    "max_sharpe": max_sharpe,
    "risk_parity": risk_parity,
}


def build_portfolio(
    price_data: dict[str, pd.DataFrame],
    method: str = "inverse_vol",
    scores: dict[str, float] | None = None,
    max_weight: float = 0.25,
    exposure: float = 1.0,
    score_tilt: float = 0.4,
    capital: float | None = None,
) -> dict:
    """Build target weights for ``price_data`` and apply the regime ``exposure`` overlay.

    Returns weights summing to ``exposure`` (the remainder is cash) plus a per-name
    allocation table. ``scores`` (e.g. factor or blended scores) tilt the weights.
    """
    returns = returns_frame(price_data)
    if returns.empty:
        return {"method": method, "weights": {}, "cash_pct": 100.0, "allocations": []}

    func = _METHODS.get(method)
    if func is None:
        raise ValueError(f"Unknown method '{method}'. Choose from {sorted(_METHODS)}")
    base = func(returns, max_weight) if method != "equal" else func(returns, max_weight)

    if scores and score_tilt > 0:
        base = _score_tilt(base, scores, score_tilt, max_weight)

    exposure = float(max(0.0, min(1.0, exposure)))
    weights = {s: round(w * exposure, 4) for s, w in base.items()}

    allocations = []
    for sym, w in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
        row = {"symbol": sym, "weight_pct": round(w * 100, 2)}
        if capital:
            row["allocation"] = round(w * capital, 2)
        if scores:
            row["score"] = round(float(scores.get(sym, 0.0)), 1)
        allocations.append(row)

    return {
        "method": method,
        "exposure_pct": round(exposure * 100, 2),
        "cash_pct": round((1 - exposure) * 100, 2),
        "max_weight_pct": round(max_weight * 100, 2),
        "weights": weights,
        "allocations": allocations,
    }
