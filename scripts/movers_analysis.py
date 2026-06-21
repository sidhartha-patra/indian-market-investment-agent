"""Market-movers analysis: fetch ALL TradingView India collections, run per-stock
fundamental analysis (Screener.in), and project high/low returns across short/mid/long term.

Pipeline:
1. Fetch every market-mover collection (gainers, losers, most active, most volatile,
   1y top/worst performers, high dividend, large cap, overbought, oversold).
2. Dedupe to a symbol set, tagging which collections each stock appears in.
3. For the most-featured symbols, pull Screener.in fundamentals, merge with the
   TradingView technicals, and run the recommendation engine (verdict + horizon
   projections with Low/Base/High return scenarios).
4. Write a markdown + JSON report.

Usage:
    python -m scripts.movers_analysis --top-each 30 --max 40
    python -m scripts.movers_analysis --no-screener        # technicals-only (faster)

> ⚠️ PERSONAL research only — TradingView and Screener.in ToS forbid public redistribution.
> Outputs are educational scenarios, NOT investment advice or profit predictions.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _merge_metrics(tv_row: dict, screener_metrics: dict | None) -> dict:
    """Merge a TradingView collection row (technicals) with Screener fundamentals."""
    m = dict(screener_metrics or {})
    m["symbol"] = tv_row.get("symbol")
    m["name"] = tv_row.get("name") or m.get("name") or tv_row.get("symbol")
    m["sector"] = tv_row.get("sector") or m.get("sector") or "OTHER"
    m["price"] = _num(tv_row.get("close")) or m.get("price")
    m["high_52w"] = _num(tv_row.get("price_52_week_high"))
    m["low_52w"] = _num(tv_row.get("price_52_week_low"))
    m["change_today_pct"] = round(_num(tv_row.get("change")), 1) if _num(tv_row.get("change")) is not None else None
    vd = _num(tv_row.get("Volatility.D"))
    if vd is not None:
        m["ann_vol_pct"] = round(vd * (252 ** 0.5), 1)  # daily vol % -> annualised
    return m


def analyze(top_each: int = 30, max_symbols: int = 40, use_screener: bool = True,
            screener_delay: float = 0.6) -> list[dict]:
    """Fetch all collections and produce per-stock recommendations + projections."""
    from src.ingestion.tradingview_scanner import fetch_all_collections
    from src.strategies import recommendation as rec

    collections = fetch_all_collections(top_each=top_each)

    membership: dict[str, dict] = {}
    for name, df in collections.items():
        if df is None or df.empty:
            continue
        for r in df.to_dict("records"):
            sym = r.get("symbol")
            if not sym:
                continue
            membership.setdefault(sym, {"collections": [], "tv": r})
            membership[sym]["collections"].append(name)

    # Analyse the most-featured names first (appearing across many collections = noteworthy).
    syms = sorted(membership, key=lambda s: len(membership[s]["collections"]), reverse=True)[:max_symbols]
    logger.info("Analysing %d symbols (of %d unique across collections)...", len(syms), len(membership))

    results = []
    for sym in syms:
        tv = membership[sym]["tv"]
        screener_m: dict = {}
        if use_screener:
            try:
                from src.ingestion.screener import get_screener_fundamentals
                from src.strategies.fundamental_analysis import normalize_screener
                screener_m = normalize_screener(get_screener_fundamentals(sym) or {})
                time.sleep(screener_delay)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Screener fetch failed for %s: %s", sym, exc)
        metrics = _merge_metrics(tv, screener_m)
        r = rec.recommend(metrics, sector_score=None)
        results.append({
            "symbol": sym,
            "name": metrics.get("name"),
            "sector": metrics.get("sector"),
            "collections": membership[sym]["collections"],
            "change_today_pct": metrics.get("change_today_pct"),
            "verdict": r["verdict"], "verdict_label": r["verdict_label"],
            "conviction": r["conviction"], "confidence": r["confidence"],
            "horizons": r["horizons"], "red_flags": r["red_flags"],
            "fundamentals_source": "screener.in" if screener_m else "tradingview-only",
        })
    results.sort(key=lambda x: x["conviction"], reverse=True)
    return results


def to_markdown(results: list[dict]) -> str:
    lines = ["# Market Movers — fundamental analysis & return projections",
             "\n> Educational scenarios, NOT advice or profit predictions. Personal research only.\n"]
    for r in results:
        h = r["horizons"]
        lines.append(f"## {r['symbol']} — {r['verdict_label']}  ·  conviction {r['conviction']}/100")
        lines.append(f"*{r.get('name')} · {r.get('sector')} · in collections: "
                     f"{', '.join(r['collections'])} · today {r.get('change_today_pct')}%*")
        if r["red_flags"]:
            lines.append("🚩 " + "; ".join(r["red_flags"]))
        lines.append("\n| Horizon | Stance | Low | Base | High |")
        lines.append("|---|---|---:|---:|---:|")
        for key, label in (("short_term", "Short"), ("mid_term", "Mid"), ("long_term", "Long")):
            hz = h.get(key, {})
            p = hz.get("projection") or {}
            lines.append(f"| {label} ({hz.get('horizon')}) | {hz.get('stance')} | "
                         f"{p.get('low_pct', '—')}% | {p.get('base_pct', '—')}% | {p.get('high_pct', '—')}% |")
        lines.append("")
    return "\n".join(lines)


def run(top_each: int = 30, max_symbols: int = 40, use_screener: bool = True,
        out_dir: str = "reports") -> dict:
    results = analyze(top_each=top_each, max_symbols=max_symbols, use_screener=use_screener)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "movers_analysis.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / "movers_analysis.md").write_text(to_markdown(results), encoding="utf-8")
    logger.info("Wrote %d analyses -> %s", len(results), out.resolve())
    return {"count": len(results), "json": str((out / "movers_analysis.json").resolve()),
            "markdown": str((out / "movers_analysis.md").resolve())}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-each", type=int, default=30, help="rows per collection")
    ap.add_argument("--max", type=int, default=40, help="max symbols to deep-analyse")
    ap.add_argument("--no-screener", action="store_true", help="skip Screener.in (technicals only)")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    res = run(top_each=args.top_each, max_symbols=args.max,
              use_screener=not args.no_screener, out_dir=args.out)
    print(f"Analysed {res['count']} movers -> {res['markdown']}")
