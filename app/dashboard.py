"""Streamlit dashboard for investment recommendations."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import DATA_DIR  # noqa: E402
from src.agent.recommend import generate_recommendations  # noqa: E402

st.set_page_config(page_title="🇮🇳 Indian Market Agent", layout="wide", page_icon="📈")

st.title("🇮🇳 Indian Market Investment Agent")
st.caption("AI-powered stock & mutual fund recommendations — *Educational only, not SEBI-registered advice*")

with st.sidebar:
    st.header("Controls")
    if st.button("🔄 Refresh recommendations", type="primary"):
        with st.spinner("Running agent pipeline..."):
            generate_recommendations()
        st.success("Done!")
        st.rerun()
    st.markdown("---")
    st.markdown("**Strategies:**\n- Momentum (52w breakouts)\n- Quality (Sharpe + DD)\n"
                "- Mean-reversion (RSI-2)\n- Trend-following (Supertrend/ADX)\n"
                "- Relative strength\n- Multi-factor composite\n- News sentiment (LLM, weak)")
    st.markdown("---")
    st.caption("Educational decision-support only. Probabilistic, risk-aware — not investment advice.")

rec_path = DATA_DIR / "recommendations.json"
if not rec_path.exists():
    st.warning("No recommendations yet. Click **Refresh** in the sidebar to generate.")
    st.stop()

recs = json.loads(rec_path.read_text())
st.caption(f"Last updated: {recs.get('generated_at', 'unknown')}")

regime = recs.get("market_regime") or {}
if regime:
    rmap = {"RISK_ON": "🟢", "NEUTRAL": "🟡", "RISK_OFF": "🔴"}
    label = regime.get("regime", "?")
    exp = regime.get("equity_exposure", 0) * 100
    banner = f"{rmap.get(label, '⚪')} **Market regime: {label}** — suggested equity exposure ~{exp:.0f}%"
    (st.success if label == "RISK_ON" else st.warning if label == "NEUTRAL" else st.error)(banner)
    if regime.get("rationale"):
        st.caption(" · ".join(regime["rationale"][:4]))

col1, col2, col3, col4 = st.columns(4)
ns = recs.get("news_summary", {})
col1.metric("Total Headlines", ns.get("total", 0))
col2.metric("🟢 Bullish", ns.get("bullish", 0))
col3.metric("🔴 Bearish", ns.get("bearish", 0))
col4.metric("⚪ Neutral", ns.get("neutral", 0))

st.subheader("🎯 Top Picks (Momentum × Quality Overlap)")
if recs.get("top_picks"):
    st.dataframe(pd.DataFrame(recs["top_picks"]), use_container_width=True)
else:
    st.info("No tickers passed both screens today.")

if recs.get("enriched_picks"):
    st.subheader("🎯 Enriched Top Picks")
    enriched_df = pd.DataFrame(recs["enriched_picks"])
    enriched_cols = [
        "ticker", "blended_score", "momentum_score", "quality_score",
        "tradingview_recommendation", "tradingview_score", "moneycontrol_buy_pct",
        "forecast_expected_return_pct", "forecast_direction", "pe", "roce", "roe",
        "debt_to_equity", "promoter_holding", "broker_live_price",
    ]
    st.dataframe(enriched_df[[c for c in enriched_cols if c in enriched_df.columns]], use_container_width=True)

    st.subheader("🔮 5-Day Price Forecasts")
    forecast_cols = [
        "ticker", "broker_live_price", "moneycontrol_live_price", "forecasted_price_5d",
        "forecast_expected_return_pct", "forecast_direction", "forecast_engine", "forecast_error",
    ]
    st.dataframe(enriched_df[[c for c in forecast_cols if c in enriched_df.columns]], use_container_width=True)

if recs.get("factor_leaders"):
    st.subheader("🧮 Multi-Factor Composite Leaders")
    st.caption("z-scored blend of momentum, low-volatility, trend and quality (plus value where available).")
    st.dataframe(pd.DataFrame(recs["factor_leaders"]), use_container_width=True)

port = recs.get("suggested_portfolio", {}) or {}
if port.get("allocations"):
    st.subheader("🧺 Suggested Portfolio (regime-scaled, with ATR stops)")
    st.caption(
        f"Method: {port.get('method')} · exposure {port.get('exposure_pct')}% · "
        f"cash {port.get('cash_pct')}% · max weight {port.get('max_weight_pct')}%"
    )
    st.dataframe(pd.DataFrame(port["allocations"]), use_container_width=True)

if recs.get("explanations"):
    st.subheader("🧾 Why these picks? (explainability)")
    for exp in recs["explanations"][:8]:
        conf = exp.get("confidence", 0) or 0
        header = (f"{exp.get('ticker')} — {exp.get('confidence_label', '?')} confidence "
                  f"({conf:.0%}) · risk {exp.get('risk_level', '?')}")
        with st.expander(header):
            st.markdown(f"**Thesis:** {exp.get('thesis', '')}")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**✅ Positives**")
                for p in exp.get("positives", []):
                    st.markdown(f"- {p}")
            with cols[1]:
                st.markdown("**⚠️ Negatives**")
                for ng in exp.get("negatives", []):
                    st.markdown(f"- {ng}")
            fr = exp.get("data_freshness", {})
            st.caption(f"Data freshness: {fr.get('last_data_date', '?')} ({fr.get('age_days', '?')}d old)")
            for g in exp.get("guardrails", []):
                st.warning(g)

if recs.get("multibagger_candidates"):
    st.subheader("💎 Multibagger Candidates")
    st.dataframe(pd.DataFrame(recs["multibagger_candidates"]), use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.subheader("🚀 Momentum Leaders")
    st.dataframe(pd.DataFrame(recs.get("momentum_leaders", [])), use_container_width=True)
with c2:
    st.subheader("💎 Quality Leaders")
    st.dataframe(pd.DataFrame(recs.get("quality_leaders", [])), use_container_width=True)

if recs.get("volatile_movers"):
    st.subheader("⚡ Volatile Movers (Aggressive / Swing Trades)")
    st.caption("Higher risk \u2014 midcap/smallcap with elevated volatility, ATR, and volume surge. "
               "Use strict stop-loss.")
    st.dataframe(pd.DataFrame(recs["volatile_movers"]), use_container_width=True)

st.subheader("📰 Market Sentiment")
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown("**🟢 Bullish News**")
    for h in ns.get("bullish_headlines", []):
        st.markdown(f"- {h}")
with sc2:
    st.markdown("**🔴 Bearish News**")
    for h in ns.get("bearish_headlines", []):
        st.markdown(f"- {h}")

st.markdown("---")
st.warning(recs.get("disclaimer", ""))
