"""LLM-based news sentiment classifier."""
from __future__ import annotations
import json
import logging
from src.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)


def classify_sentiment_openai(headlines: list[str]) -> list[dict]:
    """Classify each headline as bullish/bearish/neutral with tickers mentioned."""
    if not OPENAI_API_KEY:
        logger.warning("No OPENAI_API_KEY; using naive rule-based fallback")
        return _fallback(headlines)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = (
            "Classify each Indian market news headline as bullish, bearish, or neutral, "
            "and extract any company tickers mentioned (NSE symbols). "
            "Return a JSON array with fields: headline, sentiment, tickers (list), reason.\n\n"
            "Headlines:\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        return data.get("results", data) if isinstance(data, dict) else data
    except Exception as exc:  # noqa: BLE001
        logger.error("OpenAI failed: %s; falling back", exc)
        return _fallback(headlines)


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
    samples = [
        "Reliance Industries Q4 profit jumps 12% on retail strength",
        "TCS shares fall as deal pipeline weakens in BFSI segment",
        "Nifty closes flat as IT stocks drag",
    ]
    for r in classify_sentiment_openai(samples):
        print(r)
