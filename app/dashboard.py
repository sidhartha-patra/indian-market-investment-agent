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
    st.markdown("**Strategies:**\n- Momentum (52w breakouts)\n- Quality (Sharpe + DD)\n- News sentiment (LLM)")

rec_path = DATA_DIR / "recommendations.json"
if not rec_path.exists():
    st.warning("No recommendations yet. Click **Refresh** in the sidebar to generate.")
    st.stop()

recs = json.loads(rec_path.read_text())
st.caption(f"Last updated: {recs.get('generated_at', 'unknown')}")

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
