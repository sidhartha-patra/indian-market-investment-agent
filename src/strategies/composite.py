"""Composite recommendation: blend five independent signals into one Buy/Hold/Sell call.

Signals (each mapped to a lean in [-1, +1], higher = more bullish):
  * fundamental — our deterministic sector-relative model verdict + conviction
  * ai          — the Gen-AI analyst's grounded lean
  * analyst     — broker/analyst consensus (Moneycontrol buy/sell %, Yahoo target & rec mean)
  * ml          — conformal ML forecast P(up) / predicted return
  * news        — recent-headline net sentiment

The blend is availability-weighted (missing signals are dropped and the rest renormalized),
red-flag-guarded (governance red flags cap the upside), and data-quality-aware. We surface
the per-signal breakdown so the call is transparent, plus the two-sided Buy/Sell cases.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VERDICT_LEAN = {"STRONG_BUY": 1.0, "BUY": 0.5, "HOLD": 0.0, "SELL": -0.5, "AVOID": -1.0}

# Availability-weighted signal weights (renormalized over whichever are present).
_WEIGHTS = {"fundamental": 0.30, "ai": 0.25, "analyst": 0.20, "ml": 0.15, "news": 0.10}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _clip(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def _fundamental_lean(rec: dict) -> float | None:
    if not rec:
        return None
    base = _VERDICT_LEAN.get(str(rec.get("verdict", "")).upper())
    conv = _num(rec.get("conviction"))
    if base is None and conv is None:
        return None
    if base is None:
        base = 0.0
    # nudge by conviction distance from 50 (so a high-conviction BUY leans more than a weak one)
    nudge = ((conv - 50) / 100.0) if conv is not None else 0.0
    return _clip(base + 0.4 * nudge)


def _analyst_lean(ac: dict | None) -> float | None:
    if not ac:
        return None
    parts = []
    buy, sell = _num(ac.get("buy_pct")), _num(ac.get("sell_pct"))
    if buy is not None or sell is not None:
        parts.append(_clip(((buy or 0) - (sell or 0)) / 100.0))
    mean = _num(ac.get("rec_mean"))  # 1=strong buy .. 5=strong sell
    if mean is not None:
        parts.append(_clip((3.0 - mean) / 2.0))
    up = _num(ac.get("target_upside_pct"))
    if up is not None:
        parts.append(_clip(up / 40.0, -0.5, 0.5))  # +40% target => strong tilt
    return sum(parts) / len(parts) if parts else None


def _ml_lean(ml: dict | None) -> float | None:
    if not ml or ml.get("error"):
        return None
    p = _num(ml.get("prob_up"))
    if p is not None:
        conf = _num(ml.get("confidence")) or 0.5
        return _clip((p - 0.5) * 2.0 * (0.5 + 0.5 * conf))  # scale by model confidence
    ret = _num(ml.get("predicted_return_pct"))
    return _clip(ret / 5.0) if ret is not None else None


def _news_lean(news: dict | None) -> float | None:
    if not news:
        return None
    return _clip(_num(news.get("net_sentiment"))) if news.get("net_sentiment") is not None else None


def _bucket(lean: float, has_red_flag: bool) -> tuple[str, str]:
    """Return (granular_verdict, group) where group in {BUY, HOLD, SELL}."""
    if lean >= 0.55 and not has_red_flag:
        v = "STRONG_BUY"
    elif lean >= 0.20:
        v = "BUY"
    elif lean <= -0.55:
        v = "AVOID"
    elif lean <= -0.20:
        v = "SELL"
    else:
        v = "HOLD"
    group = "BUY" if v in ("STRONG_BUY", "BUY") else "SELL" if v in ("SELL", "AVOID") else "HOLD"
    return v, group


def composite(evidence: dict, ai: dict | None = None) -> dict:
    """Blend all available signals into one transparent recommendation."""
    rec = evidence.get("recommendation") or evidence.get("model_verdict") or {}
    leans = {
        "fundamental": _fundamental_lean(rec),
        "ai": _clip(_num(ai.get("lean"))) if ai and _num(ai.get("lean")) is not None else None,
        "analyst": _analyst_lean(evidence.get("analyst_consensus")),
        "ml": _ml_lean(evidence.get("ml_forecast")),
        "news": _news_lean(evidence.get("news")),
    }
    avail = {k: v for k, v in leans.items() if v is not None}
    if avail:
        wsum = sum(_WEIGHTS[k] for k in avail)
        blended = sum(_WEIGHTS[k] * v for k, v in avail.items()) / wsum
    else:
        blended = 0.0

    has_red_flag = bool((rec.get("red_flags")) or [])
    if has_red_flag:
        blended = min(blended, 0.10) - 0.10  # governance flags cap & dock the upside

    dq = (evidence.get("data_quality") or {}).get("confidence")
    if dq is not None and dq < 50:
        blended *= 0.7  # shrink toward neutral when the inputs are shaky

    blended = _clip(blended)
    verdict, group = _bucket(blended, has_red_flag)
    score = round(50 + 50 * blended, 1)

    n_bull = sum(1 for v in avail.values() if v > 0.1)
    n_bear = sum(1 for v in avail.values() if v < -0.1)
    agreement = round(len(avail) and max(n_bull, n_bear) / len(avail), 2)

    components = {k: round(v, 2) for k, v in leans.items() if v is not None}
    rationale = _rationale(components, evidence, ai)

    out = {
        "verdict": verdict,
        "group": group,
        "score": score,
        "lean": round(blended, 3),
        "agreement": agreement,
        "signals_used": list(avail.keys()),
        "components": components,
        "rationale": rationale,
        "data_quality": (evidence.get("data_quality") or {}).get("verdict"),
        "has_red_flag": has_red_flag,
    }
    if ai:
        out["buy_case"] = ai.get("buy_case", [])
        out["sell_case"] = ai.get("sell_case", [])
        out["thesis"] = ai.get("thesis", "")
        out["ai_provider"] = ai.get("provider")
        out["ai_source"] = ai.get("source")
        out["data_critique"] = ai.get("data_critique", [])
        out["horizon_view"] = ai.get("horizon_view", {})
    return out


def _rationale(components: dict, evidence: dict, ai: dict | None) -> str:
    bits = []
    rec = evidence.get("recommendation") or {}
    if "fundamental" in components:
        bits.append(f"fundamentals {rec.get('verdict', '?')}")
    ac = evidence.get("analyst_consensus") or {}
    if ac.get("buy_pct") is not None:
        bits.append(f"brokers {ac.get('buy_pct')}% buy")
    elif ac.get("target_upside_pct") is not None:
        bits.append(f"analysts {ac.get('target_upside_pct'):+g}% target"
                    + (f" ({ac.get('rec_key')})" if ac.get("rec_key") else ""))
    ml = evidence.get("ml_forecast") or {}
    if ml.get("direction"):
        bits.append(f"ML {ml.get('direction').lower()} (P{ml.get('prob_up')})")
    news = evidence.get("news") or {}
    if news.get("net_sentiment") is not None:
        s = news["net_sentiment"]
        bits.append(f"news {'+' if s >= 0 else ''}{round(s, 2)}")
    if ai and ai.get("lean") is not None:
        bits.append(f"AI lean {ai.get('lean'):+.2f}")
    return " · ".join(bits)


if __name__ == "__main__":
    import json
    ev = {
        "recommendation": {"verdict": "BUY", "conviction": 68, "red_flags": []},
        "analyst_consensus": {"buy_pct": 70, "sell_pct": 10, "target_upside_pct": 18},
        "ml_forecast": {"direction": "UP", "prob_up": 0.6, "confidence": 0.4},
        "news": {"net_sentiment": 0.25},
        "data_quality": {"confidence": 82, "verdict": "high"},
    }
    print(json.dumps(composite(ev, ai={"lean": 0.45, "buy_case": ["x"], "sell_case": ["y"]}), indent=2))
