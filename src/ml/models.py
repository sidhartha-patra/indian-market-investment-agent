"""Prediction models + split-conformal intervals.

Philosophy: **never trust a single point forecast.** Daily/weekly returns are
dominated by noise, so we (1) always carry a naive random-walk baseline that the
fancy models must beat, and (2) wrap every model in split-conformal prediction to
produce *calibrated* intervals with finite-sample coverage guarantees, plus an
empirical probability that the move is up. Models predict the forward return (%);
callers convert to a price with ``last_close * (1 + ret/100)``.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Base regressors. Each exposes sklearn-style fit(X, y) / predict(X).
# --------------------------------------------------------------------------- #
class NaiveRandomWalk:
    """Baseline: tomorrow ≈ today, i.e. predicted forward return = 0.

    This is the bar every other model MUST clear. Beating it on RMSE for daily
    horizons is genuinely hard and most published "predictors" silently fail to.
    """

    name = "naive_random_walk"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.zeros(len(X))


class MovingAverageDrift:
    """Baseline: predict the historical mean forward return (constant drift)."""

    name = "moving_average_drift"

    def __init__(self):
        self._mean = 0.0

    def fit(self, X, y):
        self._mean = float(np.mean(y)) if len(y) else 0.0
        return self

    def predict(self, X):
        return np.full(len(X), self._mean)


def _ridge():
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), Ridge(alpha=10.0))


def _random_forest():
    from sklearn.ensemble import RandomForestRegressor

    return RandomForestRegressor(
        n_estimators=300, max_depth=6, min_samples_leaf=20,
        max_features="sqrt", n_jobs=-1, random_state=42,
    )


def _xgboost():
    from xgboost import XGBRegressor

    return XGBRegressor(
        n_estimators=250, max_depth=4, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
        min_child_weight=5, random_state=42, n_jobs=-1,
    )


_REGISTRY = {
    "naive_random_walk": NaiveRandomWalk,
    "moving_average_drift": MovingAverageDrift,
    "ridge": _ridge,
    "random_forest": _random_forest,
    "xgboost": _xgboost,
}


def available_models() -> list[str]:
    return list(_REGISTRY.keys())


def get_model(name: str):
    """Instantiate a fresh base regressor by name."""
    if name not in _REGISTRY:
        raise ValueError(f"Unknown model '{name}'. Choose from {available_models()}")
    return _REGISTRY[name]()


# --------------------------------------------------------------------------- #
# Split-conformal wrapper — model-agnostic calibrated intervals + P(up).
# --------------------------------------------------------------------------- #
class ConformalRegressor:
    """Wrap any base regressor with split-conformal prediction intervals.

    Uses a time-ordered calibration tail (no shuffling) so intervals respect the
    temporal structure. Coverage is the nominal target (e.g. 0.80 => 80% band).
    """

    def __init__(self, base, calib_frac: float = 0.2):
        self.base = base
        self.calib_frac = calib_frac
        self._abs_residuals: np.ndarray = np.array([])
        self._signed_residuals: np.ndarray = np.array([])
        self.fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series):
        n = len(y)
        n_cal = max(20, int(n * self.calib_frac))
        if n - n_cal < 30:  # too little data to split — fit on all, residuals=in-sample
            self.base.fit(X, y)
            resid = np.asarray(y) - self.base.predict(X)
        else:
            X_tr, X_cal = X.iloc[: n - n_cal], X.iloc[n - n_cal:]
            y_tr, y_cal = y.iloc[: n - n_cal], y.iloc[n - n_cal:]
            self.base.fit(X_tr, y_tr)
            resid = np.asarray(y_cal) - self.base.predict(X_cal)
        self._signed_residuals = resid
        self._abs_residuals = np.abs(resid)
        self.fitted = True
        return self

    def _halfwidth(self, coverage: float) -> float:
        if self._abs_residuals.size == 0:
            return 0.0
        n = self._abs_residuals.size
        level = min(1.0, np.ceil((n + 1) * coverage) / n)
        return float(np.quantile(self._abs_residuals, level))

    def predict(self, X):
        return self.base.predict(X)

    def predict_interval(self, X, coverage: float = 0.8) -> dict:
        point = np.asarray(self.base.predict(X), dtype=float)
        q = self._halfwidth(coverage)
        return {"point": point, "lower": point - q, "upper": point + q, "halfwidth": q}

    def prob_up(self, X) -> np.ndarray:
        """Empirical P(forward return > 0) from the conformal residual distribution."""
        point = np.asarray(self.base.predict(X), dtype=float)
        if self._signed_residuals.size == 0:
            return (point > 0).astype(float)
        # Each point + the empirical residual sample => distribution of outcomes.
        draws = point[:, None] + self._signed_residuals[None, :]
        return (draws > 0).mean(axis=1)


def predict_with_intervals(
    df: pd.DataFrame,
    model_name: str = "xgboost",
    horizon: int = 5,
    coverage: float = 0.8,
) -> dict:
    """Train ``model_name`` on a single name's history and forecast the next move.

    Returns calibrated price/return intervals, P(up), a confidence score and the
    metadata the explainability layer and UI need (model, horizon, freshness).
    """
    from src.ml.features import latest_features, make_dataset

    X, y = make_dataset(df, horizon=horizon)
    x_live = latest_features(df)
    last_close = float(df["Close"].iloc[-1])
    last_date = str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])

    if len(X) < 80 or x_live is None:
        return {"error": "insufficient_data", "model": model_name, "last_close": last_close}

    model = ConformalRegressor(get_model(model_name))
    model.fit(X, y)
    interval = model.predict_interval(x_live, coverage=coverage)
    prob_up = float(model.prob_up(x_live)[0])

    point_ret = float(interval["point"][0])
    lo_ret, hi_ret = float(interval["lower"][0]), float(interval["upper"][0])

    def to_price(ret_pct: float) -> float:
        return round(last_close * (1 + ret_pct / 100), 2)

    # Confidence: directional conviction tempered by interval width vs typical move.
    typical_move = max(float(np.std(y.tail(252))), 1e-6)
    width_penalty = min(1.0, interval["halfwidth"] / (2 * typical_move))
    confidence = round(max(0.0, (abs(prob_up - 0.5) * 2) * (1 - 0.5 * width_penalty)), 3)

    return {
        "model": model_name,
        "horizon_days": horizon,
        "coverage": coverage,
        "last_close": last_close,
        "last_date": last_date,
        "predicted_return_pct": round(point_ret, 2),
        "predicted_price": to_price(point_ret),
        "price_lower": to_price(lo_ret),
        "price_upper": to_price(hi_ret),
        "return_lower_pct": round(lo_ret, 2),
        "return_upper_pct": round(hi_ret, 2),
        "prob_up": round(prob_up, 3),
        "direction": "UP" if point_ret > 0 else "DOWN" if point_ret < 0 else "FLAT",
        "confidence": confidence,
        "n_train": int(len(X)),
        "disclaimer": (
            "Probabilistic estimate, not a guarantee. Intervals are conformal "
            "(historically calibrated) and can break during news/earnings/macro shocks."
        ),
    }


if __name__ == "__main__":
    from src.ingestion.prices import fetch_prices

    data = fetch_prices(["RELIANCE.NS"], period="3y")["RELIANCE.NS"]
    import json

    print(json.dumps(predict_with_intervals(data, "xgboost", horizon=5), indent=2))
