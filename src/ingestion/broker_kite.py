"""Real-time quote provider with Zerodha Kite Connect + yfinance fallback.

Set KITE_API_KEY and KITE_ACCESS_TOKEN env vars to use Kite. Otherwise yfinance
(15-min delayed) is used transparently.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

import yfinance as yf

logger = logging.getLogger(__name__)

KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")


def _kite_client():
    """Return KiteConnect client if creds + lib available, else None."""
    if not (KITE_API_KEY and KITE_ACCESS_TOKEN):
        return None
    try:
        from kiteconnect import KiteConnect  # type: ignore
    except ImportError:
        logger.info("kiteconnect not installed; pip install kiteconnect to enable")
        return None
    kc = KiteConnect(api_key=KITE_API_KEY)
    kc.set_access_token(KITE_ACCESS_TOKEN)
    return kc


def _yfinance_quotes(symbols: Iterable[str]) -> dict[str, float]:
    """15-min delayed quote via yfinance fast_info."""
    out: dict[str, float] = {}
    for s in symbols:
        try:
            info = yf.Ticker(s).fast_info
            price = info.get("last_price") if isinstance(info, dict) else info.last_price
            if price:
                out[s] = float(price)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance quote failed for %s: %s", s, exc)
    return out


def get_live_quote(symbols: list[str]) -> dict[str, float]:
    """Return {symbol: last_price}. Uses Kite if configured, else yfinance fallback.

    Symbols are NSE format with .NS suffix (e.g. RELIANCE.NS).
    """
    kc = _kite_client()
    if kc is None:
        return _yfinance_quotes(symbols)
    kite_syms = [f"NSE:{s.replace('.NS', '')}" for s in symbols]
    try:
        ltp = kc.ltp(kite_syms)
        out: dict[str, float] = {}
        for orig, kite_sym in zip(symbols, kite_syms):
            if kite_sym in ltp:
                out[orig] = float(ltp[kite_sym]["last_price"])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("Kite ltp failed (%s); falling back to yfinance", exc)
        return _yfinance_quotes(symbols)


def is_realtime() -> bool:
    """Whether we have a live broker connection (vs delayed yfinance)."""
    return _kite_client() is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Realtime mode:", is_realtime())
    quotes = get_live_quote(["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"])
    for sym, px in quotes.items():
        print(f"  {sym:15} = ₹{px:,.2f}")
