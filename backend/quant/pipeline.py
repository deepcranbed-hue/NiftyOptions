"""
pipeline.py
-----------
One entry point that runs the whole v1 chain and returns a single result object
the Streamlit tabs slice into. No historical data required.

    articles (Gemini-tagged) ┐
                             ├─> regime + sector sentiment   (market_regime)
    index weights ───────────┼─> index bias + coverage        (decision_engine)
    option chain ────────────┼─> risk-neutral distribution     (rnd)
                             └─> news-vs-market comparison      (market_view)
                                     -> strategy suggestion

Feed it from your existing tab fetchers:
  * articles: your Sector-News NewsAPI results AFTER Gemini tagging, as dicts:
        {"title","body","published_at"(iso),"sentiment"(-1..1)}
  * chain:    your option-chain dashboard data:
        {"strikes":[...], "call_ltp":[...], "spot":float, "days":float, "r":float}
  * weights:  symbol->NIFTY weight (defaults to the bundled snapshot; refresh from NSE)
"""

from __future__ import annotations

from datetime import datetime, timezone

from .market_regime import (Article, Driver, assess_regime, sector_sentiment,
                           corroboration_multiplier)
from .decision_engine import index_bias, sector_weights
from .rnd import extract_rnd, rnd_stats
from .market_view import NewsView, MarketView, compare, suggest, log_run
from .sector_tagging import sector_sentiment_from_gemini
from .complacency import complacency_score, ChainComplacencyInputs
from .risk_budget import size_trade, Trade, Position, RiskConfig

# Drivers that imply a volatility-expansion regime (long-vol bias) when dominant
EXPANSION_DRIVERS = {Driver.AI_SEMI, Driver.GEOPOLITICS_OIL, Driver.RATES_FED}


def _to_articles(raw: list[dict]) -> list[Article]:
    out = []
    for a in raw:
        ts = a["published_at"]
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append(Article(title=a.get("title", ""), published_at=dt,
                            sentiment=float(a.get("sentiment", 0.0)),
                            body=a.get("body", "")))
    return out


def mean_put_oi_change_pct(chain: dict, spot: float, band_pts: int = 200) -> float:
    strikes = chain.get("strikes", [])
    put_oichg = chain.get("put_oichg", [])
    if not strikes or not put_oichg or len(strikes) != len(put_oichg):
        return 0.0
    valid = [pchg for s, pchg in zip(strikes, put_oichg) if abs(s - spot) <= band_pts]
    return sum(valid) / len(valid) if valid else 0.0


def run_pipeline(articles: list[dict], chain: dict,
                 weights: dict | None = None,
                 prev_regime: str | None = None,
                 half_life_hours: float = 12.0,
                 risk_cfg: dict | None = None,
                 book: list[dict] | None = None,
                 current_drawdown_pct: float = 0.0,
                 trade_max_loss_pts: float = 0.0,
                 trade_delta: float = 0.0,
                 trade_vega: float = 0.0,
                 override_structure: str | None = None,
                 override_is_premium_sell: bool = False,
                 do_log: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    arts = _to_articles(articles)

    # 1. regime + sector sentiment ----------------------------------------
    prev = Driver(prev_regime) if prev_regime else None
    regime = assess_regime(arts, now=now, half_life_hours=half_life_hours,
                           prev_regime=prev)
    # sector sentiment from Gemini's sectors_affected (not the thin keyword map),
    # so "banking"/"metal"/"auto" headlines classify and don't fall to OTHER.
    sect_sent = sector_sentiment_from_gemini(articles, now=now,
                                             half_life_hours=half_life_hours)

    # 2. weighted index bias + coverage -----------------------------------
    sw = sector_weights(weights) if weights else sector_weights()
    bias, coverage = index_bias(sect_sent, sw)

    # 3. news momentum (heuristic; first thing to calibrate from the log) ---
    surfaces = regime.surfaces_by_driver.get(regime.dominant, set())
    momentum = min(1.0, regime.conviction * (0.5 + 0.5 * min(len(surfaces), 4) / 4))
    
    # 4. risk-neutral distribution from the live chain --------------------
    grid, dens = extract_rnd(chain["strikes"], chain["call_ltp"],
                             chain["spot"], chain["days"] / 365.0,
                             chain.get("r", 0.0655),
                             put_prices=chain.get("put_ltp"))
    rstats = rnd_stats(grid, dens, chain["spot"])
    
    # Complacency Gauge
    put_chg = mean_put_oi_change_pct(chain, spot=chain["spot"])
    raw_atm_iv = chain.get("atm_iv", 15.0)
    atm_iv_frac = raw_atm_iv / 100.0 if raw_atm_iv > 1.0 else raw_atm_iv

    raw_iv_pct = chain.get("iv_percentile")
    iv_pct_frac = (raw_iv_pct / 100.0) if (raw_iv_pct is not None and raw_iv_pct > 1.0) else raw_iv_pct

    comp_inputs = ChainComplacencyInputs(
        atm_iv=atm_iv_frac,
        put_oi_chg_pct_atm=put_chg,
        put_call_oi_ratio=chain.get("put_call_oi_ratio", 1.0),
        skew=rstats["skew"],
        iv_percentile=iv_pct_frac,
        vix=chain.get("vix"),
        vix_chg_pct=chain.get("vix_chg_pct")
    )
    comp = complacency_score(comp_inputs)
    vol_expansion = (comp["vol_state_hint"] == "expansion")

    # 5. news-vs-market comparison + suggestion ---------------------------
    news = NewsView(index_bias=bias, momentum=momentum, coverage=coverage)
    mkt = MarketView(spot=chain["spot"], p_below_spot=rstats["p_below_spot"],
                     expected_move=rstats["sd"], skew=rstats["skew"])
    cmp = compare(news, mkt)
    rec = suggest(cmp, news)

    # 6. risk budget / sizing gate ----------------------------------------
    structure = override_structure if override_structure else (rec.get("structure") or rec.get("action", "Unknown"))
    is_premium_sell = override_is_premium_sell if override_structure else (rec.get("action") == "TRADE" and "sell" in structure.lower())
    trade = Trade(
        structure=structure,
        max_loss_pts=trade_max_loss_pts, # TODO: wire pricer
        delta_per_lot=trade_delta,       # TODO: wire pricer
        vega_per_lot=trade_vega,         # TODO: wire pricer
        is_premium_sell=is_premium_sell
    )
    cfg = RiskConfig(**risk_cfg) if risk_cfg else RiskConfig()
    pos_book = [Position(**p) for p in book] if book else []
    
    sizing = size_trade(
        trade=trade, book=pos_book, cfg=cfg,
        complacency_score=comp["score"],
        current_drawdown_pct=current_drawdown_pct
    )

    if do_log:
        log_run(news, mkt, cmp, rec)

    grid_list = grid.tolist() if hasattr(grid, "tolist") else grid
    dens_list = dens.tolist() if hasattr(dens, "tolist") else dens
    return {
        "regime": {
            "dominant": regime.dominant.value,
            "conviction": float(regime.conviction),
            "flipped_from": regime.flipped_from.value if regime.flipped_from else None,
            "surfaces": sorted(s.value for s in surfaces),
            "vol_expansion": bool(vol_expansion),
        },
        "sector_sentiment": {k: float(v) for k, v in sect_sent.items()},
        "sector_weights": {k: float(v) for k, v in sw.items()},
        "bias": float(bias), "coverage": float(coverage), "momentum": float(momentum),
        "rnd": {"grid": grid_list, "dens": dens_list, 
                "p_below_spot": float(rstats["p_below_spot"]),
                "p_above_spot": float(rstats["p_above_spot"]),
                "sd": float(rstats["sd"]),
                "skew": float(rstats["skew"]),
                "spot": float(chain["spot"])},
        "comparison": cmp,
        "suggestion": rec,
        "complacency": comp,
        "sizing": sizing,
        "articles": articles,
    }


if __name__ == "__main__":   # smoke test (no Streamlit)
    arts = [
        {"title": "KOSPI meltdown drags Indian indices; Nifty falls 279 points",
         "published_at": "2026-06-23T14:00:00+00:00", "sentiment": -0.8},
        {"title": "AI selloff hits Nasdaq; Philadelphia Semiconductor index -8%",
         "published_at": "2026-06-23T12:00:00+00:00", "sentiment": -0.7},
        {"title": "Nifty IT slides as Accenture guidance weak; Infosys, TCS drag",
         "published_at": "2026-06-24T05:00:00+00:00", "sentiment": -0.6},
        {"title": "FII selling continues as banks see pressure",
         "published_at": "2026-06-24T06:00:00+00:00", "sentiment": -0.25},
    ]
    chain = {"strikes": list(range(23750, 24850, 50)),
             "call_ltp": [478.60, 430.00, 383.85, 340.05, 295.50, 252.25, 212.40,
                          175.20, 141.65, 112.70, 86.90, 65.35, 48.40, 35.70,
                          26.30, 19.35, 14.00, 10.75, 8.30, 6.30, 5.20, 3.95],
             "spot": 24_200.0, "days": 7.0, "r": 0.0655}

    res = run_pipeline(arts, chain, prev_regime="geopolitics_oil")
    print("regime    :", res["regime"]["dominant"],
          f"(conv {res['regime']['conviction']:.0%},",
          "flip from", res["regime"]["flipped_from"], ")")
    print("bias/cov  :", round(res["bias"], 3), "/", f"{res['coverage']:.0%}")
    print("RND p<spot:", f"{res['rnd']['p_below_spot']:.0%}",
          "| move +/-", f"{res['rnd']['sd']:.0f}")
    print("relation  :", res["comparison"]["relation"])
    print("suggest   :", res["suggestion"].get("action"),
          "->", res["suggestion"].get("structure", res["suggestion"].get("why")))
