"""Twelve Data provider — a *licensed*, publishable data source for the public site.

Unlike the TradingView scanner (personal-research only), Twelve Data permits external
display of its data under a paid plan + Redistribution Add-On, making it the compliant
choice for a public website (see docs/DESIGN.md §Legal). This adapter fetches:

- ``/quote``      — latest price + % change   (cheap; refreshed HOURLY)
- ``/statistics`` — valuation/profitability/leverage fundamentals (refreshed DAILY)
- ``/profile``    — sector / industry

and maps everything onto the canonical keys consumed by
``strategies.fundamental_analysis.fundamental_scores``.

**Cost model (free tier = 8 credits/min, 800/day):** a full fetch is ~3 credits/symbol,
a quote-only refresh is ~1 credit/symbol. So the scheduler does ONE full fundamentals
refresh per day and quote-only refreshes every other hour, which keeps a ~50-stock
public site within the free tier. Larger universes need a paid plan.

Set ``TWELVEDATA_API_KEY`` in the environment. Get a key at https://twelvedata.com/.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://api.twelvedata.com"
_TIMEOUT = 20

# Twelve Data statistics/quote field -> canonical key (flattened, case-insensitive).
_FIELD_MAP = {
    "trailing_pe": "pe", "forward_pe": "pe", "price_to_book_mrq": "pb",
    "price_to_sales_ttm": "ps", "enterprise_to_ebitda": "ev_ebitda",
    "peg_ratio": "peg", "return_on_equity_ttm": "roe", "return_on_assets_ttm": "roa",
    "gross_margin": "gross_margin", "operating_margin": "opm", "profit_margin": "net_margin",
    "total_debt_to_equity_mrq": "debt_to_equity", "current_ratio_mrq": "current_ratio",
    "quick_ratio_mrq": "quick_ratio", "forward_annual_dividend_yield": "dividend_yield",
    "market_capitalization": "market_cap", "book_value_per_share_mrq": "bvps",
    "quarterly_revenue_growth": "revenue_growth_3y", "quarterly_earnings_growth_yoy": "eps_growth_3y",
}
# Default compliant public universe (Nifty 50) — keeps a public build within free tier.
NIFTY_50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "HINDUNILVR", "ICICIBANK", "SBIN", "BHARTIARTL",
    "KOTAKBANK", "ITC", "LT", "ASIANPAINT", "AXISBANK", "MARUTI", "TITAN", "NESTLEIND",
    "HCLTECH", "SUNPHARMA", "BAJFINANCE", "ULTRACEMCO", "POWERGRID", "TATAMOTORS", "ONGC",
    "WIPRO", "NTPC", "COALINDIA", "JSWSTEEL", "TATASTEEL", "GRASIM", "ADANIENT", "HDFCLIFE",
    "SBILIFE", "BPCL", "BAJAJFINSV", "TECHM", "EICHERMOT", "HINDALCO", "BRITANNIA",
    "DIVISLAB", "CIPLA", "DRREDDY", "APOLLOHOSP", "HEROMOTOCO", "TATACONSUM", "ADANIPORTS",
    "SHREECEM", "INDUSINDBK", "LTIM", "SHRIRAMFIN", "BAJAJ-AUTO",
]


def _api_key(api_key: str | None) -> str:
    key = api_key or os.getenv("TWELVEDATA_API_KEY", "")
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY not set — required for the compliant public build.")
    return key


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _flatten(d: dict, out: dict | None = None) -> dict:
    """Recursively collect scalar leaves keyed by their (lowercased) leaf key."""
    out = {} if out is None else out
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            _flatten(v, out)
        elif not isinstance(v, list):
            out[str(k).lower()] = v
    return out


def parse_quote(payload: dict) -> dict:
    """Map a /quote response to {price, change, name, exchange}."""
    return {
        "name": payload.get("name"),
        "exchange": payload.get("exchange"),
        "price": _num(payload.get("close")),
        "change": _num(payload.get("percent_change")),
    }


def parse_statistics(payload: dict) -> dict:
    """Map a /statistics response (nested) to canonical fundamental keys."""
    flat = _flatten(payload.get("statistics", payload))
    out: dict = {}
    for raw, canon in _FIELD_MAP.items():
        if raw in flat and _num(flat[raw]) is not None and canon not in out:
            out[canon] = _num(flat[raw])
    # Twelve Data returns margins/returns as fractions (0.45) — scale to % for readability.
    for k in ("roe", "roa", "gross_margin", "opm", "net_margin", "dividend_yield"):
        if k in out and abs(out[k]) <= 5:
            out[k] = round(out[k] * 100, 2)
    if out.get("pe") and out["pe"] > 0:
        out["earnings_yield"] = round(1.0 / out["pe"] * 100, 2)
    return out


def parse_profile(payload: dict) -> dict:
    return {"sector": payload.get("sector"), "industry": payload.get("industry")}


def _get(endpoint: str, params: dict) -> dict:
    resp = requests.get(f"{BASE}/{endpoint}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"Twelve Data error ({endpoint}): {data.get('message')}")
    return data


def build_dataset(
    symbols: list[str] | None = None,
    api_key: str | None = None,
    mode: str = "full",
    exchange: str = "NSE",
    rate_per_min: int = 8,
    cache_path: str | Path = "data/twelvedata_cache.json",
) -> pd.DataFrame:
    """Fetch a canonical-metric DataFrame for ``symbols`` from Twelve Data.

    mode='full'   -> quote + statistics + profile (refresh everything; writes cache).
    mode='quotes' -> load cached fundamentals, refresh only price/change (cheap).
    """
    key = _api_key(api_key)
    symbols = symbols or NIFTY_50_SYMBOLS
    cache = Path(cache_path)
    delay = 60.0 / max(1, rate_per_min)

    if mode == "quotes" and cache.exists():
        base = {r["symbol"]: r for r in json.loads(cache.read_text())}
        for sym in symbols:
            try:
                q = parse_quote(_get("quote", {"symbol": sym, "exchange": exchange, "apikey": key}))
                base.setdefault(sym, {"symbol": sym}).update(
                    {k: v for k, v in q.items() if v is not None})
            except Exception as exc:  # noqa: BLE001
                logger.warning("quote %s failed: %s", sym, exc)
            time.sleep(delay)
        rows = list(base.values())
    else:  # full
        rows = []
        for sym in symbols:
            row = {"symbol": sym, "exchange_q": exchange}
            try:
                row.update(parse_quote(_get("quote", {"symbol": sym, "exchange": exchange, "apikey": key})))
                time.sleep(delay)
                row.update(parse_statistics(_get("statistics", {"symbol": sym, "exchange": exchange, "apikey": key})))
                time.sleep(delay)
                row.update(parse_profile(_get("profile", {"symbol": sym, "exchange": exchange, "apikey": key})))
                time.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                logger.warning("full fetch %s failed: %s", sym, exc)
            rows.append(row)
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
    print(frame[[c for c in ["symbol", "price", "pe", "roe", "debt_to_equity", "sector"]
                 if c in frame.columns]])
