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
    from src.strategies.recommendation import recommend

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
        recommendation = recommend(r, sector_score=r.get("fundamental_score"))
        records.append({
            "symbol": r.get("symbol"), "name": r.get("name"), "sector": r.get("sector"),
            "exchange": r.get("exchange"), "price": r.get("price"),
            "fundamental_score": r.get("fundamental_score"), "tier": r.get("tier"),
            "reasons": reasons, "why": {"positives": positives, "negatives": negatives, "risks": []},
            "pillars": pillars, "metrics": metrics, "recommendation": recommendation,
            "source": source_label,
        })
    return records


def run(
    source: str = "tradingview",
    limit: int = 2000,
    out_dir: str = "site",
    mode: str = "full",
    symbols: list[str] | None = None,
    top: int | None = None,
    min_mcap: float = 1e10,
    with_movers: bool = False,
    movers_source: str = "yfinance",
    movers_sections: tuple[str, ...] = ("gainers", "losers", "most_active"),
    movers_top: int = 10,
    with_nifty50: bool = False,
    with_recommendations: bool = False,
    rec_top: int = 300,
    rec_min_mcap: float = 1e10,
    ai_budget: int | None = None,
    ml_budget: int | None = None,
    rec_moneycontrol: bool = False,
) -> dict:
    """Build the site from the chosen data source.

    source='demo'        -> offline sample (no network, always legal/public).
    source='yfinance'    -> FREE, no key, full fundamentals (Yahoo Finance; educational).
    source='tradingview' -> full universe via the TV scanner, PERSONAL research only.
                            With ``top``, ranks the WHOLE market and keeps the best N.
    source='hybrid'      -> rank the FULL market via TradingView, then fetch the top N
                            from Yahoo Finance for compliant public display ("top picks
                            from all stocks"). Recommended for the public site.
    source='twelvedata'  -> licensed, COMPLIANT for a public site (needs API key).

    ``min_mcap`` (INR) floors the ranked universe to exclude penny/micro-cap noise.

    ``with_movers`` adds a Top Gainers / Losers / Most-Active section (TradingView
    technicals + ``movers_source`` fundamentals). Use ``movers_source='yfinance'`` for the
    public build (CI-safe); ``'both'``/``'auto'`` add Screener.in for personal research.

    ``with_recommendations`` adds the AI-powered Buy/Hold/Sell tabs + the Search tab (exhaustive
    deep-dive over ``rec_top`` stocks; ``ai_budget``/``ml_budget`` ``None`` = unlimited).
    """
    if source == "demo":
        from scripts.build_site import (_demo_movers, _demo_records, _demo_recommendations,
                                        _demo_search_index)
        reco = _demo_recommendations() if with_recommendations else None
        return build_site(_demo_records(), out_dir=out_dir,
                          movers=_demo_movers() if with_movers else None,
                          extra_lists={"Nifty 50": _demo_records()} if with_nifty50 else None,
                          recommendations=reco,
                          search_index=_demo_search_index(reco) if reco else None)

    movers = _maybe_build_movers(with_movers, movers_source, movers_sections, movers_top)
    extra_lists = _maybe_build_nifty50(with_nifty50)
    reco = _maybe_build_recommendations(with_recommendations, rec_top, rec_min_mcap,
                                        ai_budget, ml_budget, rec_moneycontrol)
    reco_kw = {"recommendations": reco, "search_index": reco["search_index"],
               "deep_by_symbol": reco["deep_by_symbol"]} if reco else {}

    if source == "twelvedata":
        from src.ingestion.twelvedata_provider import build_dataset
        logger.info("Fetching Twelve Data (mode=%s)...", mode)
        frame = build_dataset(symbols=symbols, mode=mode)
        scored = fa.fundamental_scores(frame, sector_col="sector")
        return build_site(frame_to_records(scored, "Twelve Data (licensed)"), out_dir=out_dir,
                          movers=movers, extra_lists=extra_lists, **reco_kw)

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
        return build_site(records, out_dir=out_dir, movers=movers, extra_lists=extra_lists, **reco_kw)

    if source == "hybrid":
        top_syms = _rank_full_market_via_tradingview(top or 50, min_mcap)
        if not top_syms:
            from src.ingestion.twelvedata_provider import NIFTY_50_SYMBOLS
            top_syms = NIFTY_50_SYMBOLS
            logger.warning("TradingView ranking unavailable; falling back to Nifty 50")
        from src.ingestion.yfinance_provider import build_dataset
        logger.info("Fetching %d top picks from Yahoo Finance...", len(top_syms))
        frame = build_dataset(symbols=top_syms, mode="full")
        if "price" in frame.columns:  # drop names Yahoo couldn't resolve (SME 404s)
            frame = frame[frame["price"].notna()]
        scored = fa.fundamental_scores(frame, sector_col="sector")
        records = frame_to_records(
            scored, "Yahoo Finance — top picks screened from the full Indian market")
        if not records:
            from scripts.build_site import _demo_records
            records = _demo_records()
        return build_site(records, out_dir=out_dir, movers=movers, extra_lists=extra_lists, **reco_kw)

    # tradingview (personal research)
    from src.ingestion.tradingview_scanner import fetch_india_scanner
    if top:
        logger.info("Ranking the full market (mcap>=%.0f) -> top %d...", min_mcap, top)
        scanner_df = fetch_india_scanner(limit=10000, market_cap_min=min_mcap)
        frame = _apply_quality_gate(scanner_to_frame(scanner_df))
        scored = fa.fundamental_scores(frame, sector_col="sector")
        scored = scored.sort_values("fundamental_score", ascending=False).head(top)
    else:
        logger.info("Fetching India scanner (limit=%d)...", limit)
        scanner_df = fetch_india_scanner(limit=limit)
        frame = scanner_to_frame(scanner_df)
        scored = fa.fundamental_scores(frame, sector_col="sector")
    return build_site(frame_to_records(scored), out_dir=out_dir, movers=movers,
                      extra_lists=extra_lists, **reco_kw)


def _maybe_build_recommendations(enabled: bool, top: int, min_mcap: float,
                                 ai_budget: int | None, ml_budget: int | None,
                                 use_moneycontrol: bool) -> dict | None:
    """Build the exhaustive AI Buy/Hold/Sell + search payload (best-effort; never fatal)."""
    if not enabled:
        return None
    try:
        from scripts.recommendations import build as build_reco
        reco = build_reco(top=top, min_mcap=min_mcap, ai_budget=ai_budget,
                          ml_budget=ml_budget, use_moneycontrol=use_moneycontrol)
        if reco.get("buckets"):
            logger.info("Recommendations: %d analysed (%d via AI) -> Buy %d / Hold %d / Sell %d",
                        reco["universe_count"], reco.get("ai_analysed", 0),
                        len(reco["buckets"]["BUY"]), len(reco["buckets"]["HOLD"]),
                        len(reco["buckets"]["SELL"]))
            return reco
        logger.warning("Recommendations requested but none produced; skipping.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recommendations build failed (%s); site builds without them.", exc)
    return None


def _maybe_build_nifty50(with_nifty50: bool) -> dict | None:
    """Build a Nifty-50 named list (Yahoo Finance, CI-safe). Best-effort; never fatal."""
    if not with_nifty50:
        return None
    try:
        from src.ingestion.twelvedata_provider import NIFTY_50_SYMBOLS
        from src.ingestion.universe_fetch import nifty_index_symbols
        from src.ingestion.yfinance_provider import build_dataset
        syms = nifty_index_symbols("nifty50", fallback=NIFTY_50_SYMBOLS)
        logger.info("Building Nifty 50 list for %d symbols...", len(syms))
        frame = build_dataset(symbols=syms, mode="full")
        if "price" in frame.columns:
            frame = frame[frame["price"].notna()]
        scored = fa.fundamental_scores(frame, sector_col="sector")
        recs = frame_to_records(scored, "Yahoo Finance — Nifty 50 constituents (educational)")
        if recs:
            return {"Nifty 50": recs}
        logger.warning("Nifty 50 list empty; skipping.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nifty 50 list build failed (%s); site builds without it.", exc)
    return None


def _maybe_build_movers(with_movers: bool, source: str,
                        sections: tuple[str, ...], top: int) -> dict | None:
    """Build the market-mover sections (best-effort; never fatal to the site build)."""
    if not with_movers:
        return None
    try:
        from scripts.movers_analysis import build_section_records
        movers = build_section_records(
            sections=sections, top_each=max(top + 5, 12), source=source, max_per=top)
        if movers.get("sections"):
            return movers
        logger.warning("Movers requested but no sections returned; skipping movers page.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Movers build failed (%s); site will build without movers.", exc)
    return None


def _apply_quality_gate(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only profitable, sanely-valued, not-over-levered, data-complete names before
    ranking — so 'top picks' surface quality companies, not penny/SME data-glitch outliers."""
    if frame.empty:
        return frame
    f = frame.copy()

    def num(c):
        return pd.to_numeric(f[c], errors="coerce") if c in f.columns else pd.Series(float("nan"), index=f.index)

    pe, roe, de = num("pe"), num("roe"), num("debt_to_equity")
    keep = pe.between(3, 90) & (roe > 8) & ((de < 3) | de.isna())
    gated = f[keep]
    return gated if len(gated) >= 20 else f  # don't over-filter a tiny universe


def _rank_full_market_via_tradingview(top: int, min_mcap: float) -> list[str]:
    """Rank the entire TradingView India universe by sector-relative fundamental score."""
    try:
        from src.ingestion.tradingview_scanner import fetch_india_scanner
        scanner_df = fetch_india_scanner(limit=10000, market_cap_min=min_mcap)
        frame = _apply_quality_gate(scanner_to_frame(scanner_df))
        ranked = (fa.fundamental_scores(frame, sector_col="sector")
                  .sort_values("fundamental_score", ascending=False))
        syms = ranked["symbol"].dropna().astype(str).head(top).tolist()
        logger.info("TradingView ranked %d quality stocks -> top %d", len(frame), len(syms))
        return syms
    except Exception as exc:  # noqa: BLE001
        logger.warning("Full-market ranking failed: %s", exc)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["demo", "yfinance", "tradingview", "twelvedata", "hybrid"],
                    default="tradingview")
    ap.add_argument("--mode", choices=["full", "quotes"], default="full",
                    help="twelvedata only: 'full' refreshes fundamentals, 'quotes' only prices")
    ap.add_argument("--limit", type=int, default=2000, help="tradingview only: max symbols")
    ap.add_argument("--top", type=int, default=None,
                    help="rank the WHOLE market and keep only the best N (tradingview/hybrid)")
    ap.add_argument("--min-mcap", type=float, default=1e10,
                    help="min market cap (INR) for the ranked universe (default ~Rs 1,000 Cr)")
    ap.add_argument("--out", default="site")
    ap.add_argument("--demo", action="store_true", help="alias for --source demo")
    ap.add_argument("--with-movers", action="store_true",
                    help="add a Top Gainers/Losers/Most-Active section to the site")
    ap.add_argument("--movers-source", default="yfinance",
                    choices=["yfinance", "screener", "both", "auto", "none"],
                    help="fundamentals for movers: yfinance (CI-safe) / screener/both/auto (personal)")
    ap.add_argument("--movers-top", type=int, default=10, help="stocks per movers section")
    ap.add_argument("--with-nifty50", action="store_true",
                    help="also build a dedicated Nifty 50 page alongside the all-market Top 50")
    ap.add_argument("--with-recommendations", action="store_true",
                    help="add AI Buy/Hold/Sell tabs + Search tab (exhaustive deep-dive)")
    ap.add_argument("--rec-top", type=int, default=300,
                    help="recommendations universe size (ranked by fundamentals)")
    ap.add_argument("--rec-min-mcap", type=float, default=1e10)
    ap.add_argument("--ai-budget", type=int, default=None,
                    help="cap NEW LLM analyses per run (default: unlimited/exhaustive; cache resumes)")
    ap.add_argument("--ml-budget", type=int, default=None,
                    help="cap ML forecasts per run (default: unlimited)")
    ap.add_argument("--no-ml", action="store_true", help="skip ML forecasts in recommendations")
    ap.add_argument("--rec-moneycontrol", action="store_true",
                    help="add Moneycontrol broker buy/sell/hold %% to recommendations (personal)")
    args = ap.parse_args()
    src = "demo" if args.demo else args.source
    res = run(source=src, limit=args.limit, out_dir=args.out, mode=args.mode,
              top=args.top, min_mcap=args.min_mcap, with_movers=args.with_movers,
              movers_source=args.movers_source, movers_top=args.movers_top,
              with_nifty50=args.with_nifty50, with_recommendations=args.with_recommendations,
              rec_top=args.rec_top, rec_min_mcap=args.rec_min_mcap, ai_budget=args.ai_budget,
              ml_budget=(0 if args.no_ml else args.ml_budget),
              rec_moneycontrol=args.rec_moneycontrol)
    extras = []
    if res.get("has_movers"):
        extras.append("movers")
    if res.get("has_reco"):
        extras.append("recommendations")
    if res.get("has_search"):
        extras.append("search")
    extras += res.get("lists", [])
    print(f"Built {res['pages']} pages -> {res['index']}"
          + (f"  (+{', '.join(extras)})" if extras else ""))
