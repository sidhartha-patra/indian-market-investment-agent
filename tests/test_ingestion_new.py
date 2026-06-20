"""Tests for universe fetch + TradingView scanner parsing (offline)."""
import pandas as pd

from src.ingestion import tradingview_scanner as tv
from src.ingestion import universe_fetch as uf


def test_scanner_build_payload():
    body = tv.build_payload(["name", "close"], limit=500, market_cap_min=1e9)
    assert body["markets"] == ["india"]
    assert body["range"] == [0, 500]
    assert body["columns"] == ["name", "close"]
    assert any(f["left"] == "is_primary" for f in body["filter"])
    assert any(f["left"] == "market_cap_basic" for f in body["filter"])


def test_scanner_parse_response():
    cols = ["name", "close", "market_cap_basic", "return_on_equity", "sector", "exchange"]
    resp = {"totalCount": 2, "data": [
        {"s": "NSE:RELIANCE", "d": ["Reliance", 1310.0, 2.3e11, 0.077, "Energy", "NSE"]},
        {"s": "BSE:HCLTECH", "d": ["HCL Tech", 1500.0, 4.0e10, 0.25, "Technology", "BSE"]},
    ]}
    df = tv.parse_scanner_response(resp, cols)
    assert len(df) == 2
    assert list(df["symbol"]) == ["RELIANCE", "HCLTECH"]
    assert df.loc[0, "yf_ticker"] == "RELIANCE.NS"
    assert df.loc[1, "yf_ticker"] == "HCLTECH.BO"
    assert df.loc[0, "return_on_equity"] == 0.077


def test_scanner_to_fundamentals_dict():
    cols = ["name", "close", "price_earnings_ttm", "return_on_equity", "free_cash_flow",
            "market_cap_basic", "sector"]
    resp = {"data": [{"s": "NSE:TCS", "d": ["TCS", 3450, 27.0, 0.45, 5000, 1e11, "Technology"]}]}
    df = tv.parse_scanner_response(resp, cols)
    funds = tv.to_fundamentals_dict(df)
    assert "TCS" in funds
    assert funds["TCS"]["pe"] == 27.0 and funds["TCS"]["roe"] == 0.45
    assert funds["TCS"]["sector"] == "Technology"


def test_universe_static_fallback_on_network_failure(monkeypatch):
    monkeypatch.setattr(uf, "fetch_nse_equity_list", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    monkeypatch.setattr(uf, "fetch_kite_instruments", lambda exchange="NSE": (_ for _ in ()).throw(RuntimeError("no net")))
    df = uf.get_all_indian_stocks(use_cache=False)
    assert not df.empty
    assert {"symbol", "name", "exchange", "yf_ticker"}.issubset(df.columns)
    assert df["yf_ticker"].str.endswith(".NS").all()


def test_universe_dedup_prefers_nse(monkeypatch):
    nse = pd.DataFrame({"symbol": ["RELIANCE"], "name": ["Reliance"], "series": ["EQ"],
                        "isin": ["INE002A01018"], "exchange": ["NSE"], "yf_ticker": ["RELIANCE.NS"]})
    bse = pd.DataFrame({"symbol": ["RELIANCE"], "name": ["Reliance"], "exchange": ["BSE"],
                        "exchange_token": [500325], "isin": ["INE002A01018"], "yf_ticker": ["RELIANCE.BO"]})
    monkeypatch.setattr(uf, "fetch_nse_equity_list", lambda: nse)
    monkeypatch.setattr(uf, "fetch_kite_instruments", lambda exchange="NSE": bse)
    df = uf.get_all_indian_stocks(use_cache=False, refresh=True)
    # Same ISIN dual-listed -> keep one row, prefer NSE.
    assert len(df) == 1 and df.iloc[0]["exchange"] == "NSE"
