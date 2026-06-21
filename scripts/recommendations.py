"""Orchestrator for the Recommendations + Search surfaces.

Builds a broad universe (TradingView bulk scan ranked by sector-relative fundamentals),
then runs the per-stock :func:`deep_dive` — multi-source cross-validation + Gen-AI analyst
+ analyst/broker consensus + conformal ML forecast + news — to produce a composite
Buy/Hold/Sell call for each name. Outputs the top-N per bucket and a full search index.

Accuracy over speed (per project policy): deep analysis runs across the universe, ordered
by signal strength, bounded only by ``ai_budget`` / ``ml_budget`` per run. The on-disk AI
cache (``data/ai_cache``) makes it **resumable** — successive runs fill and refresh coverage
without repeating work, and a single local run with large budgets covers everything.

> ⚠️ Universe metrics come from the TradingView scanner (personal-research ToS). For a fully
> redistributable public product, swap the universe source to a licensed vendor.
"""
from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def _records_from_tradingview(top: int, min_mcap: float) -> list[dict]:
    """Rank the whole market by sector-relative fundamentals -> top-N website records."""
    from scripts.build_all_stocks import (_apply_quality_gate, frame_to_records,
                                          scanner_to_frame)
    from src.ingestion.tradingview_scanner import fetch_india_scanner
    from src.strategies import fundamental_analysis as fa

    scanner_df = fetch_india_scanner(limit=10000, market_cap_min=min_mcap)
    frame = _apply_quality_gate(scanner_to_frame(scanner_df))
    scored = fa.fundamental_scores(frame, sector_col="sector")
    scored = scored.sort_values("fundamental_score", ascending=False).head(top)
    return frame_to_records(scored, "TradingView (ranked) + Yahoo/Screener enrichment")


def _light_deep(rec_record: dict) -> dict:
    """Fundamental-only composite for stocks we don't deep-enrich this run."""
    from src.strategies import composite as comp
    rec = rec_record.get("recommendation") or {}
    c = comp.composite({"recommendation": rec})
    c["buy_case"] = rec.get("positives", [])
    c["sell_case"] = (rec.get("negatives", []) or []) + (rec.get("red_flags", []) or [])
    return {
        "symbol": rec_record.get("symbol"), "name": rec_record.get("name"),
        "sector": rec_record.get("sector"), "exchange": rec_record.get("exchange"),
        "price": rec_record.get("price"), "metrics": rec_record.get("metrics"),
        "recommendation": rec, "composite": c, "ai": None, "analyst_consensus": None,
        "ml_forecast": None, "news": None, "data_quality": None,
    }


def _fundamental_lean(rec: dict) -> float:
    from src.strategies.composite import _fundamental_lean as fl
    return fl(rec) or 0.0


def build(top: int = 300, min_mcap: float = 1e10, ai_budget: int | None = None,
          ml_budget: int | None = None, use_moneycontrol: bool = False, llm_news: bool = True,
          top_each: int = 10, use_news: bool = True, use_analyst: bool = True,
          records: list[dict] | None = None) -> dict:
    """Build Buy/Hold/Sell buckets + a search index for the universe.

    By default this is **exhaustive**: every stock gets the full deep-dive (Gen-AI analyst +
    ML forecast + analyst consensus + news + cross-validation). ``ai_budget`` / ``ml_budget``
    are optional caps (``None`` = unlimited); the on-disk cache makes runs resumable so even a
    capped run accumulates full coverage over time.
    """
    from src.ai import llm
    from src.strategies.deep_research import deep_dive

    if records is None:
        records = _records_from_tradingview(top, min_mcap)
    n = len(records)
    logger.info("Universe: %d stocks (exhaustive=%s)", n, ai_budget is None and ml_budget is None)

    # Enrich the strongest signals first so even an interrupted run surfaces the best ideas.
    records.sort(key=lambda r: abs(_fundamental_lean(r.get("recommendation") or {})), reverse=True)
    ai_on = llm.is_available()
    provider = llm.provider_info().get("label", "deterministic")

    deeps: list[dict] = []
    ai_used = ml_used = 0
    for i, rec in enumerate(records):
        sym = str(rec.get("symbol") or "").strip()
        if not sym:
            continue
        do_ai = ai_on and (ai_budget is None or ai_used < ai_budget)
        do_ml = (ml_budget is None or ml_used < ml_budget)
        if do_ai or do_ml:
            try:
                d = deep_dive(sym, dict(rec.get("metrics") or {}),
                              sector_score=rec.get("fundamental_score"),
                              use_ai=do_ai, use_ml=do_ml, use_news=use_news,
                              use_analyst=use_analyst, use_moneycontrol=use_moneycontrol,
                              llm_news=(llm_news and do_ai))
                d.setdefault("name", rec.get("name"))
                d.setdefault("sector", rec.get("sector"))
                d["exchange"] = rec.get("exchange") or "NSE"
                if (d.get("ai") or {}).get("source") == "ai":
                    ai_used += 1
                if d.get("ml_forecast"):
                    ml_used += 1
                deeps.append(d)
                if (i + 1) % 10 == 0 or (i + 1) == n:
                    logger.info("…analysed %d/%d (ai=%d ml=%d)", i + 1, n, ai_used, ml_used)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("deep_dive failed for %s: %s", sym, exc)
        deeps.append(_light_deep(rec))

    buckets: dict[str, list[dict]] = {"BUY": [], "HOLD": [], "SELL": []}
    for d in deeps:
        buckets[(d.get("composite") or {}).get("group", "HOLD")].append(d)
    for g in buckets:
        buckets[g].sort(key=lambda d: (d.get("composite") or {}).get("score", 50),
                        reverse=(g != "SELL"))
    top_buckets = {g: buckets[g][:top_each] for g in buckets}

    from scripts.build_site import _search_entry
    search_index = sorted((_search_entry(d) for d in deeps),
                          key=lambda e: e.get("s") or "")
    deep_by_symbol = {str(d["symbol"]).upper(): d for d in deeps if d.get("symbol")}

    from datetime import datetime, timezone
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "provider": provider, "universe_count": len(deeps),
        "ai_analysed": ai_used, "ml_analysed": ml_used,
        "buckets": top_buckets, "search_index": search_index, "deep_by_symbol": deep_by_symbol,
    }


def run(top: int = 300, out_dir: str = "site", **kw) -> dict:
    """Build the buckets and render a standalone Recommendations + Search site."""
    from scripts.build_site import build_site, _demo_records
    res = build(top=top, **kw)
    records = _demo_records()  # minimal home table; the value is in the reco/search pages
    out = build_site(records, out_dir=out_dir, recommendations=res,
                     search_index=res["search_index"], deep_by_symbol=res["deep_by_symbol"])
    out.update({"ai_analysed": res["ai_analysed"], "universe": res["universe_count"]})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300, help="universe size (ranked by fundamentals)")
    ap.add_argument("--min-mcap", type=float, default=1e10)
    ap.add_argument("--ai-budget", type=int, default=None,
                    help="cap NEW LLM analyses this run (default: unlimited/exhaustive; cache resumes)")
    ap.add_argument("--ml-budget", type=int, default=None,
                    help="cap ML forecasts this run (default: unlimited/exhaustive)")
    ap.add_argument("--top-each", type=int, default=10)
    ap.add_argument("--moneycontrol", action="store_true", help="add Moneycontrol broker %% (personal)")
    ap.add_argument("--no-ml", action="store_true", help="skip ML forecasts (faster)")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    res = run(top=args.top, min_mcap=args.min_mcap, ai_budget=args.ai_budget,
              ml_budget=(0 if args.no_ml else args.ml_budget), top_each=args.top_each,
              use_moneycontrol=args.moneycontrol, out_dir=args.out)
    print(f"Built recommendations ({res.get('universe')} stocks, {res.get('ai_analysed')} AI) -> {res['index']}")
