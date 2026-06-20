"""Tests for the ML layer: features, conformal models, evaluation, explainability."""
import numpy as np
import pandas as pd

from src.ml.evaluate import compare_models_table, walk_forward_eval
from src.ml.explain import explain_suggestion
from src.ml.features import FEATURE_COLUMNS, latest_features, make_dataset
from src.ml.models import predict_with_intervals
from tests.conftest import make_ohlcv


def _ar1_series(n=900, seed=5):
    """Mean-reverting series so models have *some* structure to (maybe) learn."""
    rng = np.random.RandomState(seed)
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = -0.05 * r[t - 1] + rng.randn() * 0.012 + 0.0004
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.004, "Low": close * 0.996,
         "Close": close, "Volume": rng.randint(1e5, 1e6, n)}, index=idx
    )


def test_make_dataset_is_clean():
    df = make_ohlcv(n=400, seed=1)
    X, y = make_dataset(df, horizon=5)
    assert list(X.columns) == FEATURE_COLUMNS
    assert len(X) == len(y) > 0
    assert not X.isnull().any().any()
    assert np.isfinite(y).all()


def test_latest_features_single_row():
    df = make_ohlcv(n=400, seed=2)
    lf = latest_features(df)
    assert lf is not None and lf.shape[0] == 1


def test_predict_with_intervals_contract():
    df = _ar1_series()
    out = predict_with_intervals(df, model_name="random_forest", horizon=5)
    assert out["price_lower"] <= out["predicted_price"] <= out["price_upper"]
    assert 0.0 <= out["prob_up"] <= 1.0
    assert 0.0 <= out["confidence"] <= 1.0
    assert out["direction"] in {"UP", "DOWN", "FLAT"}
    assert "disclaimer" in out


def test_predict_insufficient_data():
    df = make_ohlcv(n=60, seed=3)
    out = predict_with_intervals(df, horizon=5)
    assert out.get("error") == "insufficient_data"


def test_walk_forward_includes_naive_and_skill():
    df = _ar1_series()
    res = walk_forward_eval(df, horizon=5, n_splits=4)
    assert "naive_random_walk" in res["models"]
    assert res["best_model"] is not None
    for name, m in res["models"].items():
        assert 0.0 <= m["interval_coverage"] <= 1.0
        if name != "naive_random_walk":
            assert "skill_vs_naive" in m
    table = compare_models_table(res)
    assert not table.empty


def test_explain_payload_is_complete():
    record = {
        "ticker": "S0.NS", "factor_score": 82, "momentum_score": 75,
        "relative_strength_score": 71, "trend": 12, "roce": 22,
        "debt_to_equity": 0.3, "pe": 20, "ann_vol_pct": 30,
        "max_drawdown_pct": -25, "last_date": "2024-05-10",
        "prediction": {"prob_up": 0.62, "horizon_days": 5, "confidence": 0.4},
    }
    regime = {"regime": "RISK_ON"}
    exp = explain_suggestion(record, regime=regime)
    assert exp["positives"] and exp["negatives"]
    assert exp["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    assert 0.0 <= exp["confidence"] <= 1.0
    assert exp["model_limitations"]
    assert "is_stale" in exp["data_freshness"]


def test_explain_flags_risk_off_guardrail():
    record = {"ticker": "X.NS", "score": 60, "adv_cr": 2.0, "ann_vol_pct": 50}
    exp = explain_suggestion(record, regime={"regime": "RISK_OFF"})
    assert any("RISK_OFF" in g for g in exp["guardrails"])
    assert exp["risk_level"] == "HIGH"
