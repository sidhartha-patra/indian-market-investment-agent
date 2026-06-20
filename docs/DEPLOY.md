# Deploying the Stock Explorer Website

This guide takes the generated site live on **GitHub Pages**, refreshed **twice daily**
(08:30 + 18:00 IST) by `.github/workflows/deploy-site.yml`.

> ⚖️ **Read first (legal):** Scraped sources (TradingView, Screener.in, yfinance) are
> **personal-research only** — never publish them. The public site must use a **licensed**
> vendor (**Twelve Data** or EODHD). Scores are framed as **non-directional educational
> data**, never buy/sell calls (publishing recommendations needs SEBI RA registration).
> See [`DESIGN.md`](DESIGN.md) §Legal.

---

## TL;DR

1. Get a free Twelve Data API key → 2. Add it as the repo secret `TWELVEDATA_API_KEY` →
3. Settings → Pages → Source = **GitHub Actions** → 4. Run the workflow.
Without the key it deploys a **sample** site so your URL works immediately.

Public URL: `https://sidhartha-patra.github.io/indian-market-investment-agent/`

---

## Step 1 — Get a Twelve Data API key (free)

1. Go to **https://twelvedata.com/** and click **Get Free API Key** / **Sign Up**
   (email, Google, or GitHub).
2. Confirm your email and log in.
3. Your key is on the dashboard at **https://twelvedata.com/account/api-keys**
   (also shown right after signup). Copy it — it looks like `abcd1234ef567890...`.
4. The free **Basic** plan gives **8 API credits/min** and **800 credits/day**.

> ⚠️ **Honest caveat on the free plan:** Basic reliably covers **quotes** (prices), so
> price + price-based valuation work. The deeper **fundamentals** endpoint (`/statistics`:
> ROE, margins, D/E, …) is generally a **paid** feature (Grow ~$8/mo, Pro ~$29/mo). So:
> - **Free key** → prices/valuation populate; some fundamental fields may be blank.
> - **Paid key** → full fundamental scoring.
> - **No key** → the workflow deploys the built-in **sample** site (always free/legal).
> For a genuinely *public* site, Twelve Data also requires a **Redistribution Add-On**
> (email sales@twelvedata.com) — confirm in writing before publishing real data.

## Step 2 — Add the key as a GitHub secret

1. Push your branch so the repo is on GitHub: `git push`.
2. Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. **Name:** `TWELVEDATA_API_KEY`  **Value:** *(paste your key)* → **Add secret**.

The workflow auto-detects the secret: present → builds real **Twelve Data** data;
absent → builds the sample demo site.

## Step 3 — Enable GitHub Pages

Repo → **Settings** → **Pages** → **Build and deployment** → **Source: GitHub Actions**.
(No branch selection needed — the workflow publishes the artifact.)

## Step 4 — Trigger the first deploy

- Automatic: the next scheduled run (08:30 or 18:00 IST), or
- Manual: repo → **Actions** → **Build & Deploy Stock Site** → **Run workflow**.

Watch the run finish; the deploy step prints your live **Pages URL**.

---

## Refresh cadence

`deploy-site.yml` runs **twice daily, Mon–Fri**:

| Cron (UTC) | IST | Purpose |
|---|---|---|
| `0 3 * * 1-5` | 08:30 | pre-open refresh |
| `30 12 * * 1-5` | 18:00 | post-close refresh |

Both do a **full** rebuild (fundamentals + latest quote). Scores are ~95% fundamentals
(which change quarterly), so twice-daily is plenty — see the cadence discussion in the
project notes. For **intraday price**, each stock page embeds the **live TradingView
chart widget** (real-time / exchange-delayed, free, compliant), independent of the rebuild.

## Free-tier coverage & cost notes

- Default public universe = **Nifty 50** (`twelvedata_provider.NIFTY_50_SYMBOLS`).
- A **full** fetch ≈ **3 credits/stock** (quote + statistics + profile).
- Twice-daily Nifty-50 ≈ `50 × 3 × 2 = 300 credits/day` → within the **800/day** free cap.
- The adapter sleeps to respect **8 credits/min**, so a full Nifty-50 run takes a few minutes.
- Want more than ~130 stocks twice-daily? Use a **paid plan**, or reduce the symbol list,
  or switch to **once-daily**.

---

## Local "personal research" mode (no key, all stocks)

For your own machine (not public), the **TradingView scanner** path needs no key and
covers the **whole** NSE+BSE universe:

```powershell
# Build the full local site (personal research only — do NOT publish this output)
python -m scripts.build_all_stocks --source tradingview --limit 1000 --out C:\Users\sipatra\stock-site-preview
# Serve it locally
python -m http.server 8765 --directory C:\Users\sipatra\stock-site-preview
# open http://localhost:8765/
```

To refresh it on a schedule on Windows, register a **Task Scheduler** task that runs the
two commands above (e.g., 08:30 and 18:00 IST).

---

## Command reference

```bash
python -m scripts.build_all_stocks --source demo                 # offline sample (always works)
python -m scripts.build_all_stocks --source twelvedata --mode full   # licensed, public-safe (needs key)
python -m scripts.build_all_stocks --source tradingview --limit 2000 # full universe, personal only
```

> Disclaimer: educational/decision-support only — not investment advice, not a SEBI-registered
> research report. Data may be delayed or inaccurate. Consult a SEBI-registered adviser.
