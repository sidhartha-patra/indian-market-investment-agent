"""Tests for the fundamental-analysis engine (offline, synthetic data)."""
import numpy as np
import pandas as pd

from src.strategies import fundamental_analysis as fa


def test_piotroski_strong_when_improving():
    curr = {"net_income": 120, "total_assets": 1000, "cfo": 150, "lt_debt": 100,
            "current_assets": 400, "current_liabilities": 150, "shares_outstanding": 100,
            "revenue": 800, "gross_profit": 300}
    prev = {"net_income": 80, "total_assets": 1000, "cfo": 90, "lt_debt": 150,
            "current_assets": 350, "current_liabilities": 180, "shares_outstanding": 100,
            "revenue": 700, "gross_profit": 240}
    r = fa.piotroski_f_score(curr, prev)
    assert 0 <= r["f_score"] <= 9
    assert r["f_score"] >= 7 and r["signal"] == "STRONG"


def test_altman_excludes_banks():
    r = fa.altman_z_score({"total_assets": 1000, "total_liabilities": 800}, sector="Banking")
    assert r["zone"] == "EXCLUDED"


def test_altman_modified_safe_company():
    m = {"working_capital": 300, "retained_earnings": 500, "ebit": 200,
         "total_assets": 1000, "total_liabilities": 300, "book_value_equity": 700}
    r = fa.altman_z_score(m, sector="IT")
    assert r["model"] == "modified_zdd"
    assert r["z_score"] > 2.6 and r["zone"] == "Safe"


def test_beneish_clean_vs_flag():
    base = {"receivables": 100, "sales": 1000, "cogs": 600, "current_assets": 400,
            "ppe": 300, "total_assets": 1000, "depreciation": 30, "sga": 100,
            "lt_debt": 100, "current_liabilities": 150, "working_capital": 250, "cash": 80}
    clean = fa.beneish_m_score(base, dict(base))
    assert clean["signal"] in {"CLEAN", "RED_FLAG"}
    assert "m_score" in clean and "variables" in clean


def test_magic_formula_ranks():
    df = pd.DataFrame({
        "symbol": ["A", "B", "C"],
        "ebit": [100, 50, 200], "market_cap": [500, 800, 600],
        "total_debt": [50, 100, 0], "cash": [20, 10, 50],
        "current_assets": [200, 150, 300], "current_liabilities": [100, 120, 80],
        "ppe": [300, 200, 250],
    })
    ranked = fa.magic_formula_rank(df)
    assert "magic_formula_rank" in ranked.columns
    assert set(ranked["symbol"]) <= {"A", "B", "C"}
    assert ranked["magic_formula_rank"].is_monotonic_increasing


def test_graham_number_and_score():
    assert fa.graham_number(50, 200) is not None
    assert fa.graham_number(-5, 200) is None
    r = fa.graham_defensive_score({"market_cap_cr": 1000, "current_ratio": 2.5,
        "eps_positive_years": 8, "dividend_years": 6, "profit_growth_5y": 12,
        "pe": 12, "pb": 1.2, "eps": 50, "bvps": 200, "price": 1000})
    assert 0 <= r["graham_score"] <= 7 and r["graham_score"] >= 5


def test_quality_score_penalises_pledge():
    high = fa.quality_score_fundamental({"gross_profit": 500, "total_assets": 1000,
        "roe": 25, "debt_to_equity": 0.3, "net_income": 100, "cfo": 130, "roce": 28,
        "pledged_pct": 0, "promoter_holding": 65})
    pledged = fa.quality_score_fundamental({"gross_profit": 500, "total_assets": 1000,
        "roe": 25, "debt_to_equity": 0.3, "net_income": 100, "cfo": 130, "roce": 28,
        "pledged_pct": 60, "promoter_holding": 65})
    assert high["quality_score"] > pledged["quality_score"]
    assert high["signal"] == "HIGH_QUALITY"


def test_sector_relative_scores_are_percentiles():
    df = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(8)],
        "sector": ["IT", "IT", "IT", "IT", "Bank", "Bank", "Bank", "Bank"],
        "roe": [40, 20, 15, 10, 18, 16, 12, 9],
        "pe": [25, 30, 35, 40, 12, 14, 16, 20],
        "debt_to_equity": [0.1, 0.2, 0.3, 0.5, 5, 6, 7, 8],
        "profit_growth_5y": [20, 15, 10, 5, 18, 12, 8, 4],
    })
    scored = fa.fundamental_scores(df, sector_col="sector")
    assert scored["fundamental_score"].between(0, 100).all()
    assert "tier" in scored.columns and "reasons" in scored.columns
    # Best-in-IT (S0: high ROE, high growth) should outscore worst-in-IT (S3).
    s0 = scored.loc[scored.symbol == "S0", "fundamental_score"].iloc[0]
    s3 = scored.loc[scored.symbol == "S3", "fundamental_score"].iloc[0]
    assert s0 > s3


def test_normalizers():
    s = fa.normalize_screener({"Stock P/E": 20, "ROCE": 25, "ROE": 18, "Debt to Equity": 0.4,
                               "Sales Growth 5Yrs": 14, "Promoter Holding": 55})
    assert s["pe"] == 20 and s["roce"] == 25 and s["debt_to_equity"] == 0.4
    tv = fa.normalize_tradingview({"price_earnings_ttm": 25, "return_on_equity": 18,
        "debt_to_equity": 0.3, "free_cash_flow": 1000, "market_cap_basic": 50000, "sector": "IT"})
    assert tv["pe"] == 25 and tv["roe"] == 18
    assert "earnings_yield" in tv and "fcf_yield" in tv
