"""Tests for the Gen-AI layer: provider detection, JSON parsing, data reconciliation,
and the grounded analyst's deterministic fallback (all offline / no network)."""
import pytest

from src.ai import analyst, llm
from src.ingestion import data_reconcile as dr

_LLM_KEYS = ["LLM_PROVIDER", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
             "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MODELS_TOKEN",
             "GITHUB_TOKEN", "GH_TOKEN"]


def _clear_llm_env(monkeypatch):
    for k in _LLM_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_provider_detection_priority(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm, "detect_provider", llm.detect_provider)  # ensure real fn
    monkeypatch.setenv("MODELS_TOKEN", "x")
    assert llm.detect_provider() == "github"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert llm.detect_provider() == "anthropic"  # paid key wins over free
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm.detect_provider() == "openai"      # explicit override wins


def test_provider_info_none(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm, "detect_provider", lambda: "none")
    info = llm.provider_info()
    assert info["available"] is False and info["provider"] == "none"


def test_chat_json_strips_fences(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '```json\n{"verdict":"BUY","lean":0.6}\n```')
    out = llm.chat_json("sys", "user")
    assert out == {"verdict": "BUY", "lean": 0.6}


def test_chat_json_recovers_embedded_object(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: 'Sure! {"a": 1, "b": 2} hope that helps')
    assert llm.chat_json("s", "u") == {"a": 1, "b": 2}


def test_reconcile_consensus_and_confidence():
    out = dr.reconcile("RELIANCE", {
        "tradingview": {"price": 1310, "pe": 22.8, "roe": 8.9, "high_52w": 1600, "low_52w": 1100},
        "yfinance": {"price": 1316, "pe": 24.1, "roe": 8.7, "dividend_yield": 0.46},
        "screener": {"pe": 22.5, "roce": 10.3, "debt_to_equity": 0.44, "profit_growth_5y": 12.0},
    })
    q = out["data_quality"]
    assert q["n_sources"] == 3 and q["verdict"] == "high"
    assert not q["conflicts"]            # all within tolerance
    assert out["metrics"]["pe"] in (22.5, 22.8, 24.1)


def test_reconcile_flags_conflict_and_suspect():
    out = dr.reconcile("XYZ", {
        "tradingview": {"pe": 20.0, "roe": 15.0},
        "yfinance": {"pe": 32.0, "roe": 14.0},   # pe disagrees 50%+ -> conflict
        "screener": {"pe": -5.0},                # implausible -> suspect (out of [0,300])
    })
    q = out["data_quality"]
    assert any("pe" in c for c in q["conflicts"])
    assert any("pe" in s for s in q["suspect"])
    assert q["confidence"] < 75


def test_reconcile_cross_field_52w_inconsistency():
    out = dr.reconcile("ABC", {"yfinance": {"price": 100, "high_52w": 80, "low_52w": 90}})
    # high < low AND price above high -> at least one cross-field suspect note
    assert any("52-week" in s or "52w" in s for s in out["data_quality"]["suspect"])


def test_analyst_fallback_is_two_sided(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: False)  # force deterministic path
    ev = {
        "symbol": "RELIANCE",
        "metrics": {"name": "Reliance", "sector": "Energy", "price": 1310, "pe": 22.8},
        "recommendation": {"positives": ["profitability: strong ROCE"],
                           "negatives": ["valuation rich"], "red_flags": ["pledge 30%"],
                           "verdict": "HOLD", "conviction": 59, "summary": "demo",
                           "what_would_change": ["margin pickup"], "confidence": "moderate",
                           "horizons": {"long_term": {"stance": "HOLD"}}},
        "model_verdict": {"verdict": "HOLD", "conviction": 59},
        "analyst_consensus": {"buy_pct": 64, "sell_pct": 12, "target_upside_pct": 9.5},
        "ml_forecast": {"direction": "UP", "prob_up": 0.56, "predicted_return_pct": 1.4},
        "news": {"net_sentiment": -0.3, "n": 5},
    }
    r = analyst.analyze("RELIANCE", ev)
    assert r["source"] == "deterministic"
    assert r["verdict"] == "HOLD"
    assert r["buy_case"] and r["sell_case"]            # always two-sided
    assert any("Broker consensus" in b for b in r["buy_case"])
    assert any("News sentiment" in s for s in r["sell_case"])  # negative news -> sell side
    assert -1.0 <= r["lean"] <= 1.0


def test_analyst_validate_caps_conviction_on_low_quality(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "thesis": "t", "buy_case": ["b1"], "sell_case": ["s1"], "verdict": "STRONG_BUY",
        "lean": 0.9, "conviction": 95, "confidence": "high", "moat": "strong",
        "horizon_view": {"short": "x", "mid": "y", "long": "z"}})
    ev = {"symbol": "Z", "data_quality": {"confidence": 30}, "metrics": {}}
    r = analyst.analyze("Z", ev)
    assert r["source"] == "ai"
    assert r["conviction"] <= 55 and r["confidence"] == "low"  # low data quality caps it
