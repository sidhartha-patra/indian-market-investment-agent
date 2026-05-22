"""Analyst Buy% recommendations scraper from TickerTape.in."""
from __future__ import annotations
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

INDEX_PAGES = {
    "nifty50": "https://www.tickertape.in/indices/nifty-index-.NSEI/constituents?type=marketcap",
    "midcap150": "https://www.tickertape.in/indices/nifty-midcap-150-.NIMI150/constituents?type=marketcap",
    "nifty500": "https://www.tickertape.in/indices/nifty-500-index-.NIFTY500/constituents?type=marketcap",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investment-agent/0.1)"}


def _fetch_buy_pct(child_url: str) -> int:
    try:
        soup = BeautifulSoup(requests.get(child_url, headers=HEADERS, timeout=15).content, "html.parser")
        tag = soup.find("span", {"class": "percBuyReco-value"})
        return int(tag.text.replace("%", "")) if tag else -1
    except Exception:  # noqa: BLE001
        return -1


def _fetch_analyst_count(forecast_url: str) -> int:
    try:
        soup = BeautifulSoup(requests.get(forecast_url, headers=HEADERS, timeout=15).content, "html.parser")
        tag = soup.find("p", {"class": "text-light"})
        if tag:
            m = re.search(r"from\s+(\d+)\s+analyst", tag.text)
            return int(m.group(1)) if m else -1
    except Exception:  # noqa: BLE001
        pass
    return -1


def _parse_stock(args: dict) -> dict:
    rec = _fetch_buy_pct(args["url"])
    cnt = _fetch_analyst_count(args["forecast"])
    return {**args, "analyst_buy_pct": rec, "analyst_count": cnt}


def fetch_recommendations(index: str = "nifty50", limit: int | None = None,
                          max_workers: int = 10) -> list[dict]:
    """Fetch analyst Buy% + analyst count for stocks in given index."""
    root = "https://www.tickertape.in"
    page = INDEX_PAGES.get(index)
    if not page:
        raise ValueError(f"Unknown index {index}; choose from {list(INDEX_PAGES)}")

    soup = BeautifulSoup(requests.get(page, headers=HEADERS, timeout=20).content, "html.parser")
    rows = soup.findAll("div", {"class": "constituent-data-row-holder"})
    pattern = re.compile(r"(.+?)\|(.+?)\|(.+)")

    stocks = []
    for row in rows[:limit] if limit else rows:
        a = row.select_one("div > div > h5 > a")
        if not a:
            continue
        href = a.attrs.get("href", "")
        title = a.attrs.get("title", "")
        m = pattern.search(title)
        if not m:
            continue
        stocks.append({
            "name": m.group(1).strip(),
            "symbol": m.group(2).strip(),
            "sector": m.group(3).strip(),
            "url": f"{root}{href}",
            "forecast": f"{root}{href}/forecasts?section=price",
        })

    out = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_parse_stock, s) for s in stocks]
        for fut in as_completed(futures):
            r = fut.result()
            if r["analyst_buy_pct"] >= 0:
                out.append(r)
    out.sort(key=lambda x: (-x["analyst_buy_pct"], -x["analyst_count"]))
    logger.info("Got recommendations for %d stocks", len(out))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = fetch_recommendations("nifty50", limit=10)
    for r in recs[:10]:
        print(f"{r['symbol']:15} | Buy% {r['analyst_buy_pct']:3} | "
              f"analysts {r['analyst_count']:3} | {r['sector']}")
