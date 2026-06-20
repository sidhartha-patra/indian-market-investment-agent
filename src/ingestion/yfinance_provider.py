"""yfinance provider — FREE, no-API-key fundamentals + prices for ALL NSE/BSE stocks.

The practical zero-cost data source: Yahoo Finance (via the ``yfinance`` library) has
excellent India coverage (``.NS`` for NSE, ``.BO`` for BSE) and returns a full
fundamental snapshot (P/E, P/B, ROE, ROA, margins, D/E, current ratio, dividend yield,
sector, growth, …) plus the latest price — for every listed name, with no key.

Mirrors ``twelvedata_provider.build_dataset`` so it drops into ``scripts.build_all_stocks``
as ``--source yfinance``.

> ⚖️ **Legal:** Yahoo's ToS restricts redistribution; this is a **grey area for public
> sites** but fine for a **personal / educational hobby** site with attribution +
> disclaimers. Keep it non-commercial; switch to a licensed vendor (Twelve Data / EODHD)
> if it ever becomes commercial. Always show "Source: Yahoo Finance" + the not-advice
> disclaimer (the site already does). See docs/DESIGN.md §Legal.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

from src.ingestion.twelvedata_provider import NIFTY_50_SYMBOLS  # shared default universe

logger = logging.getLogger(__name__)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pct(v):
    """yfinance returns ratios as fractions (0.31) — scale to percent."""
    n = _num(v)
    return round(n * 100, 2) if n is not None else None


def map_info(info: dict) -> dict:
    """Map a yfinance ``.info`` dict to canonical metric keys."""
    g = info.get
    pe = _num(g("trailingPE"))
    mcap = _num(g("marketCap"))
    fcf = _num(g("freeCashflow"))
    de = _num(g("debtToEquity"))  # yfinance reports as a percent number (e.g. 44.0)
    out = {
        "name": g("shortName") or g("longName"),
        "sector": g("sector") or "OTHER",
        "industry": g("industry"),
        "price": _num(g("currentPrice")) or _num(g("regularMarketPrice")),
        "change": _num(g("regularMarketChangePercent")),  # already a percent in yfinance
        "pe": pe,
        "pb": _num(g("priceToBook")),
        "ps": _num(g("priceToSalesTrailing12Months")),
        "ev_ebitda": _num(g("enterpriseToEbitda")),
        "roe": _pct(g("returnOnEquity")),
        "roa": _pct(g("returnOnAssets")),
        "net_margin": _pct(g("profitMargins")),
        "gross_margin": _pct(g("grossMargins")),
        "opm": _pct(g("operatingMargins")),
        "debt_to_equity": round(de / 100, 2) if de is not None else None,  # 44.0 -> 0.44
        "current_ratio": _num(g("currentRatio")),
        "quick_ratio": _num(g("quickRatio")),
        "dividend_yield": _num(g("dividendYield")),  # already a percent in yfinance
        "market_cap": mcap,
        "market_cap_cr": round(mcap / 1e7, 1) if mcap else None,  # INR -> Crore
        "bvps": _num(g("bookValue")),
        "revenue_growth_5y": _pct(g("revenueGrowth")),
        "profit_growth_5y": _pct(g("earningsGrowth")),
        "promoter_holding": _pct(g("heldPercentInsiders")),  # proxy for promoter holding
    }
    if pe and pe > 0:
        out["earnings_yield"] = round(1.0 / pe * 100, 2)
    if fcf is not None and mcap:
        out["fcf_yield"] = round(fcf / mcap * 100, 2)
    return out


def _ticker(sym: str, exchange: str) -> str:
    if sym.endswith((".NS", ".BO")):
        return sym
    return f"{sym}.{'BO' if exchange.upper() == 'BSE' else 'NS'}"


def build_dataset(
    symbols: list[str] | None = None,
    mode: str = "full",
    exchange: str = "NSE",
    throttle: float = 0.4,
    cache_path: str | Path = "data/yfinance_cache.json",
) -> pd.DataFrame:
    """Fetch a canonical-metric DataFrame for ``symbols`` from Yahoo Finance.

    mode='full'   -> full fundamentals + price (writes cache).
    mode='quotes' -> load cached fundamentals, refresh only the latest price (cheap).
    """
    import yfinance as yf

    symbols = symbols or NIFTY_50_SYMBOLS
    cache = Path(cache_path)

    if mode == "quotes" and cache.exists():
        base = {r["symbol"]: r for r in json.loads(cache.read_text())}
        for sym in symbols:
            try:
                px = yf.Ticker(_ticker(sym, exchange)).fast_info.get("last_price")
                if px:
                    base.setdefault(sym, {"symbol": sym})["price"] = round(float(px), 2)
            except Exception as exc:  # noqa: BLE001
                logger.warning("quote %s failed: %s", sym, exc)
            time.sleep(throttle)
        rows = list(base.values())
    else:  # full
        rows = []
        for sym in symbols:
            row = {"symbol": sym, "exchange": exchange}
            try:
                info = yf.Ticker(_ticker(sym, exchange)).info or {}
                if info:
                    row.update({k: v for k, v in map_info(info).items() if v is not None})
            except Exception as exc:  # noqa: BLE001
                logger.warning("yfinance %s failed: %s", sym, exc)
            rows.append(row)
            time.sleep(throttle)
        try:
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(rows, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache write failed: %s", exc)

    df = pd.DataFrame(rows)
    if "sector" not in df.columns:
        df["sector"] = "OTHER"
    df["sector"] = df["sector"].fillna("OTHER")
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    frame = build_dataset(symbols=["RELIANCE", "TCS", "INFY"], mode="full")
    cols = ["symbol", "price", "pe", "roe", "debt_to_equity", "net_margin", "sector"]
    print(frame[[c for c in cols if c in frame.columns]].to_string(index=False))
