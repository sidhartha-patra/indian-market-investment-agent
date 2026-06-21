"""Tests for the recommendations orchestrator (offline, deterministic, no network)."""
from scripts import recommendations as R
from src.strategies.recommendation import recommend


def _record(symbol, name, sector, price, metrics, score):
    m = {**metrics, "symbol": symbol, "name": name, "sector": sector, "price": price}
    return {"symbol": symbol, "name": name, "sector": sector, "exchange": "NSE",
            "price": price, "fundamental_score": score, "metrics": m,
            "recommendation": recommend(m, sector_score=score)}


def _universe():
    return [
        _record("GOODCO", "Good Co", "IT", 1000.0,
                {"pe": 22, "roe": 30, "roce": 35, "debt_to_equity": 0.1, "profit_growth_5y": 22,
                 "dividend_yield": 1.5, "high_52w": 1200, "low_52w": 700, "sma50": 980, "sma200": 900},
                88.0),
        _record("OKCO", "Ok Co", "Energy", 500.0,
                {"pe": 28, "roe": 12, "roce": 11, "debt_to_equity": 0.8, "profit_growth_5y": 6,
                 "high_52w": 600, "low_52w": 420, "sma50": 505, "sma200": 510}, 52.0),
        _record("BADCO", "Bad Co", "Realty", 40.0,
                {"pe": 85, "roe": 2, "roce": 3, "debt_to_equity": 3.4, "profit_growth_5y": -12,
                 "pledged_pct": 60, "high_52w": 90, "low_52w": 38, "sma50": 48, "sma200": 62}, 12.0),
    ]


def test_orchestrator_buckets_offline():
    res = R.build(records=_universe(), ai_budget=0, ml_budget=0, use_news=False,
                  use_analyst=False, top_each=10)
    assert set(res["buckets"]) == {"BUY", "HOLD", "SELL"}
    assert res["universe_count"] == 3
    # every analysed stock appears exactly once across the buckets' source list
    bucketed = sum(len(v) for v in res["buckets"].values())
    assert bucketed == 3
    # the strong name lands in BUY, the distressed name in SELL
    buys = {d["symbol"] for d in res["buckets"]["BUY"]}
    sells = {d["symbol"] for d in res["buckets"]["SELL"]}
    assert "GOODCO" in buys
    assert "BADCO" in sells


def test_orchestrator_search_index_and_deep_map():
    res = R.build(records=_universe(), ai_budget=0, ml_budget=0, use_news=False,
                  use_analyst=False)
    syms = {e["s"] for e in res["search_index"]}
    assert syms == {"GOODCO", "OKCO", "BADCO"}
    assert set(res["deep_by_symbol"]) == {"GOODCO", "OKCO", "BADCO"}
    for e in res["search_index"]:
        assert "v" in e and ("b" in e and "se" in e)  # verdict + two-sided cases


def test_orchestrator_respects_top_each():
    universe = _universe() * 5  # 15 records (dup symbols ok for bucket-size test)
    res = R.build(records=universe, ai_budget=0, ml_budget=0, use_news=False,
                  use_analyst=False, top_each=2)
    for g in ("BUY", "HOLD", "SELL"):
        assert len(res["buckets"][g]) <= 2


def test_light_deep_is_two_sided():
    rec_record = _universe()[0]
    d = R._light_deep(rec_record)
    assert d["symbol"] == "GOODCO"
    assert d["composite"]["group"] in ("BUY", "HOLD", "SELL")
    assert d["composite"]["buy_case"] or d["composite"]["sell_case"]
    assert d["ai"] is None
