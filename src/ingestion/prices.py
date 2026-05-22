"""Fetch OHLCV price data for Indian equities via yfinance."""
from __future__ import annotations
import logging
import pandas as pd
import yfinance as yf
from src.config import NIFTY_50

logger = logging.getLogger(__name__)


def fetch_prices(tickers: list[str] | None = None, period: str = "1y",
                 interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV for given tickers (defaults to Nifty 50)."""
    tickers = tickers or NIFTY_50
    result: dict[str, pd.DataFrame] = {}
    for tkr in tickers:
        try:
            df = yf.download(tkr, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                logger.warning("No data for %s", tkr)
                continue
            df.columns = [c if isinstance(c, str) else c[0] for c in df.columns]
            result[tkr] = df
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed %s: %s", tkr, exc)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = fetch_prices(NIFTY_50[:5], period="3mo")
    for sym, df in data.items():
        print(f"{sym}: {len(df)} rows, last close = {df['Close'].iloc[-1]:.2f}")
