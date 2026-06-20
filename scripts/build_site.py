"""Static, shareable stock-explainer website generator.

Turns precomputed per-stock records into a **zero-server static site**:
- ``index.html`` — searchable/sortable table of every stock with its educational score.
- ``stock/{SYMBOL}.html`` — per-stock detail page: the score, the *why* (positives /
  negatives / risks, sector-relative pillar breakdown), key metrics, and a disclaimer.
- ``data/index.json`` — machine-readable bundle for reuse.

Output is plain HTML/CSS/JS — host it free on GitHub Pages / Cloudflare Pages / Vercel,
and every stock gets a shareable URL (``/stock/RELIANCE.html``) with OpenGraph preview
metadata.

> ⚠️ **SEBI-safe framing:** Scores are **non-directional educational data summaries**,
> NOT buy/sell recommendations. The generator never emits "Buy/Sell/Target" labels and
> stamps a disclaimer on every page. See docs/DESIGN.md §Legal. Use a *licensed* data
> source for prices/fundamentals before publishing — do not redistribute scraped data.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DISCLAIMER_SHORT = (
    "Educational data summary only — NOT investment advice, NOT a SEBI-registered "
    "research report, and NOT a buy/sell recommendation. A high score does not mean "
    "'buy'. Data may be delayed or inaccurate. Consult a SEBI-registered adviser."
)
DISCLAIMER_FULL = (
    "This website is an independent, educational project. It is NOT a SEBI-registered "
    "Research Analyst or Investment Adviser. Nothing here is investment advice or a "
    "recommendation to buy, sell, or hold any security. Scores and rankings are "
    "quantitative summaries of publicly available data computed for educational "
    "purposes — they are NOT trading signals. Markets carry risk of capital loss. "
    "Past performance does not guarantee future results. Always do your own research "
    "and consult a SEBI-registered adviser (verify at sebi.gov.in) before investing."
)

_CSS = """
:root{--bg:#0f1419;--card:#1a2129;--mut:#8b97a3;--fg:#e6edf3;--accent:#4493f8;--good:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
header h1{margin:0 0 4px}.sub{color:var(--mut);font-size:14px}
.disclaimer{background:#2a1f0d;border:1px solid var(--warn);color:#f0d48a;padding:10px 14px;border-radius:8px;font-size:13px;margin:14px 0}
input.search{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #30363d;background:var(--card);color:var(--fg);font-size:15px;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #21262d}
th{cursor:pointer;color:var(--mut);user-select:none;position:sticky;top:0;background:var(--bg)}tr:hover{background:#161b22}
.score{font-weight:700;border-radius:6px;padding:2px 8px;display:inline-block;min-width:42px;text-align:center}
.s-hi{background:#0f3d20;color:var(--good)}.s-mid{background:#3d3410;color:var(--warn)}.s-lo{background:#3d1414;color:var(--bad)}
.card{background:var(--card);border:1px solid #21262d;border-radius:10px;padding:16px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.metric{background:#11161c;border-radius:8px;padding:8px 10px}.metric .k{color:var(--mut);font-size:12px}.metric .v{font-size:17px;font-weight:600}
.pill{height:8px;background:#11161c;border-radius:4px;overflow:hidden;margin-top:4px}.pill>span{display:block;height:100%}
.pos{color:var(--good)}.neg{color:var(--bad)}ul.why{margin:6px 0;padding-left:18px}
.foot{color:var(--mut);font-size:12px;margin-top:24px;border-top:1px solid #21262d;padding-top:12px}
.band{font-size:12px;color:var(--mut)}
"""

_SORT_JS = """
function sortTable(n,numeric){var t=document.getElementById('tbl'),r,i,x,y,sw=true,dir='asc',c=0;
while(sw){sw=false;r=t.rows;for(i=1;i<r.length-1;i++){var s=false;x=r[i].cells[n];y=r[i+1].cells[n];
var a=numeric?parseFloat(x.getAttribute('data-v')||x.innerText)||-1e9:x.innerText.toLowerCase();
var b=numeric?parseFloat(y.getAttribute('data-v')||y.innerText)||-1e9:y.innerText.toLowerCase();
if(dir=='asc'?a>b:a<b){s=true;break}}if(s){r[i].parentNode.insertBefore(r[i+1],r[i]);sw=true;c++}
else if(c==0&&dir=='asc'){dir='desc';sw=true}}}
function filt(){var q=document.getElementById('q').value.toLowerCase(),r=document.getElementById('tbl').rows,i;
for(i=1;i<r.length;i++){r[i].style.display=r[i].innerText.toLowerCase().indexOf(q)>-1?'':'none'}}
"""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _fmt(v: Any, dp: int = 2) -> str:
    try:
        f = float(v)
        return f"{f:,.{dp}f}"
    except (TypeError, ValueError):
        return _esc(v) if v not in (None, "") else "—"


def _score_class(score: float | None) -> str:
    if score is None:
        return "s-mid"
    return "s-hi" if score >= 66 else "s-mid" if score >= 40 else "s-lo"


def _index_html(records: list[dict], generated_at: str) -> str:
    rows = []
    for r in records:
        sym = _esc(r.get("symbol"))
        score = r.get("fundamental_score")
        sc = "—" if score is None else f"{score:.0f}"
        rows.append(
            f"<tr>"
            f"<td><a href='stock/{sym}.html'>{sym}</a></td>"
            f"<td>{_esc(r.get('name'))}</td>"
            f"<td>{_esc(r.get('sector'))}</td>"
            f"<td data-v='{_esc(r.get('price'))}'>{_fmt(r.get('price'))}</td>"
            f"<td data-v='{_esc(score)}'><span class='score {_score_class(score)}'>{sc}</span></td>"
            f"<td>{_esc((r.get('recommendation') or {}).get('verdict', ''))}</td>"
            f"<td>{_esc(r.get('tier'))}</td>"
            f"</tr>"
        )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Indian Stock Explorer — Educational Fundamental Scores</title>
<meta property='og:title' content='Indian Stock Explorer — Educational Fundamental Scores'>
<meta property='og:description' content='Sector-relative fundamental scores for Indian stocks. Educational only, not investment advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<header><h1>📊 Indian Stock Explorer</h1>
<div class='sub'>Sector-relative educational fundamental scores · {len(records)} stocks · updated {_esc(generated_at)}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
<input class='search' id='q' onkeyup='filt()' placeholder='Search by symbol, name or sector…'>
<table id='tbl'><thead><tr>
<th onclick='sortTable(0,false)'>Symbol</th><th onclick='sortTable(1,false)'>Name</th>
<th onclick='sortTable(2,false)'>Sector</th><th onclick='sortTable(3,true)'>Price</th>
<th onclick='sortTable(4,true)'>Score /100</th><th onclick='sortTable(5,false)'>Signal</th><th onclick='sortTable(6,false)'>Band</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class='foot'>{DISCLAIMER_FULL}</div></div><script>{_SORT_JS}</script></body></html>"""


def _why_list(items: list[str] | None, cls: str) -> str:
    if not items:
        return "<li class='band'>—</li>"
    return "".join(f"<li class='{cls}'>{_esc(x)}</li>" for x in items)


def _metric_cards(metrics: dict | None) -> str:
    if not metrics:
        return "<div class='band'>No metrics available.</div>"
    order = ["pe", "pb", "ev_ebitda", "roe", "roce", "roa", "opm", "net_margin",
             "revenue_growth_5y", "profit_growth_5y", "debt_to_equity", "current_ratio",
             "fcf_yield", "dividend_yield", "promoter_holding", "pledged_pct", "market_cap_cr"]
    labels = {"pe": "P/E", "pb": "P/B", "ev_ebitda": "EV/EBITDA", "roe": "ROE %", "roce": "ROCE %",
              "roa": "ROA %", "opm": "OPM %", "net_margin": "Net margin %",
              "revenue_growth_5y": "Rev gr 5y %", "profit_growth_5y": "Profit gr 5y %",
              "debt_to_equity": "D/E", "current_ratio": "Current ratio", "fcf_yield": "FCF yield %",
              "dividend_yield": "Div yield %", "promoter_holding": "Promoter %",
              "pledged_pct": "Pledged %", "market_cap_cr": "Mkt cap (Cr)"}
    cards = []
    for k in order:
        if k in metrics and metrics[k] is not None:
            cards.append(f"<div class='metric'><div class='k'>{labels[k]}</div>"
                         f"<div class='v'>{_fmt(metrics[k])}</div></div>")
    return "<div class='grid'>" + "".join(cards) + "</div>" if cards else "<div class='band'>No metrics.</div>"


def _pillar_bars(pillars: dict | None) -> str:
    if not pillars:
        return ""
    bars = []
    for name, z in pillars.items():
        try:
            zf = float(z)
        except (TypeError, ValueError):
            continue
        pct = max(0, min(100, (zf + 3) / 6 * 100))  # map z in [-3,3] -> [0,100]
        color = "var(--good)" if zf > 0.2 else "var(--bad)" if zf < -0.2 else "var(--warn)"
        bars.append(f"<div style='margin:6px 0'><div class='band'>{_esc(name)} "
                    f"(z={zf:+.2f})</div><div class='pill'><span style='width:{pct:.0f}%;"
                    f"background:{color}'></span></div></div>")
    return "".join(bars)


def _horizons_block(h: dict | None) -> str:
    """Render the short/mid/long-term outlook table (educational scenarios)."""
    if not h:
        return ""

    def row(key: str, label: str) -> str:
        hz = h.get(key, {}) or {}
        gain = "—"
        if hz.get("illustrative_move_pct") is not None:
            gain = f"±{hz['illustrative_move_pct']}% typical move"
        elif hz.get("illustrative_annual_return_pct") is not None:
            gain = f"~{hz['illustrative_annual_return_pct']}%/yr (scenario)"
        if hz.get("analyst_consensus_upside_pct") is not None:
            gain += f" · analysts {hz['analyst_consensus_upside_pct']:+g}%"
        return (f"<tr><td><b>{label}</b> <span class='band'>{_esc(hz.get('horizon'))}</span></td>"
                f"<td>{_esc(hz.get('stance'))}</td>"
                f"<td class='band'>{_esc('; '.join(hz.get('drivers', [])))}</td>"
                f"<td>{_esc(gain)}</td></tr>")

    return ("<h4 style='margin:14px 0 4px'>⏱️ Short / Mid / Long-term outlook</h4>"
            "<table><thead><tr><th>Horizon</th><th>Stance</th><th>Drivers</th>"
            "<th>Illustrative gain*</th></tr></thead><tbody>"
            f"{row('short_term', 'Short')}{row('mid_term', 'Mid')}{row('long_term', 'Long')}"
            f"</tbody></table><p class='band'>*{_esc(h.get('note'))}</p>")


_VERDICT_CLASS = {"STRONG_BUY": "s-hi", "BUY": "s-hi", "HOLD": "s-mid", "SELL": "s-lo", "AVOID": "s-lo"}


def _recommendation_html(rec: dict | None) -> str:
    """Render the detailed fundamental buy/sell model signal (educational)."""
    if not rec:
        return ""
    cls = _VERDICT_CLASS.get(rec.get("verdict", ""), "s-mid")
    rows = "".join(
        f"<tr><td>{_esc(a['pillar'])}</td><td>{_esc(a['verdict'])}</td>"
        f"<td>{_esc(a['detail'])}</td><td>{a['points']:+g}</td></tr>"
        for a in rec.get("assessments", [])
    )
    fw = rec.get("frameworks", {})
    bits = []
    if fw.get("quality_score"):
        bits.append(f"Quality {fw['quality_score'].get('quality_score')}/100")
    if fw.get("altman"):
        bits.append(f"Altman Z&#39;&#39; {fw['altman'].get('z_score')} ({_esc(fw['altman'].get('zone'))})")
    if fw.get("graham"):
        bits.append(f"Graham {fw['graham'].get('graham_score')}/7")
    if "piotroski" in fw:
        bits.append(f"Piotroski {fw['piotroski']['f_score']}/9")
    if "beneish" in fw:
        bits.append(f"Beneish {fw['beneish']['m_score']} ({_esc(fw['beneish']['signal'])})")
    flags = "".join(f"<li class='neg'>🚩 {_esc(x)}</li>" for x in rec.get("red_flags", []))
    wwc = "".join(f"<li class='band'>{_esc(x)}</li>" for x in rec.get("what_would_change", []))
    pos = "".join(f"<li class='pos'>{_esc(x)}</li>" for x in rec.get("positives", []))
    neg = "".join(f"<li class='neg'>{_esc(x)}</li>" for x in rec.get("negatives", []))
    return (
        "<div class='card'><h3>📋 Model signal &amp; detailed analysis</h3>"
        f"<p><span class='score {cls}'>{_esc(rec.get('verdict_label'))}</span> &nbsp;·&nbsp; "
        f"conviction <b>{rec.get('conviction')}</b>/100 &nbsp;·&nbsp; confidence {_esc(rec.get('confidence'))}</p>"
        f"<p class='band'>{_esc(rec.get('summary'))}</p>"
        "<table><thead><tr><th>Pillar</th><th>Assessment</th><th>Detail</th><th>±</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p class='band'><b>Frameworks:</b> {' &nbsp;·&nbsp; '.join(bits)}</p>"
        + (f"<b class='neg'>🚩 Red flags</b><ul class='why'>{flags}</ul>" if flags else "")
        + f"<div class='grid' style='grid-template-columns:1fr 1fr'>"
        f"<div><b class='pos'>Positives</b><ul class='why'>{pos}</ul></div>"
        f"<div><b class='neg'>Negatives</b><ul class='why'>{neg}</ul></div></div>"
        f"<b>What would change the verdict</b><ul class='why'>{wwc}</ul>"
        + _horizons_block(rec.get("horizons"))
        + f"<p class='band'>⚠️ {_esc(rec.get('disclaimer'))}</p></div>"
    )


def _tv_chart(symbol, exchange="NSE") -> str:
    """Official TradingView chart widget embed (compliant: data stays on TV servers)."""
    sym = _esc(symbol)
    ex = "BSE" if str(exchange).upper() == "BSE" else "NSE"
    return (
        "<div class='card'><h3>📈 Live chart</h3>"
        f"<div class='tradingview-widget-container'><div id='tv_{sym}'></div>"
        "<script type='text/javascript' src='https://s3.tradingview.com/tv.js'></script>"
        "<script type='text/javascript'>new TradingView.widget({"
        f"\"width\":\"100%\",\"height\":400,\"symbol\":\"{ex}:{sym}\",\"interval\":\"D\","
        "\"timezone\":\"Asia/Kolkata\",\"theme\":\"dark\",\"style\":\"1\",\"locale\":\"in\","
        f"\"hide_side_toolbar\":true,\"allow_symbol_change\":false,\"container_id\":\"tv_{sym}\""
        "});</script></div>"
        "<p class='band'>Live chart via the official TradingView widget — price is real-time / "
        "exchange-delayed per TradingView. Educational only.</p></div>"
    )


def _stock_html(rec: dict, generated_at: str) -> str:
    sym = _esc(rec.get("symbol"))
    name = _esc(rec.get("name") or sym)
    score = rec.get("fundamental_score")
    sc = "—" if score is None else f"{score:.0f}"
    why = rec.get("why") or {}
    positives = why.get("positives") or rec.get("reasons") or []
    negatives = why.get("negatives") or []
    risks = why.get("risks") or []
    og_desc = (f"Educational fundamental score {sc}/100 ({_esc(rec.get('tier'))}) for "
               f"{name}. Not investment advice.")
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{name} ({sym}) — Educational Score {sc}/100</title>
<meta property='og:title' content='{name} ({sym}) — Score {sc}/100'>
<meta property='og:description' content='{og_desc}'>
<meta property='og:type' content='website'>
<meta name='twitter:card' content='summary'>
<style>{_CSS}</style></head><body><div class='wrap'>
<div class='sub'><a href='../index.html'>← All stocks</a></div>
<header><h1>{name} <span class='band'>({sym} · {_esc(rec.get('exchange') or 'NSE')} · {_esc(rec.get('sector') or '—')})</span></h1>
<div class='sub'>Price {_fmt(rec.get('price'))} · updated {_esc(generated_at)}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
<div class='card'><h2 style='margin:0'>Educational fundamental score:
<span class='score {_score_class(score)}'>{sc}</span> / 100
<span class='band'>(sector-relative percentile · band: {_esc(rec.get('tier'))})</span></h2>
<p class='band'>This score summarises publicly available fundamentals relative to sector peers.
It is descriptive, not a recommendation.</p>{_pillar_bars(rec.get('pillars'))}</div>
{_recommendation_html(rec.get('recommendation'))}
<div class='card'><h3>Why — what the data shows</h3>
<b class='pos'>Supportive signals</b><ul class='why'>{_why_list(positives, 'pos')}</ul>
<b class='neg'>Cautionary signals</b><ul class='why'>{_why_list(negatives, 'neg')}</ul>
{('<b>Risks</b><ul class=why>'+_why_list(risks,'band')+'</ul>') if risks else ''}</div>
{_tv_chart(rec.get('symbol'), rec.get('exchange') or 'NSE')}
<div class='card'><h3>Key fundamentals</h3>{_metric_cards(rec.get('metrics'))}</div>
<div class='foot'>{DISCLAIMER_FULL}<br>Data source: {_esc(rec.get('source') or 'see methodology')}.</div>
</div></body></html>"""


def build_site(
    records: list[dict],
    out_dir: str | Path = "site",
    generated_at: str | None = None,
) -> dict:
    """Generate the full static site. Returns counts + output paths.

    Each record: {symbol, name, sector, exchange, price, fundamental_score, tier,
    reasons|why{positives,negatives,risks}, pillars{name:z}, metrics{canonical:value}}.
    """
    out = Path(out_dir)
    (out / "stock").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    gen = generated_at or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    records = sorted(records, key=lambda r: (r.get("fundamental_score") or -1), reverse=True)
    (out / "index.html").write_text(_index_html(records, gen), encoding="utf-8")
    for rec in records:
        sym = str(rec.get("symbol", "UNKNOWN"))
        (out / "stock" / f"{sym}.html").write_text(_stock_html(rec, gen), encoding="utf-8")

    index_json = [{"symbol": r.get("symbol"), "name": r.get("name"), "sector": r.get("sector"),
                   "price": r.get("price"), "score": r.get("fundamental_score"), "tier": r.get("tier"),
                   "signal": (r.get("recommendation") or {}).get("verdict")}
                  for r in records]
    (out / "data" / "index.json").write_text(
        json.dumps({"generated_at": gen, "count": len(records), "stocks": index_json}, indent=2,
                   default=str), encoding="utf-8")

    logger.info("Built site with %d stock pages -> %s", len(records), out.resolve())
    return {"out_dir": str(out.resolve()), "pages": len(records) + 1,
            "index": str((out / "index.html").resolve())}


def _demo_records() -> list[dict]:
    from src.strategies.recommendation import recommend
    recs = [
        {"symbol": "RELIANCE", "name": "Reliance Industries Ltd", "sector": "Energy",
         "exchange": "NSE", "price": 1310.0, "fundamental_score": 78.0, "tier": "STRONG",
         "reasons": ["strong ROCE vs sector", "low leverage vs sector"],
         "why": {"positives": ["ROCE above sector median", "FCF yield positive"],
                 "negatives": ["valuation (P/E) richer than peers"], "risks": ["capex-heavy cycle"]},
         "pillars": {"valuation": -0.3, "profitability": 1.1, "growth": 0.4, "leverage": 0.8},
         "metrics": {"pe": 41.4, "pb": 3.1, "roe": 7.7, "roce": 7.8, "debt_to_equity": 0.4,
                     "promoter_holding": 50.3, "market_cap_cr": 1772085, "profit_growth_5y": 9.0,
                     "dividend_yield": 0.5, "sma50": 1290.0, "sma200": 1240.0,
                     "high_52w": 1600.0, "low_52w": 1100.0},
         "source": "demo"},
        {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT",
         "exchange": "NSE", "price": 3450.0, "fundamental_score": 91.0, "tier": "TOP_DECILE",
         "reasons": ["strong ROE vs sector", "zero pledge"],
         "why": {"positives": ["ROE 45%+", "high cash conversion", "zero debt"],
                 "negatives": [], "risks": ["IT demand cyclicality"]},
         "pillars": {"valuation": 0.1, "profitability": 1.9, "growth": 0.6, "leverage": 1.5},
         "metrics": {"pe": 27.0, "pb": 12.0, "roe": 47.0, "roce": 58.0, "debt_to_equity": 0.05,
                     "promoter_holding": 72.0, "pledged_pct": 0.0, "market_cap_cr": 1250000,
                     "profit_growth_5y": 12.0, "dividend_yield": 1.6, "sma50": 3400.0,
                     "sma200": 3550.0, "high_52w": 4200.0, "low_52w": 3000.0},
         "source": "demo"},
    ]
    for r in recs:
        m = {**r["metrics"], "sector": r["sector"], "name": r["name"],
             "symbol": r["symbol"], "price": r["price"]}
        r["recommendation"] = recommend(m, sector_score=r["fundamental_score"])
    return recs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = build_site(_demo_records(), out_dir="site")
    print(f"Open: {res['index']}  ({res['pages']} pages)")
