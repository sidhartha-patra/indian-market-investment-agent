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
