"""End-to-end recommendation pipeline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from src.config import DATA_DIR, NIFTY_50
from src.universe import HIGH_VOL_UNIVERSE
from src.ingestion.broker_kite import get_live_quote
from src.ingestion.moneycontrol import get_mc_batch
from src.ingestion.news import fetch_news
from src.ingestion.prices import fetch_prices
from src.ingestion.screener import get_screener_fundamentals
from src.ingestion.tradingview import get_tv_signals
from src.ml.forecast import forecast_price
from src.ml.store import save_predictions_from_forecast
from src.strategies.momentum import screen as momentum_screen
from src.strategies.quality import screen as quality_screen
from src.strategies.volatile_movers import screen as volatile_screen
from src.agent.sentiment import classify_sentiment_openai

logger = logging.getLogger(__name__)

_TV_SIGNAL_SCORES = {
    "STRONG_BUY": 100,
    "BUY": 75,
    "NEUTRAL": 50,
    "SELL": 25,
    "STRONG_SELL": 0,
}


def _clean_symbol(ticker: str) -> str:
    return ticker.upper().replace(".NS", "").replace(".BO", "").strip()


def _score_map(df: pd.DataFrame | None) -> dict[str, float]:
    if df is None or df.empty or "ticker" not in df.columns:
        return {}
    return {
        str(row["ticker"]): float(row.get("score") or 0)
        for row in df.to_dict("records")
    }


def _top_tickers(*frames: pd.DataFrame | None, n: int = 5) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for frame in frames:
        if frame is None or frame.empty or "ticker" not in frame.columns:
            continue
        for ticker in frame.head(n)["ticker"].dropna().astype(str):
            if ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers


def _forecast_score(forecast: dict) -> float:
    expected = forecast.get("expected_return_pct")
    if expected is None:
        expected = 0
    capped = max(-10.0, min(10.0, float(expected)))
    return (capped + 10.0) * 5.0


def _latest_forecast_price(forecast: dict) -> float | None:
    points = forecast.get("point_forecast") or []
    return round(float(points[-1]), 2) if points else None


def _enrich_one_ticker(
    ticker: str,
    clean_symbol: str,
    price_df: pd.DataFrame | None,
    momentum_scores: dict[str, float],
    quality_scores: dict[str, float],
    mc_data: dict[str, dict],
    live_quotes: dict[str, float],
) -> dict[str, Any]:
    fundamentals: dict[str, Any] = {}
    tv: dict[str, Any] = {}
    forecast: dict[str, Any] = {"error": "missing_price_data"}

    try:
        fundamentals = get_screener_fundamentals(clean_symbol) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Screener enrichment failed for %s: %s", ticker, exc)

    try:
        tv = get_tv_signals(clean_symbol, interval="1d") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingView enrichment failed for %s: %s", ticker, exc)

    if price_df is not None:
        try:
            forecast = forecast_price(price_df, horizon_days=5)
            save_predictions_from_forecast(ticker, forecast)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Forecast enrichment failed for %s: %s", ticker, exc)
            forecast = {"error": str(exc)}

    mc = mc_data.get(clean_symbol) or mc_data.get(ticker) or {}
    indicators = tv.get("indicators") or {}
    tv_summary = tv.get("summary") or {}
    tv_recommendation = tv.get("recommendation")
    tv_score = _TV_SIGNAL_SCORES.get(str(tv_recommendation or "").upper(), 50)
    momentum_score = momentum_scores.get(ticker, 0.0)
    quality_score = quality_scores.get(ticker, 0.0)
    mc_buy_pct = mc.get("buy_pct")
    if mc_buy_pct is None:
        mc_buy_pct = mc.get("community_sentiment_pct")
    mc_component = float(mc_buy_pct) if mc_buy_pct is not None else 50.0
    forecast_component = _forecast_score(forecast)

    blended_score = (
        (momentum_score / 100.0 * 25.0)
        + (quality_score / 100.0 * 25.0)
        + (tv_score * 0.20)
        + (mc_component * 0.15)
        + (forecast_component * 0.15)
    )

    return {
        "ticker": ticker,
        "symbol": clean_symbol,
        "blended_score": round(blended_score, 2),
        "momentum_score": round(momentum_score, 2),
        "quality_score": round(quality_score, 2),
        "tradingview_recommendation": tv_recommendation,
        "tradingview_score": tv_score,
        "tradingview_buy": tv_summary.get("BUY"),
        "tradingview_sell": tv_summary.get("SELL"),
        "tradingview_neutral": tv_summary.get("NEUTRAL"),
        "rsi": indicators.get("RSI"),
        "macd": indicators.get("MACD.macd"),
        "macd_signal": indicators.get("MACD.signal"),
        "moneycontrol_live_price": mc.get("live_price"),
        "broker_live_price": live_quotes.get(ticker),
        "moneycontrol_tech_rating": mc.get("tech_rating"),
        "moneycontrol_buy_pct": mc.get("buy_pct"),
        "moneycontrol_sell_pct": mc.get("sell_pct"),
        "moneycontrol_hold_pct": mc.get("hold_pct"),
        "moneycontrol_community_sentiment_pct": mc.get("community_sentiment_pct"),
        "forecast_expected_return_pct": forecast.get("expected_return_pct"),
        "forecast_score": round(forecast_component, 2),
        "forecasted_price_5d": _latest_forecast_price(forecast),
        "forecast_direction": forecast.get("direction"),
        "forecast_engine": forecast.get("engine_used"),
        "forecast_error": forecast.get("error"),
        "pe": fundamentals.get("Stock P/E") or fundamentals.get("P/E"),
        "roce": fundamentals.get("ROCE"),
        "roe": fundamentals.get("ROE"),
        "debt_to_equity": fundamentals.get("Debt to Equity"),
        "sales_growth_5y": fundamentals.get("Sales Growth 5Yrs"),
        "profit_growth_5y": fundamentals.get("Profit Growth 5Yrs"),
        "promoter_holding": fundamentals.get("Promoter Holding"),
    }


def enrich_top_picks(
    momentum: pd.DataFrame,
    quality: pd.DataFrame,
    volatile: pd.DataFrame | None,
    price_data: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    """Enrich top momentum, quality, and volatile picks with fundamentals/TA/forecasts."""
    tickers = _top_tickers(momentum, quality, volatile, n=5)
    if not tickers:
        return []

    clean_symbols = [_clean_symbol(ticker) for ticker in tickers]
    momentum_scores = _score_map(momentum)
    quality_scores = _score_map(quality)

    try:
        mc_data = get_mc_batch(clean_symbols, max_workers=6)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Moneycontrol enrichment failed: %s", exc)
        mc_data = {}

    try:
        live_quotes = get_live_quote(tickers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live quote enrichment failed: %s", exc)
        live_quotes = {}

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                _enrich_one_ticker,
                ticker,
                _clean_symbol(ticker),
                price_data.get(ticker),
                momentum_scores,
                quality_scores,
                mc_data,
                live_quotes,
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                enriched.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Enrichment failed for %s: %s", ticker, exc)
                enriched.append({"ticker": ticker, "symbol": _clean_symbol(ticker), "blended_score": 0})

    return sorted(enriched, key=lambda row: row.get("blended_score") or 0, reverse=True)


def generate_recommendations(
    universe: list[str] | None = None,
    include_volatile: bool = True,
    include_multibaggers: bool = True,
) -> dict:
    universe = universe or NIFTY_50
    logger.info("Fetching prices for %d tickers", len(universe))
    prices = fetch_prices(universe, period="1y")

    mom = momentum_screen(prices, top_n=10)
    qual = quality_screen(prices, top_n=10)

    volatile = None
    vol_prices: dict[str, pd.DataFrame] = {}
    if include_volatile:
        logger.info("Fetching prices for %d high-vol tickers", len(HIGH_VOL_UNIVERSE))
        vol_prices = fetch_prices(HIGH_VOL_UNIVERSE, period="6mo")
        volatile = volatile_screen(vol_prices, top_n=15)

    logger.info("Enriching top picks...")
    combined_prices = {**prices, **vol_prices}
    enriched_picks = enrich_top_picks(mom, qual, volatile, combined_prices)

    multibagger_candidates: list[dict] = []
    if include_multibaggers:
        try:
            from src.strategies.multibagger import screen_multibaggers

            multibagger_candidates = screen_multibaggers(top_n=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Multibagger screen skipped: %s", exc)

    logger.info("Fetching news...")
    news = fetch_news(limit_per_feed=10)
    headlines = [n["title"] for n in news[:30]]
    sentiment = classify_sentiment_openai(headlines) if headlines else []

    bullish = [s for s in sentiment if s.get("sentiment") == "bullish"]
    bearish = [s for s in sentiment if s.get("sentiment") == "bearish"]

    overlap = set(mom["ticker"]) & set(qual["ticker"])
    top_picks = mom[mom["ticker"].isin(overlap)].head(5)

    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "top_picks": top_picks.to_dict("records"),
        "momentum_leaders": mom.head(5).to_dict("records"),
        "quality_leaders": qual.head(5).to_dict("records"),
        "volatile_movers": volatile.to_dict("records") if volatile is not None else [],
        "enriched_picks": enriched_picks,
        "news_summary": {
            "total": len(sentiment),
            "bullish": len(bullish),
            "bearish": len(bearish),
            "neutral": len(sentiment) - len(bullish) - len(bearish),
            "bullish_headlines": [s["headline"] for s in bullish[:5]],
            "bearish_headlines": [s["headline"] for s in bearish[:5]],
        },
        "disclaimer": (
            "EDUCATIONAL ONLY. Not SEBI-registered investment advice. "
            "Volatile movers are higher risk — use strict stop-loss. "
            "Consult a qualified financial advisor before investing."
        ),
    }

    if include_multibaggers:
        out["multibagger_candidates"] = multibagger_candidates

    out_path = DATA_DIR / "recommendations.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    logger.info("Saved recommendations -> %s", out_path)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    recs = generate_recommendations()
    print(json.dumps(recs, indent=2, default=str)[:3000])
