"""LLM qualitative moat and integrity assessment for multibagger candidates."""
from __future__ import annotations

from functools import lru_cache
import json
import logging

from src.agent.gh_models import run_json

logger = logging.getLogger(__name__)

DEFAULT_ASSESSMENT = {
    "moat_type": "none",
    "moat_strength": 0,
    "governance_flags": [],
    "green_flags": [],
    "qualitative_score": 0,
    "summary": "LLM assessment unavailable.",
}
VALID_MOATS = {"switching_cost", "patent", "brand", "network", "scale", "regulatory", "none"}


def _default() -> dict:
    return dict(DEFAULT_ASSESSMENT, governance_flags=[], green_flags=[])


def _clamp_number(value, low: float, high: float, default: float = 0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clean_assessment(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return _default()
    moat_type = str(raw.get("moat_type", "none")).strip().lower()
    if moat_type not in VALID_MOATS:
        moat_type = "none"
    return {
        "moat_type": moat_type,
        "moat_strength": int(round(_clamp_number(raw.get("moat_strength"), 0, 10))),
        "governance_flags": raw.get("governance_flags") if isinstance(raw.get("governance_flags"), list) else [],
        "green_flags": raw.get("green_flags") if isinstance(raw.get("green_flags"), list) else [],
        "qualitative_score": int(round(_clamp_number(raw.get("qualitative_score"), 0, 100))),
        "summary": str(raw.get("summary") or "")[:500],
    }


@lru_cache(maxsize=256)
def _assess_cached(symbol: str, fundamentals_json: str, sector_theme: str | None) -> dict:
    system = (
        "You are a cautious Indian equity analyst. Return ONLY valid JSON. "
        "Do not include markdown, prose outside JSON, or unsupported fields."
    )
    prompt = f"""
Assess this Indian listed company for Lynch/Slater multibagger quality.

Company symbol: {symbol}
Sector/theme: {sector_theme or "unknown"}
Fundamentals JSON: {fundamentals_json}

Return ONLY JSON with exactly these keys:
{{
  "moat_type": "switching_cost|patent|brand|network|scale|regulatory|none",
  "moat_strength": 0-10,
  "governance_flags": ["red flags such as related-party concerns, auditor churn, pledge, aggressive accounting"],
  "green_flags": ["promoter buying, low pledge, dividends, capacity expansion, margin expansion"],
  "qualitative_score": 0-100,
  "summary": "two short lines maximum"
}}
Base the assessment only on the supplied fundamentals and generally known business context. If evidence is weak, be conservative.
""".strip()
    try:
        return _clean_assessment(run_json(prompt, system=system))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qualitative LLM assessment failed for %s: %s", symbol, exc)
        return _default()


def assess_company(symbol: str, fundamentals: dict, sector_theme: str | None = None) -> dict:
    """LLM-based qualitative moat and integrity assessment."""
    try:
        fundamentals_json = json.dumps(fundamentals or {}, sort_keys=True, default=str)
    except TypeError:
        fundamentals_json = json.dumps({k: str(v) for k, v in (fundamentals or {}).items()}, sort_keys=True)
    return dict(_assess_cached(symbol.upper().strip(), fundamentals_json, sector_theme))
