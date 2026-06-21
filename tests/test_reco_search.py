"""Tests for the Recommendations tab, Search tab, and the AI deep-research card (offline)."""
import json

from scripts.build_site import (_demo_movers, _demo_records, _demo_recommendations,
                                 _demo_search_index, _search_entry, build_site)


def _build(tmp_path):
    reco = _demo_recommendations()
    return build_site(_demo_records(), out_dir=tmp_path, movers=_demo_movers(),
                      extra_lists={"Nifty 50": _demo_records()},
                      recommendations=reco, search_index=_demo_search_index(reco)), reco


def test_recommendations_page_has_buckets_and_two_options(tmp_path):
    res, reco = _build(tmp_path)
    assert res["has_reco"] is True
    html = (tmp_path / "recommendations.html").read_text(encoding="utf-8")
    assert "Top Buy / Sell / Hold" in html
    assert "The Buy case" in html and "The Sell case" in html
    assert "showPane" in html  # tab switcher
    # every recommended stock is bucketed into exactly one of BUY/HOLD/SELL
    n = sum(len(v) for v in reco["buckets"].values())
    assert n == reco["universe_count"]


def test_search_page_embeds_index_and_two_sided(tmp_path):
    _build(tmp_path)
    html = (tmp_path / "search.html").read_text(encoding="utf-8")
    assert "window.__SEARCH__" in html
    assert "Buy or Sell" in html
    data = json.loads((tmp_path / "data" / "search.json").read_text(encoding="utf-8"))
    assert data and all({"s", "v", "b", "se"} <= set(e) for e in data)


def test_stock_detail_carries_ai_card(tmp_path):
    _build(tmp_path)
    tcs = (tmp_path / "stock" / "TCS.html").read_text(encoding="utf-8")
    assert "AI deep research" in tcs
    assert "The Buy case" in tcs and "The Sell case" in tcs


def test_index_links_reco_and_search(tmp_path):
    _build(tmp_path)
    idx = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "recommendations.html" in idx and "search.html" in idx


def test_reco_search_keep_sebi_framing(tmp_path):
    _build(tmp_path)
    for f in ("recommendations.html", "search.html"):
        low = (tmp_path / f).read_text(encoding="utf-8").lower()
        assert "not investment advice" in low and "sebi" in low
        assert "buy at" not in low and "target price" not in low


def test_search_entry_is_compact_two_sided():
    deep = {"symbol": "AAA", "name": "Alpha Ltd", "sector": "IT", "price": 100,
            "composite": {"verdict": "BUY", "score": 72, "buy_case": ["x", "y"], "sell_case": ["z"]},
            "ai": {"source": "ai"}}
    e = _search_entry(deep)
    assert e["s"] == "AAA" and e["v"] == "BUY" and e["sc"] == 72
    assert e["b"] == ["x", "y"] and e["se"] == ["z"] and e["ai"] is True


def test_build_site_without_ai_features_unchanged(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path)
    assert res["has_reco"] is False and res["has_search"] is False
    assert not (tmp_path / "recommendations.html").exists()
    assert not (tmp_path / "search.html").exists()
    # AI card only appears when deep data is attached
    assert "AI deep research" not in (tmp_path / "stock" / "TCS.html").read_text(encoding="utf-8")
