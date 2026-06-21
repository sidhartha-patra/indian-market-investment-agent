"""Gen-AI fundamental analyst — grounded, skeptical, and auditable.

Given the *reconciled* evidence for one stock (cross-validated metrics + framework scores
+ analyst/broker consensus + news sentiment + ML forecast + the deterministic model
verdict), an LLM writes the investment thesis, an explicit **Buy case** and **Sell case**,
key risks, catalysts, and a final lean. The model is instructed to:
  * reason ONLY from the numbers supplied (never invent figures),
  * down-weight low-confidence or conflicting data (it is shown the data-quality report),
  * stay framed as an *educational* view, not registered advice.

If no LLM is reachable, a deterministic fallback synthesizes the same structure from the
rules-based signals, so every stock always gets a two-sided Buy/Sell analysis.
"""
from __future__ import annotations

import json
import logging

from src.ai import llm

logger = logging.getLogger(__name__)

_VALID_VERDICTS = {"STRONG_BUY", "BUY", "HOLD", "SELL", "AVOID"}

_SYSTEM = (
    "You are a rigorous, skeptical SEBI-aware Indian equity research analyst. "
    "You reason ONLY from the structured evidence provided and NEVER invent numbers, "
    "prices, or facts that are not in the input. If the data-quality report flags low "
    "confidence or conflicts, explicitly discount those inputs and say so. You weigh the "
    "fundamental frameworks, valuation, growth, balance-sheet health, governance, the "
    "analyst/broker consensus, news sentiment, and the ML forecast, then give a balanced, "
    "two-sided view. Output ONLY valid minified JSON, no markdown, no commentary outside JSON. "
    "Everything is educational analysis, not investment advice."
)

_SCHEMA = """Return ONLY this JSON object (no markdown):
{
 "thesis": "3-4 sentence grounded synthesis citing the key numbers",
 "buy_case": ["3-5 concrete bullish points, each tied to a specific metric/signal"],
 "sell_case": ["3-5 concrete bearish/risk points, each tied to a specific metric/signal"],
 "key_risks": ["2-4 risks"],
 "catalysts": ["1-3 things that would move the stock"],
 "data_critique": ["how trustworthy the inputs are; call out conflicts/suspect values or 'inputs look consistent'"],
 "moat": "none|weak|moderate|strong",
 "verdict": "STRONG_BUY|BUY|HOLD|SELL|AVOID",
 "lean": -1.0 to 1.0,
 "conviction": 0 to 100,
 "confidence": "low|moderate|high",
 "horizon_view": {"short": "one line (weeks)", "mid": "one line (months)", "long": "one line (years)"}
}
Rules: be balanced (always give both buy_case and sell_case). If data_quality.confidence is low,
cap conviction at 55 and set confidence to "low". Tie every point to a number from the evidence."""


def _trim_evidence(evidence: dict) -> dict:
    """Keep the prompt compact and grounded — only the fields that carry signal."""
    m = evidence.get("metrics", {}) or {}
    keep_m = {k: m[k] for k in (
        "name", "sector", "price", "pe", "pb", "ev_ebitda", "roe", "roce", "roa", "opm",
        "net_margin", "revenue_growth_5y", "profit_growth_5y", "debt_to_equity",
        "current_ratio", "dividend_yield", "promoter_holding", "pledged_pct",
        "market_cap_cr", "high_52w", "low_52w", "sma50", "sma200", "analyst_target",
    ) if m.get(k) is not None}
    fw = evidence.get("frameworks", {}) or {}
    keep_fw = {}
    for name in ("quality_score", "altman", "graham", "piotroski", "beneish"):
        if name in fw and isinstance(fw[name], dict):
            keep_fw[name] = {k: v for k, v in fw[name].items()
                             if k in ("quality_score", "z_score", "zone", "graham_score",
                                      "f_score", "m_score", "signal")}
    return {
        "symbol": evidence.get("symbol"),
        "metrics": keep_m,
        "frameworks": keep_fw,
        "data_quality": evidence.get("data_quality", {}),
        "model_verdict": evidence.get("model_verdict"),
        "analyst_consensus": evidence.get("analyst_consensus"),
        "news": evidence.get("news"),
        "ml_forecast": evidence.get("ml_forecast"),
    }


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _as_list(v, limit=6):
    if isinstance(v, list):
        return [str(x)[:300] for x in v[:limit] if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v[:300]]
    return []


def _validate(raw: dict | None, evidence: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    verdict = str(raw.get("verdict", "")).strip().upper().replace(" ", "_")
    if verdict not in _VALID_VERDICTS:
        verdict = "HOLD"
    dq = (evidence.get("data_quality") or {}).get("confidence", 60)
    conviction = _clamp(raw.get("conviction"), 0, 100, 50)
    confidence = str(raw.get("confidence", "moderate")).lower()
    if dq is not None and dq < 50:
        conviction = min(conviction, 55)
        confidence = "low"
    hv = raw.get("horizon_view") if isinstance(raw.get("horizon_view"), dict) else {}
    out = {
        "thesis": str(raw.get("thesis") or "")[:900],
        "buy_case": _as_list(raw.get("buy_case")),
        "sell_case": _as_list(raw.get("sell_case")),
        "key_risks": _as_list(raw.get("key_risks"), 5),
        "catalysts": _as_list(raw.get("catalysts"), 4),
        "data_critique": _as_list(raw.get("data_critique"), 4),
        "moat": str(raw.get("moat", "none")).lower() if str(raw.get("moat", "")).lower()
                in {"none", "weak", "moderate", "strong"} else "none",
        "verdict": verdict,
        "lean": round(_clamp(raw.get("lean"), -1, 1, 0.0), 2),
        "conviction": round(conviction, 1),
        "confidence": confidence if confidence in {"low", "moderate", "high"} else "moderate",
        "horizon_view": {
            "short": str(hv.get("short") or "")[:200],
            "mid": str(hv.get("mid") or "")[:200],
            "long": str(hv.get("long") or "")[:200],
        },
        "source": "ai",
        "provider": llm.provider_info().get("label"),
    }
    if not out["buy_case"] and not out["sell_case"]:
        return None  # unusable — trigger fallback
    return out


def analyze(symbol: str, evidence: dict, *, use_llm: bool = True) -> dict:
    """Produce a grounded two-sided fundamental analysis for one stock."""
    if use_llm and llm.is_available():
        try:
            trimmed = _trim_evidence({**evidence, "symbol": symbol})
            user = (f"Analyse this Indian stock from the evidence below.\n\n{_SCHEMA}\n\n"
                    f"EVIDENCE (JSON):\n{json.dumps(trimmed, default=str)[:6000]}")
            parsed = llm.chat_json(_SYSTEM, user, max_tokens=1700, temperature=0.2)
            validated = _validate(parsed, evidence)
            if validated:
                return validated
            logger.warning("AI analysis unusable for %s; using deterministic fallback", symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI analyst error for %s: %s", symbol, exc)
    return _fallback(symbol, evidence)


def _fallback(symbol: str, evidence: dict) -> dict:
    """Deterministic two-sided analysis from the rules-based signals (no LLM)."""
    mv = evidence.get("model_verdict") or {}
    rec = evidence.get("recommendation") or {}
    m = evidence.get("metrics", {}) or {}
    ml = evidence.get("ml_forecast") or {}
    ac = evidence.get("analyst_consensus") or {}
    news = evidence.get("news") or {}

    buy = list(rec.get("positives") or [])
    sell = list(rec.get("negatives") or []) + list(rec.get("red_flags") or [])
    if ac.get("buy_pct") is not None:
        buy.append(f"Broker consensus {ac.get('buy_pct')}% buy / {ac.get('sell_pct')}% sell")
    if ac.get("target_upside_pct") is not None:
        (buy if ac["target_upside_pct"] >= 0 else sell).append(
            f"Analyst target implies {ac['target_upside_pct']:+g}% vs price")
    if ml.get("direction") == "UP":
        buy.append(f"ML forecast UP (P(up)={ml.get('prob_up')}, {ml.get('predicted_return_pct')}%)")
    elif ml.get("direction") == "DOWN":
        sell.append(f"ML forecast DOWN (P(up)={ml.get('prob_up')}, {ml.get('predicted_return_pct')}%)")
    if news.get("net_sentiment") is not None:
        (buy if news["net_sentiment"] >= 0 else sell).append(
            f"News sentiment {news.get('net_sentiment'):+.2f} ({news.get('n', 0)} headlines)")

    verdict = mv.get("verdict") or rec.get("verdict") or "HOLD"
    conviction = mv.get("conviction") or rec.get("conviction") or 50
    horizons = rec.get("horizons") or {}
    return {
        "thesis": rec.get("summary") or f"{symbol}: rules-based model verdict {verdict} "
                  f"(conviction {conviction}/100). LLM narrative unavailable; showing the "
                  f"deterministic two-sided analysis.",
        "buy_case": [str(x)[:300] for x in buy[:5]] or ["No standout strengths in the data."],
        "sell_case": [str(x)[:300] for x in sell[:5]] or ["No major weaknesses detected."],
        "key_risks": [str(x)[:300] for x in (rec.get("red_flags") or [])[:4]],
        "catalysts": [str(x)[:300] for x in (rec.get("what_would_change") or [])[:3]],
        "data_critique": list((evidence.get("data_quality") or {}).get("conflicts", []))[:3]
                         or ["Inputs not LLM-reviewed; see data-quality panel."],
        "moat": "none",
        "verdict": verdict,
        "lean": round(max(-1.0, min(1.0, (float(conviction) - 50) / 50)), 2),
        "conviction": round(float(conviction), 1),
        "confidence": rec.get("confidence") or "moderate",
        "horizon_view": {
            "short": (horizons.get("short_term") or {}).get("stance", ""),
            "mid": (horizons.get("mid_term") or {}).get("stance", ""),
            "long": (horizons.get("long_term") or {}).get("stance", ""),
        },
        "source": "deterministic",
        "provider": "deterministic (no LLM)",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ev = {
        "symbol": "RELIANCE",
        "metrics": {"name": "Reliance", "sector": "Energy", "price": 1310, "pe": 22.8,
                    "roe": 8.9, "roce": 10.3, "debt_to_equity": 0.44, "profit_growth_5y": 12.0,
                    "dividend_yield": 0.46, "high_52w": 1600, "low_52w": 1100},
        "frameworks": {"piotroski": {"f_score": 6}, "altman": {"z_score": 3.1, "zone": "safe"}},
        "data_quality": {"confidence": 78, "conflicts": []},
        "model_verdict": {"verdict": "HOLD", "conviction": 59},
        "analyst_consensus": {"buy_pct": 64, "sell_pct": 12, "target_upside_pct": 9.5},
        "ml_forecast": {"direction": "UP", "prob_up": 0.56, "predicted_return_pct": 1.4},
        "news": {"net_sentiment": 0.2, "n": 8},
    }
    print(json.dumps(analyze("RELIANCE", ev), indent=2))
