"""Cross-validate fundamentals across sources — never trust one site blindly.

Merges canonical-metric dicts from multiple providers (TradingView, Yahoo Finance,
Screener.in, Moneycontrol), then *grills* them:
  * reconciles overlapping fields to a robust consensus (median), with a source priority,
  * flags **conflicts** (sources disagree beyond tolerance),
  * flags **suspect** values that are economically implausible (e.g. P/E < 0 or > 300),
  * scores an overall **data-quality confidence** the rest of the pipeline can lean on.

Output feeds both the deterministic model and the Gen-AI analyst, which is told to treat
low-confidence / conflicting inputs with extra skepticism.
"""
from __future__ import annotations

import logging
from statistics import median

logger = logging.getLogger(__name__)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


# Fields worth cross-checking, with a relative-disagreement tolerance and plausible bounds.
# tol: |max-min|/|median| above which we flag a conflict. bounds: (low, high) plausible range.
_CHECK = {
    "price":            {"tol": 0.08, "bounds": (0.1, 1_000_000)},
    "pe":               {"tol": 0.20, "bounds": (0.0, 300)},
    "pb":               {"tol": 0.25, "bounds": (0.0, 80)},
    "roe":              {"tol": 0.30, "bounds": (-100, 200)},
    "roce":             {"tol": 0.30, "bounds": (-100, 200)},
    "debt_to_equity":   {"tol": 0.30, "bounds": (0.0, 20)},
    "dividend_yield":   {"tol": 0.40, "bounds": (0.0, 30)},
    "promoter_holding": {"tol": 0.15, "bounds": (0.0, 100)},
    "pledged_pct":      {"tol": 0.30, "bounds": (0.0, 100)},
    "high_52w":         {"tol": 0.10, "bounds": (0.1, 1_000_000)},
    "low_52w":          {"tol": 0.10, "bounds": (0.1, 1_000_000)},
    "profit_growth_5y": {"tol": 0.50, "bounds": (-100, 300)},
}

# For fundamentals, trust the most India-specific/detailed source first.
_PRIORITY = ("screener", "yfinance", "tradingview", "moneycontrol")


def reconcile(symbol: str, sources: dict[str, dict | None]) -> dict:
    """Merge + cross-validate per-source canonical metric dicts.

    ``sources`` maps a source name (``screener``/``yfinance``/``tradingview``/
    ``moneycontrol``) to its canonical metric dict (or None). Returns
    ``{"symbol", "metrics", "data_quality"}``.
    """
    clean = {name: (d or {}) for name, d in sources.items() if d}
    present_sources = [n for n in clean if any(_num(v) is not None for v in clean[n].values())]

    metrics: dict = {}
    field_source: dict[str, str] = {}
    conflicts: list[str] = []
    suspect: list[str] = []
    notes: list[str] = []

    # union of all metric keys across sources
    all_keys: set[str] = set()
    for d in clean.values():
        all_keys.update(d.keys())

    for key in all_keys:
        # collect (source, value) for numeric fields
        vals = [(name, _num(clean[name].get(key))) for name in clean if _num(clean[name].get(key)) is not None]
        if not vals:
            # carry through a non-numeric value (e.g. name/sector) from priority source
            for name in list(_PRIORITY) + list(clean):
                if clean.get(name, {}).get(key) not in (None, ""):
                    metrics[key] = clean[name][key]
                    field_source[key] = name
                    break
            continue

        nums = [v for _, v in vals]
        chosen_val, chosen_src = _pick(vals)
        metrics[key] = chosen_val
        field_source[key] = chosen_src

        cfg = _CHECK.get(key)
        if cfg:
            lo, hi = cfg["bounds"]
            for name, v in vals:  # grill EVERY source value, even ones we didn't choose
                if not (lo <= v <= hi):
                    suspect.append(f"{key}={v:g} from {name} implausible (outside [{lo:g},{hi:g}])")
            if len(nums) >= 2:
                mn, mx, md = min(nums), max(nums), median(nums)
                spread = (mx - mn) / abs(md) if md else (mx - mn)
                if spread > cfg["tol"]:
                    srcs = ", ".join(f"{n}={v:g}" for n, v in vals)
                    conflicts.append(f"{key}: sources disagree {spread*100:.0f}% ({srcs}) -> used {chosen_val:g}")

    # cross-field sanity (internal consistency, not just per-field)
    _cross_field_checks(metrics, suspect)

    n_sources = len(present_sources)
    if n_sources <= 1:
        notes.append("single-source data — limited cross-validation")

    confidence = _confidence(metrics, n_sources, conflicts, suspect)
    quality = {
        "confidence": confidence,
        "n_sources": n_sources,
        "sources": present_sources,
        "field_source": field_source,
        "conflicts": conflicts,
        "suspect": suspect,
        "notes": notes,
        "verdict": "high" if confidence >= 75 else "moderate" if confidence >= 50 else "low",
    }
    return {"symbol": str(symbol).upper(), "metrics": metrics, "data_quality": quality}


def _pick(vals: list[tuple[str, float]]) -> tuple[float, str]:
    """Choose a value: priority source if it's near the median, else the median itself."""
    nums = [v for _, v in vals]
    md = median(nums)
    for src in _PRIORITY:
        for name, v in vals:
            if name == src:
                # accept the priority source unless it's a wild outlier vs the median
                if md == 0 or abs(v - md) / abs(md) <= 0.5:
                    return v, name
                break
    # fall back to the source whose value is closest to the median (robust consensus)
    name, v = min(vals, key=lambda nv: abs(nv[1] - md))
    return v, f"{name}~median"


def _cross_field_checks(m: dict, suspect: list[str]) -> None:
    price, hi, lo = _num(m.get("price")), _num(m.get("high_52w")), _num(m.get("low_52w"))
    if hi and lo and hi < lo:
        suspect.append(f"52-week high {hi:g} < low {lo:g} (inconsistent)")
    if price and hi and price > hi * 1.05:
        suspect.append(f"price {price:g} above 52-week high {hi:g} (stale 52w?)")
    if price and lo and price < lo * 0.95:
        suspect.append(f"price {price:g} below 52-week low {lo:g} (stale 52w?)")
    pe, roe = _num(m.get("pe")), _num(m.get("roe"))
    if pe is not None and pe > 0 and roe is not None and roe < 0:
        suspect.append("positive P/E with negative ROE (loss-making yet priced as earner?)")
    ph = _num(m.get("promoter_holding"))
    if ph is not None and not (0 <= ph <= 100):
        suspect.append(f"promoter holding {ph:g}% out of [0,100]")


def _confidence(metrics: dict, n_sources: int, conflicts: list, suspect: list) -> int:
    score = 55 + min(2, max(0, n_sources - 1)) * 12  # 1 src ->55, 2 ->67, 3+ ->79
    key_fields = ("pe", "roe", "debt_to_equity", "profit_growth_5y", "price")
    present = sum(1 for k in key_fields if _num(metrics.get(k)) is not None)
    score += present * 3                      # data completeness
    score -= len(conflicts) * 8               # disagreements hurt
    score -= len(suspect) * 10                # implausible values hurt more
    return int(max(5, min(100, score)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo = reconcile("RELIANCE", {
        "tradingview": {"price": 1310, "pe": 22.8, "roe": 8.9, "high_52w": 1600, "low_52w": 1100},
        "yfinance": {"price": 1316, "pe": 24.1, "roe": 8.7, "dividend_yield": 0.46},
        "screener": {"pe": 22.5, "roe": 8.9, "roce": 10.3, "debt_to_equity": 0.44,
                     "profit_growth_5y": 12.0, "promoter_holding": 50.3},
    })
    import json
    print(json.dumps(demo["data_quality"], indent=2))
