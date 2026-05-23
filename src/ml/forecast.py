"""Forward price forecaster: SARIMAX (default) + optional Prophet."""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _try_prophet():
    try:
        from prophet import Prophet  # type: ignore

        return Prophet
    except ImportError:
        return None


def forecast_sarimax(close: pd.Series, horizon: int = 5) -> dict:
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    y = close.dropna().tail(250)
    model = SARIMAX(
        y,
        order=(2, 1, 2),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False)
    fc = fit.get_forecast(steps=horizon)
    mean = fc.predicted_mean
    ci = fc.conf_int(alpha=0.05)
    # Simple backtest: in-sample 1-step residual MAPE
    resid_pct = (fit.resid / y).abs().dropna()
    mape = float(resid_pct.mean() * 100) if len(resid_pct) else None
    return {
        "engine_used": "SARIMAX",
        "point_forecast": [float(x) for x in mean.values],
        "lower_95": [float(x) for x in ci.iloc[:, 0].values],
        "upper_95": [float(x) for x in ci.iloc[:, 1].values],
        "mape_backtest_pct": mape,
    }


def forecast_prophet(close: pd.Series, horizon: int = 5):
    Prophet = _try_prophet()
    if Prophet is None:
        return None
    df = close.dropna().reset_index()
    df.columns = ["ds", "y"]
    m = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=True)
    m.fit(df)
    future = m.make_future_dataframe(periods=horizon, freq="B")
    fc = m.predict(future).tail(horizon)
    return {
        "engine_used": "Prophet",
        "point_forecast": fc["yhat"].tolist(),
        "lower_95": fc["yhat_lower"].tolist(),
        "upper_95": fc["yhat_upper"].tolist(),
        "mape_backtest_pct": None,
    }


def forecast_price(
    df: pd.DataFrame,
    horizon_days: int = 5,
    engine: str = "auto",
) -> dict:
    """df must have a 'Close' column with a DatetimeIndex (business days OK)."""
    if "Close" not in df.columns or len(df) < 60:
        return {"error": "insufficient_data"}
    close = df["Close"].dropna()
    last_price = float(close.iloc[-1])
    last_date = close.index[-1]

    result = None
    if engine in ("auto", "prophet"):
        result = forecast_prophet(close, horizon_days)
    if result is None:
        result = forecast_sarimax(close, horizon_days)

    # Build forecast dates as next N business days
    future_dates = pd.bdate_range(
        start=last_date + pd.Timedelta(days=1),
        periods=horizon_days,
    ).strftime("%Y-%m-%d").tolist()

    end_price = result["point_forecast"][-1]
    expected_return = (end_price / last_price - 1) * 100
    direction = "UP" if expected_return > 1 else "DOWN" if expected_return < -1 else "FLAT"

    return {
        **result,
        "dates": future_dates,
        "last_close": last_price,
        "last_date": str(last_date.date()),
        "expected_return_pct": round(expected_return, 2),
        "direction": direction,
        "horizon_days": horizon_days,
    }


def forecast_batch(price_data: dict, horizon_days: int = 5) -> dict[str, dict]:
    out = {}
    for ticker, df in price_data.items():
        try:
            out[ticker] = forecast_price(df, horizon_days=horizon_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Forecast failed for %s: %s", ticker, exc)
            out[ticker] = {"error": str(exc)}
    return out


if __name__ == "__main__":
    import json

    from src.ingestion.prices import fetch_prices

    data = fetch_prices(["RELIANCE.NS"], period="1y")
    result = forecast_price(data["RELIANCE.NS"], horizon_days=5)
    print(json.dumps(result, indent=2, default=str))
