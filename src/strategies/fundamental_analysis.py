"""Detailed fundamental analysis — scoring frameworks + sector-relative composite.

Implements the canonical fundamental tool-kit recommended in docs/DESIGN.md and the
research report:

- **Piotroski F-Score (0-9)** — fundamental momentum / financial-health.
- **Altman Z-Score** (original + Z'' for emerging-market / non-manufacturing).
- **Beneish M-Score** — earnings-manipulation red flag.
- **Greenblatt Magic Formula** — earnings yield + return on capital ranking.
- **Graham** defensive criteria + Graham Number (margin of safety).
- **Quality score** — Novy-Marx gross profitability + cash-conversion + governance.
- **Sector-relative 0-100 composite** — winsorised z-scores computed *within sector*
  (never compare a bank's P/E to an IT firm's), with explainable sub-scores + reasons.

All functions are pure and offline-testable. Inputs use canonical snake_case keys;
``normalize_screener``/``normalize_tradingview`` map the two data sources onto them.

> ⚠️ Educational/decision-support use only — not investment advice, not a SEBI-registered
> research report. A high score is NOT a "buy".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Sectors whose capital structure makes Altman-Z / leverage ratios non-comparable.
_FINANCIAL_SECTORS = {"banking", "financial", "finance", "bank", "nbfc", "insurance", "financials"}
# Manufacturing-ish sectors use the *original* Altman Z; everything else uses Z''.
_MANUFACTURING_SECTORS = {"metals", "cement", "auto", "automobile", "steel", "manufacturing"}


def _num(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        f = float(value)
        return f if np.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_div(a, b, default: float = 0.0) -> float:
    a, b = _num(a), _num(b)
    if a is None or b in (None, 0):
        return default
    return a / b


# --------------------------------------------------------------------------- #
# 1. Piotroski F-Score (0-9)
# --------------------------------------------------------------------------- #
def piotroski_f_score(curr: dict, prev: dict) -> dict:
    """9-point Piotroski F-Score from current + prior-year statement dicts.

    Required keys (per year): net_income, total_assets, cfo, lt_debt, current_assets,
    current_liabilities, shares_outstanding, revenue, gross_profit.
    """
    roa_t = _safe_div(curr.get("net_income"), curr.get("total_assets"))
    roa_t1 = _safe_div(prev.get("net_income"), prev.get("total_assets"))
    cfo_ta = _safe_div(curr.get("cfo"), curr.get("total_assets"))

    f1 = int(roa_t > 0)
    f2 = int((_num(curr.get("cfo")) or 0) > 0)
    f3 = int(roa_t > roa_t1)
    f4 = int(cfo_ta > roa_t)  # accruals: cash earnings exceed accrual earnings

    lev_t = _safe_div(curr.get("lt_debt"), curr.get("total_assets"))
    lev_t1 = _safe_div(prev.get("lt_debt"), prev.get("total_assets"))
    cr_t = _safe_div(curr.get("current_assets"), curr.get("current_liabilities"))
    cr_t1 = _safe_div(prev.get("current_assets"), prev.get("current_liabilities"))

    f5 = int(lev_t < lev_t1)  # leverage decreased
    f6 = int(cr_t > cr_t1)    # liquidity improved
    f7 = int((_num(curr.get("shares_outstanding")) or 0) <= (_num(prev.get("shares_outstanding")) or 0))

    gm_t = _safe_div(curr.get("gross_profit"), curr.get("revenue"))
    gm_t1 = _safe_div(prev.get("gross_profit"), prev.get("revenue"))
    at_t = _safe_div(curr.get("revenue"), curr.get("total_assets"))
    at_t1 = _safe_div(prev.get("revenue"), prev.get("total_assets"))

    f8 = int(gm_t > gm_t1)  # gross margin improved
    f9 = int(at_t > at_t1)  # asset turnover improved

    score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9
    return {
        "f_score": score,
        "components": {
            "roa_positive": f1, "cfo_positive": f2, "roa_improving": f3, "low_accruals": f4,
            "leverage_down": f5, "liquidity_up": f6, "no_dilution": f7,
            "gross_margin_up": f8, "asset_turnover_up": f9,
        },
        "signal": "STRONG" if score >= 7 else "NEUTRAL" if score >= 4 else "WEAK",
    }


# --------------------------------------------------------------------------- #
# 2. Altman Z-Score (original + Z'' modified)
# --------------------------------------------------------------------------- #
def altman_z_score(metrics: dict, sector: str | None = None) -> dict:
    """Altman Z (manufacturing) or Z'' (everything else, incl. emerging markets).

    Required: working_capital, retained_earnings, ebit, total_assets, total_liabilities,
    and either market_cap (original) or book_value_equity (Z''). Sales for original.
    Banks/NBFCs/insurers are excluded (capital structure not comparable).
    """
    sec = (sector or "").lower()
    if any(s in sec for s in _FINANCIAL_SECTORS):
        return {"z_score": None, "model": "n/a", "zone": "EXCLUDED",
                "note": "Altman Z not meaningful for banks/NBFCs/insurers"}

    ta = _num(metrics.get("total_assets"))
    tl = _num(metrics.get("total_liabilities"))
    if not ta or not tl or ta <= 0 or tl <= 0:
        return {"z_score": None, "model": "n/a", "zone": "NO_DATA"}

    wc = _num(metrics.get("working_capital")) or 0.0
    re = _num(metrics.get("retained_earnings")) or 0.0
    ebit = _num(metrics.get("ebit")) or 0.0

    use_original = any(s in sec for s in _MANUFACTURING_SECTORS) and metrics.get("sales") is not None
    if use_original:
        mve = _num(metrics.get("market_cap")) or _num(metrics.get("book_value_equity")) or (ta - tl)
        sales = _num(metrics.get("sales")) or 0.0
        z = (1.2 * wc / ta + 1.4 * re / ta + 3.3 * ebit / ta + 0.6 * mve / tl + 1.0 * sales / ta)
        zone = "Distress" if z < 1.81 else "Grey" if z < 2.99 else "Safe"
        model = "original"
    else:
        bve = _num(metrics.get("book_value_equity")) or (ta - tl)
        z = (6.56 * wc / ta + 3.26 * re / ta + 6.72 * ebit / ta + 1.05 * bve / tl)
        zone = "Distress" if z < 1.1 else "Grey" if z < 2.6 else "Safe"
        model = "modified_zdd"
    return {"z_score": round(z, 2), "model": model, "zone": zone}


# --------------------------------------------------------------------------- #
# 3. Beneish M-Score (earnings-manipulation flag)
# --------------------------------------------------------------------------- #
def beneish_m_score(curr: dict, prev: dict) -> dict:
    """8-variable Beneish M-Score. M > -1.78 => likely earnings manipulator.

    Required (per year): receivables, sales, cogs, current_assets, ppe, total_assets,
    depreciation, sga, lt_debt, current_liabilities, working_capital, cash.
    """
    dsri = _safe_div(_safe_div(curr.get("receivables"), curr.get("sales")),
                     _safe_div(prev.get("receivables"), prev.get("sales")))
    gm_t = _safe_div((_num(curr.get("sales")) or 0) - (_num(curr.get("cogs")) or 0), curr.get("sales"))
    gm_t1 = _safe_div((_num(prev.get("sales")) or 0) - (_num(prev.get("cogs")) or 0), prev.get("sales"))
    gmi = _safe_div(gm_t1, gm_t)
    soft_t = 1 - _safe_div((_num(curr.get("current_assets")) or 0) + (_num(curr.get("ppe")) or 0), curr.get("total_assets"))
    soft_t1 = 1 - _safe_div((_num(prev.get("current_assets")) or 0) + (_num(prev.get("ppe")) or 0), prev.get("total_assets"))
    aqi = _safe_div(soft_t, soft_t1)
    sgi = _safe_div(curr.get("sales"), prev.get("sales"))
    dep_t = _safe_div(curr.get("depreciation"), (_num(curr.get("ppe")) or 0) + (_num(curr.get("depreciation")) or 0))
    dep_t1 = _safe_div(prev.get("depreciation"), (_num(prev.get("ppe")) or 0) + (_num(prev.get("depreciation")) or 0))
    depi = _safe_div(dep_t1, dep_t)
    sgai = _safe_div(_safe_div(curr.get("sga"), curr.get("sales")), _safe_div(prev.get("sga"), prev.get("sales")))
    lev_t = _safe_div((_num(curr.get("lt_debt")) or 0) + (_num(curr.get("current_liabilities")) or 0), curr.get("total_assets"))
    lev_t1 = _safe_div((_num(prev.get("lt_debt")) or 0) + (_num(prev.get("current_liabilities")) or 0), prev.get("total_assets"))
    lvgi = _safe_div(lev_t, lev_t1)
    d_wc = (_num(curr.get("working_capital")) or 0) - (_num(prev.get("working_capital")) or 0)
    d_cash = (_num(curr.get("cash")) or 0) - (_num(prev.get("cash")) or 0)
    tata = _safe_div(d_wc - d_cash - (_num(curr.get("depreciation")) or 0), curr.get("total_assets"))

    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    return {
        "m_score": round(m, 3),
        "manipulator": m > -1.78,
        "signal": "RED_FLAG" if m > -1.78 else "CLEAN",
        "variables": {"DSRI": round(dsri, 3), "GMI": round(gmi, 3), "AQI": round(aqi, 3),
                      "SGI": round(sgi, 3), "DEPI": round(depi, 3), "SGAI": round(sgai, 3),
                      "LVGI": round(lvgi, 3), "TATA": round(tata, 4)},
    }


# --------------------------------------------------------------------------- #
# 4. Greenblatt Magic Formula
# --------------------------------------------------------------------------- #
def magic_formula_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Rank stocks by Greenblatt Magic Formula (earnings yield + return on capital).

    Requires columns: symbol, ebit, market_cap, total_debt, cash, current_assets,
    current_liabilities, ppe. Financials should be pre-excluded by the caller.
    """
    out = df.copy()
    for col in ("ebit", "market_cap", "total_debt", "cash", "current_assets",
                "current_liabilities", "ppe"):
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["ev"] = out["market_cap"] + out["total_debt"].fillna(0) - out["cash"].fillna(0)
    out["earnings_yield"] = out["ebit"] / out["ev"].replace(0, np.nan)
    nwc = (out["current_assets"] - out["cash"].fillna(0)) - out["current_liabilities"].fillna(0)
    out["roc"] = out["ebit"] / (nwc + out["ppe"]).replace(0, np.nan)
    out = out[out["ev"] > 0].copy()
    if out.empty:
        return out
    out["rank_ey"] = out["earnings_yield"].rank(ascending=False, na_option="bottom")
    out["rank_roc"] = out["roc"].rank(ascending=False, na_option="bottom")
    out["magic_formula_rank"] = (out["rank_ey"] + out["rank_roc"]).rank(method="first")
    return out.sort_values("magic_formula_rank").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 5. Graham defensive criteria + Graham Number
# --------------------------------------------------------------------------- #
def graham_number(eps: float | None, bvps: float | None) -> float | None:
    eps, bvps = _num(eps), _num(bvps)
    if not eps or not bvps or eps <= 0 or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


def graham_defensive_score(metrics: dict) -> dict:
    """Graham's defensive-investor criteria (0-7) + Graham Number margin of safety."""
    score, flags = 0, {}
    checks = {
        "adequate_size": (_num(metrics.get("market_cap_cr")) or 0) >= 500,
        "current_ratio_ge2": (_num(metrics.get("current_ratio")) or 0) >= 2.0,
        "earnings_stability": (_num(metrics.get("eps_positive_years")) or 0) >= 7,
        "dividend_record": (_num(metrics.get("dividend_years")) or 0) >= 5,
        "earnings_growth": (_num(metrics.get("profit_growth_5y")) or 0) >= 10,
        "moderate_pe": 0 < (_num(metrics.get("pe")) or 999) <= 15,
        "moderate_pb": (_num(metrics.get("pb")) or 999) <= 1.5
        or ((_num(metrics.get("pe")) or 999) * (_num(metrics.get("pb")) or 999) <= 22.5),
    }
    for k, ok in checks.items():
        flags[k] = bool(ok)
        score += int(bool(ok))
    gn = graham_number(metrics.get("eps"), metrics.get("bvps"))
    price = _num(metrics.get("price")) or _num(metrics.get("current_price"))
    mos = round((gn - price) / gn * 100, 1) if gn and price else None
    return {
        "graham_score": score, "criteria_met": flags, "graham_number": gn,
        "margin_of_safety_pct": mos,
        "signal": "DEFENSIVE_BUY" if score >= 5 and mos and mos > 0
        else "WATCH" if score >= 4 else "AVOID",
    }


# --------------------------------------------------------------------------- #
# 6. Quality score (Novy-Marx GP/TA + cash conversion + governance)
# --------------------------------------------------------------------------- #
def quality_score_fundamental(metrics: dict) -> dict:
    """Composite 0-100 quality score with reasons (governance-aware for India)."""
    score, reasons = 0, []
    gp_ta = _safe_div(metrics.get("gross_profit"), metrics.get("total_assets"))
    if gp_ta > 0.40:
        score += 25; reasons.append(f"High gross profitability GP/TA={gp_ta:.2f}")
    elif gp_ta > 0.25:
        score += 15
    elif gp_ta > 0.10:
        score += 5

    roe = _num(metrics.get("roe")) or 0
    de = _num(metrics.get("debt_to_equity")) or 0
    if roe > 20 and de < 1.0:
        score += 20; reasons.append(f"ROE {roe:.0f}% with low D/E {de:.2f}")
    elif roe > 15:
        score += 12
    elif roe > 10:
        score += 5

    np_ = _num(metrics.get("net_profit") or metrics.get("net_income"))
    cfo = _num(metrics.get("cfo"))
    if np_ and np_ > 0 and cfo is not None:
        cfq = cfo / np_
        if cfq > 1.0:
            score += 20; reasons.append(f"Strong cash conversion CFO/NP={cfq:.1f}")
        elif cfq > 0.7:
            score += 10
        elif cfq < 0.4:
            score -= 10; reasons.append(f"Weak cash conversion CFO/NP={cfq:.1f}")

    roce = _num(metrics.get("roce")) or 0
    if roce > 25:
        score += 20; reasons.append(f"ROCE {roce:.0f}% (excellent)")
    elif roce > 15:
        score += 12
    elif 0 < roce < 10:
        score -= 5

    pledged = _num(metrics.get("pledged_pct")) or 0
    if pledged > 50:
        score -= 20; reasons.append(f"High promoter pledge {pledged:.0f}%")
    elif pledged > 25:
        score -= 10; reasons.append(f"Promoter pledge {pledged:.0f}%")
    elif pledged == 0:
        score += 5

    prom = _num(metrics.get("promoter_holding")) or 0
    if prom > 60:
        score += 10
    elif prom > 40:
        score += 5

    score = int(max(0, min(100, score)))
    return {
        "quality_score": score, "gp_ta": round(gp_ta, 3), "roe": roe, "roce": roce,
        "reasons": reasons,
        "signal": "HIGH_QUALITY" if score >= 70 else "QUALITY" if score >= 50 else "LOW_QUALITY",
    }


# --------------------------------------------------------------------------- #
# 7. Sector-relative 0-100 composite (the flagship fundamental score)
# --------------------------------------------------------------------------- #
PILLAR_WEIGHTS = {
    "valuation": 0.20, "profitability": 0.25, "growth": 0.20,
    "leverage": 0.15, "cashflow": 0.10, "efficiency": 0.05, "ownership": 0.05,
}

# pillar -> {metric: direction}  (+1 higher is better, -1 lower is better)
PILLAR_METRICS: dict[str, dict[str, int]] = {
    "valuation": {"pe": -1, "pb": -1, "ev_ebitda": -1, "peg": -1, "fcf_yield": +1, "earnings_yield": +1},
    "profitability": {"roe": +1, "roce": +1, "roa": +1, "opm": +1, "net_margin": +1, "gross_margin": +1},
    "growth": {"revenue_growth_5y": +1, "profit_growth_5y": +1, "eps_growth_3y": +1, "revenue_growth_3y": +1},
    "leverage": {"debt_to_equity": -1, "interest_coverage": +1, "current_ratio": +1, "quick_ratio": +1},
    "cashflow": {"fcf_yield": +1, "cfo_np": +1, "cfo_ebitda": +1},
    "efficiency": {"asset_turnover": +1, "ccc": -1, "working_capital_days": -1},
    "ownership": {"promoter_holding": +1, "pledged_pct": -1, "fii_holding": +1},
}


def _winsor_zscore(s: pd.Series, limit: float = 3.0) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    mean, std = s.mean(), s.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    return ((s - mean) / std).clip(-limit, limit).fillna(0.0)


def _sector_zscore(series: pd.Series, sectors: pd.Series, limit: float = 3.0) -> pd.Series:
    """Winsorised z-score computed *within each sector*."""
    out = pd.Series(0.0, index=series.index, dtype=float)
    for sector in sectors.fillna("OTHER").unique():
        mask = sectors.fillna("OTHER") == sector
        out.loc[mask] = _winsor_zscore(series[mask], limit)
    return out


def fundamental_scores(
    df: pd.DataFrame,
    sector_col: str = "sector",
    pillar_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compute an explainable sector-relative 0-100 fundamental score per stock.

    ``df`` rows are stocks; columns are canonical metric keys (+ a sector column).
    Returns the input plus per-pillar z-scores, ``composite_z``, ``fundamental_score``
    (0-100 percentile), ``tier`` and a ``reasons`` list of the biggest +/- drivers.
    """
    if df.empty:
        return df.assign(fundamental_score=pd.Series(dtype=float))
    weights = pillar_weights or PILLAR_WEIGHTS
    out = df.copy()
    sectors = out[sector_col] if sector_col in out.columns else pd.Series("OTHER", index=out.index)

    metric_z: dict[str, pd.Series] = {}
    pillar_z: dict[str, pd.Series] = {}
    for pillar, metrics in PILLAR_METRICS.items():
        present = [(m, d) for m, d in metrics.items() if m in out.columns and out[m].notna().any()]
        if not present:
            continue
        zs = []
        for metric, direction in present:
            z = _sector_zscore(out[metric], sectors) * direction
            metric_z[metric] = z
            zs.append(z)
        pillar_z[pillar] = pd.concat(zs, axis=1).mean(axis=1)
        out[f"z_{pillar}"] = pillar_z[pillar].round(2)

    if not pillar_z:
        out["fundamental_score"] = 50.0
        out["tier"] = "NO_DATA"
        out["reasons"] = [[] for _ in range(len(out))]
        return out

    norm = sum(weights[p] for p in pillar_z) or 1.0
    composite = sum(weights[p] / norm * z for p, z in pillar_z.items())
    out["composite_z"] = composite.round(3)
    out["fundamental_score"] = (composite.rank(pct=True) * 100).round(1)
    out["tier"] = np.where(out["fundamental_score"] >= 80, "TOP_DECILE",
                  np.where(out["fundamental_score"] >= 60, "STRONG",
                  np.where(out["fundamental_score"] >= 40, "AVERAGE", "WEAK")))
    out["reasons"] = _build_reasons(out, metric_z)
    return out


_FRIENDLY = {
    "pe": "valuation (P/E)", "pb": "valuation (P/B)", "ev_ebitda": "EV/EBITDA",
    "roe": "ROE", "roce": "ROCE", "roa": "ROA", "opm": "operating margin",
    "net_margin": "net margin", "revenue_growth_5y": "5y revenue growth",
    "profit_growth_5y": "5y profit growth", "debt_to_equity": "leverage (D/E)",
    "current_ratio": "current ratio", "fcf_yield": "FCF yield", "cfo_np": "cash conversion",
    "promoter_holding": "promoter holding", "pledged_pct": "promoter pledge",
    "fii_holding": "FII holding",
}


def _build_reasons(out: pd.DataFrame, metric_z: dict[str, pd.Series]) -> list[list[str]]:
    """Per-stock top-3 positive and top-2 negative metric drivers (sector-relative)."""
    if not metric_z:
        return [[] for _ in range(len(out))]
    zmat = pd.DataFrame(metric_z)
    reasons = []
    for i in out.index:
        row = zmat.loc[i].dropna().sort_values(ascending=False)
        pos = [f"strong {_FRIENDLY.get(m, m)} vs sector" for m in row.head(3).index if row[m] > 0.4]
        neg = [f"weak {_FRIENDLY.get(m, m)} vs sector" for m in row.tail(2).index if row[m] < -0.4]
        reasons.append(pos + neg)
    return reasons


# --------------------------------------------------------------------------- #
# 8. Source field mappers (Screener.in / TradingView -> canonical keys)
# --------------------------------------------------------------------------- #
def normalize_screener(d: dict) -> dict:
    """Map a Screener.in fundamentals dict (from ingestion.screener) to canonical keys."""
    g = d.get
    return {
        "pe": _num(g("Stock P/E") or g("P/E")),
        "pb": _num(g("P/B")),
        "market_cap_cr": _num(g("Market Cap")),
        "dividend_yield": _num(g("Dividend Yield")),
        "roce": _num(g("ROCE")),
        "roe": _num(g("ROE")),
        "opm": _num(g("OPM")),
        "debt_to_equity": _num(g("Debt to Equity")),
        "revenue_growth_3y": _num(g("Sales Growth 3Yrs")),
        "revenue_growth_5y": _num(g("Sales Growth 5Yrs")),
        "profit_growth_3y": _num(g("Profit Growth 3Yrs")),
        "profit_growth_5y": _num(g("Profit Growth 5Yrs")),
        "promoter_holding": _num(g("Promoter Holding")),
        "pledged_pct": _num(g("Pledged %")),
        "bvps": _num(g("Book Value")),
        "price": _num(g("Current Price")),
    }


# TradingView scanner column -> canonical key
_TV_MAP = {
    "price_earnings_ttm": "pe", "price_book_fq": "pb", "price_sales_ratio": "ps",
    "enterprise_value_ebitda_ttm": "ev_ebitda", "return_on_equity": "roe",
    "return_on_assets": "roa", "return_on_invested_capital": "roic",
    "operating_margin_ttm": "opm", "net_margin_ttm": "net_margin",
    "gross_margin_ttm": "gross_margin", "debt_to_equity": "debt_to_equity",
    "current_ratio_fq": "current_ratio", "quick_ratio_fq": "quick_ratio",
    "free_cash_flow": "fcf", "earnings_per_share_basic_ttm": "eps",
    "dividend_yield_recent": "dividend_yield", "market_cap_basic": "market_cap",
    "earnings_per_share_diluted_yoy_growth_ttm": "eps_growth_3y", "sector": "sector",
    "industry": "industry",
}


def normalize_tradingview(d: dict) -> dict:
    """Map a TradingView scanner row (column->value) to canonical keys."""
    out = {_TV_MAP[k]: v for k, v in d.items() if k in _TV_MAP}
    pe, fcf, mcap = _num(out.get("pe")), _num(d.get("free_cash_flow")), _num(d.get("market_cap_basic"))
    if pe and pe > 0:
        out["earnings_yield"] = round(1.0 / pe * 100, 2)
    if fcf is not None and mcap and mcap > 0:
        out["fcf_yield"] = round(fcf / mcap * 100, 2)
    return out
