"""Persist price predictions to SQLite for time series tracking + accuracy review."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from src.config import DATA_DIR

DB_PATH = DATA_DIR / "market.db"


def init_predictions_table() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            ticker TEXT NOT NULL,
            predicted_for_date TEXT NOT NULL,
            predicted_close REAL,
            lower_95 REAL,
            upper_95 REAL,
            generated_at TEXT NOT NULL,
            engine TEXT,
            horizon_days INTEGER,
            PRIMARY KEY (ticker, predicted_for_date, generated_at)
        );
        CREATE INDEX IF NOT EXISTS idx_pred_ticker_date
            ON predictions(ticker, predicted_for_date);
        """
    )
    return conn


def save_predictions_from_forecast(ticker: str, forecast: dict) -> int:
    if "error" in forecast or "point_forecast" not in forecast:
        return 0
    conn = init_predictions_table()
    gen_at = datetime.utcnow().isoformat()
    n = 0
    for d, pt, lo, hi in zip(
        forecast["dates"],
        forecast["point_forecast"],
        forecast["lower_95"],
        forecast["upper_95"],
    ):
        conn.execute(
            "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?)",
            (
                ticker,
                d,
                float(pt),
                float(lo),
                float(hi),
                gen_at,
                forecast.get("engine_used", ""),
                forecast.get("horizon_days", 0),
            ),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def save_batch_predictions(batch_forecasts: dict[str, dict]) -> int:
    total = 0
    for ticker, fc in batch_forecasts.items():
        total += save_predictions_from_forecast(ticker, fc)
    return total


def query_predictions(ticker: str | None = None, limit: int = 50) -> list[dict]:
    conn = init_predictions_table()
    sql = "SELECT * FROM predictions"
    params = ()
    if ticker:
        sql += " WHERE ticker = ?"
        params = (ticker,)
    sql += " ORDER BY generated_at DESC, predicted_for_date LIMIT ?"
    params = params + (limit,)
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


if __name__ == "__main__":
    from src.ingestion.prices import fetch_prices
    from src.ml.forecast import forecast_price

    data = fetch_prices(["RELIANCE.NS", "TCS.NS"], period="1y")
    all_fc = {sym: forecast_price(df, 5) for sym, df in data.items()}
    n = save_batch_predictions(all_fc)
    print(f"Saved {n} predictions")
    for row in query_predictions("RELIANCE.NS", limit=5):
        print(row)
