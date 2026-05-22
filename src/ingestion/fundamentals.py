"""Fundamentals scraper from Finology.in (ROCE, ROE, P/B, Cash-Debt, etc.)."""
from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investment-agent/0.1)"}


@lru_cache(maxsize=1024)
def get_fundamentals(symbol: str) -> dict:
    """Return key ratios for a symbol from Finology (without .NS suffix)."""
    sym = symbol.replace(".NS", "").replace(".BO", "")
    url = f"https://ticker.finology.in/company/{sym}"
    ratios: dict = {}
    try:
        res = requests.get(url, timeout=15, headers=HEADERS)
        if res.status_code != 200:
            return ratios
        soup = BeautifulSoup(res.text, "html.parser")
        section = soup.select("#mainContent_updAddRatios")
        for div in section:
            for small, p in zip(div.select("small"), div.select("p")):
                val = (p.text.replace("\n", "").replace("Cr.", "")
                       .replace("₹", "").replace(",", "")
                       .replace("%", "").replace("-", "").strip())
                try:
                    ratios[small.text.strip()] = float(val)
                except ValueError:
                    ratios[small.text.strip()] = None
        if "CASH" in ratios and "DEBT" in ratios and ratios["CASH"] and ratios["DEBT"]:
            ratios["Cash/Debt"] = round(ratios["CASH"] - ratios["DEBT"], 2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Finology fail for %s: %s", sym, exc)
    return ratios


def fundamentals_batch(symbols: list[str], max_workers: int = 8) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_fundamentals, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("%s: %s", sym, exc)
    return out


def quality_filter(fundamentals: dict[str, dict],
                   min_roce: float = 15.0,
                   min_roe: float = 12.0,
                   max_pe: float = 50.0) -> dict[str, dict]:
    """Filter stocks by quality criteria (Graham/Coffee-Can inspired)."""
    out = {}
    for sym, r in fundamentals.items():
        roce = r.get("ROCE") or 0
        roe = r.get("ROE") or 0
        pe = r.get("P/E") or 999
        if roce >= min_roce and roe >= min_roe and pe <= max_pe:
            out[sym] = r
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = fundamentals_batch(["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"])
    for sym, r in res.items():
        print(sym, {k: r.get(k) for k in ["ROCE", "ROE", "P/E", "P/B", "Cash/Debt"]})
