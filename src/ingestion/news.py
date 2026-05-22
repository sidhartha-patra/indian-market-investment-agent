"""Fetch Indian market news from RSS feeds."""
from __future__ import annotations
import logging
from datetime import datetime
import feedparser
from src.config import NEWS_FEEDS

logger = logging.getLogger(__name__)


def fetch_news(limit_per_feed: int = 20) -> list[dict]:
    """Return list of news items: {source, title, link, published, summary}."""
    items: list[dict] = []
    for source, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit_per_feed]:
                items.append({
                    "source": source,
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": entry.get("summary", "")[:500],
                    "fetched_at": datetime.utcnow().isoformat(),
                })
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed %s: %s", source, exc)
    logger.info("Fetched %d news items", len(items))
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    news = fetch_news(limit_per_feed=5)
    for n in news[:10]:
        print(f"[{n['source']}] {n['title']}")
