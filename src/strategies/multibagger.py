"""Multibagger candidate screener — Univest + Lynch/Slater framework.

Criteria include ROCE, growth, leverage, promoter quality, PEG, pledge,
OPM expansion, insider buying, qualitative moat, and power-law sizing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.qualitative import assess_company
from src.config import DATA_DIR
from src.ingestion import bse_bulk_deals
from src.ingestion.screener import get_screener_batch

logger = logging.getLogger(__name__)

THEMATIC_UNIVERSE = {
    "defence": ["HAL", "BEL", "BDL", "MAZDOCK", "COCHINSHIP", "GRSE",
                "MTARTECH", "ASTRAMICRO", "PARAS", "DATAPATTNS"],
    "ev_components": ["SONACOMS", "TIINDIA", "BHARATFORG", "MOTHERSON",
                      "GREAVESCOT", "HBLPOWER", "IGARASHI"],
    "specialty_chem": ["SRF", "NAVINFLUOR", "AARTIIND", "PIIND",
                       "DEEPAKNTR", "GUJFLUORO", "FLUOROCHEM", "GALAXYSURF"],
    "water": ["VATECH", "IONEXCHANG", "VAWASTAR", "EMSLIMITED", "GENESYS"],
    "railways": ["RVNL", "IRCON", "TITAGARH", "JKILSON", "JWL", "RAILTEL"],
    "healthcare_diag": ["METROPOLIS", "KIMS", "MEDPLUS", "KRSNAA",
                        "VIJAYA", "THYROCARE"],
    "fintech": ["ANGELONE", "CDSL", "BSE", "MCX", "KFINTECH", "NUVAMA", "360ONE"],
    "renewables": ["TATAPOWER", "JSWENERGY", "INOXWIND", "SUZLON",
                   "NHPC", "SJVN", "IREDA", "BORORENEW", "ORIENTGREN"],
}


def _flat_universe() -> dict[str, str]:
    """Return {symbol: sector_theme}."""
    out = {}
    for theme, syms in THEMATIC_UNIVERSE.items():
        for s in syms:
            out[s] = theme
    return out


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _peg_ratio(pe: float | None, profit_growth_5y: float | None) -> float | None:
    if pe is None or pe <= 0 or profit_growth_5y is None or profit_growth_5y <= 0:
        return None
    return round(pe / profit_growth_5y, 2)


def _opm_metrics(fund: dict) -> tuple[float | None, float | None, float | None]:
    values = [_num(v) for v in fund.get("OPM Quarterly", [])]
    values = [v for v in values if v is not None]
    if len(values) < 4:
        return None, None, None
    latest = values[0]
    prior = values[1:4]
    avg_prior = sum(prior) / len(prior)
    diff_pct = latest - avg_prior
    return round(latest, 2), round(avg_prior, 2), round(diff_pct * 100, 0)


def multibagger_score(fund: dict, sector_theme: str | None = None) -> dict:
    """Score one company on multibagger framework. fund is from screener.get_screener_fundamentals."""
    empty = {
        "score": 0,
        "final_score": 0,
        "signal": "NO_DATA",
        "sector_theme": sector_theme,
        "ROCE": 0,
        "ROE": 0,
        "PE": 0,
        "peg_ratio": None,
        "sales_growth_5y": 0,
        "profit_growth_5y": 0,
        "debt_to_equity": 99,
        "promoter_holding": 0,
        "pledged_pct": None,
        "opm_latest": None,
        "opm_avg_prior_3": None,
        "opm_expansion_bps": None,
        "market_cap_cr": 0,
        "current_price": 0,
    }
    if not fund:
        return empty

    def _v(k, default=None):
        v = fund.get(k)
        return v if v is not None else default

    roce = _num(_v("ROCE"), 0) or 0
    roe = _num(_v("ROE"), 0) or 0
    pe = _num(_v("Stock P/E") or _v("P/E"), 0) or 0
    debt_eq = _num(_v("Debt to Equity"), 99) or 99
    prom = _num(_v("Promoter Holding"), 0) or 0
    pledged_pct = _num(_v("Pledged %"))
    sales_g_5 = _num(_v("Sales Growth 5Yrs"), 0) or 0
    profit_g_5 = _num(_v("Profit Growth 5Yrs"), 0) or 0
    mcap = _num(_v("Market Cap"), 0) or 0  # Cr
    px = _num(_v("Current Price"), 0) or 0
    high_52 = _num(_v("High"), 0) or 0
    peg = _peg_ratio(pe, profit_g_5)
    opm_latest, opm_avg_prior_3, opm_expansion_bps = _opm_metrics(fund)

    score = 0
    if roce > 20:
        score += 25
    elif roce > 15:
        score += 12
    if profit_g_5 > 15:
        score += 20
    elif profit_g_5 > 10:
        score += 10
    if sales_g_5 > 20:
        score += 20
    elif sales_g_5 > 15:
        score += 10
    if debt_eq < 0.5:
        score += 15
    elif debt_eq < 1.0:
        score += 7
    if prom > 45:
        score += 10
    elif prom > 30:
        score += 5
    if 500 <= mcap <= 50000:
        score += 10
    elif 50 <= mcap < 500:
        score += 5

    if peg is not None:
        if peg < 1:
            score += 15
        elif peg < 1.5:
            score += 8
        elif peg > 3:
            score -= 5

    if pledged_pct is not None and pledged_pct > 0:
        score -= 20

    if opm_expansion_bps is not None:
        opm_diff_pct = opm_expansion_bps / 100
        if opm_diff_pct > 2.0:
            score += 10
        elif opm_diff_pct > 0.5:
            score += 5
        elif opm_diff_pct < -2.0:
            score -= 5

    if high_52 and px:
        pct_from_high = (px / high_52 - 1) * 100
        if pct_from_high > -10:
            score -= 10
        elif pct_from_high < -25:
            score += 5

    if sector_theme:
        score += 10

    signal = "MULTIBAGGER_HIGH" if score >= 80 else \
             "MULTIBAGGER_MEDIUM" if score >= 60 else \
             "MULTIBAGGER_LOW" if score >= 40 else "NO"

    score = int(round(score))
    return {
        "score": score,
        "final_score": score,
        "signal": signal,
        "sector_theme": sector_theme,
        "ROCE": roce,
        "ROE": roe,
        "PE": pe,
        "peg_ratio": peg,
        "sales_growth_5y": sales_g_5,
        "profit_growth_5y": profit_g_5,
        "debt_to_equity": debt_eq,
        "promoter_holding": prom,
        "pledged_pct": pledged_pct,
        "opm_latest": opm_latest,
        "opm_avg_prior_3": opm_avg_prior_3,
        "opm_expansion_bps": opm_expansion_bps,
        "market_cap_cr": mcap,
        "current_price": px,
    }


def _insider_defaults(raw: dict | None) -> dict:
    raw = raw or {}
    return {
        "net_qty": raw.get("promoter_net_buy_qty", 0) or 0,
        "buy_count": raw.get("promoter_buy_count", 0) or 0,
        "last_date": raw.get("last_buy_date"),
    }


def enrich_with_advanced_signals(symbols: list[str], base_results: list[dict]) -> list[dict]:
    """Merge insider buying and LLM moat/integrity signals into top candidates."""
    symbol_set = {s.upper() for s in symbols}
    enriched: list[dict] = []
    for row in base_results:
        symbol = str(row.get("symbol", "")).upper()
        if symbol_set and symbol not in symbol_set:
            continue
        item = dict(row)
        fundamentals = item.pop("_fundamentals", {}) or item

        try:
            insider = _insider_defaults(bse_bulk_deals.get_promoter_buying(symbol))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Insider buying enrichment failed for %s: %s", symbol, exc)
            insider = _insider_defaults({})

        try:
            qualitative = assess_company(symbol, fundamentals, sector_theme=item.get("sector_theme"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Qualitative enrichment failed for %s: %s", symbol, exc)
            qualitative = {}

        moat_strength = int(_num(qualitative.get("moat_strength"), 0) or 0)
        insider_bonus = 10 if (_num(insider.get("net_qty"), 0) or 0) > 0 else 0
        item.update({
            "insider_buying": insider,
            "moat_type": qualitative.get("moat_type", "none"),
            "moat_strength": moat_strength,
            "governance_flags": qualitative.get("governance_flags", []),
            "green_flags": qualitative.get("green_flags", []),
            "qualitative_score": qualitative.get("qualitative_score", 0),
            "qualitative_summary": qualitative.get("summary", ""),
            "insider_bonus": insider_bonus,
        })
        item["final_score"] = min(150, int(round((item.get("score") or 0) + insider_bonus + moat_strength)))
        enriched.append(item)

    enriched.sort(key=lambda r: r.get("final_score", r.get("score", 0)), reverse=True)
    return enriched


def suggest_portfolio_allocation(top_picks: list[dict], total_capital_pct: float = 50,
                                 max_picks: int = 10) -> list[dict]:
    """Power-law sizing with weights summing to total_capital_pct."""
    picks = top_picks[:max_picks]
    if not picks:
        return []

    n = len(picks)
    floor = min(4.0, total_capital_pct / n)
    ceiling = max(15.0, floor)
    scores = [max(_num(p.get("final_score") or p.get("score"), 1) or 1, 1) for p in picks]
    weights: list[float | None] = [None] * n
    remaining = set(range(n))
    remaining_total = float(total_capital_pct)

    while remaining:
        score_sum = sum(scores[i] for i in remaining) or len(remaining)
        changed = False
        for i in list(remaining):
            weight = scores[i] / score_sum * remaining_total
            if weight < floor:
                weights[i] = floor
            elif weight > ceiling:
                weights[i] = ceiling
            else:
                continue
            remaining.remove(i)
            remaining_total -= weights[i] or 0
            changed = True
        if not changed:
            for i in remaining:
                weights[i] = scores[i] / score_sum * remaining_total
            break

    rounded = [round(float(w or 0), 2) for w in weights]
    rounded[0] = round(rounded[0] + (total_capital_pct - sum(rounded)), 2)

    allocation = []
    for pick, weight in zip(picks, rounded, strict=False):
        peg = pick.get("peg_ratio")
        peg_text = f"PEG {peg}" if peg is not None else "PEG n/a"
        rationale = (
            f"{peg_text}, ROCE {pick.get('ROCE', 0)}%, "
            f"moat={pick.get('moat_type', 'none')}, final_score={pick.get('final_score', pick.get('score'))}"
        )
        allocation.append({"symbol": pick.get("symbol"), "weight_pct": weight, "rationale": rationale})
    return allocation


def screen_multibaggers(symbols: list[str] | None = None,
                        top_n: int = 20) -> dict:
    """Screen given symbols (or thematic universe) for multibagger candidates."""
    if symbols is None:
        symbol_map = _flat_universe()
        symbols = list(symbol_map.keys())
    else:
        symbol_map = _flat_universe()

    logger.info("Fetching fundamentals for %d candidates...", len(symbols))
    funds = get_screener_batch(symbols, max_workers=5)

    rows = []
    for sym in symbols:
        f = funds.get(sym, {})
        sector = symbol_map.get(sym)
        r = multibagger_score(f, sector_theme=sector)
        r["symbol"] = sym
        r["_fundamentals"] = f
        rows.append(r)

    rows.sort(key=lambda r: r["score"], reverse=True)
    top = rows[:top_n]
    ranked = enrich_with_advanced_signals([r["symbol"] for r in top], top)
    allocation = suggest_portfolio_allocation(ranked, total_capital_pct=50, max_picks=min(10, top_n))
    output = {"ranked": ranked, "allocation": allocation}

    out_path = DATA_DIR / "multibaggers.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Saved -> %s", out_path)
    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    all_syms = list(_flat_universe().keys())[:30]
    results = screen_multibaggers(all_syms, top_n=10)

    print("\n=== Top 5 enriched multibagger candidates ===")
    for r in results["ranked"][:5]:
        print(
            f"  {r['symbol']:14} final_score={r.get('final_score', 0):3} "
            f"score={r.get('score', 0):3} PEG={r.get('peg_ratio')} "
            f"moat={r.get('moat_type', 'none')} insider={r.get('insider_buying', {})}"
        )

    print()
    print("=== Power-law portfolio allocation ===")
    for row in results["allocation"]:
        print(f"  {row['symbol']:14} weight={row['weight_pct']:5.2f}%  {row['rationale']}")
