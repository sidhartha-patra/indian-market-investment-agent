"""End-to-end: pull all-India fundamentals -> sector-relative scores -> shareable site.

Pipeline:
1. Fetch the full India universe fundamentals+technicals from the TradingView scanner.
2. Normalise to canonical metric keys.
3. Compute an explainable, **sector-relative** 0-100 fundamental score per stock.
4. Render per-stock explanation records and build the static website.

Usage:
    python -m scripts.build_all_stocks                 # live (personal research)
    python -m scripts.build_all_stocks --limit 500     # top-500 by mcap
    python -m scripts.build_all_stocks --demo          # offline demo (no network)

> ⚠️ Live mode reads TradingView data, permissible for PERSONAL research only. Do NOT
> publish TradingView-sourced values — swap in a licensed vendor before deploying the
> generated site publicly. See docs/DESIGN.md §Legal.
"""
from __future__ import annotations

import argparse
import logging

import pandas as pd

from scripts.build_site import build_site
from src.strategies import fundamental_analysis as fa

logger = logging.getLogger(__name__)

_METRIC_KEYS = [
    "pe", "pb", "ps", "ev_ebitda", "roe", "roa", "roic", "opm", "net_margin",
    "gross_margin", "debt_to_equity", "current_ratio", "quick_ratio", "fcf",
    "fcf_yield", "earnings_yield", "eps", "dividend_yield", "eps_growth_3y",
    "market_cap",
]


def scanner_to_frame(scanner_df: pd.DataFrame) -> pd.DataFrame:
    """Convert a TradingView scanner DataFrame to a canonical-metric DataFrame."""
    rows = []
    for r in scanner_df.to_dict("records"):
        canon = fa.normalize_tradingview(r)
        canon.update({
            "symbol": r.get("symbol"),
            "name": r.get("name") or r.get("symbol"),
            "sector": r.get("sector") or "OTHER",
            "exchange": r.get("tv_exchange") or "NSE",
            "price": r.get("close"),
            "market_cap_cr": (fa._num(r.get("market_cap_basic")) or 0) / 1e7  # USD->~Cr rough
            if r.get("market_cap_basic") else None,
        })
        rows.append(canon)
    df = pd.DataFrame(rows)
    for k in _METRIC_KEYS:
        if k not in df.columns:
            df[k] = None
    return df


def frame_to_records(scored: pd.DataFrame,
                     source_label: str = "TradingView (personal research)") -> list[dict]:
    """Build per-stock website records from a scored DataFrame."""
    pillar_cols = [c for c in scored.columns if c.startswith("z_")]
    records = []
    for r in scored.to_dict("records"):
        pillars = {c[2:]: r[c] for c in pillar_cols if pd.notna(r.get(c))}
        reasons = r.get("reasons") or []
        positives = [x for x in reasons if x.startswith("strong")]
        negatives = [x for x in reasons if x.startswith("weak")]
        metrics = {k: r.get(k) for k in
                   ("pe", "pb", "ev_ebitda", "roe", "roce", "roa", "opm", "net_margin",
                    "revenue_growth_5y", "profit_growth_5y", "debt_to_equity", "current_ratio",
                    "fcf_yield", "dividend_yield", "promoter_holding", "pledged_pct", "market_cap_cr")
                   if r.get(k) is not None}
        records.append({
            "symbol": r.get("symbol"), "name": r.get("name"), "sector": r.get("sector"),
            "exchange": r.get("exchange"), "price": r.get("price"),
            "fundamental_score": r.get("fundamental_score"), "tier": r.get("tier"),
            "reasons": reasons, "why": {"positives": positives, "negatives": negatives, "risks": []},
            "pillars": pillars, "metrics": metrics,
            "source": source_label,
        })
    return records


def run(
    source: str = "tradingview",
    limit: int = 2000,
    out_dir: str = "site",
    mode: str = "full",
    symbols: list[str] | None = None,
) -> dict:
    """Build the site from the chosen data source.

    source='demo'        -> offline sample (no network, always legal/public).
    source='yfinance'    -> FREE, no key, full fundamentals for all NSE/BSE stocks
                            (Yahoo Finance; educational/personal use — see docs/DESIGN.md).
    source='tradingview' -> full universe, PERSONAL research only (not for public).
    source='twelvedata'  -> licensed, COMPLIANT for a public site (needs API key).
                            mode='quotes' refreshes only prices (cheap, hourly);
                            mode='full' refreshes fundamentals too (daily).
    """
    if source == "demo":
        from scripts.build_site import _demo_records
        return build_site(_demo_records(), out_dir=out_dir)

    if source == "twelvedata":
        from src.ingestion.twelvedata_provider import build_dataset
        logger.info("Fetching Twelve Data (mode=%s)...", mode)
        frame = build_dataset(symbols=symbols, mode=mode)
        scored = fa.fundamental_scores(frame, sector_col="sector")
        return build_site(frame_to_records(scored, "Twelve Data (licensed)"), out_dir=out_dir)

    if source == "yfinance":
        from src.ingestion.twelvedata_provider import NIFTY_50_SYMBOLS
        from src.ingestion.yfinance_provider import build_dataset
        if symbols is None:
            from src.ingestion.universe_fetch import nifty_index_symbols
            symbols = nifty_index_symbols("nifty200", fallback=NIFTY_50_SYMBOLS)
        logger.info("Fetching Yahoo Finance for %d symbols (mode=%s)...", len(symbols), mode)
        frame = build_dataset(symbols=symbols, mode=mode)
        scored = fa.fundamental_scores(frame, sector_col="sector")
        records = frame_to_records(scored, "Yahoo Finance (educational; not for commercial use)")
        if not records:  # CI resilience: never deploy an empty site
            from scripts.build_site import _demo_records
            records = _demo_records()
        return build_site(records, out_dir=out_dir)

    # default: tradingview (personal research)
    from src.ingestion.tradingview_scanner import fetch_india_scanner
    logger.info("Fetching India scanner (limit=%d)...", limit)
    scanner_df = fetch_india_scanner(limit=limit)
    frame = scanner_to_frame(scanner_df)
    logger.info("Scoring %d stocks sector-relative...", len(frame))
    scored = fa.fundamental_scores(frame, sector_col="sector")
    return build_site(frame_to_records(scored), out_dir=out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["demo", "yfinance", "tradingview", "twelvedata"],
                    default="tradingview")
    ap.add_argument("--mode", choices=["full", "quotes"], default="full",
                    help="twelvedata only: 'full' refreshes fundamentals, 'quotes' only prices")
    ap.add_argument("--limit", type=int, default=2000, help="tradingview only: max symbols")
    ap.add_argument("--out", default="site")
    ap.add_argument("--demo", action="store_true", help="alias for --source demo")
    args = ap.parse_args()
    src = "demo" if args.demo else args.source
    res = run(source=src, limit=args.limit, out_dir=args.out, mode=args.mode)
    print(f"Built {res['pages']} pages -> {res['index']}")
