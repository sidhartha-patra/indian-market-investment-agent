"""Moneycontrol scraper for Indian equity price, sentiment, and ratings."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}

AUTOSUGGEST_URLS = (
    "https://www.moneycontrol.com/mccode/common/autosuggesion.php?query={query}&type=1",
    "https://www.moneycontrol.com/mccode/common/autosuggestion.php?query={query}&type=1",
)

FIELDS = (
    "live_price",
    "price_change_pct",
    "day_high",
    "day_low",
    "52w_high",
    "52w_low",
    "tech_rating",
    "buy_pct",
    "sell_pct",
    "hold_pct",
    "community_sentiment_pct",
)


def _number(text: str | None) -> float | None:
    """Extract the first number from a Moneycontrol text fragment."""
    try:
        if not text:
            return None
        match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", unescape(text))
        return float(match.group(0).replace(",", "")) if match else None
    except Exception:  # noqa: BLE001
        return None


def _text(tag) -> str | None:
    try:
        return tag.get_text(" ", strip=True) if tag else None
    except Exception:  # noqa: BLE001
        return None


def _first_number(soup: BeautifulSoup, *, id_: str | None = None,
                  class_part: str | None = None) -> float | None:
    try:
        tag = soup.find(id=id_) if id_ else None
        if tag is None and class_part:
            tag = soup.find(class_=lambda c: c and class_part.lower() in c.lower())
        return _number(tag.get("rel") or _text(tag)) if tag else None
    except Exception:  # noqa: BLE001
        return None


def _strip_jsonp(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("[") or raw.startswith("{"):
        return raw
    match = re.search(r"^[\w$.]+\((.*)\)\s*;?$", raw, flags=re.S)
    return match.group(1) if match else raw


def _parse_autosuggest_json(raw: str) -> dict | None:
    try:
        data = json.loads(_strip_jsonp(raw))
        if isinstance(data, dict):
            rows = data.get("data") or data.get("results") or data.get("items") or [data]
        else:
            rows = data
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        link = row.get("link_src") or row.get("link") or row.get("url")
        if not link:
            return None
        return {
            "name": row.get("name") or row.get("stock_name") or row.get("company") or row.get("title"),
            "symbol": row.get("symbol") or row.get("stock_symbol") or row.get("sc_ticker"),
            "sc_id": row.get("sc_id") or row.get("scid") or row.get("id"),
            "link_src": link,
        }
    except Exception:  # noqa: BLE001
        return None


def _parse_autosuggest_html(raw: str) -> dict | None:
    try:
        soup = BeautifulSoup(raw, "html.parser")
        anchor = soup.select_one("ul.suglist li a[href]") or soup.find("a", href=True)
        if not anchor:
            return None
        span_text = _text(anchor.find("span")) or ""
        parts = [p.strip() for p in span_text.split(",")]
        link = anchor.get("href")
        return {
            "name": anchor.get("title") or next(anchor.stripped_strings, None),
            "symbol": parts[1] if len(parts) > 1 else None,
            "sc_id": link.rstrip("/").split("/")[-1] if link else None,
            "link_src": link,
        }
    except Exception:  # noqa: BLE001
        return None


def search_mc(query: str) -> dict | None:
    """Return {name, symbol, sc_id, link_src (url)} for first match or None."""
    q = quote_plus(query.strip())
    if not q:
        return None
    for template in AUTOSUGGEST_URLS:
        url = template.format(query=q)
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code in {403, 429}:
                logger.warning("Moneycontrol autosuggest blocked for %s: HTTP %s", query, res.status_code)
                return None
            if res.status_code != 200:
                logger.debug("Moneycontrol autosuggest endpoint failed for %s: HTTP %s", query, res.status_code)
                continue
            match = _parse_autosuggest_json(res.text) or _parse_autosuggest_html(res.text)
            if match:
                return match
        except Exception as exc:  # noqa: BLE001
            logger.debug("Moneycontrol autosuggest endpoint failed for %s: %s", query, exc)
    logger.warning("Moneycontrol autosuggest returned no match for %s", query)
    return None


def _price_change_pct(soup: BeautifulSoup) -> float | None:
    try:
        tag = soup.find(id="nsechange") or soup.find(id="bsechange")
        text = _text(tag)
        match = re.search(r"\(([-+]?\d+(?:\.\d+)?)%\)", text or "")
        return float(match.group(1)) if match else None
    except Exception:  # noqa: BLE001
        return None


def _map_tech_rating(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[_\s-]+", " ", value).strip().lower()
    mapping = {
        "very bullish": "Strong Buy",
        "strong buy": "Strong Buy",
        "bullish": "Buy",
        "buy": "Buy",
        "neutral": "Neutral",
        "bearish": "Sell",
        "sell": "Sell",
        "very bearish": "Strong Sell",
        "strong sell": "Strong Sell",
    }
    return mapping.get(normalized)


def _tech_rating(soup: BeautifulSoup, html: str) -> str | None:
    try:
        match = re.search(r'id=["\']topTechnicalIndi["\'][^>]*>([^<]+)', html, flags=re.I)
        rating = _map_tech_rating(match.group(1) if match else None)
        if rating:
            return rating

        tag = soup.find(id=re.compile(r"TechnicalIndi$", re.I))
        rating = _map_tech_rating(_text(tag))
        if rating:
            return rating

        section = soup.find(class_=lambda c: c and ("tech_rating" in c.lower() or "techrating" in c.lower()))
        if section:
            visible = section.find(style=lambda s: s and "display: none" not in s.lower())
            rating = _map_tech_rating(visible.get("title") or _text(visible) if visible else None)
            if rating:
                return rating
    except Exception:  # noqa: BLE001
        return None
    return None


def _pct_from_graphblocks(soup: BeautifulSoup) -> tuple[float | None, float | None, float | None]:
    vals: dict[str, float] = {}
    try:
        graph = soup.find(id="anRatingGraph")
        for block in graph.select(".graphblock") if graph else []:
            heading = (_text(block.find(class_="heading")) or "").lower()
            pct_tag = block.find(class_="percentage")
            value = _number(_text(pct_tag))
            if value is None:
                bar = block.find(style=re.compile(r"width", re.I))
                style = bar.get("style", "") if bar else ""
                match = re.search(r"width\s*:\s*([-+]?\d+(?:\.\d+)?)%", style, flags=re.I)
                value = float(match.group(1)) if match and float(match.group(1)) > 0 else None
            if heading and value is not None:
                vals[heading] = value
    except Exception:  # noqa: BLE001
        return None, None, None
    buy = vals.get("buy")
    sell = vals.get("sell")
    hold = vals.get("hold")
    return buy, sell, hold


def _pct_from_buy_sell_list(soup: BeautifulSoup) -> tuple[float | None, float | None, float | None]:
    vals: dict[str, float] = {}
    try:
        for ul in soup.select("div.chart_fl ul, ul.buy_sellper"):
            for li in ul.find_all("li"):
                text = _text(li) or ""
                value = _number(text)
                lower = text.lower()
                if "buy" in lower and value is not None:
                    vals["buy"] = value
                elif "sell" in lower and value is not None:
                    vals["sell"] = value
                elif "hold" in lower and value is not None:
                    vals["hold"] = value
            if {"buy", "sell", "hold"}.issubset(vals):
                break
    except Exception:  # noqa: BLE001
        return None, None, None
    return vals.get("buy"), vals.get("sell"), vals.get("hold")


def _community_sentiment_pct(soup: BeautifulSoup, html: str) -> float | None:
    try:
        section_match = re.search(
            r"Community Sentiments(?P<section>.{0,8000}?)(?:</script>|</div>\s*</div>\s*</div>)",
            html,
            flags=re.I | re.S,
        )
        section = section_match.group("section") if section_match else html
        match = re.search(r"buy_percentage\s*=\s*parseInt\(['\"]?([-+]?\d+(?:\.\d+)?)", section, flags=re.I)
        if match:
            return float(match.group(1))
        buy = soup.find(class_=lambda c: c and "buy_results" in c.lower())
        if buy and buy.get("result") is not None:
            return _number(buy.get("result"))
    except Exception:  # noqa: BLE001
        return None
    return None


def get_mc_data(url: str) -> dict:
    """Scrape Moneycontrol price, rating, recommendation, and community sentiment fields."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
        if res.status_code in {403, 429}:
            logger.warning("Moneycontrol blocked detail page %s: HTTP %s", url, res.status_code)
            return {}
        if res.status_code != 200:
            logger.warning("Moneycontrol detail page failed %s: HTTP %s", url, res.status_code)
            return {}
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Moneycontrol detail scrape failed for %s: %s", url, exc)
        return {}

    data = {field: None for field in FIELDS}
    try:
        data["live_price"] = _first_number(soup, id_="nsecp") or _first_number(soup, id_="bsecp", class_part="pcstkspr")
    except Exception:  # noqa: BLE001
        pass
    try:
        data["price_change_pct"] = _price_change_pct(soup)
    except Exception:  # noqa: BLE001
        pass
    try:
        data["day_high"] = _first_number(soup, id_="sp_high") or _first_number(soup, class_part="nseHP")
    except Exception:  # noqa: BLE001
        pass
    try:
        data["day_low"] = _first_number(soup, id_="sp_low") or _first_number(soup, class_part="nseLP")
    except Exception:  # noqa: BLE001
        pass
    try:
        data["52w_high"] = _first_number(soup, class_part="nseH52") or _first_number(soup, class_part="bseH52")
    except Exception:  # noqa: BLE001
        pass
    try:
        data["52w_low"] = _first_number(soup, class_part="nseL52") or _first_number(soup, class_part="bseL52")
    except Exception:  # noqa: BLE001
        pass
    try:
        data["tech_rating"] = _tech_rating(soup, res.text)
    except Exception:  # noqa: BLE001
        pass
    try:
        buy, sell, hold = _pct_from_graphblocks(soup)
        if buy is None and sell is None and hold is None:
            buy, sell, hold = _pct_from_buy_sell_list(soup)
        data["buy_pct"], data["sell_pct"], data["hold_pct"] = buy, sell, hold
    except Exception:  # noqa: BLE001
        pass
    try:
        data["community_sentiment_pct"] = _community_sentiment_pct(soup, res.text)
    except Exception:  # noqa: BLE001
        pass

    return data


def _search_and_scrape(symbol: str) -> tuple[str, dict]:
    try:
        match = search_mc(symbol)
        if not match or not match.get("link_src"):
            return symbol, {}
        data = get_mc_data(match["link_src"])
        if data:
            data.update({k: v for k, v in match.items() if k != "link_src"})
            data["url"] = match["link_src"]
        return symbol, data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Moneycontrol batch failed for %s: %s", symbol, exc)
        return symbol, {}


def get_mc_batch(symbols: list[str], max_workers: int = 8) -> dict[str, dict]:
    """Resolve each symbol with Moneycontrol autosuggest, then scrape details."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_search_and_scrape, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                key, data = fut.result()
                out[key] = data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Moneycontrol batch failed for %s: %s", sym, exc)
                out[sym] = {}
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for symbol, data in get_mc_batch(["RELIANCE", "TCS"], max_workers=2).items():
        print(symbol, data)
