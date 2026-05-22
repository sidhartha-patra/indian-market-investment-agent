"""Send recommendations via Telegram."""
from __future__ import annotations
import logging
import requests
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram send failed: %s", exc)
        return False


def format_recommendations(recs: dict) -> str:
    lines = ["*📊 Daily Investment Recommendations*", ""]
    lines.append(f"_Generated: {recs.get('generated_at', '')}_\n")

    if recs.get("top_picks"):
        lines.append("*🎯 Top Picks (Momentum + Quality)*")
        for p in recs["top_picks"]:
            lines.append(f"• `{p['ticker']}` — score {p.get('score', 0)} | "
                         f"₹{p.get('last_price', 0)} | {p.get('signal', '')}")
        lines.append("")

    if recs.get("momentum_leaders"):
        lines.append("*🚀 Momentum Leaders*")
        for p in recs["momentum_leaders"][:3]:
            lines.append(f"• `{p['ticker']}` — {p.get('return_3m_pct', 0)}% (3M)")
        lines.append("")

    ns = recs.get("news_summary", {})
    if ns:
        lines.append(f"*📰 News Pulse:* 🟢 {ns.get('bullish', 0)} | "
                     f"🔴 {ns.get('bearish', 0)} | ⚪ {ns.get('neutral', 0)}")

    lines.append("\n_⚠️ Educational only. Not investment advice._")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    from src.config import DATA_DIR
    p = DATA_DIR / "recommendations.json"
    if p.exists():
        recs = json.loads(p.read_text())
        msg = format_recommendations(recs)
        print(msg)
        send_message(msg)
