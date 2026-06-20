"""Performance metrics for return streams (prediction-free, reusable everywhere)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1 + returns.fillna(0.0)).cumprod()


def max_drawdown(returns: pd.Series) -> float:
    curve = equity_curve(returns)
    dd = curve / curve.cummax() - 1
    return float(dd.min()) if len(dd) else 0.0


def cagr(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    r = returns.dropna()
    if r.empty:
        return 0.0
    growth = float((1 + r).prod())
    years = len(r) / periods
    if years <= 0 or growth <= 0:
        return 0.0
    return growth ** (1 / years) - 1


def sharpe(returns: pd.Series, risk_free: float = 0.06, periods: int = TRADING_DAYS) -> float:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return 0.0
    excess = r.mean() - risk_free / periods
    return float(excess / r.std() * np.sqrt(periods))


def sortino(returns: pd.Series, risk_free: float = 0.06, periods: int = TRADING_DAYS) -> float:
    r = returns.dropna()
    if r.empty:
        return 0.0
    downside = r[r < 0]
    dd = downside.std()
    if dd == 0 or np.isnan(dd):
        return 0.0
    excess = r.mean() - risk_free / periods
    return float(excess / dd * np.sqrt(periods))


def deflated_sharpe(observed_sharpe: float, n_trials: int, n_obs: int) -> float:
    """Haircut a Sharpe for multiple-testing/selection bias (López de Prado, simplified).

    Returns a probability-style score in [0, 1]: the chance the true Sharpe > 0 given
    that ``n_trials`` strategies were tried. Lower => more likely a fluke.
    """
    if n_obs < 2 or n_trials < 1:
        return float("nan")
    from scipy.stats import norm

    # Expected max Sharpe under the null from n_trials independent tries.
    emc = 0.5772
    z1 = norm.ppf(1 - 1 / n_trials)
    z2 = norm.ppf(1 - 1 / (n_trials * np.e))
    expected_max = (1 - emc) * z1 + emc * z2
    sr_std = np.sqrt(1.0 / (n_obs - 1))
    return float(norm.cdf((observed_sharpe - expected_max * sr_std) / sr_std))


def performance_metrics(
    returns: pd.Series,
    benchmark: pd.Series | None = None,
    risk_free: float = 0.06,
    periods: int = TRADING_DAYS,
    n_trials: int = 1,
) -> dict:
    """Full performance summary for a daily return series."""
    r = returns.dropna()
    if r.empty:
        return {"error": "no_returns"}

    mdd = max_drawdown(r)
    cg = cagr(r, periods)
    wins = r[r > 0]
    losses = r[r < 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    sr = sharpe(r, risk_free, periods)

    metrics = {
        "total_return_pct": round(float((1 + r).prod() - 1) * 100, 2),
        "cagr_pct": round(cg * 100, 2),
        "ann_volatility_pct": round(float(r.std() * np.sqrt(periods)) * 100, 2),
        "sharpe": round(sr, 2),
        "sortino": round(sortino(r, risk_free, periods), 2),
        "calmar": round(cg / abs(mdd), 2) if mdd != 0 else None,
        "max_drawdown_pct": round(mdd * 100, 2),
        "win_rate_pct": round(float((r > 0).mean()) * 100, 2),
        "avg_win_pct": round(avg_win * 100, 3),
        "avg_loss_pct": round(avg_loss * 100, 3),
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else None,
        "best_day_pct": round(float(r.max()) * 100, 2),
        "worst_day_pct": round(float(r.min()) * 100, 2),
        "n_days": int(len(r)),
    }
    if n_trials > 1:
        metrics["deflated_sharpe_prob"] = round(deflated_sharpe(sr, n_trials, len(r)), 3)

    if benchmark is not None:
        b = benchmark.reindex(r.index).dropna()
        common = r.index.intersection(b.index)
        if len(common) > 20:
            rr, bb = r.loc[common], b.loc[common]
            beta = float(np.cov(rr, bb)[0, 1] / np.var(bb)) if np.var(bb) > 0 else 0.0
            excess = rr - bb
            metrics.update(
                {
                    "benchmark_cagr_pct": round(cagr(bb, periods) * 100, 2),
                    "alpha_cagr_pct": round((cagr(rr, periods) - cagr(bb, periods)) * 100, 2),
                    "beta": round(beta, 2),
                    "information_ratio": round(
                        float(excess.mean() / excess.std() * np.sqrt(periods)), 2
                    )
                    if excess.std() > 0
                    else None,
                }
            )
    return metrics
