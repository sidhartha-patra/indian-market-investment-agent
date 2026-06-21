"""Tests for the website Top Gainers/Losers/Most-Active movers section (offline)."""
import pandas as pd

from scripts.build_site import _demo_movers, _demo_records, build_site


def test_build_section_records_offline(monkeypatch):
    """build_section_records shapes build_site-ready records without any network."""
    from scripts import movers_analysis as ma
    from src.ingestion import tradingview_scanner as tv

    def fake_fetch_collection(name, top=50, **kw):
        return pd.DataFrame([
            {"symbol": "AAA", "name": "Alpha Ltd", "tv_exchange": "NSE", "yf_ticker": "AAA.NS",
             "close": 100.0, "change": 5.53, "sector": "IT", "Volatility.D": 2.0,
             "price_52_week_high": 150.0, "price_52_week_low": 80.0, "collection": name},
            {"symbol": "BBB", "name": "Beta Ltd", "tv_exchange": "BSE", "yf_ticker": "BBB.BO",
             "close": 50.0, "change": -3.2, "sector": "Energy", "Volatility.D": 3.0,
             "price_52_week_high": 70.0, "price_52_week_low": 40.0, "collection": name},
        ])

    monkeypatch.setattr(tv, "fetch_collection", fake_fetch_collection)

    payload = ma.build_section_records(sections=("gainers",), top_each=5, source="none", max_per=5)
    assert set(payload) == {"generated_at", "sections"}
    recs = payload["sections"]["gainers"]
    assert [r["symbol"] for r in recs] == ["AAA", "BBB"]

    first = recs[0]
    assert first["change_today_pct"] == 5.5  # rounded to 1dp
    assert first["price"] == 100.0
    assert first["exchange"] == "NSE"
    assert first["fundamentals_source"] == "TradingView technicals only"
    assert first["recommendation"]["verdict"]  # a framed verdict was produced
    proj = first["recommendation"]["horizons"]["long_term"]["projection"]
    assert proj["low_pct"] <= proj["base_pct"] <= proj["high_pct"]


def test_enriched_metrics_merges_yahoo_under_tradingview(monkeypatch):
    """Yahoo fundamentals fill gaps; TradingView technicals stay authoritative."""
    from scripts import movers_analysis as ma

    monkeypatch.setattr(ma, "_yf_enrich", lambda t: {"pe": 18.0, "roe": 22.0, "price": 999.0})
    tv_row = {"symbol": "AAA", "yf_ticker": "AAA.NS", "name": "Alpha", "sector": "IT",
              "close": 100.0, "change": 4.0, "price_52_week_high": 150.0, "price_52_week_low": 80.0}
    metrics, label = ma._enriched_metrics(tv_row, source="yfinance")
    assert metrics["pe"] == 18.0 and metrics["roe"] == 22.0  # from Yahoo
    assert metrics["price"] == 100.0  # TradingView close wins over Yahoo price
    assert "Yahoo Finance" in label


def test_movers_page_rendered(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path, movers=_demo_movers())
    assert res["has_movers"] is True

    movers = (tmp_path / "movers.html").read_text(encoding="utf-8")
    for section_title in ("Top Gainers", "Top Losers", "Most Active"):
        assert section_title in movers
    # Columns proving technicals + fundamentals + signal + projections are present.
    for col in ("Chg%", "P/E", "ROE%", "Div%", "Model signal", "Long (3y)"):
        assert col in movers

    # Every mover symbol links to a real detail page.
    assert "stock/ADANIPORTS.html" in movers
    assert (tmp_path / "stock" / "ADANIPORTS.html").exists()
    assert (tmp_path / "stock" / "YESBANK.html").exists()

    # The index banner links to the movers page.
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "movers.html" in index and "Most-Active" in index


def test_movers_page_keeps_sebi_framing(tmp_path):
    build_site(_demo_records(), out_dir=tmp_path, movers=_demo_movers())
    html = (tmp_path / "movers.html").read_text(encoding="utf-8").lower()
    assert "not investment advice" in html and "sebi" in html
    assert "scenarios, not predictions" in html
    # No specific entry/target trading calls.
    assert "buy at" not in html and "target price" not in html


def test_build_site_without_movers_is_unchanged(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path)
    assert res["has_movers"] is False
    assert not (tmp_path / "movers.html").exists()
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "movers.html" not in index
