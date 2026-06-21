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
    if _num(tv_row.get("price_52_week_high")) is not None:
        m["high_52w"] = _num(tv_row.get("price_52_week_high"))
    if _num(tv_row.get("price_52_week_low")) is not None:
        m["low_52w"] = _num(tv_row.get("price_52_week_low"))
    m["change_today_pct"] = round(_num(tv_row.get("change")), 1) if _num(tv_row.get("change")) is not None else None
    vd = _num(tv_row.get("Volatility.D"))
    if vd is not None:
        m["ann_vol_pct"] = round(vd * (252 ** 0.5), 1)  # daily vol % -> annualised
    return m


def _yf_enrich(yf_ticker: str) -> dict:
    """Best-effort Yahoo Finance fundamentals for a ticker (FREE, no key, CI-safe)."""
    try:
        import yfinance as yf

        from src.ingestion.yfinance_provider import map_info
        info = yf.Ticker(yf_ticker).info or {}
        return {k: v for k, v in map_info(info).items() if v is not None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo enrich failed for %s: %s", yf_ticker, exc)
        return {}


def _screener_enrich(symbol: str, delay: float = 0.6) -> dict:
    """Best-effort Screener.in fundamentals (PERSONAL research only — keep off public CI)."""
    try:
        from src.ingestion.screener import get_screener_fundamentals
        from src.strategies.fundamental_analysis import normalize_screener
        m = normalize_screener(get_screener_fundamentals(symbol) or {})
        time.sleep(delay)
        return m
    except Exception as exc:  # noqa: BLE001
        logger.warning("Screener enrich failed for %s: %s", symbol, exc)
        return {}


def _enriched_metrics(tv_row: dict, source: str, screener_delay: float = 0.6) -> tuple[dict, str]:
    """Merge fundamentals (Screener and/or Yahoo) UNDER TradingView technicals.

    ``source``:
      * ``yfinance`` — Yahoo only (CI-safe, publishable).
      * ``screener`` — Screener.in only (personal use).
      * ``both``     — Screener + Yahoo (Screener wins; Yahoo fills gaps).
      * ``auto``     — Screener, falling back to Yahoo when Screener is empty.
      * ``none``     — TradingView technicals only.
    Returns ``(metrics, fundamentals_source_label)``.
    """
    sym = str(tv_row.get("symbol") or "")
    yf_ticker = tv_row.get("yf_ticker") or f"{sym}.NS"
    fund: dict = {}
    used: list[str] = []

    if source in ("screener", "both", "auto"):
        scr = _screener_enrich(sym, screener_delay)
        if scr:
            fund.update(scr)
            used.append("Screener.in")

    want_yahoo = source in ("yfinance", "both") or (source == "auto" and not fund)
    if want_yahoo:
        yfm = _yf_enrich(yf_ticker)
        for k, v in yfm.items():
            fund.setdefault(k, v)  # don't clobber Screener values
        if yfm:
            used.append("Yahoo Finance")

    metrics = _merge_metrics(tv_row, fund)  # TV technicals authoritative on top
    return metrics, (" + ".join(used) if used else "TradingView technicals only")


# Display titles/icons for the website movers sections.
SECTION_TITLES: dict[str, tuple[str, str]] = {
    "gainers": ("Top Gainers", "📈"),
    "losers": ("Top Losers", "📉"),
    "most_active": ("Most Active", "🔥"),
    "most_volatile": ("Most Volatile", "🎢"),
    "high_dividend": ("High Dividend", "💰"),
    "top_performers_1y": ("1-Year Leaders", "🏆"),
    "oversold": ("Oversold (RSI<30)", "🧲"),
    "overbought": ("Overbought (RSI>70)", "🌡️"),
}


def build_section_records(
    sections: tuple[str, ...] = ("gainers", "losers", "most_active"),
    top_each: int = 15,
    source: str = "auto",
    max_per: int = 10,
    screener_delay: float = 0.6,
) -> dict:
    """Build website-ready market-mover records grouped by TradingView collection.

    Each record is :func:`scripts.build_site.build_site`-compatible (symbol / name /
    sector / price / metrics / recommendation / why) and additionally carries
    ``change_today_pct`` + ``collection``. Returns
    ``{"generated_at": ..., "sections": {name: [record, ...]}}``.

    Resilient: a failing collection or stock is skipped, never fatal.
    """
    from datetime import datetime, timezone

    from src.ingestion.tradingview_scanner import fetch_collection
    from src.strategies import recommendation as rec

    out_sections: dict[str, list[dict]] = {}
    for name in sections:
        try:
            df = fetch_collection(name, top=top_each)
        except Exception as exc:  # noqa: BLE001
            logger.warning("collection %s failed: %s", name, exc)
            continue
        if df is None or df.empty:
            continue

        recs: list[dict] = []
        for tv_row in df.to_dict("records")[:max_per]:
            sym = tv_row.get("symbol")
            if not sym:
                continue
            try:
                metrics, fsrc = _enriched_metrics(tv_row, source, screener_delay)
                r = rec.recommend(metrics, sector_score=None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("recommend failed for %s: %s", sym, exc)
                continue
            recs.append({
                "symbol": sym,
                "name": metrics.get("name"),
                "sector": metrics.get("sector"),
                "exchange": tv_row.get("tv_exchange") or "NSE",
                "price": metrics.get("price"),
                "fundamental_score": None,
                "tier": None,
                "change_today_pct": metrics.get("change_today_pct"),
                "collection": name,
                "collections": [name],
                "metrics": metrics,
                "recommendation": r,
                "why": {"positives": r.get("positives", []),
                        "negatives": r.get("negatives", []),
                        "risks": r.get("red_flags", [])},
                "fundamentals_source": fsrc,
                "source": f"TradingView technicals + {fsrc}",
            })
        if recs:
            out_sections[name] = recs
        logger.info("section %-14s -> %d records", name, len(recs))

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "sections": out_sections,
    }


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
