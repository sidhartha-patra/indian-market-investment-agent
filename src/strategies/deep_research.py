"""Per-stock deep research: assemble every signal, then reason over them.

``deep_dive(symbol, metrics, ...)`` is the single build-time unit behind both the
Recommendations tab and the Search tab. It:
  1. cross-validates the supplied multi-source metrics (grill the data),
  2. runs the deterministic sector-relative fundamental model,
  3. pulls **analyst/broker consensus** (Yahoo target + Moneycontrol buy/sell/hold %),
  4. computes a **news-sentiment** signal from recent headlines,
  5. runs the **conformal ML forecast**,
  6. asks the **Gen-AI analyst** to synthesize a grounded, two-sided view,
  7. blends it all into a transparent composite Buy/Hold/Sell.

Every external call is best-effort and isolated — a failure degrades that one signal,
never the whole analysis.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BULL = ("surge", "rally", "gain", "beat", "high", "record", "growth", "jump", "soar",
         "upgrade", "profit", "wins", "order", "expansion", "strong", "outperform", "buyback")
_BEAR = ("fall", "drop", "loss", "miss", "low", "decline", "crash", "downgrade", "slump",
         "fraud", "probe", "default", "cut", "weak", "lawsuit", "ban", "resign", "plunge")


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def analyst_consensus(symbol: str, yf_info: dict | None = None,
                      use_moneycontrol: bool = False) -> dict | None:
    """Broker/analyst consensus from Yahoo (target, mean rating) + optional Moneycontrol %."""
    out: dict = {}
    info = yf_info
    if info is None:
        try:
            import yfinance as yf
            tk = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
            info = yf.Ticker(tk).info or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("yf analyst info failed for %s: %s", symbol, exc)
            info = {}
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    target = _num(info.get("targetMeanPrice"))
    if target and price:
        out["target_upside_pct"] = round((target / price - 1) * 100, 1)
        out["analyst_target"] = target
    if _num(info.get("recommendationMean")) is not None:
        out["rec_mean"] = round(_num(info.get("recommendationMean")), 2)
    if info.get("recommendationKey"):
        out["rec_key"] = str(info.get("recommendationKey"))
    if _num(info.get("numberOfAnalystOpinions")) is not None:
        out["analyst_n"] = int(_num(info.get("numberOfAnalystOpinions")))

    if use_moneycontrol:
        try:
            from src.ingestion.moneycontrol import get_mc_batch
            mc = (get_mc_batch([symbol], max_workers=1) or {}).get(symbol) or {}
            for k_src, k_dst in (("buy_pct", "buy_pct"), ("sell_pct", "sell_pct"),
                                 ("hold_pct", "hold_pct"), ("tech_rating", "tech_rating"),
                                 ("community_sentiment_pct", "community_sentiment_pct")):
                if mc.get(k_src) is not None:
                    out[k_dst] = mc[k_src]
        except Exception as exc:  # noqa: BLE001
            logger.debug("moneycontrol consensus failed for %s: %s", symbol, exc)

    out["source"] = "yahoo+moneycontrol" if use_moneycontrol else "yahoo"
    return out or None


def news_signal(symbol: str, limit: int = 8, use_llm: bool = False) -> dict | None:
    """Net news sentiment from recent headlines (lexicon by default; LLM for finalists)."""
    titles: list[str] = []
    try:
        import yfinance as yf
        tk = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
        for item in (yf.Ticker(tk).news or [])[:limit]:
            t = (item.get("content", {}) or {}).get("title") or item.get("title")
            if t:
                titles.append(str(t))
    except Exception as exc:  # noqa: BLE001
        logger.debug("news fetch failed for %s: %s", symbol, exc)
    if not titles:
        return None

    if use_llm:
        try:
            from src.ai import llm
            if llm.is_available():
                sysmsg = ("You classify Indian market news headlines by sentiment for the named "
                          "company. Output ONLY JSON {\"results\":[{\"sentiment\":\"bullish|bearish|"
                          "neutral\"}, ...]} with one item per headline, in the same order.")
                user = (f"Company: {symbol}\nHeadlines:\n"
                        + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles)))
                data = llm.chat_json(sysmsg, user, max_tokens=500, temperature=0.0)
                results = data.get("results") if isinstance(data, dict) else data
                if isinstance(results, list) and results:
                    sc = sum(1 if r.get("sentiment") == "bullish" else
                             -1 if r.get("sentiment") == "bearish" else 0 for r in results)
                    return {"net_sentiment": round(sc / max(1, len(results)), 2),
                            "n": len(results), "sample": titles[:3], "source": "llm"}
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM news classify failed for %s: %s", symbol, exc)

    score = 0
    for t in titles:
        low = t.lower()
        score += sum(1 for k in _BULL if k in low) - sum(1 for k in _BEAR if k in low)
    net = round(max(-1.0, min(1.0, score / max(3, len(titles)))), 2)
    return {"net_sentiment": net, "n": len(titles), "sample": titles[:3], "source": "lexicon"}


def ml_signal(symbol: str, horizon: int = 21) -> dict | None:
    """Conformal ML forecast for one stock (best-effort; needs price history)."""
    try:
        from src.strategies.recommendation import ml_forecast
        out = ml_forecast(symbol, horizons=(horizon,))
        for f in out:
            if isinstance(f, dict) and not f.get("error"):
                return f
    except Exception as exc:  # noqa: BLE001
        logger.debug("ML forecast failed for %s: %s", symbol, exc)
    return None


def deep_dive(symbol: str, metrics: dict, *, sector_score: float | None = None,
              sources: dict | None = None, yf_info: dict | None = None,
              use_ai: bool = True, use_ml: bool = True, use_news: bool = True,
              use_analyst: bool = True, use_moneycontrol: bool = False,
              llm_news: bool = False) -> dict:
    """Full single-stock research bundle (build-time). Returns a render-ready dict."""
    from src.strategies import composite as comp
    from src.strategies.recommendation import recommend
    from src.ai import analyst as ai_analyst

    data_quality = None
    if sources:
        from src.ingestion.data_reconcile import reconcile
        r = reconcile(symbol, sources)
        metrics = {**metrics, **r["metrics"]}
        data_quality = r["data_quality"]
    metrics.setdefault("symbol", symbol)

    rec = recommend(metrics, sector_score=sector_score)

    ac = analyst_consensus(symbol, yf_info=yf_info, use_moneycontrol=use_moneycontrol) if use_analyst else None
    news = news_signal(symbol, use_llm=llm_news) if use_news else None
    ml = ml_signal(symbol) if use_ml else None

    evidence = {
        "symbol": symbol, "metrics": metrics, "frameworks": rec.get("frameworks", {}),
        "data_quality": data_quality, "model_verdict": {"verdict": rec["verdict"],
                                                        "conviction": rec["conviction"]},
        "recommendation": rec, "analyst_consensus": ac, "news": news, "ml_forecast": ml,
    }
    ai = ai_analyst.analyze(symbol, evidence) if use_ai else None
    composite_rec = comp.composite(evidence, ai=ai)

    return {
        "symbol": symbol, "name": metrics.get("name"), "sector": metrics.get("sector"),
        "price": metrics.get("price"), "metrics": metrics, "recommendation": rec,
        "ai": ai, "composite": composite_rec, "analyst_consensus": ac, "news": news,
        "ml_forecast": ml, "data_quality": data_quality,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    res = deep_dive("RELIANCE", {"name": "Reliance", "sector": "Energy", "price": 1310,
                                 "pe": 22.8, "roe": 8.9, "roce": 10.3, "debt_to_equity": 0.44,
                                 "profit_growth_5y": 12.0, "high_52w": 1600, "low_52w": 1100},
                    use_ml=False, use_news=False)
    print(json.dumps(res["composite"], indent=2, default=str))
