# 🇮🇳 Indian Market Investment Agent

An AI-powered agent that analyzes Indian stock market news (NSE/BSE) and mutual funds, then generates investment recommendations to help maximize earnings.

> ⚠️ **Disclaimer:** This project is for **educational purposes only**. It is **not** SEBI-registered investment advice. Always consult a qualified financial advisor before investing. Past performance does not guarantee future results.

---

## ✨ Features

- 📰 **News ingestion** from Moneycontrol, Economic Times, LiveMint, Business Standard RSS
- 📈 **8 strategy screeners**: momentum, quality, mean-reversion, trend-following,
  relative-strength, low-volatility, a **multi-factor composite**, and a multibagger framework
- 🧭 **Market-regime filter** (index trend + breadth) that gates equity exposure
- 🧮 **Portfolio optimisers**: equal / inverse-vol / min-variance / max-Sharpe / risk-parity
- 🛡️ **Risk engine**: ATR stop-loss, fixed-fractional position sizing, volatility targeting
- 🔮 **Probabilistic price prediction**: naive → ridge → RandomForest → XGBoost, each wrapped
  in **split-conformal intervals** with a calibrated `prob_up` — always benchmarked vs naive
- 🧾 **Explainability** for every pick: positives, negatives, risk level, confidence, freshness, limits
- 🌐 **All-stocks coverage**: dynamic NSE+BSE universe (~2,375 NSE live) via `EQUITY_L.csv` + Kite dump
- 🔬 **Detailed fundamental analysis**: Piotroski F-Score, Altman Z″, Beneish M-Score, Magic Formula,
  Graham, quality score, and a **sector-relative 0-100 composite** (bank P/E never vs IT P/E)
- 📡 **Bulk fundamentals** from the TradingView India scanner (8,000+ symbols, personal-research use)
- 🌍 **Shareable static website**: per-stock `/stock/SYMBOL.html` pages with the score + *why*,
  deployable free to GitHub Pages / Vercel / Cloudflare Pages
- 💰 **Mutual fund analyzer** using AMFI NAV data (rolling returns, Sharpe)
- 🤖 **LLM-powered sentiment** — GitHub Models (free) / OpenAI, marked as a *weak* signal
- 📊 **Streamlit dashboard** + 📱 **Telegram** alerts
- 🔁 **Built-in backtester** (lookahead-free, cost + slippage aware, deflated Sharpe)
- ⏰ **Scheduled runs** with APScheduler (pre-market, EOD)

> ⚖️ **Responsible by design:** no guaranteed predictions, no "buy" calls, no promised returns.
> Outputs are probabilistic and always show uncertainty, risk, data-freshness and model limits.
> See [`docs/DESIGN.md`](docs/DESIGN.md) for the full system design.

---

## 🔬 All-Stocks Fundamental Analysis & Shareable Website

New in this branch — pull fundamentals for the **entire** NSE+BSE universe, score every
stock with a transparent **sector-relative** model, and publish a shareable explainer site.

```bash
# 1. Fetch the full universe (~2,375 NSE + BSE), cached to data/
python -m src.ingestion.universe_fetch

# 2. End-to-end: TradingView fundamentals -> sector-relative scores -> static website
python -m scripts.build_all_stocks --limit 500     # writes ./site_live/
python -m scripts.build_all_stocks --demo          # offline demo -> ./site/

# 3. Open ./site/index.html (or deploy the folder to GitHub Pages / Vercel / Cloudflare)
```

**New modules**
| Module | Purpose |
|---|---|
| `src/ingestion/universe_fetch.py` | Full NSE+BSE list (`EQUITY_L.csv` + Kite dump), ISIN-deduped, cached, offline fallback |
| `src/ingestion/tradingview_scanner.py` | Bulk fundamentals + technicals from the TradingView India scanner (8,000+ symbols) |
| `src/strategies/fundamental_analysis.py` | Piotroski F-Score · Altman Z/Z″ · Beneish M-Score · Magic Formula · Graham · quality · **sector-relative 0-100 composite** with reasons |
| `src/strategies/factor_model.py` | `factor_composite(..., sector_map=...)` — optional **sector-neutral** z-scoring |
| `scripts/build_site.py` | Static per-stock `/stock/SYMBOL.html` site (score + why + metrics + disclaimer) |
| `scripts/build_all_stocks.py` | Orchestrates scanner → scoring → site |

> ⚠️ **Read before publishing (legal / SEBI):**
> - **Scraping Screener.in or TradingView for a *public* website is prohibited by their ToS.**
>   These modules are for **personal research only**. To publish, swap in a **licensed vendor**
>   (e.g. Twelve Data + Redistribution Add-On, or EODHD) for prices/fundamentals.
> - The generated site is **SEBI-safe by design**: scores are framed as **non-directional
>   educational data summaries**, never "Buy/Sell/Target", and every page carries a disclaimer.
>   Publishing explicit buy/sell calls would require **SEBI Research-Analyst registration**.
> - Full analysis: see the research report under `~/.copilot/.../research/` and `docs/DESIGN.md`.

---

## 🎯 Buy/Sell Signals, Multi-Horizon Outlook & Predictions

Every stock gets a transparent, rules-based **fundamental signal** plus a **short/mid/long-term outlook**:

- **Model signal** (`src/strategies/recommendation.py`): `STRONG_BUY / BUY / HOLD / SELL / AVOID`
  with **conviction /100** and *extreme detail* — pillar-by-pillar assessment, the frameworks
  (Piotroski F · Altman Z″ · Beneish M · Graham · quality), positives/negatives, 🚩 red flags,
  and "what would change the verdict".
- **Short / Mid / Long-term outlook**: short = technicals (50-DMA, 52-week position); mid = trend
  (50/200-DMA, golden/death cross); long = the fundamental verdict + an *illustrative* return
  scenario (≈ earnings growth + dividend yield). Gains are **scenarios, never promises**.
- **ML price prediction** (single stock, personal): conformal 5/21-day forecast with calibrated
  intervals + `P(up)` — `python -m src.strategies.recommendation RELIANCE`.

```bash
python -m src.strategies.recommendation TCS    # full detailed report + ML forecast (yfinance)
# in code: recommendation.recommend_from_screener("TCS")   # Screener.in fundamentals (personal)
```

### Data sources — "top picks from ALL stocks"

| `--source` | What it does | Use |
|---|---|---|
| `demo` | offline 2-stock sample | always works, public |
| `yfinance` | free, no key, full fundamentals for a list (default Nifty 200) | **public, free** |
| `hybrid` | **rank the WHOLE TradingView market → display the top N via yfinance** | **public "top 50 from all stocks"** |
| `tradingview --top 50` | rank the whole market, TradingView data | personal research |
| `twelvedata` | licensed vendor (API key) | public, licensed |

```bash
python -m scripts.build_all_stocks --source hybrid --top 50          # top 50 screened from the full market
python -m scripts.build_all_stocks --source tradingview --top 50     # same, personal (TV data)
```

A **quality gate** (profitable, sanely-valued, not over-levered) + a market-cap floor (`--min-mcap`,
default ~₹1,000 Cr) keep penny/SME data-glitch outliers out of the rankings. Note: ranking the whole
market on fundamentals naturally surfaces strong **small/mid-caps** — higher reward *and* higher risk.

> ⚖️ Signals are **educational model signals**, never SEBI-registered advice or price-target calls.

### 📈 Today's Market Movers (Gainers / Losers / Most-Active)

Add a live **Top Gainers · Losers · Most-Active** page to the site. Each mover pulls **TradingView
technicals** (price, % change, 52-week range, volatility) and is enriched with **fundamentals**
(P/E, ROE, ROCE, dividend yield) plus an **educational model signal** and **Low / Base / High**
return scenarios for short / mid / long term. A banner on the home page links to `movers.html`,
and every mover gets a full detail page.

```bash
# Public, CI-safe: TradingView technicals + Yahoo Finance fundamentals
python -m scripts.build_all_stocks --source hybrid --top 50 --with-movers --movers-source yfinance

# Personal research: add Screener.in depth (ROCE, Piotroski, 5y growth, pledging)
python -m scripts.build_all_stocks --source tradingview --top 50 --with-movers --movers-source both
```

`--movers-source`: `yfinance` (free, publishable) · `screener`/`both`/`auto` (Screener.in — personal
use only, keep off the public site). The public GitHub Pages build uses `yfinance`. Volatility and
return bands are **capped** so penny-stock movers can't produce absurd scenarios.

---

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Data Ingestion │ -> │  Analysis Engine │ -> │ Recommendation  │
│  (News + Prices)│    │  (LLM + Quant)   │    │  App (UI/API)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                      │                       │
    Scheduler            SQLite / Postgres        Telegram / Web
```

---

## 🚀 Quick Start

### 1. Setup

```bash
git clone https://github.com/sidhartha-patra/indian-market-investment-agent.git
cd indian-market-investment-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` to `.env` and fill in API keys:

```bash
cp .env.example .env
```

```env
# GitHub Models — FREE, just use your GitHub token
GITHUB_TOKEN=ghp_...
LLM_MODEL=gpt-4o-mini

# Optional fallback
# OPENAI_API_KEY=sk-...

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

> 💡 **Free LLM tip:** Generate a GitHub token at https://github.com/settings/tokens (no special scopes needed) and use [GitHub Models](https://github.com/marketplace?type=models) for free access to GPT-4o-mini, Llama 3, Mistral, and more.

### 3. Run

```bash
# Fetch today's data
python -m src.ingestion.run_all

# Generate recommendations
python -m src.agent.recommend

# Launch dashboard
streamlit run app/dashboard.py

# Start scheduler (runs pre-market + EOD)
python -m src.scheduler
```

---

## 📊 Strategies Implemented

| Strategy | Description |
|---|---|
| **Momentum** | Stocks breaking 52-week highs on above-average volume |
| **Quality (price)** | Sharpe + low drawdown + low volatility |
| **Quality (fundamentals)** | ROCE > 15%, ROE > 12%, P/E < 50 (via Finology) |
| **Mean-reversion** | RSI(2) oversold *inside* a long-term uptrend (Connors-style) |
| **Trend-following** | Supertrend + EMA(20/50/200) stack + ADX, with trailing stop |
| **Relative strength** | Cross-sectional, risk-adjusted momentum vs the benchmark |
| **Low volatility** | Low-vol anomaly factor (backtest ranker) |
| **Multi-factor composite** | z-scored momentum + low-vol + trend + quality (+ value/ROCE) |
| **Analyst consensus** | TickerTape Buy% + analyst count rank |
| **MF Rolling Returns** | Top mutual funds by 3Y/5Y rolling CAGR vs benchmark |
| **Sector rotation (LLM)** | Multi-factor 5-sector allocation generated by GPT-4o-mini |

## 🧭 Risk, Regime & Portfolio

- **Market regime** (`src/risk/regime.py`) reads index trend + breadth → `RISK_ON / NEUTRAL /
  RISK_OFF` and a recommended equity exposure that scales gross positions.
- **Position sizing** (`src/risk/position_sizing.py`): ATR stop-loss, fixed-fractional sizing
  (risk ≤ X% per trade), volatility targeting, half-Kelly.
- **Portfolio optimiser** (`src/portfolio/optimizer.py`): equal / inverse-vol / min-variance /
  max-Sharpe / risk-parity, with per-name caps, a score tilt and the regime exposure overlay.

## 🔮 Probabilistic Price Prediction

```bash
python -m src.ml.models        # one-name forecast with conformal price band + prob_up
python -m src.ml.evaluate      # walk-forward: MAE/RMSE/directional/coverage + skill vs naive
```

Models predict the **forward return** (not a price level), are wrapped in **split-conformal**
intervals (calibrated coverage), and are **always compared to a naive random walk**. If nothing
beats naive out-of-sample, the tool says so and leans on the interval + risk framing instead of
a point forecast.

## 🔁 Backtesting (lookahead-free)

```python
from src.ingestion.prices import fetch_prices
from src.backtest.engine import backtest_strategy, composite_ranker
from src.config import NIFTY_50

prices = fetch_prices(NIFTY_50, period="3y")
res = backtest_strategy(prices, composite_ranker(top_n=10), rebalance="M",
                        cost_bps=10, slippage_bps=5, n_trials=5)
print(res["metrics"])   # CAGR, Sharpe, Sortino, Calmar, maxDD, alpha/beta, deflated Sharpe
```

The ranker only ever sees prices up to the rebalance date; turnover is charged transaction
**cost + slippage**, and `n_trials` enables a **Deflated Sharpe** to discount data snooping.

## 🌌 Universe Coverage

- **Nifty 50 + Nifty 100** ticker lists
- **10 sector buckets**: banking, IT, auto, pharma, FMCG, energy, metals, financial, cement, realty
- **12 sectoral ETFs** (NIFTYBEES, BANKBEES, PHARMABEES, ITBEES, GOLDBEES, etc.)
- **All ~2,500 mutual fund schemes** via AMFI NAV feed + MFAPI history

---

## 🗂️ Project Structure

```
indian-market-investment-agent/
├── src/
│   ├── ingestion/      # News + price + fundamentals + MF data fetchers
│   ├── strategies/     # indicators + 8 screeners (momentum, quality, mean-rev,
│   │                   #   trend, relative-strength, low-vol, factor-model, multibagger)
│   ├── risk/           # market regime + ATR stops / position sizing
│   ├── portfolio/      # weight optimisers (equal/inv-vol/min-var/max-Sharpe/risk-parity)
│   ├── ml/             # features, conformal prediction models, walk-forward eval, explain
│   ├── backtest/       # lookahead-free engine + performance metrics
│   ├── agent/          # recommendation orchestration + LLM sentiment/sector rotation
│   ├── notify/         # Telegram bot
│   └── scheduler.py    # APScheduler daily jobs
├── app/
│   └── dashboard.py    # Streamlit UI
├── docs/
│   └── DESIGN.md       # full system design (A–J)
├── data/               # SQLite/DuckDB (gitignored)
├── tests/              # 51 synthetic-data tests (no network)
├── requirements.txt
└── README.md
```

---

## 🛣️ Roadmap

- [x] Multi-factor composite + relative-strength + mean-reversion + trend-following strategies
- [x] Market-regime filter and ATR-based risk management
- [x] Portfolio optimizer (Markowitz max-Sharpe / Risk Parity / inverse-vol)
- [x] Built-in backtesting engine (costs, slippage, deflated Sharpe)
- [x] Probabilistic price prediction (RF/XGBoost) with conformal intervals + naive benchmark
- [x] Per-suggestion explainability payloads
- [ ] Sector rotation strategy based on RBI rate cycle
- [ ] Paper trading tracker with P&L
- [ ] FastAPI backend + React frontend; backtesting UI
- [ ] Scheduled data validation + model-drift monitoring
- [ ] Multi-user support with auth
- [ ] Auto-execute via Zerodha Kite API (with user consent)

---

## 📜 License

MIT — see [LICENSE](LICENSE)

---

## 🙌 Contributing

PRs welcome! Open an issue first to discuss major changes.
