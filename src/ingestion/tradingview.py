"""TradingView technical-analysis signals via tradingview-ta."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from tradingview_ta import Interval, TA_Handler

logger = logging.getLogger(__name__)

_INTERVALS = {
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
}

_INDICATOR_KEYS = ("RSI", "MACD.macd", "MACD.signal", "Stoch.K", "ADX")


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().removesuffix(".NS")


def _summary_counts(summary: dict[str, Any]) -> dict[str, int]:
    return {
        "BUY": int(summary.get("BUY", 0) or 0),
        "SELL": int(summary.get("SELL", 0) or 0),
        "NEUTRAL": int(summary.get("NEUTRAL", 0) or 0),
    }


def _section(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "recommendation": section.get("RECOMMENDATION"),
        "buy": int(section.get("BUY", 0) or 0),
        "sell": int(section.get("SELL", 0) or 0),
        "neutral": int(section.get("NEUTRAL", 0) or 0),
    }


def _selected_indicators(indicators: dict[str, Any]) -> dict[str, float | int | None]:
    return {key: indicators.get(key) for key in _INDICATOR_KEYS}


def get_tv_signals(symbol: str, interval: str = "1d") -> dict:
    """Fetch TradingView TA signals for an NSE symbol; returns {} on failure."""
    clean_symbol = _normalize_symbol(symbol)
    tv_interval = _INTERVALS.get(interval)
    if tv_interval is None:
        logger.warning("Unsupported TradingView interval %s", interval)
        return {}

    try:
        handler = TA_Handler(
            symbol=clean_symbol,
            screener="india",
            exchange="NSE",
            interval=tv_interval,
        )
        analysis = handler.get_analysis()
        summary = analysis.summary or {}

        return {
            "symbol": clean_symbol,
            "interval": interval,
            "recommendation": summary.get("RECOMMENDATION"),
            "summary": _summary_counts(summary),
            "moving_averages": _section(analysis.moving_averages or {}),
            "oscillators": _section(analysis.oscillators or {}),
            "indicators": _selected_indicators(analysis.indicators or {}),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch TradingView signals for %s: %s", clean_symbol, exc)
        return {}


def get_tv_batch(symbols: list[str], interval: str = "1d", max_workers: int = 8) -> dict[str, dict]:
    """Fetch TradingView TA signals for multiple symbols concurrently."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_tv_signals, symbol, interval): symbol for symbol in symbols}
        for fut in as_completed(futures):
            try:
                data = fut.result()
                if data:
                    results[data["symbol"]] = data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed TradingView batch item %s: %s", futures[fut], exc)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    signals = get_tv_batch(["RELIANCE", "TCS", "HDFCBANK"], interval="1d")
    for symbol in ["RELIANCE", "TCS", "HDFCBANK"]:
        data = signals.get(symbol)
        if not data:
            print(f"{symbol}: no data")
            continue
        indicators = data["indicators"]
        summary = data["summary"]
        print(
            f"{symbol:10} | {data['recommendation']:11} | "
            f"RSI {indicators.get('RSI'):.2f} | "
            f"MACD {indicators.get('MACD.macd'):.2f} | "
            f"BUY {summary['BUY']:2} SELL {summary['SELL']:2} NEUTRAL {summary['NEUTRAL']:2}"
        )
