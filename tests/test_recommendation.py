"""Tests for the fundamental buy/sell recommendation + multi-horizon outlook engine."""
from src.strategies.recommendation import (
    detailed_markdown,
    horizon_outlook,
    recommend,
)

# A high-quality, reasonably-valued, clean-governance name.
_STRONG = {
    "symbol": "GOODCO", "name": "Good Co", "sector": "IT",
    "pe": 18, "pb": 4, "roe": 32, "roce": 38, "opm": 25, "net_margin": 20,
    "revenue_growth_5y": 18, "profit_growth_5y": 20, "debt_to_equity": 0.1,
    "current_ratio": 2.5, "promoter_holding": 60, "pledged_pct": 0,
    "dividend_yield": 1.5, "price": 1000, "sma50": 980, "sma200": 900,
    "high_52w": 1100, "low_52w": 700, "analyst_target": 1200,
}
# A weak, over-levered, pledged, declining name.
_WEAK = {
    "symbol": "BADCO", "name": "Bad Co", "sector": "Industrials",
    "pe": 60, "pb": 6, "roe": 5, "roce": 7, "net_margin": 2,
    "revenue_growth_5y": 1, "profit_growth_5y": -8, "debt_to_equity": 2.6,
    "current_ratio": 0.8, "promoter_holding": 30, "pledged_pct": 55,
    "price": 100, "sma50": 110, "sma200": 130, "high_52w": 200, "low_52w": 95,
}


def test_strong_company_gets_buy():
    r = recommend(_STRONG, sector_score=85)
    assert r["verdict"] in ("STRONG_BUY", "BUY")
    assert r["conviction"] >= 62
    assert "educational model signal" in r["verdict_label"].lower()
    assert r["red_flags"] == []


def test_weak_company_gets_sell_or_avoid():
    r = recommend(_WEAK, sector_score=20)
    assert r["verdict"] in ("SELL", "AVOID")
    # Pledge + leverage must surface as red flags.
    assert any("pledge" in f.lower() for f in r["red_flags"])
    assert any("leverage" in f.lower() for f in r["red_flags"])


def test_recommend_has_full_detail_and_frameworks():
    r = recommend(_STRONG, sector_score=85)
    assert {"assessments", "frameworks", "positives", "negatives",
            "what_would_change", "horizons", "disclaimer"} <= set(r)
    assert len(r["assessments"]) == 5
    assert "quality_score" in r["frameworks"] and "altman" in r["frameworks"]


def test_horizons_three_buckets():
    h = horizon_outlook(_STRONG, "BUY", 70)
    assert set(h) >= {"short_term", "mid_term", "long_term", "note"}
    # Uptrend (price > 50DMA > 200DMA) -> constructive short/mid stances.
    assert h["mid_term"]["stance"] == "BUY"
    # Long-term carries an illustrative annual-return scenario (not a promise).
    assert h["long_term"]["illustrative_annual_return_pct"] is not None
    assert "scenario" in h["long_term"]["scenario"].lower() or "%" in h["long_term"]["scenario"]


def test_downtrend_mid_term_bearish():
    h = horizon_outlook(_WEAK, "AVOID", 18)
    # price < 200DMA and 50DMA < 200DMA (death cross) -> AVOID mid-term.
    assert h["mid_term"]["stance"] == "AVOID"


def test_detailed_markdown_renders():
    md = detailed_markdown(recommend(_STRONG, sector_score=85), "GOODCO")
    assert "GOODCO" in md
    assert "Short / Mid / Long-term outlook" in md
    assert "Frameworks" in md
