"""Tests for the composite blend + deep-research bundle (offline, no network)."""
from src.strategies import composite as comp
from src.strategies import deep_research as dr


def test_composite_blends_bullish_signals():
    ev = {
        "recommendation": {"verdict": "BUY", "conviction": 70, "red_flags": []},
        "analyst_consensus": {"buy_pct": 75, "sell_pct": 8, "target_upside_pct": 20},
        "ml_forecast": {"direction": "UP", "prob_up": 0.62, "confidence": 0.4},
        "news": {"net_sentiment": 0.3},
        "data_quality": {"confidence": 80, "verdict": "high"},
    }
    out = comp.composite(ev, ai={"lean": 0.5, "buy_case": ["a"], "sell_case": ["b"]})
    assert out["group"] == "BUY"
    assert out["score"] > 60 and out["lean"] > 0.2
    assert set(out["signals_used"]) == {"fundamental", "ai", "analyst", "ml", "news"}


def test_composite_red_flag_caps_upside():
    ev = {
        "recommendation": {"verdict": "BUY", "conviction": 80,
                           "red_flags": ["Beneish flags manipulation"]},
        "analyst_consensus": {"buy_pct": 90, "sell_pct": 2, "target_upside_pct": 40},
    }
    out = comp.composite(ev, ai={"lean": 0.9})
    assert out["has_red_flag"] is True
    assert out["verdict"] != "STRONG_BUY"           # red flag forbids strong-buy
    assert out["group"] in ("HOLD", "SELL")          # capped down despite bullish inputs


def test_composite_sell_side():
    ev = {
        "recommendation": {"verdict": "AVOID", "conviction": 20, "red_flags": []},
        "analyst_consensus": {"buy_pct": 10, "sell_pct": 70, "target_upside_pct": -25},
        "ml_forecast": {"direction": "DOWN", "prob_up": 0.35, "confidence": 0.4},
        "news": {"net_sentiment": -0.4},
    }
    out = comp.composite(ev, ai={"lean": -0.6})
    assert out["group"] == "SELL"
    assert out["score"] < 40


def test_composite_availability_renormalises():
    # Only the fundamental signal is present -> it drives the result alone.
    out = comp.composite({"recommendation": {"verdict": "BUY", "conviction": 65}})
    assert out["signals_used"] == ["fundamental"]
    assert out["group"] == "BUY"


def test_composite_low_data_quality_shrinks():
    strong = {"recommendation": {"verdict": "BUY", "conviction": 70},
              "analyst_consensus": {"buy_pct": 80, "sell_pct": 5},
              "data_quality": {"confidence": 30}}
    weak = comp.composite(strong, ai={"lean": 0.5})
    strong2 = dict(strong, data_quality={"confidence": 85})
    full = comp.composite(strong2, ai={"lean": 0.5})
    assert weak["lean"] < full["lean"]   # shaky data pulls the call toward neutral


def test_deep_dive_offline_pure_fundamental():
    res = dr.deep_dive(
        "TCS", {"name": "TCS", "sector": "IT", "price": 3450, "pe": 27, "roe": 47,
                "roce": 58, "debt_to_equity": 0.05, "profit_growth_5y": 12, "dividend_yield": 1.6,
                "high_52w": 4200, "low_52w": 3000, "sma50": 3400, "sma200": 3550},
        use_ai=False, use_ml=False, use_news=False, use_analyst=False)
    assert res["symbol"] == "TCS"
    assert res["recommendation"]["verdict"]
    assert res["composite"]["signals_used"] == ["fundamental"]
    assert res["composite"]["group"] in ("BUY", "HOLD", "SELL")


def test_deep_dive_reconciles_sources_offline():
    res = dr.deep_dive(
        "ZZ", {"name": "ZZ", "sector": "IT", "price": 100},
        sources={"tradingview": {"pe": 20, "roe": 15, "price": 100},
                 "yfinance": {"pe": 33, "roe": 14}},  # pe conflict
        use_ai=False, use_ml=False, use_news=False, use_analyst=False)
    assert res["data_quality"] is not None
    assert any("pe" in c for c in res["data_quality"]["conflicts"])


def test_news_signal_lexicon(monkeypatch):
    import sys
    import types

    class _Tk:
        def __init__(self, *a, **k):
            self.news = [{"content": {"title": "Company posts record profit, shares surge to high"}},
                         {"content": {"title": "Firm wins large export order, growth strong"}}]

    fake = types.ModuleType("yfinance")
    fake.Ticker = _Tk
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    sig = dr.news_signal("AAA", use_llm=False)
    assert sig["n"] == 2 and sig["net_sentiment"] > 0 and sig["source"] == "lexicon"
