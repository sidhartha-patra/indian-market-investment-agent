"""Explainability layer — turn raw signals into a transparent, honest rationale.

Every suggestion the engine emits must answer: *why this name, what could go wrong,
how risky is it, how sure are we, how fresh is the data, and what can the models NOT
do?* This module is deliberately rule-based (not a black box) so the explanation is
auditable. It never says "buy" and always surfaces the negatives and the limitations.
"""
from __future__ import annotations

from datetime import datetime, timezone

_LIMITATIONS = [
    "Signals are statistical edges, not certainties; any single trade can lose.",
    "Models assume the near future resembles the recent past and break on news, "
    "earnings surprises, macro shocks and black-swan events.",
    "Backtested/expected behaviour is not a promise of future results.",
    "Not SEBI/SEC-registered advice. Educational and decision-support use only.",
]


def _num(v):
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _risk_level(record: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    vol = _num(record.get("ann_vol_pct"))
    dd = _num(record.get("max_drawdown_pct"))
    adv = _num(record.get("adv_cr"))
    score = 0
    if vol is not None:
        if vol > 45:
            score += 2; notes.append(f"High volatility ({vol:.0f}% annualised)")
        elif vol > 28:
            score += 1; notes.append(f"Moderate volatility ({vol:.0f}% annualised)")
    if dd is not None and dd < -35:
        score += 1; notes.append(f"Deep historical drawdown ({dd:.0f}%)")
    if adv is not None and adv < 5:
        score += 2; notes.append(f"Thin liquidity (~Rs {adv:.1f} Cr ADV) — slippage risk")
    level = "HIGH" if score >= 3 else "MEDIUM" if score >= 1 else "LOW"
    return level, notes


def _positives(r: dict) -> list[str]:
    out = []
    fs = _num(r.get("factor_score") or r.get("score"))
    if fs is not None and fs >= 70:
        out.append(f"Top-decile multi-factor score ({fs:.0f}/100)")
    if _num(r.get("momentum_score")) and _num(r.get("momentum_score")) >= 70:
        out.append("Strong price momentum (near 52-week strength)")
    if _num(r.get("relative_strength_score")) and _num(r.get("relative_strength_score")) >= 70:
        out.append("Outperforming the benchmark (high relative strength)")
    if _num(r.get("trend")) and _num(r.get("trend")) > 0:
        out.append(f"Trades above its long-term average (+{_num(r.get('trend')):.0f}% vs 200DMA)")
    roce = _num(r.get("roce"))
    if roce is not None and roce > 15:
        out.append(f"High capital efficiency (ROCE {roce:.0f}%)")
    de = _num(r.get("debt_to_equity"))
    if de is not None and de < 0.5:
        out.append(f"Low leverage (D/E {de:.2f})")
    pe = _num(r.get("pe"))
    if pe is not None and 0 < pe < 25:
        out.append(f"Undemanding valuation (P/E {pe:.0f})")
    pred = r.get("prediction") or {}
    if _num(pred.get("prob_up")) and _num(pred.get("prob_up")) >= 0.6:
        out.append(f"Model leans up ({pred['prob_up']:.0%} prob, {pred.get('horizon_days','?')}d)")
    return out or ["Ranks acceptably on the composite but without a standout edge"]


def _negatives(r: dict) -> list[str]:
    out = []
    rsi = _num(r.get("rsi") or r.get("rsi_14"))
    if rsi is not None and rsi > 75:
        out.append(f"Overbought (RSI {rsi:.0f}) — risk of near-term pullback")
    pe = _num(r.get("pe"))
    if pe is not None and pe > 60:
        out.append(f"Rich valuation (P/E {pe:.0f}) — little margin for error")
    de = _num(r.get("debt_to_equity"))
    if de is not None and de > 1.5:
        out.append(f"Elevated leverage (D/E {de:.1f})")
    vol = _num(r.get("ann_vol_pct"))
    if vol is not None and vol > 45:
        out.append(f"High volatility ({vol:.0f}%) — size positions down")
    pred = r.get("prediction") or {}
    pu = _num(pred.get("prob_up"))
    if pu is not None and pu < 0.45:
        out.append(f"Model leans down ({pu:.0%} prob up)")
    if _num(r.get("pledged_pct")) and _num(r.get("pledged_pct")) > 0:
        out.append(f"Promoter pledging present ({_num(r.get('pledged_pct')):.0f}%)")
    return out or ["No single dominant red flag detected, but data coverage is partial"]


def _freshness(last_date: str | None) -> dict:
    if not last_date:
        return {"last_data_date": None, "age_days": None, "is_stale": True}
    try:
        d = datetime.fromisoformat(str(last_date)[:10]).date()
        age = (datetime.now(timezone.utc).date() - d).days
        return {"last_data_date": str(d), "age_days": age, "is_stale": age > 5}
    except ValueError:
        return {"last_data_date": last_date, "age_days": None, "is_stale": True}


def _confidence(r: dict, risk_level: str) -> float:
    """0-1 conviction = signal strength x model agreement x freshness, risk-tempered."""
    fs = _num(r.get("factor_score") or r.get("score")) or 50.0
    base = min(1.0, max(0.0, (fs - 50) / 50))  # 50->0, 100->1
    pred = r.get("prediction") or {}
    model_conf = _num(pred.get("confidence"))
    agreement = 1.0
    if model_conf is not None:
        pu = _num(pred.get("prob_up")) or 0.5
        # Penalise when the model disagrees with the factor thesis.
        agreement = 0.7 + 0.3 * model_conf if pu >= 0.5 else 0.5
    fresh = 0.7 if _freshness(r.get("last_date")).get("is_stale") else 1.0
    risk_factor = {"LOW": 1.0, "MEDIUM": 0.9, "HIGH": 0.75}.get(risk_level, 0.9)
    return round(min(1.0, base * agreement * fresh * risk_factor), 3)


def explain_suggestion(record: dict, regime: dict | None = None) -> dict:
    """Build a full, guardrailed explanation payload for one suggested stock."""
    ticker = record.get("ticker") or record.get("symbol")
    risk_level, risk_notes = _risk_level(record)
    positives = _positives(record)
    negatives = _negatives(record)
    confidence = _confidence(record, risk_level)
    freshness = _freshness(record.get("last_date"))

    guardrails: list[str] = []
    if record.get("is_penny") or (_num(record.get("price")) or 1e9) < 10:
        guardrails.append("Low-priced/penny stock — disabled by default; higher manipulation risk.")
    if _num(record.get("adv_cr")) is not None and _num(record.get("adv_cr")) < 5:
        guardrails.append("Illiquid — exit may be costly; keep position small.")
    if regime and regime.get("regime") == "RISK_OFF":
        guardrails.append("Market regime is RISK_OFF — the engine recommends reduced exposure.")

    thesis = (
        f"{ticker} ranks in the {'top' if confidence >= 0.5 else 'middle'} tier on a blend of "
        f"momentum, quality, value and trend"
        + (f"; market regime is {regime['regime']}." if regime else ".")
    )

    return {
        "ticker": ticker,
        "thesis": thesis,
        "positives": positives,
        "negatives": negatives,
        "risk_level": risk_level,
        "risk_notes": risk_notes,
        "confidence": confidence,
        "confidence_label": (
            "high" if confidence >= 0.66 else "moderate" if confidence >= 0.4 else "low"
        ),
        "data_freshness": freshness,
        "guardrails": guardrails,
        "model_limitations": _LIMITATIONS,
        "disclaimer": "Probabilistic, risk-aware insight for education only. Not investment advice.",
    }


def explain_many(records: list[dict], regime: dict | None = None) -> list[dict]:
    return [explain_suggestion(r, regime=regime) for r in records]
