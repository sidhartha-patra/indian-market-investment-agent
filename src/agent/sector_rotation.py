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
    """Call LLM (gh models CLI > GitHub Models REST > OpenAI) for sector allocation JSON."""
    import os
    from src.agent import gh_models
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
        + "\n\nGenerate the 5-sector allocation JSON now. Output ONLY the JSON."
    )

    data: dict | None = None

    # Tier 1: gh models CLI
    if gh_models.is_available():
        data = gh_models.run_json(user_msg, system=SECTOR_ALLOCATION_PROMPT)

    # Tier 2/3: REST fallbacks
    if not data:
        gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN", "")
        base_url = "https://models.inference.ai.azure.com" if gh_token else None
        api_key = gh_token or OPENAI_API_KEY
        model = os.getenv("LLM_MODEL", "gpt-4o-mini").split("/", 1)[-1]

        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
                    else OpenAI(api_key=api_key)
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SECTOR_ALLOCATION_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                data = json.loads(resp.choices[0].message.content)
            except Exception as exc:  # noqa: BLE001
                logger.error("REST LLM call failed: %s", exc)

    if not data:
        logger.warning("No LLM available; returning skeleton")
        return _skeleton(headlines)

    out_path = DATA_DIR / "sector_allocation.json"
    out_path.write_text(json.dumps(data, indent=2))
    logger.info("Saved sector allocation -> %s", out_path)
    return data


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
