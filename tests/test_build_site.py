"""Tests for the shareable static-site generator (offline)."""
import json

from scripts.build_site import _demo_records, build_site


def test_build_site_creates_pages(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path)
    index = tmp_path / "index.html"
    reliance = tmp_path / "stock" / "RELIANCE.html"
    tcs = tmp_path / "stock" / "TCS.html"
    assert index.exists() and reliance.exists() and tcs.exists()
    assert res["pages"] == 3  # 2 stocks + index


def test_pages_carry_disclaimer_and_framed_signal(tmp_path):
    build_site(_demo_records(), out_dir=tmp_path)
    for f in (tmp_path / "index.html", tmp_path / "stock" / "RELIANCE.html"):
        html = f.read_text(encoding="utf-8").lower()
        assert "not investment advice" in html
        assert "sebi" in html
        # The hard SEBI line: no specific entry/target price trading calls.
        assert "buy at" not in html and "target price" not in html
    # Buy/sell verdicts are allowed but must be framed as an educational model signal,
    # with the multi-horizon outlook present.
    stock = (tmp_path / "stock" / "TCS.html").read_text(encoding="utf-8").lower()
    assert "model signal" in stock
    assert "educational model signal" in stock
    assert "short / mid / long-term outlook" in stock


def test_index_json_is_valid(tmp_path):
    build_site(_demo_records(), out_dir=tmp_path)
    data = json.loads((tmp_path / "data" / "index.json").read_text(encoding="utf-8"))
    assert data["count"] == 2
    assert {s["symbol"] for s in data["stocks"]} == {"RELIANCE", "TCS"}
    # Sorted by score desc -> TCS (91) first.
    assert data["stocks"][0]["symbol"] == "TCS"


def test_stock_page_has_score_and_why(tmp_path):
    build_site(_demo_records(), out_dir=tmp_path)
    html = (tmp_path / "stock" / "TCS.html").read_text(encoding="utf-8")
    assert "91" in html
    assert "Why" in html and "Supportive signals" in html
    assert "og:title" in html  # shareable preview metadata


def test_extra_list_builds_named_page_and_links(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path, extra_lists={"Nifty 50": _demo_records()})
    assert "nifty50.html" in res["lists"]
    page = tmp_path / "nifty50.html"
    assert page.exists()
    html = page.read_text(encoding="utf-8")
    assert "Nifty 50" in html
    assert "RELIANCE" in html and "stock/RELIANCE.html" in html
    # The home page links to the Nifty 50 page (coexists with the all-market Top 50).
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "nifty50.html" in index and "Nifty 50" in index
    # SEBI framing preserved, no entry/target calls.
    low = html.lower()
    assert "not investment advice" in low and "sebi" in low
    assert "buy at" not in low and "target price" not in low


def test_extra_list_empty_is_ignored(tmp_path):
    res = build_site(_demo_records(), out_dir=tmp_path, extra_lists={"Nifty 50": []})
    assert res["lists"] == []
    assert not (tmp_path / "nifty50.html").exists()
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "nifty50.html" not in index
