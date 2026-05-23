"""Screener.in fundamentals scraper for Indian equities."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

logger = logging.getLogger(__name__)

BASE_URL = "https://www.screener.in/company/{symbol}{suffix}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 20

TOP_RATIO_KEYS = {
    "Market Cap",
    "Current Price",
    "High / Low",
    "High/Low",
    "Stock P/E",
    "Book Value",
    "Dividend Yield",
    "ROCE",
    "ROE",
    "Face Value",
}


_numeric_re = re.compile(r"-?\d+(?:,\d+)*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _clean_symbol(symbol: str) -> str:
    return symbol.upper().replace(".NS", "").replace(".BO", "").strip()


def _parse_number(value: str | None) -> float | None:
    if not value:
        return None
    text = (
        value.replace("₹", "")
        .replace("Rs.", "")
        .replace("Cr.", "")
        .replace("Cr", "")
        .replace("Crore", "")
        .replace("%", "")
        .replace(",", "")
        .strip()
    )
    if not text or text in {"-", "--"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_numbers(value: str | None) -> list[float]:
    if not value:
        return []
    text = value.replace(",", "")
    out: list[float] = []
    for match in _numeric_re.finditer(text):
        try:
            out.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _normalize_label(label: str) -> str:
    return " ".join(label.replace("+", "").split()).strip()


def _last_number(values: Iterable[str]) -> float | None:
    for value in reversed(list(values)):
        parsed = _parse_number(value)
        if parsed is not None:
            return parsed
    return None


def _row_values(section: Tag | None, label_contains: str) -> list[str]:
    if not section:
        return []
    needle = label_contains.lower()
    for row in section.select("table.data-table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if cells and needle in _normalize_label(cells[0]).lower():
            return cells[1:]
    return []


def _parse_top_ratios(soup: BeautifulSoup) -> dict:
    ratios: dict = {}
    for item in soup.select("#top-ratios li, li.flex.flex-space-between"):
        name_tag = item.select_one("span.name")
        value_tag = item.select_one("span.number, span.value")
        if not name_tag or not value_tag:
            continue
        label = _normalize_label(name_tag.get_text(" ", strip=True))
        value_text = value_tag.get_text(" ", strip=True)
        if label not in TOP_RATIO_KEYS:
            continue
        key = "High/Low" if label in {"High / Low", "High/Low"} else label
        if key == "High/Low":
            numbers = _parse_numbers(value_text)
            ratios[key] = numbers[:2] if len(numbers) >= 2 else None
            if len(numbers) >= 2:
                ratios["High"] = numbers[0]
                ratios["Low"] = numbers[1]
        else:
            ratios[key] = _parse_number(value_text)
    if "Stock P/E" in ratios:
        ratios["P/E"] = ratios["Stock P/E"]
    return ratios


def _parse_growth_tables(soup: BeautifulSoup) -> dict:
    ratios: dict = {}
    mapping = {
        "Compounded Sales Growth": "Sales Growth",
        "Compounded Profit Growth": "Profit Growth",
    }
    for table in soup.select("table.ranges-table"):
        header = table.find("th")
        if not header:
            continue
        base_key = mapping.get(_normalize_label(header.get_text(" ", strip=True)))
        if not base_key:
            continue
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) != 2:
                continue
            period = cells[0].replace(":", "").strip()
            if period == "3 Years":
                ratios[f"{base_key} 3Yrs"] = _parse_number(cells[1])
            elif period == "5 Years":
                ratios[f"{base_key} 5Yrs"] = _parse_number(cells[1])
    return ratios


def _parse_company_ratios(soup: BeautifulSoup) -> dict:
    ratios = _parse_growth_tables(soup)

    profit_loss = soup.select_one("section#profit-loss")
    balance_sheet = soup.select_one("section#balance-sheet")
    shareholding = soup.select_one("section#shareholding")

    ratios["OPM"] = _last_number(_row_values(profit_loss, "OPM %"))

    borrowings = _last_number(_row_values(balance_sheet, "Borrowings"))
    equity_capital = _last_number(_row_values(balance_sheet, "Equity Capital"))
    reserves = _last_number(_row_values(balance_sheet, "Reserves"))
    if borrowings is not None and equity_capital is not None and reserves is not None:
        equity = equity_capital + reserves
        ratios["Debt to Equity"] = round(borrowings / equity, 4) if equity else None
    else:
        ratios["Debt to Equity"] = None

    ratios["Promoter Holding"] = _last_number(_row_values(shareholding, "Promoters"))
    if ratios["Promoter Holding"] is None:
        meta = soup.select_one('meta[name="description"]')
        content = meta.get("content", "") if meta else ""
        match = re.search(r"Promoter Holding:\s*([\d,.]+)%", content, re.IGNORECASE)
        ratios["Promoter Holding"] = _parse_number(match.group(1)) if match else None

    return ratios


def _parse_quarters(soup: BeautifulSoup) -> dict:
    section = soup.select_one("section#quarters")
    if not section:
        return {"Quarterly Results": []}

    table = section.select_one("table.data-table")
    if not table:
        return {"Quarterly Results": []}

    header_row = table.find("tr")
    headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all(["th", "td"])] if header_row else []
    quarters = headers[1:]
    sales = _row_values(section, "Sales")
    net_profit = _row_values(section, "Net Profit")

    results = []
    count = min(len(quarters), len(sales), len(net_profit))
    start = max(0, count - 4)
    for idx in range(start, count):
        results.append(
            {
                "Quarter": quarters[idx],
                "Revenue": _parse_number(sales[idx]),
                "Net Profit": _parse_number(net_profit[idx]),
            }
        )
    return {"Quarterly Results": results}


def _fetch_screener_page(symbol: str) -> BeautifulSoup | None:
    sym = _clean_symbol(symbol)
    last_status: int | None = None
    for suffix in ("/consolidated", ""):
        url = BASE_URL.format(symbol=sym, suffix=suffix)
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Screener request failed for %s (%s): %s", sym, url, exc)
            return None
        last_status = response.status_code
        if response.status_code == 404 and suffix:
            continue
        if response.status_code != 200:
            logger.warning("Screener returned HTTP %s for %s", response.status_code, url)
            return None
        return BeautifulSoup(response.text, "html.parser")
    logger.warning("Screener page not found for %s (last HTTP %s)", sym, last_status)
    return None


@lru_cache(maxsize=1024)
def get_screener_fundamentals(symbol: str) -> dict:
    """Fetch key Screener.in fundamentals for a symbol without exchange suffix."""
    sym = _clean_symbol(symbol)
    try:
        soup = _fetch_screener_page(sym)
        if soup is None:
            return {}
        fundamentals: dict = {}
        fundamentals.update(_parse_top_ratios(soup))
        fundamentals.update(_parse_company_ratios(soup))
        fundamentals.update(_parse_quarters(soup))
        return fundamentals
    except Exception as exc:  # noqa: BLE001
        logger.warning("Screener parse failed for %s: %s", sym, exc)
        return {}


def get_screener_batch(symbols: list[str], max_workers: int = 5) -> dict[str, dict]:
    """Fetch Screener.in fundamentals concurrently for multiple symbols."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_screener_fundamentals, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                out[symbol] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Screener batch failed for %s: %s", symbol, exc)
                out[symbol] = {}
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    keys = ["Market Cap", "Stock P/E", "ROCE", "ROE", "Debt to Equity", "Profit Growth 5Yrs"]
    data = get_screener_batch(["RELIANCE", "TCS", "INFY"])
    for symbol, fundamentals in data.items():
        summary = {key: fundamentals.get(key) for key in keys}
        print(symbol, summary)
