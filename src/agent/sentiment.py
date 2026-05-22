"""LLM-based news sentiment classifier.

Supports GitHub Models (free, uses GITHUB_TOKEN) or OpenAI as fallback.
"""
from __future__ import annotations
import json
import logging
import os
from src.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN", "")
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def _call_llm(system: str, user: str, model: str = DEFAULT_MODEL) -> str | None:
    """Try GitHub Models first (free), then OpenAI. Returns raw text response."""
    # Try GitHub Models
    if GITHUB_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=GITHUB_MODELS_ENDPOINT, api_key=GITHUB_TOKEN)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return resp.choices[0].message.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("GitHub Models failed: %s", exc)

    # Fallback: OpenAI
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return resp.choices[0].message.content
        except Exception as exc:  # noqa: BLE001
            logger.error("OpenAI failed: %s", exc)
    return None


def classify_sentiment(headlines: list[str]) -> list[dict]:
    """Classify each headline as bullish/bearish/neutral with tickers mentioned."""
    if not headlines:
        return []
    system = ("You classify Indian market news headlines. Return JSON {\"results\":[...]} "
              "where each item has: headline, sentiment (bullish|bearish|neutral), "
              "tickers (list of NSE symbols), reason (short).")
    user = "Headlines:\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    raw = _call_llm(system, user)
    if raw:
        try:
            data = json.loads(raw)
            return data.get("results", data) if isinstance(data, dict) else data
        except Exception as exc:  # noqa: BLE001
            logger.error("Bad JSON from LLM: %s", exc)
    logger.warning("LLM unavailable; using keyword fallback")
    return _fallback(headlines)


# Backwards-compatible alias used by older callers
classify_sentiment_openai = classify_sentiment


def _fallback(headlines: list[str]) -> list[dict]:
    bullish_kw = ["surge", "rally", "gain", "beat", "high", "growth", "jump", "soar", "upgrade"]
    bearish_kw = ["fall", "drop", "loss", "miss", "low", "decline", "crash", "downgrade", "slump"]
    out = []
    for h in headlines:
        low = h.lower()
        b = sum(1 for k in bullish_kw if k in low)
        s = sum(1 for k in bearish_kw if k in low)
        sentiment = "bullish" if b > s else "bearish" if s > b else "neutral"
        out.append({"headline": h, "sentiment": sentiment, "tickers": [], "reason": "keyword-based"})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    samples = [
        "Reliance Industries Q4 profit jumps 12% on retail strength",
        "TCS shares fall as deal pipeline weakens in BFSI segment",
        "Nifty closes flat as IT stocks drag",
    ]
    for r in classify_sentiment(samples):
        print(r)
