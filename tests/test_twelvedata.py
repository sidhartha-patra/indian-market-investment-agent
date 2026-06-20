"""Offline tests for the Twelve Data adapter parsers (no network)."""
import pytest

from src.ingestion import twelvedata_provider as td


def test_parse_quote():
    q = td.parse_quote({"name": "Reliance", "exchange": "NSE",
                        "close": "1310.5", "percent_change": "0.83"})
    assert q["price"] == 1310.5 and q["change"] == 0.83 and q["exchange"] == "NSE"


def test_parse_statistics_maps_and_scales():
    payload = {"symbol": "RELIANCE", "statistics": {
        "valuations_metrics": {"market_capitalization": 1.7e13, "trailing_pe": 22.0,
                               "price_to_book_mrq": 3.1, "enterprise_to_ebitda": 14.0},
        "financials": {"return_on_equity_ttm": 0.09, "profit_margin": 0.08,
                       "operating_margin": 0.12,
                       "balance_sheet": {"total_debt_to_equity_mrq": 0.44,
                                         "current_ratio_mrq": 1.1,
                                         "book_value_per_share_mrq": 418.0}},
        "dividends_and_splits": {"forward_annual_dividend_yield": 0.005}}}
    s = td.parse_statistics(payload)
    assert s["pe"] == 22.0 and s["pb"] == 3.1 and s["ev_ebitda"] == 14.0
    assert s["debt_to_equity"] == 0.44 and s["current_ratio"] == 1.1
    assert s["roe"] == 9.0          # 0.09 fraction -> scaled to percent
    assert s["net_margin"] == 8.0
    assert round(s["earnings_yield"], 2) == 4.55


def test_parse_profile():
    p = td.parse_profile({"symbol": "RELIANCE", "sector": "Energy", "industry": "Oil & Gas"})
    assert p["sector"] == "Energy" and p["industry"] == "Oil & Gas"


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        td.build_dataset(symbols=["RELIANCE"])


def test_nifty50_default_universe():
    assert "RELIANCE" in td.NIFTY_50_SYMBOLS
    assert len(td.NIFTY_50_SYMBOLS) >= 50
