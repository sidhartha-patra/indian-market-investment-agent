"""Tests for the risk engine: regime detection and position sizing."""
from src.risk import position_sizing as ps
from src.risk.regime import detect_regime, regime_exposure_multiplier
from tests.conftest import make_ohlcv


def test_regime_risk_on_in_uptrend(universe, benchmark):
    up_bench = make_ohlcv(n=500, seed=42, drift=0.0015, vol=0.008)
    reg = detect_regime(up_bench, universe)
    assert reg["regime"] in {"RISK_ON", "NEUTRAL"}
    assert 0 < reg["equity_exposure"] <= 1.0
    assert 0 <= reg["risk_score"] <= 100


def test_regime_risk_off_in_downtrend():
    down_bench = make_ohlcv(n=500, seed=43, drift=-0.0018, vol=0.016)
    down_universe = {f"D{k}.NS": make_ohlcv(n=500, seed=50 + k, drift=-0.0015, vol=0.015)
                     for k in range(8)}
    reg = detect_regime(down_bench, down_universe)
    assert reg["regime"] in {"RISK_OFF", "NEUTRAL"}
    assert reg["equity_exposure"] < 1.0


def test_exposure_multiplier_known_values():
    assert regime_exposure_multiplier("RISK_ON") == 1.0
    assert regime_exposure_multiplier("RISK_OFF") < regime_exposure_multiplier("NEUTRAL")


def test_atr_stop_long_below_entry(uptrend):
    s = ps.atr_stop_loss(uptrend, side="long")
    assert s["stop"] < s["entry"] < s["target"]
    assert s["stop_pct"] < 0


def test_atr_stop_short_above_entry(uptrend):
    s = ps.atr_stop_loss(uptrend, side="short")
    assert s["stop"] > s["entry"] > s["target"]


def test_position_size_respects_risk_budget():
    out = ps.position_size(capital=1_000_000, entry=100, stop=95, risk_per_trade_pct=1.0)
    # Risk should not exceed ~1% of capital (= 10,000).
    assert out["risk_amount"] <= 10_000 + 1e-6
    assert out["shares"] >= 0


def test_position_size_caps_notional():
    # Tiny stop distance would imply a huge position; cap must bind.
    out = ps.position_size(capital=1_000_000, entry=100, stop=99.9,
                           risk_per_trade_pct=5.0, max_position_pct=20.0)
    assert out["position_pct"] <= 20.0 + 1e-6


def test_volatility_target_weight_prefers_low_vol():
    low = make_ohlcv(n=300, seed=1, vol=0.006)["Close"].pct_change()
    high = make_ohlcv(n=300, seed=2, vol=0.03)["Close"].pct_change()
    w_low = ps.volatility_target_weight(low, target_vol=0.15)
    w_high = ps.volatility_target_weight(high, target_vol=0.15)
    assert 0 <= w_high <= w_low <= 1.0


def test_kelly_fraction_bounds():
    assert ps.kelly_fraction(0.6, 1.5) > 0
    assert ps.kelly_fraction(0.3, 0.5) == 0.0  # negative edge -> no bet
    assert 0 <= ps.kelly_fraction(0.9, 5) <= 1.0


def test_size_from_chart(uptrend):
    out = ps.size_from_chart(uptrend, capital=500_000, risk_per_trade_pct=1.0)
    assert "shares" in out and "stop" in out
