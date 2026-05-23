"""LLM-based news sentiment classifier.

Priority: gh models CLI (free, uses gh auth) > GitHub Models REST > OpenAI > fallback.
"""
from __future__ import annotations
import json
import logging
import os
from src.config import OPENAI_API_KEY
from src.agent import gh_models

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN", "")
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")


def _call_via_gh(system: str, user: str) -> str | None:
    return gh_models.run(user, system=system, model=DEFAULT_MODEL)


def _call_via_rest(system: str, user: str, api_key: str, base_url: str | None) -> str | None:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        # REST endpoints expect non-prefixed model name
        model = DEFAULT_MODEL.split("/", 1)[-1] if "/" in DEFAULT_MODEL else DEFAULT_MODEL
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return resp.choices[0].message.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("REST LLM call failed: %s", exc)
        return None


def classify_sentiment(headlines: list[str]) -> list[dict]:
    """Classify each headline as bullish/bearish/neutral with tickers mentioned."""
    if not headlines:
        return []
    system = ("You classify Indian market news headlines. Return ONLY a JSON object "
              "{\"results\":[...]} where each item has: headline, sentiment "
              "(bullish|bearish|neutral), tickers (list of NSE symbols), reason (short).")
    user = "Headlines:\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))

    # Tier 1: gh models CLI
    raw = _call_via_gh(system, user) if gh_models.is_available() else None

    # Tier 2: GitHub Models REST with token
    if not raw and GITHUB_TOKEN:
        raw = _call_via_rest(system, user, GITHUB_TOKEN, GITHUB_MODELS_ENDPOINT)

    # Tier 3: OpenAI direct
    if not raw and OPENAI_API_KEY:
        raw = _call_via_rest(system, user, OPENAI_API_KEY, None)

    if raw:
        s = raw.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[1] if "\n" in s else s
            if s.endswith("```"):
                s = s.rsplit("```", 1)[0]
        try:
            data = json.loads(s)
            return data.get("results", data) if isinstance(data, dict) else data
        except json.JSONDecodeError as exc:
            logger.error("Bad JSON from LLM: %s", exc)

    logger.warning("LLM unavailable; using keyword fallback")
    return _fallback(headlines)


# Backwards-compatible alias
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
