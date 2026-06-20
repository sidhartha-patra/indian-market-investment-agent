"""Fetch the complete NSE + BSE equity universe ("consider all stocks").

The repo's hardcoded ~192-symbol universe covers ~10% of NSE. This module pulls the
full listed universe (~2,000+ NSE, ~5,000+ BSE) from free, no-auth sources and unifies
them on ISIN:

- NSE security master: ``archives.nseindia.com/content/equities/EQUITY_L.csv``
- Zerodha Kite public instruments dump: ``api.kite.trade/instruments/{NSE,BSE}``

Results are cached to ``data/`` so the rest of the pipeline works offline after one
successful fetch. Falls back to the static ``src.universe`` lists if the network fails.

> ⚠️ NSE/Kite reference data is used here for *metadata* (symbol, name, ISIN). Displaying
> derived data publicly requires attribution / licensing — see docs/DESIGN.md.
"""
from __future__ import annotations

import logging
from io import StringIO

import pandas as pd
import requests

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

NSE_EQUITY_L = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
KITE_INSTRUMENTS = "https://api.kite.trade/instruments/{exchange}"
INDEX_CSV = {
    "nifty50": "ind_nifty50list.csv",
    "nifty100": "ind_nifty100list.csv",
    "nifty200": "ind_nifty200list.csv",
    "nifty500": "ind_nifty500list.csv",
}
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_CACHE = DATA_DIR / "universe_all.csv"
_TIMEOUT = 30


def _get(url: str) -> str:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def fetch_nse_equity_list() -> pd.DataFrame:
    """All NSE equities from EQUITY_L.csv -> symbol/name/series/isin/listing_date."""
    df = pd.read_csv(StringIO(_get(NSE_EQUITY_L)))
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].astype(str).str.strip(),
        "name": df["NAME OF COMPANY"].astype(str).str.strip(),
        "series": df.get("SERIES", pd.Series(["EQ"] * len(df))).astype(str).str.strip(),
        "isin": df.get("ISIN NUMBER", df.get("ISIN", pd.Series([None] * len(df)))).astype(str).str.strip(),
        "exchange": "NSE",
    })
    out["yf_ticker"] = out["symbol"] + ".NS"
    return out


def fetch_kite_instruments(exchange: str = "NSE") -> pd.DataFrame:
    """Full Kite instrument dump for an exchange (no auth). exchange in {NSE, BSE}."""
    df = pd.read_csv(StringIO(_get(KITE_INSTRUMENTS.format(exchange=exchange))))
    eq = df[df["instrument_type"] == "EQ"].copy() if "instrument_type" in df.columns else df
    suffix = ".NS" if exchange == "NSE" else ".BO"
    return pd.DataFrame({
        "symbol": eq["tradingsymbol"].astype(str).str.strip(),
        "name": eq["name"].astype(str).str.strip(),
        "exchange": exchange,
        "exchange_token": eq.get("exchange_token"),
        "yf_ticker": eq["tradingsymbol"].astype(str).str.strip() + suffix,
    })


def nifty_index_symbols(index: str = "nifty200", fallback: list[str] | None = None) -> list[str]:
    """Fetch the constituent symbols of an NSE index (e.g. 'nifty200') from the public CSV.

    Falls back to ``fallback`` (or []) on any network/parse failure so callers never crash.
    """
    try:
        url = f"https://archives.nseindia.com/content/indices/{INDEX_CSV[index]}"
        df = pd.read_csv(StringIO(_get(url)))
        df.columns = [c.strip() for c in df.columns]
        syms = df["Symbol"].astype(str).str.strip().tolist()
        if syms:
            logger.info("Fetched %d symbols for %s", len(syms), index)
            return syms
    except Exception as exc:  # noqa: BLE001
        logger.warning("Index %s fetch failed: %s", index, exc)
    return fallback or []


def get_all_indian_stocks(
    include_bse: bool = True, use_cache: bool = True, refresh: bool = False
) -> pd.DataFrame:
    """Unified NSE (+BSE) equity universe, deduped by ISIN where possible.

    Columns: symbol, name, exchange, isin, yf_ticker. Cached to ``data/``; on any
    network failure falls back to the static ``src.universe`` lists so callers never
    crash.
    """
    if use_cache and not refresh and _CACHE.exists():
        try:
            return pd.read_csv(_CACHE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Universe cache unreadable (%s); refetching", exc)

    frames: list[pd.DataFrame] = []
    try:
        frames.append(fetch_nse_equity_list())
        logger.info("Fetched %d NSE symbols", len(frames[-1]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("NSE equity list fetch failed: %s", exc)

    if include_bse:
        try:
            bse = fetch_kite_instruments("BSE")
            frames.append(bse)
            logger.info("Fetched %d BSE symbols", len(bse))
        except Exception as exc:  # noqa: BLE001
            logger.warning("BSE (Kite) fetch failed: %s", exc)

    if not frames:
        logger.warning("All universe fetches failed; falling back to static lists")
        return _static_fallback()

    combined = pd.concat(frames, ignore_index=True)
    combined["isin"] = combined.get("isin")
    # Dedup: prefer NSE listing where the same ISIN is dual-listed.
    combined["_pref"] = (combined["exchange"] == "NSE").astype(int)
    combined = (
        combined.sort_values("_pref", ascending=False)
        .drop_duplicates(subset=["isin"], keep="first")
        .drop(columns="_pref")
        .reset_index(drop=True)
    )
    # Rows with no/duplicate ISIN: also dedup on (symbol, exchange).
    combined = combined.drop_duplicates(subset=["symbol", "exchange"]).reset_index(drop=True)

    try:
        DATA_DIR.mkdir(exist_ok=True)
        combined.to_csv(_CACHE, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write universe cache: %s", exc)
    return combined


def _static_fallback() -> pd.DataFrame:
    from src.universe import NIFTY_100

    syms = [s.replace(".NS", "") for s in NIFTY_100]
    return pd.DataFrame({
        "symbol": syms, "name": syms, "exchange": "NSE",
        "isin": [None] * len(syms), "yf_ticker": [s + ".NS" for s in syms],
    })


def all_yf_tickers(limit: int | None = None, **kwargs) -> list[str]:
    """Convenience: list of yfinance tickers for the whole universe."""
    df = get_all_indian_stocks(**kwargs)
    tickers = df["yf_ticker"].dropna().astype(str).tolist()
    return tickers[:limit] if limit else tickers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    u = get_all_indian_stocks()
    print(f"Total symbols: {len(u)}")
    print(u.groupby("exchange").size())
    print(u.head())
