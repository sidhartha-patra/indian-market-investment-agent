"""Fundamental buy/sell recommendation engine — transparent, rules-based, explainable.

Turns the fundamental analysis (valuation, profitability, growth, financial health,
governance, quality) plus the canonical frameworks (Piotroski F-Score, Altman Z'',
Beneish M-Score, Graham, quality score) and the sector-relative composite into a single
verdict — STRONG_BUY / BUY / HOLD / SELL / AVOID — with an **extremely detailed**,
auditable rationale (pillar-by-pillar assessment, framework results, positives,
negatives, red flags, and "what would change my mind").

Works off the canonical metric keys produced by ``fundamental_analysis.normalize_*`` —
so it runs on **Screener.in** data (``recommend_from_screener``) or any other source.

> ⚠️ Educational/decision-support only. A "BUY" here is a quantitative *model signal*,
> NOT investment advice and NOT a SEBI-registered research recommendation. Publishing
> explicit buy/sell calls to the public in India requires SEBI RA registration — keep
> this for personal research or frame it as an educational signal with disclaimers.
"""
from __future__ import annotations

from src.strategies import fundamental_analysis as fa

_num = fa._num

VERDICTS = ["STRONG_BUY", "BUY", "HOLD", "SELL", "AVOID"]
DISCLAIMER = (
    "Educational model signal only — NOT investment advice, NOT a SEBI-registered research "
    "recommendation. Derived purely from public fundamentals; ignores price action, news, "
    "management quality and macro. Do your own research; consult a SEBI-registered adviser."
)


def _assess(pillar: str, verdict: str, detail: str, points: float) -> dict:
    return {"pillar": pillar, "verdict": verdict, "detail": detail, "points": round(points, 1)}


def _valuation(m: dict) -> dict:
    pe, pb = _num(m.get("pe")), _num(m.get("pb"))
    ey = _num(m.get("earnings_yield"))
    bits, pts = [], 0.0
    if pe is not None:
        bits.append(f"P/E {pe:.1f}")
        if 0 < pe < 15:
            pts += 6
        elif pe < 25:
            pts += 2
        elif pe > 45:
            pts -= 5
        elif pe > 35:
            pts -= 2
    if pb is not None:
        bits.append(f"P/B {pb:.1f}")
        if pb > 8:
            pts -= 3
        elif pb < 1.5:
            pts += 2
    if ey is not None:
        bits.append(f"earnings yield {ey:.1f}%")
    v = "Cheap" if pts >= 5 else "Fair" if pts >= 0 else "Expensive"
    return _assess("Valuation", v, ", ".join(bits) or "no valuation data", pts)


def _profitability(m: dict) -> dict:
    roe, roce, roa = _num(m.get("roe")), _num(m.get("roce")), _num(m.get("roa"))
    opm, nm = _num(m.get("opm")), _num(m.get("net_margin"))
    bits, pts = [], 0.0
    if roce is not None:
        bits.append(f"ROCE {roce:.1f}%")
        pts += 8 if roce > 20 else 4 if roce > 15 else -4 if roce < 10 else 0
    if roe is not None:
        bits.append(f"ROE {roe:.1f}%")
        pts += 5 if roe > 18 else 2 if roe > 12 else -3 if roe < 8 else 0
    if nm is not None:
        bits.append(f"net margin {nm:.1f}%")
    if opm is not None:
        bits.append(f"OPM {opm:.1f}%")
    if roa is not None:
        bits.append(f"ROA {roa:.1f}%")
    v = "Strong" if pts >= 8 else "Decent" if pts >= 2 else "Weak"
    return _assess("Profitability", v, ", ".join(bits) or "no profitability data", pts)


def _growth(m: dict) -> dict:
    rg, pg = _num(m.get("revenue_growth_5y")), _num(m.get("profit_growth_5y"))
    bits, pts = [], 0.0
    if pg is not None:
        bits.append(f"profit growth {pg:.1f}%")
        pts += 6 if pg > 15 else 3 if pg > 8 else -6 if pg < 0 else 0
    if rg is not None:
        bits.append(f"revenue growth {rg:.1f}%")
        pts += 3 if rg > 12 else -2 if rg < 3 else 0
    v = "Strong" if pts >= 6 else "Steady" if pts >= 0 else "Declining"
    return _assess("Growth", v, ", ".join(bits) or "no growth data", pts)


def _health(m: dict) -> tuple[dict, list[str]]:
    de, cr = _num(m.get("debt_to_equity")), _num(m.get("current_ratio"))
    bits, pts, flags = [], 0.0, []
    if de is not None:
        bits.append(f"D/E {de:.2f}")
        if de < 0.5:
            pts += 5
        elif de < 1.0:
            pts += 2
        elif de > 2.0:
            pts -= 8; flags.append(f"High leverage (D/E {de:.1f})")
        elif de > 1.5:
            pts -= 3
    if cr is not None:
        bits.append(f"current ratio {cr:.1f}")
        if cr < 1:
            pts -= 3; flags.append(f"Weak liquidity (current ratio {cr:.1f})")
        elif cr > 2:
            pts += 2
    az = fa.altman_z_score(m, sector=m.get("sector"))
    if az.get("z_score") is not None:
        bits.append(f"Altman Z'' {az['z_score']} ({az['zone']})")
        if az["zone"] == "Distress":
            pts -= 12; flags.append(f"Altman Z'' in distress zone ({az['z_score']})")
        elif az["zone"] == "Safe":
            pts += 4
    v = "Solid" if pts >= 5 else "Adequate" if pts >= 0 else "Stretched"
    return _assess("Financial health", v, ", ".join(bits) or "no balance-sheet data", pts), flags


def _governance(m: dict) -> tuple[dict, list[str]]:
    pledged, prom = _num(m.get("pledged_pct")), _num(m.get("promoter_holding"))
    bits, pts, flags = [], 0.0, []
    if pledged is not None:
        bits.append(f"promoter pledge {pledged:.0f}%")
        if pledged > 50:
            pts -= 15; flags.append(f"Very high promoter pledge ({pledged:.0f}%)")
        elif pledged > 25:
            pts -= 8; flags.append(f"Elevated promoter pledge ({pledged:.0f}%)")
        elif pledged == 0:
            pts += 4
    if prom is not None:
        bits.append(f"promoter holding {prom:.0f}%")
        pts += 4 if prom > 55 else 2 if prom > 40 else 0
    v = "Clean" if pts >= 4 else "Acceptable" if pts >= 0 else "Concern"
    return _assess("Governance", v, ", ".join(bits) or "no ownership data", pts), flags


def _verdict(conviction: float, red_flags: list[str]) -> str:
    if red_flags and conviction < 50:
        return "AVOID"
    if conviction >= 78:
        return "STRONG_BUY"
    if conviction >= 62:
        return "BUY"
    if conviction >= 42:
        return "HOLD"
    if conviction >= 28:
        return "SELL"
    return "AVOID"


def _illustrative_long_return(m: dict) -> tuple[float | None, str]:
    """Illustrative long-run annual total return ≈ earnings growth + dividend yield."""
    pg = _num(m.get("profit_growth_5y"))
    dy = _num(m.get("dividend_yield")) or 0.0
    if pg is None:
        return None, "growth data unavailable"
    g = max(-15.0, min(25.0, pg))
    annual = round(g + dy, 1)
    return annual, (f"~{annual:.0f}%/yr illustrative total return IF profits compound near their "
                    f"5y rate ({pg:.0f}%) and the P/E holds, plus ~{dy:.1f}% dividend — a scenario, "
                    "not a forecast")


def horizon_outlook(m: dict, long_verdict: str, conviction: float) -> dict:
    """Short / mid / long-term outlook from cheap technicals + the fundamental verdict.

    Short/mid-term use 50/200-DMA and 52-week range (proxies, weak signals). Long-term
    uses the fundamental verdict + an illustrative return scenario. Nothing here is a
    profit prediction.
    """
    price = _num(m.get("price"))
    sma50, sma200 = _num(m.get("sma50")), _num(m.get("sma200"))
    hi, lo = _num(m.get("high_52w")), _num(m.get("low_52w"))
    pos = (price - lo) / (hi - lo) if (price and hi and lo and hi > lo) else None
    vol_band = round((hi - lo) / price * 100, 0) if (price and hi and lo and price > 0) else None

    # Short term (days–weeks)
    s_drivers, s_stance = [], "WATCH"
    if price and sma50:
        s_drivers.append(("above" if price > sma50 else "below") + f" 50-DMA ({sma50:.0f})")
    if pos is not None:
        s_drivers.append(f"{pos*100:.0f}% up its 52-week range")
        if price and sma50 and price > sma50 and 0.35 <= pos <= 0.85:
            s_stance = "BUY"
        elif pos > 0.92:
            s_stance = "CAUTION (extended near 52w high)"
        elif price and sma50 and price < sma50 and pos < 0.35:
            s_stance = "AVOID"
        else:
            s_stance = "HOLD"
    short = {"horizon": "days–weeks", "stance": s_stance,
             "drivers": s_drivers or ["insufficient price data"],
             "illustrative_move_pct": round(vol_band / 6, 1) if vol_band else None,
             "confidence": "low"}

    # Mid term (1–6 months)
    m_drivers, m_stance = [], "HOLD"
    if price and sma200:
        above200 = price > sma200
        m_drivers.append(("above" if above200 else "below") + f" 200-DMA ({sma200:.0f})")
        if sma50 and sma200:
            golden = sma50 > sma200
            m_drivers.append("50-DMA " + ("above" if golden else "below")
                             + " 200-DMA " + ("(golden cross)" if golden else "(death cross)"))
            m_stance = "BUY" if (above200 and golden) else "AVOID" if (not above200 and not golden) else "HOLD"
        else:
            m_stance = "BUY" if above200 else "HOLD"
    mid = {"horizon": "1–6 months", "stance": m_stance,
           "drivers": m_drivers or ["insufficient trend data"], "confidence": "low-moderate"}

    # Long term (1–3+ years)
    annual, scenario = _illustrative_long_return(m)
    l_stance = {"STRONG_BUY": "ACCUMULATE", "BUY": "ACCUMULATE", "HOLD": "HOLD",
                "SELL": "REDUCE", "AVOID": "AVOID"}.get(long_verdict, "HOLD")
    target = _num(m.get("analyst_target"))
    analyst_upside = round((target / price - 1) * 100, 1) if (target and price) else None
    long = {"horizon": "1–3+ years", "stance": l_stance,
            "drivers": [f"fundamental model: {long_verdict.replace('_', ' ').title()} "
                        f"(conviction {conviction}/100)"],
            "illustrative_annual_return_pct": annual, "scenario": scenario,
            "analyst_consensus_upside_pct": analyst_upside, "confidence": "moderate"}

    return {"short_term": short, "mid_term": mid, "long_term": long,
            "note": ("Outlooks are scenario-based, NOT profit predictions. Short/mid-term are "
                     "technical and weak; the long-term fundamental view is the most reliable. "
                     "Any of them can fail on news, earnings, macro or black-swan events.")}


def recommend(metrics: dict, sector_score: float | None = None,
              framework_inputs: dict | None = None) -> dict:
    """Produce a detailed fundamental buy/sell recommendation for one stock.

    ``metrics``: canonical metric dict. ``sector_score``: the 0-100 sector-relative
    fundamental score (anchors conviction). ``framework_inputs``: optional
    {curr, prev} statement dicts to enable Piotroski + Beneish.
    """
    base = _num(sector_score)
    if base is None:
        base = 50.0  # neutral prior when no sector score is supplied

    val = _valuation(metrics)
    prof = _profitability(metrics)
    grow = _growth(metrics)
    health, hflags = _health(metrics)
    gov, gflags = _governance(metrics)
    assessments = [val, prof, grow, health, gov]

    quality = fa.quality_score_fundamental(metrics)
    graham = fa.graham_defensive_score(metrics)
    frameworks: dict = {"quality_score": quality, "graham": graham,
                        "altman": fa.altman_z_score(metrics, sector=metrics.get("sector"))}

    red_flags = list(hflags) + list(gflags)
    fi = framework_inputs or {}
    if fi.get("curr") and fi.get("prev"):
        pio = fa.piotroski_f_score(fi["curr"], fi["prev"])
        ben = fa.beneish_m_score(fi["curr"], fi["prev"])
        frameworks["piotroski"] = pio
        frameworks["beneish"] = ben
        if ben.get("manipulator"):
            red_flags.append(f"Beneish M-Score flags possible earnings manipulation ({ben['m_score']})")
        if pio["f_score"] <= 2:
            red_flags.append(f"Very weak Piotroski F-Score ({pio['f_score']}/9)")

    conviction = base + sum(a["points"] for a in assessments)
    if quality["quality_score"] >= 70:
        conviction += 4
    elif quality["quality_score"] < 30:
        conviction -= 6
    if "piotroski" in frameworks and frameworks["piotroski"]["f_score"] >= 7:
        conviction += 5
    conviction = round(max(0.0, min(100.0, conviction)), 1)

    verdict = _verdict(conviction, red_flags)

    positives = [f"{a['pillar']}: {a['detail']}" for a in assessments if a["points"] >= 3]
    negatives = [f"{a['pillar']}: {a['detail']}" for a in assessments if a["points"] <= -2]
    data_gaps = [a["pillar"] for a in assessments if "no " in a["detail"]]
    what_would_change = _what_would_change(metrics, verdict, red_flags)

    present = sum(1 for k in ("pe", "roe", "debt_to_equity", "profit_growth_5y", "pledged_pct")
                  if _num(metrics.get(k)) is not None)
    confidence = "high" if present >= 4 and not data_gaps else "moderate" if present >= 2 else "low"

    horizons = horizon_outlook(metrics, verdict, conviction)

    return {
        "verdict": verdict,
        "verdict_label": _LABEL[verdict],
        "conviction": conviction,
        "confidence": confidence,
        "summary": _summary(metrics, verdict, conviction, assessments, red_flags),
        "assessments": assessments,
        "frameworks": frameworks,
        "positives": positives or ["No standout strengths from the available data"],
        "negatives": negatives or ["No major fundamental weaknesses detected"],
        "red_flags": red_flags,
        "what_would_change": what_would_change,
        "data_gaps": data_gaps,
        "horizons": horizons,
        "disclaimer": DISCLAIMER,
    }


_LABEL = {
    "STRONG_BUY": "Strong Buy (educational model signal)",
    "BUY": "Buy (educational model signal)",
    "HOLD": "Hold / Watch (educational model signal)",
    "SELL": "Reduce / Sell (educational model signal)",
    "AVOID": "Avoid (educational model signal)",
}


def _summary(m: dict, verdict: str, conviction: float, assessments: list[dict],
             red_flags: list[str]) -> str:
    name = m.get("name") or m.get("symbol") or "This stock"
    strong = [a["pillar"].lower() for a in assessments if a["points"] >= 3]
    weak = [a["pillar"].lower() for a in assessments if a["points"] <= -2]
    s = f"{name} scores {conviction}/100 on the fundamental model → {verdict.replace('_', ' ').title()}. "
    if strong:
        s += "Supported by " + ", ".join(strong) + ". "
    if weak:
        s += "Held back by " + ", ".join(weak) + ". "
    if red_flags:
        s += f"⚠ {len(red_flags)} red flag(s): " + "; ".join(red_flags[:2]) + ". "
    return s.strip()


def _what_would_change(m: dict, verdict: str, red_flags: list[str]) -> list[str]:
    out = []
    pe = _num(m.get("pe"))
    de = _num(m.get("debt_to_equity"))
    pg = _num(m.get("profit_growth_5y"))
    pledged = _num(m.get("pledged_pct"))
    if verdict in ("STRONG_BUY", "BUY"):
        if pe and pe > 30:
            out.append(f"A de-rating below ~25x P/E (now {pe:.0f}) would improve the margin of safety")
        out.append("Deteriorating margins, falling ROCE, or rising leverage would downgrade the signal")
    else:
        if pg is not None and pg < 8:
            out.append("A sustained pickup in profit growth (>15%) would upgrade the signal")
        if de is not None and de > 1.5:
            out.append(f"De-leveraging (D/E from {de:.1f} toward <1.0) would improve the verdict")
        if pledged and pledged > 25:
            out.append("Promoters releasing pledged shares would remove a key governance red flag")
    if not out:
        out.append("Material change in profitability, leverage, growth, or governance would move the verdict")
    return out


def recommend_from_screener(symbol: str, framework_inputs: dict | None = None) -> dict:
    """Pull Screener.in fundamentals for ``symbol`` and produce a detailed recommendation.

    PERSONAL research use (Screener.in ToS). Returns the recommendation dict plus the
    normalized metrics used.
    """
    from src.ingestion.screener import get_screener_fundamentals

    raw = get_screener_fundamentals(symbol) or {}
    metrics = fa.normalize_screener(raw)
    metrics["symbol"] = symbol.upper()
    rec = recommend(metrics, sector_score=None, framework_inputs=framework_inputs)
    rec["_metrics"] = metrics
    return rec


def detailed_markdown(rec: dict, symbol: str = "") -> str:
    """Render an extreme-detail recommendation report as markdown (CLI / docs)."""
    lines = [f"# {symbol or rec.get('verdict')} — {rec['verdict_label']}",
             f"\n**Conviction:** {rec['conviction']}/100  ·  **Confidence:** {rec['confidence']}",
             f"\n{rec['summary']}\n", "## Pillar-by-pillar"]
    for a in rec["assessments"]:
        lines.append(f"- **{a['pillar']}** — {a['verdict']} ({a['points']:+}): {a['detail']}")
    lines.append("\n## Frameworks")
    fw = rec["frameworks"]
    q = fw.get("quality_score", {})
    lines.append(f"- Quality score: {q.get('quality_score')}/100 ({q.get('signal')})")
    az = fw.get("altman", {})
    lines.append(f"- Altman Z'': {az.get('z_score')} ({az.get('zone')})")
    g = fw.get("graham", {})
    lines.append(f"- Graham defensive: {g.get('graham_score')}/7 ({g.get('signal')})")
    if "piotroski" in fw:
        lines.append(f"- Piotroski F-Score: {fw['piotroski']['f_score']}/9 ({fw['piotroski']['signal']})")
    if "beneish" in fw:
        lines.append(f"- Beneish M-Score: {fw['beneish']['m_score']} ({fw['beneish']['signal']})")
    if rec["red_flags"]:
        lines.append("\n## 🚩 Red flags")
        lines += [f"- {x}" for x in rec["red_flags"]]
    lines.append("\n## ✅ Positives")
    lines += [f"- {x}" for x in rec["positives"]]
    lines.append("\n## ⚠️ Negatives")
    lines += [f"- {x}" for x in rec["negatives"]]
    lines.append("\n## What would change the verdict")
    lines += [f"- {x}" for x in rec["what_would_change"]]
    h = rec.get("horizons", {})
    if h:
        lines.append("\n## Short / Mid / Long-term outlook")
        for key, label in (("short_term", "Short"), ("mid_term", "Mid"), ("long_term", "Long")):
            hz = h.get(key, {})
            extra = ""
            if hz.get("illustrative_move_pct") is not None:
                extra = f" · ±{hz['illustrative_move_pct']}% typical move"
            elif hz.get("illustrative_annual_return_pct") is not None:
                extra = f" · ~{hz['illustrative_annual_return_pct']}%/yr (scenario)"
            lines.append(f"- **{label}** ({hz.get('horizon')}): {hz.get('stance')}{extra} — "
                         + "; ".join(hz.get("drivers", [])))
        lines.append(f"\n> {h.get('note')}")
    lines.append(f"\n---\n*{rec['disclaimer']}*")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    try:
        from src.ingestion.yfinance_provider import build_dataset
        df = build_dataset(symbols=[sym], mode="full")
        metrics = df.iloc[0].to_dict()
    except Exception:  # noqa: BLE001
        metrics = {"symbol": sym}
    print(detailed_markdown(recommend(metrics, sector_score=metrics.get("fundamental_score")), sym))
