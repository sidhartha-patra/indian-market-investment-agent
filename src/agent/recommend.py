"""End-to-end recommendation pipeline."""
from __future__ import annotations
import json
import logging
from datetime import datetime
from src.config import DATA_DIR, NIFTY_50
from src.universe import HIGH_VOL_UNIVERSE
from src.ingestion.prices import fetch_prices
from src.ingestion.news import fetch_news
from src.strategies.momentum import screen as momentum_screen
from src.strategies.quality import screen as quality_screen
from src.strategies.volatile_movers import screen as volatile_screen
from src.agent.sentiment import classify_sentiment_openai

logger = logging.getLogger(__name__)


def generate_recommendations(universe: list[str] | None = None,
                             include_volatile: bool = True) -> dict:
    universe = universe or NIFTY_50
    logger.info("Fetching prices for %d tickers", len(universe))
    prices = fetch_prices(universe, period="1y")

    mom = momentum_screen(prices, top_n=10)
    qual = quality_screen(prices, top_n=10)

    volatile = None
    if include_volatile:
        logger.info("Fetching prices for %d high-vol tickers", len(HIGH_VOL_UNIVERSE))
        vol_prices = fetch_prices(HIGH_VOL_UNIVERSE, period="6mo")
        volatile = volatile_screen(vol_prices, top_n=15)

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
            "Volatile movers are higher risk \u2014 use strict stop-loss. "
            "Consult a qualified financial advisor before investing."
        ),
    }

    out_path = DATA_DIR / "recommendations.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    logger.info("Saved recommendations -> %s", out_path)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    recs = generate_recommendations()
    print(json.dumps(recs, indent=2, default=str)[:3000])
