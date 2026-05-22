"""Run all ingestion jobs and persist to SQLite."""
from __future__ import annotations
import json
import logging
import sqlite3
from pathlib import Path
from src.config import DATA_DIR
from src.ingestion.prices import fetch_prices
from src.ingestion.news import fetch_news

logger = logging.getLogger(__name__)
DB_PATH = DATA_DIR / "market.db"


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, title TEXT, link TEXT UNIQUE,
            published TEXT, summary TEXT, fetched_at TEXT,
            sentiment TEXT, tickers TEXT
        );
    """)
    return conn


def save_prices(conn: sqlite3.Connection, data: dict) -> int:
    n = 0
    for ticker, df in data.items():
        for date, row in df.iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                (ticker, str(date.date()), float(row["Open"]),
                 float(row["High"]), float(row["Low"]),
                 float(row["Close"]), float(row["Volume"])),
            )
            n += 1
    conn.commit()
    return n


def save_news(conn: sqlite3.Connection, items: list[dict]) -> int:
    n = 0
    for item in items:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO news (source, title, link, published, summary, fetched_at) "
                "VALUES (?,?,?,?,?,?)",
                (item["source"], item["title"], item["link"],
                 item["published"], item["summary"], item["fetched_at"]),
            )
            n += 1
        except sqlite3.Error as e:
            logger.warning("Skip news: %s", e)
    conn.commit()
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = init_db()
    logger.info("Fetching prices...")
    prices = fetch_prices(period="6mo")
    n_prices = save_prices(conn, prices)
    logger.info("Saved %d price rows for %d tickers", n_prices, len(prices))

    logger.info("Fetching news...")
    news = fetch_news()
    n_news = save_news(conn, news)
    logger.info("Saved %d news items", n_news)

    conn.close()
    logger.info("Ingestion complete. DB: %s", DB_PATH)


if __name__ == "__main__":
    main()
