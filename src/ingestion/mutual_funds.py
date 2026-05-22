"""Fetch mutual fund NAV data from AMFI."""
from __future__ import annotations
import logging
import io
import pandas as pd
import requests

logger = logging.getLogger(__name__)

AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"


def fetch_amfi_navs() -> pd.DataFrame:
    """Fetch all mutual fund NAVs from AMFI as a DataFrame."""
    resp = requests.get(AMFI_URL, timeout=30)
    resp.raise_for_status()
    rows = []
    current_amc = None
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue
        parts = line.split(";")
        if len(parts) >= 5:
            try:
                rows.append({
                    "scheme_code": parts[0].strip(),
                    "isin_growth": parts[1].strip(),
                    "isin_div": parts[2].strip(),
                    "scheme_name": parts[3].strip(),
                    "nav": float(parts[4].strip()) if parts[4].strip() not in ("N.A.", "") else None,
                    "date": parts[5].strip() if len(parts) > 5 else "",
                    "amc": current_amc,
                })
            except ValueError:
                continue
        elif "Mutual Fund" in line:
            current_amc = line
    df = pd.DataFrame(rows)
    logger.info("Loaded %d MF schemes", len(df))
    return df


def fetch_scheme_history(scheme_code: str) -> pd.DataFrame:
    """Get historical NAV for a single scheme via mfapi.in."""
    url = f"https://api.mfapi.in/mf/{scheme_code}"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y")
    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = fetch_amfi_navs()
    print(df.head())
    print(f"Total schemes: {len(df)}")
