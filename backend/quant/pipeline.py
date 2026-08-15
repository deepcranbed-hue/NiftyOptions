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
from .decision_engine import index_bias
from .sector_map import sector_weights
from .rnd import extract_rnd, rnd_stats
from .market_view import NewsView, MarketView, compare, log_run
from . import strategy_suggester
from . import strike_optimizer
from .sector_tagging import sector_sentiment_from_gemini
from .complacency import complacency_score, ChainComplacencyInputs
from .risk_budget import size_trade, Trade, Position, RiskConfig
import event_calendar
from .flows_fetcher import fetch_nse_cash_sync, fetch_amfi_sip_sync, fetch_sector_fpi_sync
from flows import flow_bias
from .formulas import trace_bias, trace_complacency, trace_rnd, trace_sizing
from . import provenance

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
    put_oichg = chain.get("put_oichg") or chain.get("put_oi_chg_pct", [])
    if not strikes or not put_oichg or len(strikes) != len(put_oichg):
        return 0.0
    valid = [pchg for s, pchg in zip(strikes, put_oichg) if abs(s - spot) <= band_pts and pchg is not None]
    return sum(valid) / len(valid) if valid else 0.0


def run_pipeline(chain: dict,
                 articles: list | None = None,
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
                 do_log: bool = False,
                 news_state: dict | None = None,
                 flows_state: dict | None = None,
                 events_state: dict | None = None,
                 macro_state: dict | None = None,
                 cues_state: dict | None = None,
                 opt_weights: dict | None = None,
                 opt_bias: float | None = None,
                 opt_min_pop: float = 0.0,
                 opt_allow_undefined: bool = False,
                 opt_cost_per_leg: float = 20.0,
                 opt_window_pts: int = 500,
                 opt_max_wing: int = 300,
                 opt_top_n: int = 6,
                 opt_max_loss_budget: float = 0.0,
                 opt_allow_bad_rnd: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    
    # 1-3. news state (regime, bias, sentiment) ---------------------------
    if news_state and not news_state.get("stale", True):
        regime_dict = news_state.get("regime", {})
        dominant = regime_dict.get("dominant", "unknown")
        conviction = regime_dict.get("conviction", 0.0)
        flipped_from = regime_dict.get("flipped_from", None)
        surfaces = regime_dict.get("surfaces", [])
        vol_expansion = regime_dict.get("vol_expansion", False)
        
        sect_sent = news_state.get("sector_sentiment", {})
        bias = news_state.get("bias", 0.0)
        coverage = news_state.get("coverage", 0.0)
        momentum = news_state.get("momentum", 0.0)
        articles_out = news_state.get("articles", [])
        
        vix_dict = news_state.get("india_vix", {})
        news_vix = vix_dict.get("value")
        vix_stale = vix_dict.get("stale", True)
        vix_note = vix_dict.get("note", "No India VIX value found in current news — VIX unavailable.")
        
        sw = sector_weights(weights) if weights else sector_weights()
    else:
        articles = articles or []
        arts = _to_articles(articles)
        prev = Driver(prev_regime) if prev_regime else None
        regime = assess_regime(arts, now=now, half_life_hours=half_life_hours, prev_regime=prev)
        sect_sent = sector_sentiment_from_gemini(articles, now=now, half_life_hours=half_life_hours)
        sw = sector_weights(weights) if weights else sector_weights()
        bias, coverage = index_bias(sect_sent, sw)
        surfaces_raw = regime.surfaces_by_driver.get(regime.dominant, set())
        momentum = min(1.0, regime.conviction * (0.5 + 0.5 * min(len(surfaces_raw), 4) / 4))
        
        dominant = regime.dominant.value
        conviction = float(regime.conviction)
        flipped_from = regime.flipped_from.value if regime.flipped_from else None
        surfaces = sorted(s.value for s in surfaces_raw)
        
        from backend.quant.vol_attribution import vix_from_news
        vix_res = vix_from_news(articles, now=now)
        news_vix = vix_res.value
        vix_stale = vix_res.stale
        vix_note = vix_res.note
        
        articles_out = articles
        republish_check = None
        if news_state and "republish_check" in news_state:
            republish_check = news_state["republish_check"]
    
    # Interpretation Layer: Breadth Classification
    from backend.quant.index_attribution import attribute_index_move
    from backend.quant.regime_synthesis import classify_breadth
    try:
        from backend.quant.flows_fetcher import fetch_index_quotes
        quotes = fetch_index_quotes()
        attr = attribute_index_move(quotes)
        # NIFTY Bank or Reliance could be passed as heavyweight, default to None for now
        breadth_classification = classify_breadth(attr)
    except Exception as e:
        breadth_classification = {"regime": "UNKNOWN", "read": f"Failed to classify breadth: {e}"}
    
    # 4. risk-neutral distribution from the live chain --------------------
    grid, dens = extract_rnd(chain["strikes"], chain["call_ltp"],
                             chain["spot"], chain["days"] / 365.0,
                             chain.get("r", 0.0655),
                             put_prices=chain.get("put_ltp"))
    rstats = rnd_stats(grid, dens, chain["spot"], 
                       strikes=chain.get("strikes"),
                       call_ltp=chain.get("call_ltp"),
                       put_ltp=chain.get("put_ltp"))
    
    # Complacency Gauge
    put_chg = mean_put_oi_change_pct(chain, spot=chain["spot"])
    raw_atm_iv = chain.get("atm_iv", 15.0)
    atm_iv_frac = raw_atm_iv / 100.0 if raw_atm_iv > 1.0 else raw_atm_iv

    raw_iv_pct = chain.get("iv_percentile")
    iv_pct_frac = (raw_iv_pct / 100.0) if (raw_iv_pct is not None and raw_iv_pct > 1.0) else raw_iv_pct

    # Try loading high-fidelity INDIAVIX from the database using option chain capture timestamp
    db_vix = None
    try:
        from bar_store import get_latest_vix
        db_vix = get_latest_vix(before_ts=chain.get("captured_at"))
    except Exception as db_vix_err:
        print("Failed to query VIX from DB in pipeline:", db_vix_err)
        
    # Prefer database-sourced VIX over news-sourced VIX
    final_vix = db_vix if db_vix is not None else (news_vix if news_vix is not None else chain.get("vix"))

    comp_inputs = ChainComplacencyInputs(
        atm_iv=atm_iv_frac,
        put_oi_chg_pct_atm=put_chg,
        put_call_oi_ratio=chain.get("put_call_oi_ratio", 1.0),
        skew=rstats["skew"],
        iv_percentile=iv_pct_frac,
        vix=final_vix,
        vix_chg_pct=chain.get("vix_chg_pct")
    )
    comp = complacency_score(comp_inputs)
    # Re-evaluate vol_expansion dynamically from chain if news_state is absent, otherwise keep it from news
    if not news_state or news_state.get("stale", True):
        vol_expansion = (comp["vol_state_hint"] == "expansion")

    # 5. Events & Flows (needed for suggester & gates) --------------------
    if events_state and not events_state.get("stale", True):
        prox_dict = events_state.get("proximity", {})
        event_action = prox_dict.get("action", "normal")
        event_near_days = prox_dict.get("days_away")
    else:
        events = event_calendar.upcoming_events()
        prox = event_calendar.event_proximity(events)
        event_action = prox.get("action", "normal")
        event_near_days = prox.get("days_away")

    if flows_state and not flows_state.get("stale", True):
        flow_regime = flows_state.get("trend", {}).get("regime", "unknown")
        flow_tilt = flows_state.get("flow_tilt", 0.0)
    else:
        cash_days, _ = fetch_nse_cash_sync()
        sip_months, _ = fetch_amfi_sip_sync()
        fb = flow_bias(cash_days, sip_months)
        flow_regime = fb.get("trend", {}).get("regime", "unknown")
        flow_tilt = fb.get("flow_tilt", 0.0)

    # Risk hook: broad_outflow downsize
    if flow_regime == "broad_outflow" and event_action != "block_premium_sell":
        event_action = "caution_downsize"

    # 6. news-vs-market comparison + strategy suggester -------------------
    news = NewsView(index_bias=bias, momentum=momentum, coverage=coverage)
    print(f"\n[DEBUG] RND Expected Move (1 SD) estimated at: {rstats['sd']:.2f} pts\n")
    mkt = MarketView(spot=chain["spot"], p_below_spot=rstats["p_below_spot"],
                     expected_move=rstats["sd"], skew=rstats["skew"])
    cmp = compare(news, mkt)
    
    # Divergence flags
    if bias > 0.2 and flow_tilt < -0.2:
        cmp["flow_divergence"] = "News bullish but money flow is net negative (FII selling dominates)."
    elif bias < -0.2 and flow_tilt > 0.2:
        cmp["flow_divergence"] = "News bearish but money flow is net positive (Domestic absorption)."
        
    if macro_state:
        macro_tilt = macro_state.get("net_tilt", 0.0)
        if bias > 0.2 and macro_tilt < -0.2:
            cmp["macro_divergence"] = "News bullish but US Macro is net negative."
        elif bias < -0.2 and macro_tilt > 0.2:
            cmp["macro_divergence"] = "News bearish but US Macro is net positive."

    from backend.quant.india_macro import earnings_regime
    er = earnings_regime(now.date())
    
    rec = strategy_suggester.suggest(
        complacency=comp["score"],
        bias=bias,
        expected_move_pts=rstats["sd"],
        straddle_pts=None,
        event_near_days=event_near_days,
        iv_percentile=iv_pct_frac,
        earnings_season=er["active"]
    )

    # 7. Strike Optimizer & Sizing Gates ----------------------------------
    cfg = RiskConfig(**risk_cfg) if risk_cfg else RiskConfig()
    pos_book = [Position(**p) for p in book] if book else []
    
    # Extract list representations properly for JSON serialization
    grid_list = grid.tolist() if hasattr(grid, "tolist") else grid
    dens_list = dens.tolist() if hasattr(dens, "tolist") else dens

    opt_res = strike_optimizer.optimize(
        chain=chain,
        rnd={"grid": grid_list, "dens": dens_list, "spot": chain["spot"], 
             "provenance": rstats.get("provenance", "PRIMARY"), 
             "warning": rstats.get("warning", "")},
        objective="ev",
        weights=opt_weights,
        max_loss_budget_pts=opt_max_loss_budget if opt_max_loss_budget > 0 else ((cfg.capital * cfg.risk_per_trade_pct) / cfg.lot_size if cfg else 100),
        min_pop=opt_min_pop,
        cost_per_leg_pts=opt_cost_per_leg,
        window_pts=opt_window_pts,
        max_wing=opt_max_wing,
        allow_undefined=opt_allow_undefined,
        oi_weight=opt_weights.get("oi", 0.0) if opt_weights else 0.0,
        top_n=opt_top_n,
        bias=opt_bias,
        allow_bad_rnd=opt_allow_bad_rnd,
        earnings_season=er["active"]
    )
    
    from backend.quant.vol_attribution import attribute_vol
    
    # Fetch realized vol to see if current IV is justified by recent real movement
    real_vol_val = None
    try:
        from bar_store import realized_vol as get_real_vol
        rv = get_real_vol(days=20)
        if "realized_vol_pct" in rv:
            real_vol_val = rv["realized_vol_pct"] / 100.0
    except Exception as e:
        print(f"Could not calculate realized vol: {e}")
        
    vol_att = attribute_vol(
        chain_atm_iv=atm_iv_frac,
        days_to_expiry=chain.get("days", 0),
        india_vix=final_vix,
        event_within_days=event_near_days,
        realized_vol=real_vol_val
    )
    
    # Apply gates to the optimizer's top candidates
    sizing_results = {}
    if opt_res.get("status") == "ok":
        for rank, candidate in enumerate(opt_res["ranked"]):
            kind = candidate["kind"]
            is_premium_sell = "short" in kind.lower() or "credit" in kind.lower() or "condor" in kind.lower() or "butterfly" in kind.lower() or ("spread" in kind.lower() and candidate.get("credit_pts", 0) > 0)
            
            if is_premium_sell and vol_att["sell_premium_verdict"].startswith("CAUTION"):
                candidate["vol_caution"] = vol_att["sell_premium_verdict"]
                
            c_trade = Trade(
                structure=kind,
                max_loss_pts=candidate.get("max_loss_pts", 0),
                delta_per_lot=0.0, # Not computed by basic optimizer yet
                vega_per_lot=0.0,
                is_premium_sell=is_premium_sell
            )
            s_res = size_trade(
                trade=c_trade, book=pos_book, cfg=cfg,
                complacency_score=comp["score"],
                current_drawdown_pct=current_drawdown_pct,
                event_action=event_action
            )
            candidate["sizing_gate"] = s_res
    

    # if do_log:
    #     log_run(news, mkt, cmp, rec)

    formula_traces = {}
    formula_traces["bias"] = trace_bias(sect_sent, sw, coverage * 100, bias)
    if "components" in comp and "weights" in comp:
        formula_traces["complacency"] = trace_complacency(comp["components"], comp["weights"], comp["score"])
    formula_traces["rnd"] = trace_rnd(rstats["sd"], rstats["p_below_spot"], rstats["p_above_spot"], rstats["skew"])
    
    sizing = opt_res.get("ranked", [{}])[0].get("sizing_gate", {}) if opt_res.get("ranked") else {}
    top_cand = opt_res.get("ranked", [{}])[0] if opt_res.get("ranked") else {}
    if sizing.get("approved") and "lot_caps" in sizing:
        # replace None with inf for tracing display
        caps_for_trace = {k: (v if v is not None else float('inf')) for k, v in sizing["lot_caps"].items()}
        formula_traces["sizing"] = trace_sizing(caps_for_trace, sizing["lots"], top_cand.get("max_loss_pts", 0), cfg.lot_size)

    # ── Provenance generation ───────────────────────────────────────────────
    prov_records = []
    
    # 1. Sentiment (if LLM is down, gemini_down will be explicitly true on the fallback)
    # Check if ALL articles fell back or if any explicitly say gemini_down
    gemini_down_explicit = any(a.get("gemini_down", False) for a in articles_out)
    if articles_out:
        prov_records.append(provenance.sentiment_provenance(articles_out, llm_down=gemini_down_explicit, sector_weight=sw))
    else:
        prov_records.append(provenance.unavailable("sentiment", "no articles provided"))
        
    # 2. Coverage
    prov_records.append(provenance.coverage_provenance(coverage))
    
    # 3. RND
    has_put_leg = "put_ltp" in chain and chain["put_ltp"] is not None and len(chain["put_ltp"]) > 0
    straddle_ok = True # simplified for now, as we're not running the full straddle check here
    prov_records.append(provenance.rnd_provenance(has_put_leg, straddle_ok))
    
    # 4. Complacency
    prov_records.append(provenance.complacency_provenance(comp["components"]))
    
    # 7. VIX from News
    if news_vix is not None:
        if vix_stale:
            prov_records.append(provenance.partial("vix", "news_sourced", vix_note, value=news_vix))
        else:
            prov_records.append(provenance.primary("vix", "news_sourced", value=news_vix, note=vix_note))
    else:
        prov_records.append(provenance.unavailable("vix", "No VIX data found in news (using no-VIX complacency path)."))
        
    prov = provenance.summarize(prov_records)
    
    # 5. Persisted State Freshness
    def _parse_age(iso_str: str | None) -> float:
        if not iso_str: return 999999.0
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return (now - dt).total_seconds()
        except:
            return 999999.0
            
    if news_state:
        age = _parse_age(news_state.get("as_of"))
        prov_records.append(provenance.state_provenance("news_state", age, 3600, news_state.get("as_of", "")))
    if flows_state:
        age = _parse_age(flows_state.get("as_of"))
        prov_records.append(provenance.state_provenance("flows_state", age, 86400, flows_state.get("as_of", "")))
    if events_state:
        age = _parse_age(events_state.get("as_of"))
        prov_records.append(provenance.state_provenance("events_state", age, 86400, events_state.get("as_of", "")))
    if macro_state:
        age = _parse_age(macro_state.get("as_of"))
        prov_records.append(provenance.state_provenance("macro_state", age, 86400, macro_state.get("as_of", "")))
    if cues_state:
        age = _parse_age(cues_state.get("as_of"))
        prov_records.append(provenance.state_provenance("cues_state", age, 3600, cues_state.get("as_of", "")))

    prov_summary = provenance.summarize(prov_records)

    grid_list = grid.tolist() if hasattr(grid, "tolist") else grid
    from backend.quant.india_macro import conclude
    
    # Try to determine if oil is falling from cues
    oil_falling = None
    cues_dict = cues_state.get("cues", cues_state) if cues_state else {}
    
    if cues_state:
        # Simplistic assumption: if commodity sentiment is bearish, oil might be soft.
        # Ideally, we'd have a specific oil directional flag.
        cm_tilt = cues_state.get("commodities", {}).get("tilt", 0.0) if isinstance(cues_state.get("commodities"), dict) else 0.0
        oil_falling = cm_tilt < -0.1
        
        # Interpretation Layer: Semi Transmission
        from backend.quant.regime_synthesis import semi_transmission
        sox_pct = cues_dict.get("^SOX", 0.0)
        cues_state["semi_transmission"] = semi_transmission(sox_pct, persistent=False)

    conclusion = conclude(
        proximity=events_state.get("proximity", {}) if events_state else {},
        today=now.date(),
        oil_falling=oil_falling,
        flow_regime=flows_state.get("trend", {}).get("flow_regime") if flows_state else None,
        complacency=comp["score"],
        bias=bias,
        cues_state=cues_state,
        flows_state=flows_state
    )
    
    return {
        "regime": {
            "dominant": dominant,
            "conviction": conviction,
            "flipped_from": flipped_from,
            "surfaces": surfaces,
            "vol_expansion": bool(vol_expansion),
        },
        "sector_sentiment": {k: float(v["combined"] if isinstance(v, dict) else v)
                             for k, v in sect_sent.items() if not k.startswith("__")},
        "sector_weights": {k: float(v) for k, v in sw.items()},
        "bias": float(bias), "coverage": float(coverage), "momentum": float(momentum),
        "rnd": {
            "grid": grid_list,
            "dens": dens_list,
            "spot": chain["spot"],
            "sd": rstats["sd"],
            "skew": rstats["skew"],
            "p_below_spot": rstats["p_below_spot"],
            "p_above_spot": rstats["p_above_spot"],
            "provenance": rstats.get("provenance", "PRIMARY")
        },
        "vol_attribution": vol_att,
        "formulas": formula_traces,
        "comparison": cmp,
        "suggestion": rec,
        "optimizer": opt_res,
        "complacency": comp,
        "sizing": sizing,
        "articles": articles_out,
        "cues": cues_state,
        "timestamps": {
            "news": news_state.get("as_of") if news_state else None,
            "flows": flows_state.get("as_of") if flows_state else None,
            "events": events_state.get("as_of") if events_state else None,
            "cues": cues_state.get("as_of") if cues_state else None,
            "macro": macro_state.get("as_of") if macro_state else None,
            "chain": now.isoformat()
        },
        "formulas": formula_traces,
        "provenance": prov_summary,
        "conclusion": conclusion,
        "interpretations": {
            "breadth": breadth_classification,
            "republish": news_state.get("republish_check") if news_state else republish_check
        }
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
