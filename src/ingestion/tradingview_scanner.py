"""Bulk TradingView-India ingestion via the public scanner endpoint.

``scanner.tradingview.com/india/scan`` is the same endpoint that powers
https://in.tradingview.com/markets/stocks-india/ and returns ~3,000 columns
(fundamentals **and** technicals) for the whole ~8,000-symbol NSE+BSE universe in a
single paginated request. This module hits it directly (robust to wrapper-library
drift) and parses the positional response into a tidy DataFrame.

> ⚠️ **Legal:** TradingView's ToS restricts data to display-only personal use and
> prohibits non-display/automated redistribution. Use this for **personal research
> only** — do NOT serve TradingView-sourced values on a public website. For a public
> site use a licensed vendor (see docs/DESIGN.md). This module is opt-in and never
> runs automatically.
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SCANNER_URL = "https://scanner.tradingview.com/india/scan"
_HEADERS = {
    "authority": "scanner.tradingview.com",
    "accept": "text/plain, */*; q=0.01",
    "content-type": "application/json; charset=UTF-8",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
    "accept-language": "en-US,en;q=0.9",
}

# Curated default columns: identity + valuation + profitability + growth + leverage +
# cashflow + ownership-ish + a few technicals. Maps to canonical keys via
# fundamental_analysis.normalize_tradingview().
DEFAULT_COLUMNS = [
    "name", "close", "change", "volume", "exchange", "sector", "industry",
    "market_cap_basic", "price_earnings_ttm", "price_book_fq", "price_sales_ratio",
    "enterprise_value_ebitda_ttm", "earnings_per_share_basic_ttm",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "return_on_equity", "return_on_assets", "return_on_invested_capital",
    "gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm",
    "debt_to_equity", "current_ratio_fq", "quick_ratio_fq",
    "free_cash_flow", "dividend_yield_recent",
    "RSI", "MACD.macd", "EMA50", "EMA200", "price_52_week_high", "price_52_week_low",
]


def build_payload(
    columns: list[str],
    limit: int = 10_000,
    offset: int = 0,
    only_primary: bool = True,
    market_cap_min: float | None = None,
    sort_by: str = "market_cap_basic",
    sort_order: str = "desc",
    extra_filters: list[dict] | None = None,
) -> dict:
    """Construct the scanner POST body."""
    filters = []
    if only_primary:
        filters.append({"left": "is_primary", "operation": "equal", "right": True})
    if market_cap_min is not None:
        filters.append({"left": "market_cap_basic", "operation": "greater", "right": market_cap_min})
    if extra_filters:
        filters.extend(extra_filters)
    return {
        "markets": ["india"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "en"},
        "columns": columns,
        "filter": filters,
        "sort": {"sortBy": sort_by, "sortOrder": sort_order},
        "range": [offset, offset + limit],
    }


def parse_scanner_response(payload: dict, columns: list[str]) -> pd.DataFrame:
    """Turn a scanner JSON response into a DataFrame (offline-testable)."""
    rows = []
    for item in payload.get("data", []):
        ticker = item.get("s", "")
        values = item.get("d", []) or []
        exch, _, sym = ticker.partition(":")
        row = {"ticker": ticker, "symbol": sym or ticker, "tv_exchange": exch}
        for col, val in zip(columns, values):
            row[col] = val
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["yf_ticker"] = df.apply(
            lambda r: f"{r['symbol']}{'.NS' if r.get('tv_exchange') == 'NSE' else '.BO'}", axis=1
        )
    return df


def fetch_india_scanner(
    columns: list[str] | None = None,
    limit: int = 10_000,
    only_primary: bool = True,
    market_cap_min: float | None = None,
    timeout: int = 30,
    sort_by: str = "market_cap_basic",
    sort_order: str = "desc",
    extra_filters: list[dict] | None = None,
) -> pd.DataFrame:
    """Fetch fundamentals+technicals for the India universe in one call.

    ``market_cap_min`` is in the scanner's USD-denominated units; pass None for all.
    Returns a DataFrame with ``ticker``/``symbol``/``yf_ticker`` + the requested columns.
    """
    columns = columns or DEFAULT_COLUMNS
    body = build_payload(columns, limit=limit, only_primary=only_primary,
                         market_cap_min=market_cap_min, sort_by=sort_by, sort_order=sort_order,
                         extra_filters=extra_filters)
    resp = requests.post(SCANNER_URL, json=body, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    logger.info("TradingView scanner returned totalCount=%s", data.get("totalCount"))
    return parse_scanner_response(data, columns)


# Columns fetched for market-mover collections (technical + a couple of fundamentals).
MOVER_COLUMNS = [
    "name", "close", "change", "volume", "market_cap_basic", "sector",
    "RSI", "Volatility.D", "price_52_week_high", "price_52_week_low",
    "Perf.Y", "dividend_yield_recent",
]

# The TradingView "market movers" categories, each expressed as a scanner sort/filter.
COLLECTIONS: dict[str, dict] = {
    "gainers":             {"sort_by": "change", "sort_order": "desc", "market_cap_min": 1e9},
    "losers":              {"sort_by": "change", "sort_order": "asc", "market_cap_min": 1e9},
    "most_active":         {"sort_by": "volume", "sort_order": "desc", "market_cap_min": 1e9},
    "most_volatile":       {"sort_by": "Volatility.D", "sort_order": "desc", "market_cap_min": 1e9},
    "top_performers_1y":   {"sort_by": "Perf.Y", "sort_order": "desc", "market_cap_min": 5e9},
    "worst_performers_1y": {"sort_by": "Perf.Y", "sort_order": "asc", "market_cap_min": 5e9},
    "high_dividend":       {"sort_by": "dividend_yield_recent", "sort_order": "desc",
                            "market_cap_min": 5e9,
                            "extra_filters": [{"left": "dividend_yield_recent",
                                               "operation": "greater", "right": 0}]},
    "large_cap":           {"sort_by": "market_cap_basic", "sort_order": "desc"},
    "overbought":          {"sort_by": "RSI", "sort_order": "desc", "market_cap_min": 5e9,
                            "extra_filters": [{"left": "RSI", "operation": "greater", "right": 70}]},
    "oversold":            {"sort_by": "RSI", "sort_order": "asc", "market_cap_min": 5e9,
                            "extra_filters": [{"left": "RSI", "operation": "less", "right": 30}]},
}


def fetch_collection(name: str, top: int = 50, columns: list[str] | None = None,
                     timeout: int = 30) -> pd.DataFrame:
    """Fetch one TradingView market-mover collection (e.g. 'gainers')."""
    if name not in COLLECTIONS:
        raise ValueError(f"Unknown collection '{name}'. Choose from {sorted(COLLECTIONS)}")
    cfg = COLLECTIONS[name]
    df = fetch_india_scanner(
        columns=columns or MOVER_COLUMNS, limit=top, market_cap_min=cfg.get("market_cap_min"),
        sort_by=cfg["sort_by"], sort_order=cfg["sort_order"],
        extra_filters=cfg.get("extra_filters"), timeout=timeout)
    if not df.empty:
        df["collection"] = name
    return df


def fetch_all_collections(top_each: int = 50, names: list[str] | None = None,
                          timeout: int = 30) -> dict[str, pd.DataFrame]:
    """Fetch every market-mover collection. Resilient — failures yield an empty frame."""
    out: dict[str, pd.DataFrame] = {}
    for name in (names or list(COLLECTIONS)):
        try:
            out[name] = fetch_collection(name, top=top_each, timeout=timeout)
            logger.info("collection %-20s -> %d rows", name, len(out[name]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("collection %s failed: %s", name, exc)
            out[name] = pd.DataFrame()
    return out


def to_fundamentals_dict(df: pd.DataFrame) -> dict[str, dict]:
    """Map scanner rows -> {symbol: canonical fundamentals dict} for the scorer."""
    from src.strategies.fundamental_analysis import normalize_tradingview

    out: dict[str, dict] = {}
    for row in df.to_dict("records"):
        canon = normalize_tradingview(row)
        canon["sector"] = row.get("sector")
        out[str(row.get("symbol"))] = canon
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    frame = fetch_india_scanner(limit=50)
    print(f"Rows: {len(frame)}")
    cols = ["symbol", "tv_exchange", "market_cap_basic", "price_earnings_ttm",
            "return_on_equity", "debt_to_equity", "sector"]
    print(frame[[c for c in cols if c in frame.columns]].head(10))
