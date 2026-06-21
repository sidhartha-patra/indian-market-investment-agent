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
import re
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
.movers-banner{display:block;background:#0d2818;border:1px solid var(--good);color:#7ee2a8;padding:11px 14px;border-radius:8px;margin:12px 0;font-weight:600}
.movers-banner:hover{text-decoration:none;background:#10301d}
.chip{display:inline-block;font-size:11px;color:var(--mut);background:#11161c;border:1px solid #21262d;border-radius:10px;padding:1px 7px;margin:0 4px 0 0}
td .sub2{font-size:11px;color:var(--mut)}
.twocol{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0}
@media(max-width:640px){.twocol{grid-template-columns:1fr}}
.opt{border-radius:10px;padding:10px 12px}
.opt-buy{background:#0d2818;border:1px solid #1c5235}.opt-sell{background:#2a1416;border:1px solid #5a2630}
.opt-h{font-weight:700;margin-bottom:4px}
.comp{display:inline-block;font-size:12px;background:#11161c;border:1px solid #21262d;border-radius:8px;padding:2px 8px;margin:2px 4px 2px 0}
.aicard{border-left:3px solid var(--accent)}
.tabs{display:flex;gap:6px;margin:10px 0;flex-wrap:wrap}
.tab{cursor:pointer;padding:8px 16px;border-radius:8px;border:1px solid #30363d;background:var(--card);color:var(--fg);font-weight:600;font-size:14px}
.tab.active{background:var(--accent);border-color:var(--accent);color:#fff}
.tab.buy.active{background:var(--good);border-color:var(--good)}.tab.sell.active{background:var(--bad);border-color:var(--bad)}
.aibadge{font-size:11px;padding:1px 7px;border-radius:8px;background:#13294a;color:#7fb0ff;border:1px solid #1e3a63}
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


def _stock_table(records: list[dict]) -> str:
    """The shared sortable/searchable stock table (used by index + named-list pages)."""
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
    return ("<table id='tbl'><thead><tr>"
            "<th onclick='sortTable(0,false)'>Symbol</th><th onclick='sortTable(1,false)'>Name</th>"
            "<th onclick='sortTable(2,false)'>Sector</th><th onclick='sortTable(3,true)'>Price</th>"
            "<th onclick='sortTable(4,true)'>Score /100</th><th onclick='sortTable(5,false)'>Signal</th>"
            "<th onclick='sortTable(6,false)'>Band</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(title).lower()) or "list"


def _index_html(records: list[dict], generated_at: str, has_movers: bool = False,
                extra_links: list[tuple[str, str]] | None = None,
                has_reco: bool = False, has_search: bool = False) -> str:
    banners = []
    if has_reco:
        banners.append(
            "<a class='movers-banner' href='recommendations.html'>🎯 Top Buy / Sell / Hold — "
            "AI + fundamentals + analyst consensus + ML, with an explicit Buy case &amp; Sell case →</a>")
    if has_search:
        banners.append(
            "<a class='movers-banner' href='search.html'>🔎 Search any stock — deep AI fundamental "
            "analysis: Buy or Sell? →</a>")
    if has_movers:
        banners.append(
            "<a class='movers-banner' href='movers.html'>📈 Today&#39;s Top Gainers · Losers · "
            "Most-Active — live technicals + fundamentals, model signals &amp; Low/Base/High "
            "projections →</a>")
    for title, href in (extra_links or []):
        banners.append(
            f"<a class='movers-banner' href='{_esc(href)}'>📋 {_esc(title)} — fundamental scores, "
            f"model signals &amp; multi-horizon projections →</a>")
    nav = "\n".join(banners)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Indian Stock Explorer — Educational Fundamental Scores</title>
<meta property='og:title' content='Indian Stock Explorer — Educational Fundamental Scores'>
<meta property='og:description' content='Sector-relative fundamental scores for Indian stocks. Educational only, not investment advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<header><h1>📊 Indian Stock Explorer</h1>
<div class='sub'>Top picks screened from the WHOLE Indian market · sector-relative educational scores · {len(records)} stocks · updated {_esc(generated_at)}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
{nav}
<input class='search' id='q' onkeyup='filt()' placeholder='Search by symbol, name or sector…'>
{_stock_table(records)}
<div class='foot'>{DISCLAIMER_FULL}</div></div><script>{_SORT_JS}</script></body></html>"""


def _list_page_html(title: str, records: list[dict], generated_at: str, subtitle: str = "") -> str:
    """A standalone named-list page (e.g. 'Nifty 50') reusing the index table."""
    sub = subtitle or f"{len(records)} stocks · sector-relative educational scores"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_esc(title)} — Educational Fundamental Scores</title>
<meta property='og:title' content='{_esc(title)} — Educational Fundamental Scores &amp; Model Signals'>
<meta property='og:description' content='{_esc(title)} constituents with sector-relative fundamental scores, educational model signals and multi-horizon projections. Not investment advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<div class='sub'><a href='index.html'>← All stocks (Top 50 from the whole market)</a></div>
<header><h1>📋 {_esc(title)}</h1>
<div class='sub'>{_esc(sub)} · updated {_esc(generated_at)}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
<input class='search' id='q' onkeyup='filt()' placeholder='Search by symbol, name or sector…'>
{_stock_table(records)}
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
    """Render the short/mid/long-term outlook + Low/Base/High return scenarios."""
    if not h:
        return ""

    def _pct(v):
        return f"{v:+g}%" if isinstance(v, (int, float)) else "—"

    def row(key: str, label: str) -> str:
        hz = h.get(key, {}) or {}
        p = hz.get("projection") or {}
        if p:
            proj = (f"<span class='neg'>{_pct(p.get('low_pct'))}</span> / {_pct(p.get('base_pct'))} / "
                    f"<span class='pos'>{_pct(p.get('high_pct'))}</span>")
        else:
            proj = "—"
        if hz.get("analyst_consensus_upside_pct") is not None:
            proj += f"<br><span class='band'>analysts {hz['analyst_consensus_upside_pct']:+g}%</span>"
        return (f"<tr><td><b>{label}</b> <span class='band'>{_esc(hz.get('horizon'))}</span></td>"
                f"<td>{_esc(hz.get('stance'))}</td>"
                f"<td class='band'>{_esc('; '.join(hz.get('drivers', [])))}</td>"
                f"<td>{proj}</td></tr>")

    return ("<h4 style='margin:14px 0 4px'>⏱️ Short / Mid / Long-term outlook</h4>"
            "<table><thead><tr><th>Horizon</th><th>Stance</th><th>Drivers</th>"
            "<th>Return scenario — Low / Base / High*</th></tr></thead><tbody>"
            f"{row('short_term', 'Short')}{row('mid_term', 'Mid')}{row('long_term', 'Long')}"
            f"</tbody></table><p class='band'>*Low = bearish / High = bullish <b>scenarios, not "
            f"predictions</b>. {_esc(h.get('note'))}</p>")


_VERDICT_CLASS = {"STRONG_BUY": "s-hi", "BUY": "s-hi", "HOLD": "s-mid", "SELL": "s-lo", "AVOID": "s-lo"}


def _pctf(v: Any) -> str:
    return f"{v:+g}%" if isinstance(v, (int, float)) else "—"


def _proj_cell(hz: dict | None) -> str:
    """One horizon's Low/Base/High return scenario as a compact table cell."""
    if not hz:
        return "<td class='band'>—</td>"
    p = hz.get("projection") or {}
    base, low, high = p.get("base_pct"), p.get("low_pct"), p.get("high_pct")
    stance = _esc(hz.get("stance") or "")
    if not isinstance(base, (int, float)):
        return f"<td class='band'>{stance}</td>"
    cls = "pos" if base > 0 else "neg" if base < 0 else "band"
    rng = (f"{_pctf(low)} … {_pctf(high)}"
           if isinstance(low, (int, float)) and isinstance(high, (int, float)) else "")
    return (f"<td data-v='{base}'><span class='{cls}'><b>{_pctf(base)}</b></span>"
            f"<div class='sub2'>{rng}</div><div class='sub2'>{stance}</div></td>")


def _mover_row(rec: dict) -> str:
    """A market-mover table row: technicals + fundamentals + signal + S/M/L projection."""
    sym = _esc(rec.get("symbol"))
    name = _esc((rec.get("name") or "")[:24])
    m = rec.get("metrics") or {}
    rc = rec.get("recommendation") or {}
    h = rc.get("horizons") or {}
    chg = rec.get("change_today_pct")
    chg_cls = ("pos" if isinstance(chg, (int, float)) and chg > 0
               else "neg" if isinstance(chg, (int, float)) and chg < 0 else "band")
    vcls = _VERDICT_CLASS.get(rc.get("verdict", ""), "s-mid")
    conv = rc.get("conviction")
    roce = m.get("roce") if m.get("roce") is not None else m.get("roa")
    return (
        "<tr>"
        f"<td><a href='stock/{sym}.html'><b>{sym}</b></a><div class='sub2'>{name}</div></td>"
        f"<td data-v='{_esc(rec.get('price'))}'>{_fmt(rec.get('price'))}</td>"
        f"<td data-v='{_esc(chg)}'><span class='{chg_cls}'>{_pctf(chg)}</span></td>"
        f"<td data-v='{_esc(m.get('pe'))}'>{_fmt(m.get('pe'), 1)}</td>"
        f"<td data-v='{_esc(m.get('roe'))}'>{_fmt(m.get('roe'), 1)}</td>"
        f"<td data-v='{_esc(roce)}'>{_fmt(roce, 1)}</td>"
        f"<td data-v='{_esc(m.get('dividend_yield'))}'>{_fmt(m.get('dividend_yield'), 2)}</td>"
        f"<td data-v='{conv if isinstance(conv, (int, float)) else 0}'>"
        f"<span class='score {vcls}'>{_esc(rc.get('verdict'))}</span>"
        f"<div class='sub2'>conv {_esc(conv)}/100</div></td>"
        f"{_proj_cell(h.get('short_term'))}{_proj_cell(h.get('mid_term'))}{_proj_cell(h.get('long_term'))}"
        "</tr>"
    )


def _movers_section(name: str, recs: list[dict]) -> str:
    from scripts.movers_analysis import SECTION_TITLES
    title, icon = SECTION_TITLES.get(name, (name.replace("_", " ").title(), "•"))
    src = _esc((recs[0].get("fundamentals_source") if recs else "") or "")
    rows = "".join(_mover_row(r) for r in recs)
    return (
        f"<div class='card'><h2 style='margin:.1em 0'>{icon} {title} "
        f"<span class='band' style='font-size:13px'>· {len(recs)} stocks · "
        f"TradingView technicals + {src}</span></h2>"
        "<div style='overflow-x:auto'><table><thead><tr>"
        "<th onclick='sortTable(0,false)'>Symbol</th><th onclick='sortTable(1,true)'>Price</th>"
        "<th onclick='sortTable(2,true)'>Chg%</th><th onclick='sortTable(3,true)'>P/E</th>"
        "<th onclick='sortTable(4,true)'>ROE%</th><th onclick='sortTable(5,true)'>ROCE%</th>"
        "<th onclick='sortTable(6,true)'>Div%</th><th onclick='sortTable(7,true)'>Model signal</th>"
        "<th>Short (~1m)</th><th>Mid (~6m)</th><th>Long (3y)</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        "<p class='band'>Tap a symbol for the full model signal, frameworks &amp; detailed "
        "Low/Base/High scenarios. Return cells are <b>scenarios, not predictions</b>.</p></div>"
    )


def _movers_page_html(movers: dict, generated_at: str) -> str:
    """Render the standalone Top Gainers / Losers / Most-Active movers page."""
    secs = movers.get("sections") or {}
    order = ["gainers", "losers", "most_active", "most_volatile", "high_dividend",
             "top_performers_1y", "oversold", "overbought"]
    ordered = [n for n in order if secs.get(n)] + [n for n in secs if n not in order and secs.get(n)]
    body = "".join(_movers_section(n, secs[n]) for n in ordered)
    if not body:
        body = "<div class='card'><p class='band'>No market-mover data available right now.</p></div>"
    gen = _esc(movers.get("generated_at") or generated_at)
    n_secs = len(ordered)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Today&#39;s Market Movers — Gainers, Losers &amp; Most-Active (India)</title>
<meta property='og:title' content='Today&#39;s Indian Market Movers — with model signals &amp; projections'>
<meta property='og:description' content='Top gainers, losers and most-active Indian stocks with fundamentals, educational model signals and Low/Base/High return scenarios. Not investment advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<div class='sub'><a href='index.html'>← All stocks</a></div>
<header><h1>📈 Today&#39;s Market Movers</h1>
<div class='sub'>{n_secs} live sections · TradingView technicals enriched with fundamentals · updated {gen}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
{body}
<div class='foot'>{DISCLAIMER_FULL}<br>Technicals: TradingView (display-only). Fundamentals: Yahoo Finance / Screener.in. Educational use only.</div>
</div><script>{_SORT_JS}</script></body></html>"""



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


def _verdict_pill(verdict: Any) -> str:
    cls = _VERDICT_CLASS.get(str(verdict).upper(), "s-mid")
    return f"<span class='score {cls}'>{_esc(str(verdict).replace('_', ' '))}</span>"


def _components_badges(components: dict | None) -> str:
    """Per-signal lean badges (fundamental / AI / analyst / ML / news), colour-coded."""
    if not components:
        return ""
    order = [("fundamental", "Fundamentals"), ("ai", "AI"), ("analyst", "Analysts"),
             ("ml", "ML"), ("news", "News")]
    out = []
    for k, lbl in order:
        if k in components and isinstance(components[k], (int, float)):
            v = components[k]
            cls = "pos" if v > 0.1 else "neg" if v < -0.1 else "band"
            out.append(f"<span class='comp'>{lbl} <b class='{cls}'>{v:+.2f}</b></span>")
    return "".join(out)


def _two_options_html(buy_case: list | None, sell_case: list | None) -> str:
    """The two explicit options the user asked for: a Buy case and a Sell case."""
    bc = "".join(f"<li class='pos'>{_esc(x)}</li>" for x in (buy_case or [])) or "<li class='band'>—</li>"
    sc = "".join(f"<li class='neg'>{_esc(x)}</li>" for x in (sell_case or [])) or "<li class='band'>—</li>"
    return ("<div class='twocol'>"
            f"<div class='opt opt-buy'><div class='opt-h pos'>🟢 The Buy case</div>"
            f"<ul class='why'>{bc}</ul></div>"
            f"<div class='opt opt-sell'><div class='opt-h neg'>🔴 The Sell case</div>"
            f"<ul class='why'>{sc}</ul></div></div>")


def _analyst_line(ac: dict | None) -> str:
    if not ac:
        return ""
    bits = []
    if ac.get("buy_pct") is not None:
        bits.append(f"{_fmt(ac.get('buy_pct'), 0)}% buy / {_fmt(ac.get('sell_pct'), 0)}% sell (brokers)")
    if ac.get("rec_key"):
        bits.append(_esc(str(ac["rec_key"]).replace("_", " ")))
    if ac.get("target_upside_pct") is not None:
        cls = "pos" if ac["target_upside_pct"] >= 0 else "neg"
        bits.append(f"target <span class='{cls}'>{ac['target_upside_pct']:+g}%</span>")
    if ac.get("analyst_n"):
        bits.append(f"{_esc(ac['analyst_n'])} analysts")
    if ac.get("tech_rating"):
        bits.append(f"tech: {_esc(ac['tech_rating'])}")
    return " · ".join(bits)


def _ml_line(ml: dict | None) -> str:
    if not ml or ml.get("error"):
        return ""
    cls = "pos" if ml.get("direction") == "UP" else "neg" if ml.get("direction") == "DOWN" else "band"
    return (f"<span class='{cls}'>{_esc(ml.get('direction'))} {_fmt(ml.get('predicted_return_pct'), 1)}%</span> "
            f"over ~{_esc(ml.get('horizon_days'))}d · P(up) {_esc(ml.get('prob_up'))} · "
            f"range {_fmt(ml.get('return_lower_pct'), 1)}%…{_fmt(ml.get('return_upper_pct'), 1)}% "
            f"<span class='band'>({_esc(ml.get('model'))}, conformal)</span>")


def _ai_analysis_html(deep: dict | None) -> str:
    """The headline Gen-AI deep-research card: composite verdict + grounded two-sided cases."""
    if not deep:
        return ""
    comp = deep.get("composite") or {}
    ai = deep.get("ai") or {}
    ac = deep.get("analyst_consensus") or {}
    ml = deep.get("ml_forecast") or {}
    news = deep.get("news") or {}
    if not comp and not ai:
        return ""

    provider = comp.get("ai_provider") or ai.get("provider") or "model"
    is_ai = ai.get("source") == "ai"
    badge = (f"<span class='aibadge'>🤖 {_esc(provider)}</span>" if is_ai
             else "<span class='aibadge'>rules-based (no LLM)</span>")
    verdict = comp.get("verdict") or ai.get("verdict") or "HOLD"
    score = comp.get("score")
    thesis = ai.get("thesis") or comp.get("thesis") or ""
    buy_case = comp.get("buy_case") or ai.get("buy_case") or []
    sell_case = comp.get("sell_case") or ai.get("sell_case") or []
    critique = comp.get("data_critique") or ai.get("data_critique") or []
    hv = ai.get("horizon_view") or comp.get("horizon_view") or {}
    dq = deep.get("data_quality") or {}

    extras = []
    a_line = _analyst_line(ac)
    if a_line:
        extras.append(f"<div class='metric'><div class='k'>Analyst / broker consensus</div>"
                      f"<div class='v' style='font-size:14px'>{a_line}</div></div>")
    m_line = _ml_line(ml)
    if m_line:
        extras.append(f"<div class='metric'><div class='k'>ML forecast (conformal)</div>"
                      f"<div class='v' style='font-size:14px'>{m_line}</div></div>")
    if news and news.get("net_sentiment") is not None:
        cls = "pos" if news["net_sentiment"] >= 0 else "neg"
        extras.append(f"<div class='metric'><div class='k'>News sentiment ({_esc(news.get('n'))} headlines)</div>"
                      f"<div class='v'><span class='{cls}'>{news['net_sentiment']:+.2f}</span> "
                      f"<span class='band'>{_esc(news.get('source'))}</span></div></div>")
    if dq.get("confidence") is not None:
        extras.append(f"<div class='metric'><div class='k'>Data quality (cross-checked)</div>"
                      f"<div class='v'>{_esc(dq.get('confidence'))}/100 "
                      f"<span class='band'>{_esc(dq.get('verdict'))}, {_esc(dq.get('n_sources'))} sources</span></div></div>")

    horizons = ""
    if any(hv.values()):
        horizons = ("<p class='band'><b>Outlook:</b> "
                    f"Short — {_esc(hv.get('short'))} · Mid — {_esc(hv.get('mid'))} · "
                    f"Long — {_esc(hv.get('long'))}</p>")
    critique_html = ("<p class='band'><b>🔎 Data critique:</b> " + "; ".join(_esc(c) for c in critique) + "</p>"
                     if critique else "")
    conflicts = dq.get("conflicts") or []
    suspect = dq.get("suspect") or []
    flags_html = ""
    if conflicts or suspect:
        items = "".join(f"<li class='neg'>⚠ {_esc(x)}</li>" for x in (conflicts + suspect)[:6])
        flags_html = f"<details><summary class='band'>Source disagreements &amp; suspect values</summary><ul class='why'>{items}</ul></details>"

    return (
        "<div class='card aicard'><h3>🤖 AI deep research — Buy or Sell?</h3>"
        f"<p>{_verdict_pill(verdict)} &nbsp; "
        + (f"<b>{_esc(score)}</b>/100 composite &nbsp; " if score is not None else "")
        + f"{badge} &nbsp;·&nbsp; <span class='band'>{_esc(comp.get('rationale'))}</span></p>"
        + (f"<p>{_esc(thesis)}</p>" if thesis else "")
        + _two_options_html(buy_case, sell_case)
        + (f"<p class='band'><b>Signals:</b> {_components_badges(comp.get('components'))} "
           f"&nbsp; agreement {_esc(comp.get('agreement'))}</p>" if comp.get("components") else "")
        + (f"<div class='grid'>{''.join(extras)}</div>" if extras else "")
        + horizons + critique_html + flags_html
        + "<p class='band'>⚠️ AI-assisted educational analysis, grounded in the data shown — "
        "scenarios and reasoning, NOT investment advice or a guaranteed prediction.</p></div>"
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
{_ai_analysis_html(rec.get('deep'))}
{_recommendation_html(rec.get('recommendation'))}
<div class='card'><h3>Why — what the data shows</h3>
<b class='pos'>Supportive signals</b><ul class='why'>{_why_list(positives, 'pos')}</ul>
<b class='neg'>Cautionary signals</b><ul class='why'>{_why_list(negatives, 'neg')}</ul>
{('<b>Risks</b><ul class=why>'+_why_list(risks,'band')+'</ul>') if risks else ''}</div>
{_tv_chart(rec.get('symbol'), rec.get('exchange') or 'NSE')}
<div class='card'><h3>Key fundamentals</h3>{_metric_cards(rec.get('metrics'))}</div>
<div class='foot'>{DISCLAIMER_FULL}<br>Data source: {_esc(rec.get('source') or 'see methodology')}.</div>
</div></body></html>"""


def _reco_card(rank: int, deep: dict) -> str:
    sym = _esc(deep.get("symbol"))
    name = _esc((deep.get("name") or "")[:42])
    comp = deep.get("composite") or {}
    a = _analyst_line(deep.get("analyst_consensus"))
    m = _ml_line(deep.get("ml_forecast"))
    return (
        "<div class='card'>"
        f"<h3 style='margin:.1em 0'>#{rank} <a href='stock/{sym}.html'>{sym}</a> "
        f"<span class='band' style='font-size:13px'>{name} · {_esc(deep.get('sector'))} · "
        f"₹{_fmt(deep.get('price'))}</span></h3>"
        f"<p>{_verdict_pill(comp.get('verdict'))} &nbsp; <b>{_esc(comp.get('score'))}</b>/100 "
        f"&nbsp;·&nbsp; <span class='band'>{_esc(comp.get('rationale'))}</span></p>"
        f"{_two_options_html(comp.get('buy_case'), comp.get('sell_case'))}"
        + (f"<p class='band'>{_components_badges(comp.get('components'))} · agreement "
           f"{_esc(comp.get('agreement'))}</p>" if comp.get("components") else "")
        + (f"<p class='band'><b>Analysts:</b> {a}</p>" if a else "")
        + (f"<p class='band'><b>ML:</b> {m}</p>" if m else "")
        + f"<p class='band'><a href='stock/{sym}.html'>Full AI deep research →</a></p></div>"
    )


def _reco_pane(group: str, cards: list[dict], visible: bool) -> str:
    style = "" if visible else "display:none"
    if not cards:
        body = f"<p class='band'>No {group.lower()} ideas surfaced in this run.</p>"
    else:
        body = "".join(_reco_card(i + 1, d) for i, d in enumerate(cards))
    return f"<div class='reco-pane' data-g='{group}' style='{style}'>{body}</div>"


_RECO_JS = """
function showPane(g,el){var p=document.querySelectorAll('.reco-pane');
for(var i=0;i<p.length;i++){p[i].style.display=p[i].getAttribute('data-g')==g?'':'none';}
var t=document.querySelectorAll('.tab');for(var j=0;j<t.length;j++){t[j].classList.remove('active');}
el.classList.add('active');}
"""


def _reco_page_html(reco: dict, generated_at: str) -> str:
    buckets = reco.get("buckets") or {}
    buy, hold, sell = buckets.get("BUY", []), buckets.get("HOLD", []), buckets.get("SELL", [])
    provider = _esc(reco.get("provider") or "model")
    gen = _esc(reco.get("generated_at") or generated_at)
    ucount = _esc(reco.get("universe_count") or "")
    tabs = ("<div class='tabs'>"
            f"<div class='tab buy active' onclick=\"showPane('BUY',this)\">🟢 Buy ({len(buy)})</div>"
            f"<div class='tab' onclick=\"showPane('HOLD',this)\">🟡 Hold ({len(hold)})</div>"
            f"<div class='tab sell' onclick=\"showPane('SELL',this)\">🔴 Sell ({len(sell)})</div></div>")
    panes = (_reco_pane("BUY", buy, True) + _reco_pane("HOLD", hold, False)
             + _reco_pane("SELL", sell, False))
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Top Buy / Sell / Hold — AI + Fundamental Recommendations (India)</title>
<meta property='og:title' content='Top Buy / Sell / Hold Indian stocks — AI + fundamentals + analyst consensus'>
<meta property='og:description' content='Top 10 Buy, Sell and Hold Indian stocks ranked by a composite of fundamentals, Gen-AI analysis, analyst/broker consensus, ML forecast and news. Educational, not advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<div class='sub'><a href='index.html'>← All stocks</a> &nbsp;·&nbsp; <a href='search.html'>🔎 Search a stock</a></div>
<header><h1>🎯 Top Buy / Sell / Hold</h1>
<div class='sub'>Composite of fundamentals + Gen-AI analyst + analyst/broker consensus + ML forecast + news ·
screened from {ucount} stocks · AI: {provider} · updated {gen}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
<p class='band'>Each idea blends five independent signals and shows an explicit <b class='pos'>Buy case</b>
and <b class='neg'>Sell case</b>. Ranked by composite conviction. Educational model output — not advice.</p>
{tabs}{panes}
<div class='foot'>{DISCLAIMER_FULL}</div></div><script>{_RECO_JS}</script></body></html>"""


_SEARCH_JS = """
var D=window.__SEARCH__||[];
var CLS={STRONG_BUY:'s-hi',BUY:'s-hi',HOLD:'s-mid',SELL:'s-lo',AVOID:'s-lo'};
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function li(a,cls){return (a&&a.length)?a.map(function(x){return "<li class='"+cls+"'>"+esc(x)+"</li>";}).join(''):"<li class='band'>—</li>";}
function card(e){var c=CLS[e.v]||'s-mid';
return "<div class='card'><h3 style='margin:.1em 0'><a href='stock/"+esc(e.s)+".html'>"+esc(e.s)+"</a> "+
"<span class='band' style='font-size:13px'>"+esc(e.n)+" · "+esc(e.sec)+" · ₹"+esc(e.p)+"</span></h3>"+
"<p><span class='score "+c+"'>"+esc((e.v||'').replace('_',' '))+"</span> "+(e.sc!=null?"<b>"+esc(e.sc)+"</b>/100":'')+
(e.ai?" <span class='aibadge'>🤖 AI</span>":"")+"</p>"+
"<div class='twocol'><div class='opt opt-buy'><div class='opt-h pos'>🟢 Buy case</div><ul class='why'>"+li(e.b,'pos')+"</ul></div>"+
"<div class='opt opt-sell'><div class='opt-h neg'>🔴 Sell case</div><ul class='why'>"+li(e.se,'neg')+"</ul></div></div>"+
"<p class='band'><a href='stock/"+esc(e.s)+".html'>Full deep research →</a></p></div>";}
function run(){var q=document.getElementById('q').value.trim().toLowerCase();var box=document.getElementById('res');
if(!q){box.innerHTML="<p class='band'>Type a stock symbol or company name to see its deep analysis and the Buy vs Sell case.</p>";return;}
var m=D.filter(function(e){return ((e.s||'')+' '+(e.n||'')).toLowerCase().indexOf(q)>-1;});
m.sort(function(a,b){return (a.s.toLowerCase()==q?-1:b.s.toLowerCase()==q?1:0);});
m=m.slice(0,12);
if(!m.length){box.innerHTML="<div class='card'><p>No analysed match for <b>"+esc(q)+"</b>. This site searches our pre-analysed universe of "+D.length+" stocks. Try the NSE symbol (e.g. RELIANCE, TCS), or view it on <a target='_blank' rel='noopener' href='https://www.screener.in/company/"+esc(q.toUpperCase())+"/'>Screener.in</a> / <a target='_blank' rel='noopener' href='https://www.tradingview.com/symbols/NSE-"+esc(q.toUpperCase())+"/'>TradingView</a>.</p></div>";return;}
box.innerHTML=m.map(card).join('');}
"""


def _search_entry(deep_or_rec: dict) -> dict:
    """Compact, embeddable search record with the two-sided case."""
    comp = deep_or_rec.get("composite") or {}
    ai = deep_or_rec.get("ai") or {}
    rec = deep_or_rec.get("recommendation") or {}
    buy = comp.get("buy_case") or ai.get("buy_case") or rec.get("positives") or []
    sell = comp.get("sell_case") or ai.get("sell_case") or rec.get("negatives") or []
    return {
        "s": deep_or_rec.get("symbol"),
        "n": (deep_or_rec.get("name") or "")[:48],
        "sec": deep_or_rec.get("sector") or "",
        "p": deep_or_rec.get("price"),
        "v": comp.get("verdict") or rec.get("verdict") or "HOLD",
        "sc": comp.get("score"),
        "b": [str(x)[:200] for x in buy[:3]],
        "se": [str(x)[:200] for x in sell[:3]],
        "ai": bool(ai.get("source") == "ai"),
    }


def _search_page_html(search_index: list[dict], generated_at: str, provider: str = "") -> str:
    data = json.dumps(search_index, default=str, ensure_ascii=False)
    n = len(search_index)
    prov = _esc(provider or "model")
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Search a stock — AI fundamental analysis, Buy or Sell? (India)</title>
<meta property='og:title' content='Search any Indian stock — AI fundamental analysis: Buy or Sell?'>
<meta property='og:description' content='Search a stock and get its deep fundamental analysis with an explicit Buy case and Sell case. Educational, not advice.'>
<style>{_CSS}</style></head><body><div class='wrap'>
<div class='sub'><a href='index.html'>← All stocks</a> &nbsp;·&nbsp; <a href='recommendations.html'>🎯 Top Buy/Sell/Hold</a></div>
<header><h1>🔎 Search a stock — Buy or Sell?</h1>
<div class='sub'>Deep fundamental analysis across {n} pre-analysed stocks · AI: {prov} · updated {_esc(generated_at)}</div></header>
<div class='disclaimer'>⚠️ {DISCLAIMER_SHORT}</div>
<input class='search' id='q' oninput='run()' autofocus placeholder='Type a symbol or company name — e.g. RELIANCE, Infosys, HDFC Bank…'>
<div id='res'><p class='band'>Type a stock symbol or company name to see its deep analysis and the Buy vs Sell case.</p></div>
<div class='foot'>{DISCLAIMER_FULL}</div></div>
<script>window.__SEARCH__={data};</script><script>{_SEARCH_JS}</script></body></html>"""


def build_site(
    records: list[dict],
    out_dir: str | Path = "site",
    generated_at: str | None = None,
    movers: dict | None = None,
    extra_lists: dict[str, list[dict]] | None = None,
    recommendations: dict | None = None,
    search_index: list[dict] | None = None,
    deep_by_symbol: dict[str, dict] | None = None,
) -> dict:
    """Generate the full static site. Returns counts + output paths.

    Each record: {symbol, name, sector, exchange, price, fundamental_score, tier,
    reasons|why{positives,negatives,risks}, pillars{name:z}, metrics{canonical:value}}.

    ``movers`` (optional): ``{"generated_at", "sections": {name: [record, ...]}}`` from
    :func:`scripts.movers_analysis.build_section_records`. When supplied, a ``movers.html``
    page (Top Gainers / Losers / Most-Active …) is rendered, a detail page is generated for
    every mover symbol, and the index links to it.

    ``extra_lists`` (optional): ``{"Nifty 50": [record, ...], ...}`` named stock lists. Each
    becomes a standalone ``<slug>.html`` page (e.g. ``nifty50.html``) with the same sortable
    table + detail pages, linked from the home page. Lets the all-market Top 50 coexist with
    curated index lists like Nifty 50.

    ``recommendations`` (optional): ``{"generated_at","provider","universe_count",
    "buckets":{"BUY":[deep,...],"HOLD":[...],"SELL":[...]}}`` -> ``recommendations.html``.
    ``search_index`` (optional): list of compact search records -> ``search.html``.
    ``deep_by_symbol`` (optional): ``{SYMBOL: deep_dive}`` attached to matching stock detail
    pages so they show the full AI deep-research card.
    """
    out = Path(out_dir)
    (out / "stock").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    gen = generated_at or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    has_movers = bool(movers and movers.get("sections"))
    has_reco = bool(recommendations and (recommendations.get("buckets") or {}))
    has_search = bool(search_index)
    lists = {t: r for t, r in (extra_lists or {}).items() if r}
    extra_links = [(t, f"{_slug(t)}.html") for t in lists]
    deep_by_symbol = {str(k).upper(): v for k, v in (deep_by_symbol or {}).items()}
    if recommendations:  # reco stocks always carry their AI card onto the detail page
        for bucket in (recommendations.get("buckets") or {}).values():
            for d in bucket:
                deep_by_symbol.setdefault(str(d.get("symbol", "")).upper(), d)

    def _with_deep(rec: dict) -> dict:
        d = deep_by_symbol.get(str(rec.get("symbol", "")).upper())
        return {**rec, "deep": d} if d and "deep" not in rec else rec

    records = sorted(records, key=lambda r: (r.get("fundamental_score") or -1), reverse=True)
    (out / "index.html").write_text(
        _index_html(records, gen, has_movers=has_movers, extra_links=extra_links,
                    has_reco=has_reco, has_search=has_search), encoding="utf-8")
    written: set[str] = set()

    def _write_stock_page(rec: dict) -> bool:
        sym = str(rec.get("symbol") or "UNKNOWN")
        try:
            (out / "stock" / f"{sym}.html").write_text(_stock_html(_with_deep(rec), gen), encoding="utf-8")
            written.add(sym)
            return True
        except Exception as exc:  # noqa: BLE001 — one bad page must not abort a 300-stock build
            logger.warning("skipped stock page %s: %s", sym, exc)
            written.add(sym)
            return False

    for rec in records:
        _write_stock_page(rec)

    def _emit_detail_pages(recs: list[dict]) -> int:
        n = 0
        for rec in recs:
            sym = str(rec.get("symbol") or "").strip()
            if sym and sym not in written and _write_stock_page(rec):
                n += 1
        return n

    extra_pages = 0
    if has_movers:
        for sec in movers["sections"].values():
            extra_pages += _emit_detail_pages(sec)
        (out / "movers.html").write_text(_movers_page_html(movers, gen), encoding="utf-8")
        (out / "data" / "movers.json").write_text(
            json.dumps(movers, indent=2, default=str), encoding="utf-8")

    for title, recs in lists.items():
        recs_sorted = sorted(recs, key=lambda r: (r.get("fundamental_score") or -1), reverse=True)
        extra_pages += _emit_detail_pages(recs_sorted)
        (out / f"{_slug(title)}.html").write_text(
            _list_page_html(title, recs_sorted, gen), encoding="utf-8")

    if has_reco:
        # ensure every recommended stock has a detail page (with its deep card)
        for bucket in recommendations["buckets"].values():
            extra_pages += _emit_detail_pages([
                {"symbol": d.get("symbol"), "name": d.get("name"), "sector": d.get("sector"),
                "exchange": d.get("exchange") or "NSE", "price": d.get("price"),
                "metrics": d.get("metrics"), "recommendation": d.get("recommendation"),
                "deep": d} for d in bucket])
        (out / "recommendations.html").write_text(_reco_page_html(recommendations, gen), encoding="utf-8")
        (out / "data" / "recommendations.json").write_text(
            json.dumps(recommendations, indent=2, default=str), encoding="utf-8")

    if has_search:
        prov = (recommendations or {}).get("provider", "")
        (out / "search.html").write_text(
            _search_page_html(search_index, gen, provider=prov), encoding="utf-8")
        (out / "data" / "search.json").write_text(
            json.dumps(search_index, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    index_json = [{"symbol": r.get("symbol"), "name": r.get("name"), "sector": r.get("sector"),
                   "price": r.get("price"), "score": r.get("fundamental_score"), "tier": r.get("tier"),
                   "signal": (r.get("recommendation") or {}).get("verdict")}
                  for r in records]
    (out / "data" / "index.json").write_text(
        json.dumps({"generated_at": gen, "count": len(records), "stocks": index_json}, indent=2,
                   default=str), encoding="utf-8")

    logger.info("Built site: %d main + %d extra (movers=%s, lists=%s, reco=%s, search=%s) -> %s",
                len(records), extra_pages, has_movers, list(lists), has_reco, has_search, out.resolve())
    return {"out_dir": str(out.resolve()),
            "pages": len(records) + 1 + extra_pages + (1 if has_movers else 0) + len(lists)
                     + (1 if has_reco else 0) + (1 if has_search else 0),
            "index": str((out / "index.html").resolve()), "has_movers": has_movers,
            "has_reco": has_reco, "has_search": has_search,
            "lists": [h for _, h in extra_links],
            "recommendations": str((out / "recommendations.html").resolve()) if has_reco else None,
            "search": str((out / "search.html").resolve()) if has_search else None,
            "movers": str((out / "movers.html").resolve()) if has_movers else None}


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


def _demo_movers() -> dict:
    """Offline sample movers payload (no network) for demos and tests."""
    from src.strategies.recommendation import recommend
    seed = {
        "gainers": [
            ("ADANIPORTS", "Adani Ports & SEZ", "Industrials", 1450.0, 6.2,
             {"pe": 28.0, "roe": 16.0, "roce": 13.5, "debt_to_equity": 0.9, "dividend_yield": 0.5,
              "profit_growth_5y": 18.0, "sma50": 1380.0, "sma200": 1320.0,
              "high_52w": 1620.0, "low_52w": 1000.0}),
            ("TATAMOTORS", "Tata Motors", "Consumer Cyclical", 720.0, 4.1,
             {"pe": 9.0, "roe": 22.0, "roce": 16.0, "debt_to_equity": 1.2, "dividend_yield": 0.6,
              "profit_growth_5y": 25.0, "sma50": 700.0, "sma200": 760.0,
              "high_52w": 1180.0, "low_52w": 600.0}),
        ],
        "losers": [
            ("YESBANK", "Yes Bank", "Financial Services", 18.5, -5.4,
             {"pe": 32.0, "roe": 3.0, "roce": 5.0, "debt_to_equity": 2.4, "dividend_yield": 0.0,
              "profit_growth_5y": -4.0, "sma50": 21.0, "sma200": 23.0,
              "high_52w": 28.0, "low_52w": 17.0}),
        ],
        "most_active": [
            ("RELIANCE", "Reliance Industries", "Energy", 1310.0, 1.2,
             {"pe": 22.8, "roe": 8.9, "roce": 10.3, "debt_to_equity": 0.44, "dividend_yield": 0.46,
              "profit_growth_5y": 12.0, "sma50": 1290.0, "sma200": 1240.0,
              "high_52w": 1600.0, "low_52w": 1100.0}),
        ],
    }
    sections: dict[str, list[dict]] = {}
    for name, items in seed.items():
        recs = []
        for sym, nm, sec, price, chg, metrics in items:
            m = {**metrics, "symbol": sym, "name": nm, "sector": sec, "price": price}
            r = recommend(m, sector_score=None)
            recs.append({
                "symbol": sym, "name": nm, "sector": sec, "exchange": "NSE", "price": price,
                "fundamental_score": None, "tier": None, "change_today_pct": chg,
                "collection": name, "collections": [name], "metrics": m, "recommendation": r,
                "why": {"positives": r.get("positives", []), "negatives": r.get("negatives", []),
                        "risks": r.get("red_flags", [])},
                "fundamentals_source": "demo", "source": "demo",
            })
        sections[name] = recs
    return {"generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            "sections": sections}


def _demo_recommendations() -> dict:
    """Offline sample Buy/Hold/Sell buckets (deterministic deep-dive, no network)."""
    from src.strategies.deep_research import deep_dive
    seed = [
        ("TCS", "Tata Consultancy Services", "IT", 3450.0,
         {"pe": 27.0, "roe": 47.0, "roce": 58.0, "debt_to_equity": 0.05, "dividend_yield": 1.6,
          "profit_growth_5y": 12.0, "high_52w": 4200, "low_52w": 3000, "sma50": 3400, "sma200": 3550}),
        ("TATAMOTORS", "Tata Motors", "Consumer Cyclical", 720.0,
         {"pe": 9.0, "roe": 22.0, "roce": 16.0, "debt_to_equity": 1.2, "profit_growth_5y": 25.0,
          "high_52w": 1180, "low_52w": 600, "sma50": 700, "sma200": 760}),
        ("RELIANCE", "Reliance Industries", "Energy", 1310.0,
         {"pe": 22.8, "roe": 8.9, "roce": 10.3, "debt_to_equity": 0.44, "profit_growth_5y": 12.0,
          "high_52w": 1600, "low_52w": 1100, "sma50": 1290, "sma200": 1240}),
        ("YESBANK", "Yes Bank", "Financial Services", 18.5,
         {"pe": 32.0, "roe": 3.0, "roce": 5.0, "debt_to_equity": 2.4, "profit_growth_5y": -4.0,
          "high_52w": 28, "low_52w": 17, "sma50": 21, "sma200": 23}),
    ]
    deeps = [deep_dive(s, {**m, "name": n, "sector": sec, "price": p}, sector_score=None,
                       use_ai=False, use_ml=False, use_news=False, use_analyst=False)
             for s, n, sec, p, m in seed]
    buckets: dict[str, list[dict]] = {"BUY": [], "HOLD": [], "SELL": []}
    for d in deeps:
        buckets[d["composite"]["group"]].append(d)
    for g in buckets:
        buckets[g].sort(key=lambda d: d["composite"]["score"], reverse=(g != "SELL"))
    return {"generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            "provider": "deterministic (demo)", "universe_count": len(seed), "buckets": buckets}


def _demo_search_index(reco: dict) -> list[dict]:
    out = []
    for bucket in (reco.get("buckets") or {}).values():
        out.extend(_search_entry(d) for d in bucket)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reco = _demo_recommendations()
    res = build_site(_demo_records(), out_dir="site", movers=_demo_movers(),
                     extra_lists={"Nifty 50": _demo_records()},
                     recommendations=reco, search_index=_demo_search_index(reco))
    print(f"Open: {res['index']}  ({res['pages']} pages)")
