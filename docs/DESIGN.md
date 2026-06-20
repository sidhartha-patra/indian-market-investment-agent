# Design — Indian Market Stock Suggester & Price Predictor

> **Scope.** A *decision-support* tool that produces **probabilistic, explainable, risk-aware**
> insights for the Indian market (with optional US support). It does **not** give financial
> advice, guarantee predictions, or promise returns. Every output carries uncertainty, risk,
> data-freshness, and an explicit statement of model limitations.

This document is the canonical design. It is intentionally skeptical: most of the work in a
responsible tool is *preventing* over-claiming (look-ahead bias, survivorship bias, data
snooping, false precision in price forecasts), not adding more signals.

---

## A. System Design

```
 Schedulers ─▶ 1. Ingestion ─▶ 2. Storage ─▶ 3. Feature Engineering ─┬─▶ 4. Prediction Service
 (APScheduler)  yfinance/RSS    DuckDB/PG     point-in-time, leak-safe │     naive→linear→RF/XGB
                                                                       └─▶ 5. Suggestion/Ranking
                                                                             multi-factor + regime
                              6. Backtesting (prediction + strategy tracks, costs/slippage)
                              7. API (FastAPI) ─▶ 8. Frontend (Streamlit→React, Plotly)
                              9. Model registry (MLflow/light) · 10. Audit & monitoring
```

**Principles**
- **Separate history from the future.** Technical/fundamental *analysis* of the past is
  reported as fact; anything forward-looking is a probability with an interval.
- **Point-in-time everything.** A feature at date *t* uses only data available at *t*.
- **Baselines are mandatory.** No model ships without being compared to a naive random walk.
- **Stack:** Python 3.12 · pandas/numpy · scikit-learn · XGBoost · (PyTorch only if it beats
  trees out-of-sample) · FastAPI · Streamlit→React · DuckDB→Postgres · MLflow · Plotly.

### Module map (this repo)
| Layer | Code |
|---|---|
| Ingestion | `src/ingestion/*` (prices, news, fundamentals, mutual funds, broker) |
| Indicators | `src/strategies/indicators.py` |
| Strategies / ranking | `src/strategies/{momentum,quality,volatile_movers,mean_reversion,trend_following,relative_strength,factor_model,multibagger}.py` |
| Risk | `src/risk/{regime,position_sizing}.py` |
| Portfolio | `src/portfolio/optimizer.py` |
| Prediction (ML) | `src/ml/{features,models,evaluate}.py` |
| Explainability | `src/ml/explain.py` |
| Backtesting | `src/backtest/{metrics,engine}.py` |
| Orchestration | `src/agent/recommend.py`, `src/scheduler.py` |
| UI | `app/dashboard.py` |

---

## B. Data Model

| Table | Key columns | Notes |
|---|---|---|
| `instruments` | symbol, name, exchange, sector, industry, mcap_band, is_liquid, country | universe + penny/illiquid flags |
| `prices_daily` | symbol, date, OHLC, adj_close, volume | adjusted; keep delisted (no survivorship) |
| `prices_weekly` | symbol, week, OHLCV | resampled view |
| `index_prices` | index, date, OHLCV | NIFTY50/MIDCAP, S&P500 |
| `fundamentals` | symbol, report_date, **as_of_date**, revenue, profit, eps, pe, pb, roe, de, fcf, promoter_hold, inst_hold | `as_of_date` lags filing to prevent leakage |
| `sector_map` | symbol, sector, sub_industry, index_membership | |
| `news` / `sentiment` | symbol, ts, source, headline, score, magnitude, label | sentiment flagged **weak/noisy** |
| `features` | symbol, date, feature..., target | parquet/long; computed point-in-time |
| `predictions` | symbol, asof_date, horizon, model_id, point, q_lo, q_hi, prob_up, confidence | always stored with interval |
| `model_registry` | model_id, type, params, train_window, metrics_json, status, created_at | MLflow-style |
| `backtests` | run_id, kind, period, cost_bps, slippage_bps, metrics_json, equity_curve | reproducible config |
| `watchlists` / `suggestions` | user_id, symbol, score, tier, rationale_json, asof | explainability payload |
| `runs_audit` | run_id, stage, status, rows, started/ended, error | pipeline observability |

Storage: **DuckDB** for the prototype (single-file, fast analytical SQL), **Postgres** in prod.

---

## C. Feature Engineering Plan

All features are backward-looking; the target is a **forward return** (not a price level).

- **Returns:** 1/5/10/21/63/252-day returns; 12-1 momentum (skip last month).
- **Technical:** SMA/EMA(20/50/200), RSI(2/14), MACD histogram, Bollinger %B & bandwidth,
  ATR%, ADX/±DI, Supertrend, volume z-score, distance-from-MA.
- **Volatility/risk:** realised vol (21/63), max-drawdown features, beta vs index.
- **Liquidity:** 20-day ADV (₹), Amihud illiquidity, %% zero-volume days → hard filter.
- **Cross-sectional:** winsorise + z-score / percentile-rank **within sector & date**.
- **Fundamental:** growth (rev/profit/EPS), value (E/P, B/P, FCF yield), quality (ROE/ROCE,
  D/E), ownership — keyed on `as_of_date`.
- **Sentiment:** rolling polarity, novelty/volume spikes, event flags — low weight.
- **Targets:** forward 5/21-day return (regression), sign (classification), vol-scaled return.
- **Leakage controls:** scalers fit on the train fold only; fundamentals lagged to availability.

Implemented in `src/ml/features.py` (`FEATURE_COLUMNS`, `make_dataset`, `latest_features`).

---

## D. ML Modeling Plan

**Honest framing.** Daily price ≈ random walk; beating naive on RMSE is hard. We therefore
predict **distributions + direction** and *always* benchmark against naive.

| Tier | Model | Output | Status |
|---|---|---|---|
| 0 | Naive random walk, MA-drift | point | `NaiveRandomWalk`, `MovingAverageDrift` |
| 1 | Ridge (scaled) | point | `ridge` |
| 2 | RandomForest, **XGBoost** | point + conformal interval | `random_forest`, `xgboost` |
| 3 | LSTM / Temporal Fusion Transformer | seq + quantile | **only if** it beats Tier-2 OOS |

- **Intervals:** model-agnostic **split conformal** (`ConformalRegressor`) → finite-sample
  coverage; plus empirical `prob_up`.
- **Validation:** expanding **walk-forward** with **purge/embargo = horizon** (`evaluate.py`).
- **Anti-overfit:** few motivated features, regularisation, depth caps, no test-window tuning.

---

## E. Suggestion / Ranking Algorithm

```
factors = {momentum, value, growth, quality, low_vol, trend, liquidity, sentiment(weak), sector_trend}
z_f      = winsorise → zscore(factor)  within {date, sector}
score    = Σ w_f(risk_profile) · z_f            # weights vary by Conservative/Balanced/Aggressive
score   *= regime_exposure_multiplier            # RISK_ON / NEUTRAL / RISK_OFF gate
filters  : drop illiquid/penny (unless enabled), drop stale data, cap sector concentration
rank → percentile → tier {Strong / Watch / Avoid} + confidence(score, model agreement, freshness)
```

Implemented across `factor_model.py` (composite), `relative_strength.py`, `regime.py`
(exposure gate), `optimizer.py` (sizing), `explain.py` (rationale + confidence). Risk-profile
presets shift weights: Conservative → quality/low-vol/liquidity; Aggressive → momentum/growth.

---

## F. Backtesting Plan

Two independent tracks:
1. **Prediction backtest** (`ml/evaluate.py`) — walk-forward retrain; MAE/RMSE/MAPE,
   directional accuracy, interval coverage; skill vs naive.
2. **Strategy backtest** (`backtest/engine.py`) — periodic cross-sectional rebalance of top-N;
   **transaction cost (bps) + slippage (bps)** charged on turnover; long-only with position
   caps; benchmark comparison.

**Bias controls:** point-in-time data, no survivorship drop, no look-ahead (the ranker only
sees `prices.loc[:t]`), out-of-sample + walk-forward, **Deflated Sharpe Ratio**
(`metrics.deflated_sharpe`) to discount multiple-testing, turnover/cost reporting.

---

## G. Evaluation Metrics

- **Prediction:** MAE, RMSE, price-MAPE, directional accuracy, **prediction-interval coverage**
  (e.g. an 80%% band should contain ~80%% of outcomes), skill vs naive (RMSE ratio).
- **Strategy:** CAGR, Sharpe, Sortino, Calmar, max drawdown, win rate, avg gain/loss & payoff,
  turnover, alpha/beta & information ratio vs benchmark, Deflated Sharpe.
- **Calibration:** reliability of `prob_up`; coverage table for the conformal band.

---

## H. UI / Dashboard Design

Pages: **Dashboard** (regime banner + index + top suggestions) · **Stock search** ·
**Watchlist** · **Stock detail** (price + indicators + Plotly **forecast fan-chart** with the
conformal band) · **Suggested Stocks** (ranked, filter by risk profile/liquidity) ·
**Risk Dashboard** (vol, drawdown, ATR stop, position size, sector concentration, R/R) ·
**Explanation panel** (positives, negatives, risk level, confidence, freshness, limitations).
A persistent **uncertainty + disclaimer** footer is always visible.

---

## I. Implementation Milestones

| Phase | Scope | Status |
|---|---|---|
| **1 MVP** | OHLCV fetch, indicators, naive/MA baseline, dashboard, watchlist | ✅ ingestion+dashboard+indicators |
| **2 Suggestion engine** | factor ranking, risk scoring, fundamentals, explainability | ✅ rankers/regime/sizing/optimiser/explain |
| **3 ML models** | features, RF/XGB + conformal, naive comparison, dual backtesting, metrics | ✅ `src/ml/*`, `src/backtest/*` |
| **4 Production hardening** | scheduled refresh, logging, validation, drift checks, monitoring | ⏳ scheduler exists; validation/drift pending |

---

## J. Risks & Limitations

- **Modeling:** markets are largely efficient and non-stationary; models fail on news shocks,
  earnings surprises, macro events and black swans; a good backtest ≠ live edge (overfitting,
  regime change, capacity limits).
- **Data:** yfinance gaps/adjustment errors, fundamentals lag, sentiment is noisy; NSE/BSE and
  vendor data carry **licensing constraints** — no scraping where terms prohibit it.
- **Execution:** slippage/impact on illiquid names; costs erode thin edges.
- **Product/legal:** **not SEBI/SEC-registered advice**; educational and decision-support only;
  no guaranteed returns; penny/illiquid names disabled by default; every suggestion shows
  confidence, risk, freshness and limitations.
