"""BSE bulk-deal ingestion for recent insider/promoter buying signals."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
import logging
import re
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

API_URL = "https://api.bseindia.com/BseIndiaAPI/api/BulkDeals/w"
FALLBACK_URL = "https://www.bseindia.com/markets/equity/EQReports/bulk_blockdeals.aspx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}
COLUMNS = ["date", "scrip_code", "scrip_name", "client_name", "buy_sell", "qty", "price"]
PROMOTER_RE = re.compile(r"PROMOTER|PROMOTER GROUP|HOLDING|HOLDINGS|HUF|TRUST|LLP|PVT|PRIVATE|FAMILY", re.I)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _pick(row: dict, *names: str) -> Any:
    normalized = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in row.items()}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in normalized:
            return normalized[key]
    return None


def _normalize_records(records: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for raw in records:
        row = {
            "date": _parse_date(_pick(raw, "Deal Date", "Date", "DT_TM", "dealdate")),
            "scrip_code": str(_pick(raw, "Security Code", "Scrip Code", "SC_CODE", "scode") or "").strip(),
            "scrip_name": str(_pick(raw, "Security Name", "Scrip Name", "SC_NAME", "scripname") or "").strip(),
            "client_name": str(_pick(raw, "Client Name", "CLIENT_NAME", "clientname") or "").strip(),
            "buy_sell": str(_pick(raw, "Buy/Sell", "B/S", "BUY_SELL", "buysell") or "").strip().upper(),
            "qty": _to_float(_pick(raw, "Quantity", "Qty", "QTY_TRD", "qty")) or 0.0,
            "price": _to_float(_pick(raw, "Trade Price", "Price", "WATP", "price")) or 0.0,
        }
        if row["client_name"] or row["scrip_name"]:
            rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty_frame()


def _fetch_api(from_date: date, to_date: date) -> pd.DataFrame:
    params = {"Fdate": from_date.strftime("%Y%m%d"), "Tdate": to_date.strftime("%Y%m%d"), "Scode": ""}
    response = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    records = payload.get("Table") or payload.get("data") or payload.get("BulkDeals") or []
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        records = []
    return _normalize_records(records)


def _fetch_scrape() -> pd.DataFrame:
    response = requests.get(FALLBACK_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for table in soup.find_all("table"):
        rows = []
        headers: list[str] = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if not cells:
                continue
            if not headers and tr.find_all("th"):
                headers = cells
                continue
            if not headers:
                headers = [f"col_{idx}" for idx in range(len(cells))]
            if len(cells) >= len(headers):
                rows.append(dict(zip(headers, cells, strict=False)))
        frame = _normalize_records(rows)
        if not frame.empty:
            return frame
    return _empty_frame()


@lru_cache(maxsize=8)
def get_bulk_deals(days_back: int = 30) -> pd.DataFrame:
    """Fetch recent BSE bulk deals. Returns an empty frame on blocking/failure."""
    to_date = date.today()
    from_date = to_date - timedelta(days=max(days_back, 1))
    try:
        frame = _fetch_api(from_date, to_date)
        if not frame.empty:
            return frame
        logger.warning("BSE bulk-deals API returned no rows; trying scrape fallback")
    except Exception as exc:  # noqa: BLE001
        logger.warning("BSE bulk-deals API unavailable: %s", exc)

    try:
        frame = _fetch_scrape()
        if frame.empty:
            logger.warning("BSE bulk-deals scrape returned no rows")
        return frame
    except Exception as exc:  # noqa: BLE001
        logger.warning("BSE bulk-deals scrape unavailable: %s", exc)
        return _empty_frame()


def _matches_symbol(row: pd.Series, symbol: str) -> bool:
    sym = symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    code = str(row.get("scrip_code", "")).upper().strip()
    name = str(row.get("scrip_name", "")).upper().strip()
    return bool(sym and (sym == code or sym in name or name in {sym}))


def get_promoter_buying(symbol: str, days_back: int = 30) -> dict:
    """Return suspected promoter/insider net buying summary for a symbol."""
    try:
        deals = get_bulk_deals(days_back=days_back)
        if deals.empty:
            return {}
        matched = deals[deals.apply(lambda row: _matches_symbol(row, symbol), axis=1)]
        if matched.empty:
            return {"promoter_net_buy_qty": 0, "promoter_buy_count": 0, "last_buy_date": None}

        suspected = matched[matched["client_name"].astype(str).str.contains(PROMOTER_RE, na=False)]
        if suspected.empty:
            return {"promoter_net_buy_qty": 0, "promoter_buy_count": 0, "last_buy_date": None}

        buys = suspected[suspected["buy_sell"].astype(str).str.startswith("B")]
        sells = suspected[suspected["buy_sell"].astype(str).str.startswith("S")]
        net_qty = float(buys["qty"].sum() - sells["qty"].sum())
        last_buy_date = None
        if not buys.empty:
            last_buy_date = str(buys["date"].dropna().max())
        return {
            "promoter_net_buy_qty": int(net_qty) if net_qty.is_integer() else net_qty,
            "promoter_buy_count": int(len(buys)),
            "last_buy_date": last_buy_date,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Promoter buying check failed for %s: %s", symbol, exc)
        return {}
