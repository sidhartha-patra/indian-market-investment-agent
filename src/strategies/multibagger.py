"""Multibagger candidate screener — Univest-inspired framework.

Criteria: ROCE>20%, Sales Growth 5Y>20%, Profit Growth 5Y>15%,
D/E<0.5, Promoter>45%, Mkt Cap 500-50000 Cr, in emerging sector.
"""
from __future__ import annotations

import json
import logging

from src.config import DATA_DIR
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


def multibagger_score(fund: dict, sector_theme: str | None = None) -> dict:
    """Score one company on multibagger framework. fund is from screener.get_screener_fundamentals."""
    empty = {
        "score": 0,
        "signal": "NO_DATA",
        "sector_theme": sector_theme,
        "ROCE": 0,
        "ROE": 0,
        "PE": 0,
        "sales_growth_5y": 0,
        "profit_growth_5y": 0,
        "debt_to_equity": 99,
        "promoter_holding": 0,
        "market_cap_cr": 0,
        "current_price": 0,
    }
    if not fund:
        return empty

    def _v(k, default=None):
        v = fund.get(k)
        return v if v is not None else default

    roce = _v("ROCE", 0) or 0
    roe = _v("ROE", 0) or 0
    pe = _v("Stock P/E") or _v("P/E", 0) or 0
    debt_eq = _v("Debt to Equity", 99) or 99
    prom = _v("Promoter Holding", 0) or 0
    sales_g_5 = _v("Sales Growth 5Yrs", 0) or 0
    profit_g_5 = _v("Profit Growth 5Yrs", 0) or 0
    mcap = _v("Market Cap", 0) or 0  # Cr
    px = _v("Current Price", 0) or 0
    high_52 = _v("High", 0) or 0

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
        score += 5  # micro-cap higher risk

    # Distance from 52w high
    if high_52 and px:
        pct_from_high = (px / high_52 - 1) * 100
        if pct_from_high > -10:
            score -= 10  # too close to high
        elif pct_from_high < -25:
            score += 5  # nice pullback

    if sector_theme:
        score += 10

    signal = "MULTIBAGGER_HIGH" if score >= 80 else \
             "MULTIBAGGER_MEDIUM" if score >= 60 else \
             "MULTIBAGGER_LOW" if score >= 40 else "NO"

    return {
        "score": int(score),
        "signal": signal,
        "sector_theme": sector_theme,
        "ROCE": roce,
        "ROE": roe,
        "PE": pe,
        "sales_growth_5y": sales_g_5,
        "profit_growth_5y": profit_g_5,
        "debt_to_equity": debt_eq,
        "promoter_holding": prom,
        "market_cap_cr": mcap,
        "current_price": px,
    }


def screen_multibaggers(symbols: list[str] | None = None,
                        top_n: int = 20) -> list[dict]:
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
        rows.append(r)

    rows.sort(key=lambda r: r["score"], reverse=True)
    top = rows[:top_n]
    out_path = DATA_DIR / "multibaggers.json"
    out_path.write_text(json.dumps(top, indent=2, default=str))
    logger.info("Saved -> %s", out_path)
    return top


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Limit to first 30 to keep runtime reasonable in tests
    all_syms = list(_flat_universe().keys())[:30]
    results = screen_multibaggers(all_syms, top_n=10)
    print("\n=== Top 10 multibagger candidates ===")
    for r in results:
        print(f"  {r['symbol']:14} {r['sector_theme']:18} score={r['score']:3} "
              f"{r['signal']:18} ROCE={r['ROCE']:>5} Prof5Y={r['profit_growth_5y']:>5} "
              f"D/E={r['debt_to_equity']:>5} Prom={r['promoter_holding']:>5} "
              f"MCap={r['market_cap_cr']:>9}")
