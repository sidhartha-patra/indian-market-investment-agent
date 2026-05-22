"""Central config for the agent."""
import os
from pathlib import Path
from dotenv import load_dotenv
from src.universe import NIFTY_50, NIFTY_100, ETF_UNIVERSE, SECTOR_STOCKS  # noqa: F401

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

NEWS_FEEDS = {
    "moneycontrol": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "economic_times": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "livemint": "https://www.livemint.com/rss/markets",
    "business_standard": "https://www.business-standard.com/rss/markets-106.rss",
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/market.db")
