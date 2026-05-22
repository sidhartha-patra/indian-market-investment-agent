"""Sector rotation agent — generates JSON sector allocation calls via LLM."""
from __future__ import annotations
import json
import logging
from datetime import datetime
from src.config import OPENAI_API_KEY, DATA_DIR
from src.agent.sector_prompt import SECTOR_ALLOCATION_PROMPT
from src.ingestion.news import fetch_news

logger = logging.getLogger(__name__)


def generate_sector_allocation(market_context: dict | None = None) -> dict:
    """Call LLM to produce 5-sector allocation JSON.

    market_context: optional dict with keys like nifty_spot, fii_5d, etc.,
    plus 'headlines' list. If absent, recent news will be pulled.
    """
    if market_context is None:
        market_context = {}

    headlines = market_context.get("headlines")
    if headlines is None:
        news = fetch_news(limit_per_feed=15)
        headlines = [n["title"] for n in news[:30]]

    user_msg = (
        f"Current date/time: {datetime.utcnow().isoformat()}Z\n\n"
        f"Recent headlines (Indian markets):\n"
        + "\n".join(f"- {h}" for h in headlines[:25])
        + "\n\nKnown market context (may be partial): "
        + json.dumps(market_context, default=str)
        + "\n\nGenerate the 5-sector allocation JSON now."
    )

    if not OPENAI_API_KEY:
        logger.warning("No OPENAI_API_KEY — returning skeleton")
        return _skeleton(headlines)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SECTOR_ALLOCATION_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(resp.choices[0].message.content)
        out_path = DATA_DIR / "sector_allocation.json"
        out_path.write_text(json.dumps(data, indent=2))
        logger.info("Saved sector allocation -> %s", out_path)
        return data
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM call failed: %s", exc)
        return _skeleton(headlines)


def _skeleton(headlines: list[str]) -> dict:
    return {
        "date_time_generated": datetime.utcnow().isoformat() + "Z",
        "nifty_context": {"note": "LLM unavailable; configure OPENAI_API_KEY"},
        "sectors": [],
        "_input_headlines": headlines[:10],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = generate_sector_allocation()
    print(json.dumps(out, indent=2)[:3000])
