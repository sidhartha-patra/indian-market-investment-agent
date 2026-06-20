"""Walk-forward evaluation of the prediction models — honest, leakage-controlled.

Uses an expanding-window walk-forward with a **purge/embargo** equal to the forecast
horizon, so overlapping forward-return labels never straddle the train/test boundary.
Every model is scored against the naive random walk; if a model cannot beat naive on
RMSE, we say so plainly (``skill_vs_naive`` <= 0).

Reported metrics:
- MAE / RMSE      — forward-return error (percentage points).
- price_mape_pct  — mean abs %% error on the implied price (the intuitive number).
- directional_accuracy — fraction of correct up/down calls (0.5 = coin flip).
- interval_coverage    — fraction of actuals inside the conformal band (target = coverage).
- skill_vs_naive  — 1 - RMSE_model / RMSE_naive  (>0 means it beats the baseline).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.ml.features import make_dataset
from src.ml.models import ConformalRegressor, available_models, get_model

logger = logging.getLogger(__name__)


def _folds(n: int, min_train: int, n_splits: int, embargo: int) -> list[tuple[int, int, int, int]]:
    if n <= min_train + n_splits:
        return []
    test_size = max(1, (n - min_train) // n_splits)
    folds = []
    for k in range(n_splits):
        test_start = min_train + k * test_size
        test_end = n if k == n_splits - 1 else test_start + test_size
        train_end = test_start - embargo
        if train_end < 60 or test_start >= n:
            continue
        folds.append((0, train_end, test_start, min(test_end, n)))
    return folds


def _metrics(actual: np.ndarray, pred: np.ndarray, lower: np.ndarray, upper: np.ndarray,
             price_ape: np.ndarray, naive_rmse: float | None) -> dict:
    err = pred - actual
    rmse = float(np.sqrt(np.mean(err ** 2)))
    # Directional accuracy is undefined for a constant (e.g. naive=0) predictor that
    # never makes a directional call; only score it on non-zero forecasts.
    nonzero = np.sign(pred) != 0
    dir_acc = (
        round(float(np.mean(np.sign(pred[nonzero]) == np.sign(actual[nonzero]))), 3)
        if nonzero.any() else None
    )
    out = {
        "mae_pct": round(float(np.mean(np.abs(err))), 3),
        "rmse_pct": round(rmse, 3),
        "price_mape_pct": round(float(np.mean(price_ape) * 100), 3),
        "directional_accuracy": dir_acc,
        "interval_coverage": round(float(np.mean((actual >= lower) & (actual <= upper))), 3),
        "n_test": int(len(actual)),
    }
    if naive_rmse and naive_rmse > 0:
        out["skill_vs_naive"] = round(1 - rmse / naive_rmse, 3)
    return out


def walk_forward_eval(
    df: pd.DataFrame,
    model_names: list[str] | None = None,
    horizon: int = 5,
    n_splits: int = 5,
    coverage: float = 0.8,
    min_train: int = 252,
) -> dict:
    """Walk-forward backtest of prediction models on one instrument's history."""
    model_names = model_names or available_models()
    X, y = make_dataset(df, horizon=horizon)
    if len(X) < min_train + n_splits * 5:
        return {"error": "insufficient_data", "n_rows": int(len(X))}

    close = df["Close"].astype(float)
    fwd_price = close.shift(-horizon)
    folds = _folds(len(X), min_train, n_splits, embargo=horizon)
    if not folds:
        return {"error": "no_folds"}

    # Naive RMSE first (denominator for skill of every other model).
    collected: dict[str, dict] = {}
    for name in model_names:
        preds, lowers, uppers, actuals, apes = [], [], [], [], []
        for a, train_end, ts, te in folds:
            X_tr, y_tr = X.iloc[a:train_end], y.iloc[a:train_end]
            X_te, y_te = X.iloc[ts:te], y.iloc[ts:te]
            if len(X_tr) < 60 or X_te.empty:
                continue
            model = ConformalRegressor(get_model(name))
            model.fit(X_tr, y_tr)
            iv = model.predict_interval(X_te, coverage=coverage)
            preds.append(iv["point"]); lowers.append(iv["lower"]); uppers.append(iv["upper"])
            actuals.append(y_te.values)

            dates = X_te.index
            c_t = close.reindex(dates).values
            p_actual = fwd_price.reindex(dates).values
            p_pred = c_t * (1 + iv["point"] / 100)
            with np.errstate(divide="ignore", invalid="ignore"):
                ape = np.abs(p_pred - p_actual) / np.where(p_actual == 0, np.nan, p_actual)
            apes.append(ape)
        if not preds:
            continue
        collected[name] = {
            "actual": np.concatenate(actuals),
            "pred": np.concatenate(preds),
            "lower": np.concatenate(lowers),
            "upper": np.concatenate(uppers),
            "ape": np.concatenate(apes),
        }

    naive_rmse = None
    if "naive_random_walk" in collected:
        c = collected["naive_random_walk"]
        naive_rmse = float(np.sqrt(np.mean((c["pred"] - c["actual"]) ** 2)))

    results = {}
    for name, c in collected.items():
        results[name] = _metrics(c["actual"], c["pred"], c["lower"], c["upper"], c["ape"], naive_rmse)

    ranked = sorted(
        results.items(),
        key=lambda kv: kv[1].get("skill_vs_naive", -1),
        reverse=True,
    )
    beats_naive = [n for n, m in results.items()
                   if m.get("skill_vs_naive", -1) > 0 and n != "naive_random_walk"]
    return {
        "horizon_days": horizon,
        "coverage_target": coverage,
        "n_folds": len(folds),
        "models": results,
        "best_model": ranked[0][0] if ranked else None,
        "models_beating_naive": beats_naive,
        "verdict": (
            f"{len(beats_naive)} of {len(model_names) - 1} models beat the naive baseline "
            "out-of-sample." if beats_naive
            else "No model beat the naive random walk out-of-sample -- treat point forecasts "
                 "as low-information and lean on the intervals/risk framing."
        ),
    }


def compare_models_table(eval_result: dict) -> pd.DataFrame:
    """Flatten a walk-forward result into a sortable DataFrame for the UI/CLI."""
    models = eval_result.get("models", {})
    if not models:
        return pd.DataFrame()
    return (
        pd.DataFrame(models).T.reset_index().rename(columns={"index": "model"})
        .sort_values("skill_vs_naive", ascending=False, na_position="last")
        .reset_index(drop=True)
    )


if __name__ == "__main__":
    import json

    from src.ingestion.prices import fetch_prices

    data = fetch_prices(["RELIANCE.NS"], period="5y")["RELIANCE.NS"]
    res = walk_forward_eval(data, horizon=5)
    print(json.dumps(res, indent=2, default=str))
