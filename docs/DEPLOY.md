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

## 🆓 Recommended free path — no key needed (Yahoo Finance)

Set **no** API key and the workflow builds from **Yahoo Finance** (`--source yfinance`):
**full fundamentals (P/E, ROE, ROA, margins, D/E, dividend yield, sector, …) for every
NSE/BSE stock**, no signup. Just do **Step 3 + Step 4** below and skip the key.

- ⚖️ Yahoo's ToS is a grey area for *public* redistribution — fine for a **personal,
  non-commercial, educational** hobby site with attribution + the not-advice disclaimer
  (both added automatically). Don't monetise it; for commercial use, use a licensed vendor.
- If Yahoo rate-limits the GitHub runner, the build **falls back to the sample site** so
  your URL never breaks. (You can also build locally and commit the output.)

---

## (Optional) Licensed source — Twelve Data

## Step 1 — Get a Twelve Data API key (free)

1. Go to **https://twelvedata.com/** and click **Get Free API Key** / **Sign Up**
   (email, Google, or GitHub).
2. Confirm your email and log in.
3. Your key is on the dashboard at **https://twelvedata.com/account/api-keys**
   (also shown right after signup). Copy it — it looks like `abcd1234ef567890...`.
4. The free **Basic** plan gives **8 API credits/min** and **800 credits/day**.

> ⚠️ **Caveat (from live testing):** the free Basic plan has **limited India symbol
> coverage** — INFY returned full fundamentals, but RELIANCE and TCS returned `404`.
> Broad NSE coverage needs a **paid** plan, and public redistribution needs the
> **Redistribution Add-On** (email sales@twelvedata.com). For a free, full-coverage
> option, prefer the **Yahoo Finance** path above.

## Step 2 — Add the key as a GitHub secret

1. Push your branch so the repo is on GitHub: `git push`.
2. Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. **Name:** `TWELVEDATA_API_KEY`  **Value:** *(paste your key)* → **Add secret**.

The workflow auto-detects the secret: present → builds from **Twelve Data**;
absent → builds from **free Yahoo Finance** (falls back to the sample site if Yahoo
blocks the runner).

## Step 3 — Enable GitHub Pages

Repo → **Settings** → **Pages** → **Build and deployment** → **Source: GitHub Actions**.
(No branch selection needed — the workflow publishes the artifact.)

## Step 4 — Trigger the first deploy

- Automatic: the next scheduled run (08:30 or 18:00 IST), or
- Manual: repo → **Actions** → **Build & Deploy Stock Site** → **Run workflow**.

Watch the run finish; the deploy step prints your live **Pages URL**.

---

## 🤖 AI recommendations + Search (Buy / Sell / Hold)

The site can include **AI-powered Buy/Hold/Sell tabs** and a **Search tab** that runs a
deep, *grounded* fundamental analysis per stock — blending the deterministic model,
**analyst/broker consensus**, a **conformal ML forecast**, **news sentiment**, and a
**Gen-AI analyst** that reasons only from the validated numbers (and cross-checks data
across sources). Each stock gets an explicit **Buy case** and **Sell case**.

### Free AI in CI — no PAT needed

The workflow grants `permissions: models: read` and passes the **built-in `GITHUB_TOKEN`** to the
build, so **GitHub Models works in CI out of the box — no secret required**. The pages build with
full AI automatically. (Optional overrides: set a `MODELS_TOKEN` PAT, or an `ANTHROPIC_API_KEY`
to switch the analyst to **Claude Opus**, or `OPENAI_API_KEY` / Azure. No key of any kind ⇒ the
build still works via the deterministic rules-based analyst.)

Optional: add a repo **Variable** `REC_TOP` (e.g. `300`) to set how many stocks the
recommendations analyse.

> Free GitHub Models has a **daily request cap**. The build is **exhaustive but resumable**:
> the AI cache (`data/ai_cache`, persisted via `actions/cache`) accumulates coverage across
> runs, so the whole universe gets full AI analysis over a few runs.

### 💻 Alternative: build locally & auto-publish (no CI limits, runs for hours)

Prefer to do the exhaustive analysis on your own machine (free AI via your `gh` login, no rate
caps, no 6-hour job limit)? A twice-daily Scheduled Task can build everything and **push it to a
`gh-pages` branch** that Pages serves:

```powershell
# one-time: register the auto-publish task (08:30 + 18:00 local), exhaustive over 300 stocks
powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1 -Publish -RecTop 300
# then flip: Settings -> Pages -> Source = "Deploy from a branch" -> gh-pages / root
```

The script (`scripts/refresh_and_publish.ps1`) builds with your local `gh` token (free GitHub
Models) into a staging dir and pushes it to `gh-pages`. Your analysis **state** (`data/ai_cache`)
stays on your machine and is reused every run, so coverage accumulates. Caveat: your PC must be
awake at the scheduled times. (Pages serves **either** the Actions build **or** the `gh-pages`
branch — pick one Source.)

### Upgrade to Claude Opus (optional, paid)

Add an **`ANTHROPIC_API_KEY`** secret and the client switches to **Claude (incl. Opus)**
automatically — zero code change, no daily cap. Set `ANTHROPIC_MODEL` (repo variable) to the
exact Opus model id you want. `OPENAI_API_KEY` / Azure OpenAI are also supported.

### Cost / time

Exhaustive AI + ML over ~300 stocks can take a while (the workflow allows up to ~6 h and
caches results). ML forecasts train a small per-stock model on price history; the AI calls
are cached by a data fingerprint so unchanged stocks are never re-analysed.

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
python -m scripts.build_all_stocks --source yfinance             # FREE, no key, full fundamentals (educational)
python -m scripts.build_all_stocks --source twelvedata --mode full   # licensed, public-safe (needs key)
python -m scripts.build_all_stocks --source tradingview --limit 2000 # full universe, personal only

# AI Buy/Sell/Hold + Search (exhaustive deep-dive; set MODELS_TOKEN for free GitHub Models AI):
python -m scripts.build_all_stocks --source hybrid --top 50 --with-movers --with-nifty50 \
  --with-recommendations --rec-top 300

# Recommendations only (standalone), exhaustive over 300 names:
python -m scripts.recommendations --top 300            # add --no-ml to skip ML, --moneycontrol for broker %
```

Set the AI provider via env/secrets: `MODELS_TOKEN` (free GitHub Models, default) ·
`ANTHROPIC_API_KEY` (Claude Opus) · `OPENAI_API_KEY` / Azure. No key ⇒ deterministic analyst.

> Disclaimer: educational/decision-support only — not investment advice, not a SEBI-registered
> research report. Data may be delayed or inaccurate. Consult a SEBI-registered adviser.
