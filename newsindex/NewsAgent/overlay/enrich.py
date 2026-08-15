"""
enrich.py — apply the overlay to a Core-built MIO.

Given the MIO (from mcp_server/mio_builder.build_mio) and the core module, this returns an
ENRICHED MIO that addresses the institutional review:

  * transmission[]        -> replaced with full multi-hop economic chains (never Driver->Nifty)
  * market_context        -> gains USDINR + VIX level amplifiers (alongside oil)
  * interactions[]        -> explicit named cross-driver terms (Oil×USD, FII×VIX, ...)
  * driver_dominance      -> gains an interaction-adjusted view (answers 'oil 7% too low')
  * affected_sectors[]    -> Energy split into Upstream/OMC + company catalysts folded in
  * validation[]          -> gains status_label (Economic Prior / Supported / Dominated)
  * causal_graph          -> the live v2.0 graph with rich per-node attributes

The machine-readable MIO stays schema-valid: we ADD fields and a parallel
`transmission_multihop`, keeping the original single-hop `transmission` for schema conformance.
"""
from __future__ import annotations

import os
import chains as chainlib
import amplifiers
import interactions as interlib
import terminology
import sectors as sectorlib
import causal_graph
import semis_regime
import subsectors as subseclib
import calibration as calib
import extract as extractlib
import impact_scoring as impactlib
import macro_expectations as macroexp
import policy_catalysts as polcat
import sector_factors as secfac
import macro_dashboard as dashboard
import relationship_tiers as reltiers


_DRIVER_LABEL = causal_graph._DRIVER_LABEL

# semis target category → who's actually in it. Indian names come from the gazetteer (by
# sector); global names (which are NOT Indian-listed) are shown as illustrative context.
_SEMIS_TARGETS = {
    "AI infrastructure (semis/EMS/power)": {
        "india_sectors": ["ems", "electronic", "semiconductor", "power", "electrical", "cable"],
        "global": ["Nvidia", "TSMC", "AMD", "Micron", "Broadcom"]},
    "Indian IT services": {
        "india_sectors": ["it"]},                  # the actionable Indian leg — TCS/Infosys/...
    "Enterprise software": {
        "global": ["Microsoft", "Oracle", "SAP", "Salesforce", "ServiceNow", "Adobe"],
        "note": "GLOBAL — no direct Indian-listed proxy (this is the software the budget is rotating AWAY from)"},
    "Cloud providers": {
        "global": ["Amazon (AWS)", "Microsoft (Azure)", "Alphabet (GCP)"],
        "india_sectors": ["telecom", "power"],
        "note": "global hyperscalers; Indian proxy = data-centre / telecom / power"},
}


def _semis_target_companies(core, snap, target_reads):
    """For each semis target, name the INDIAN stocks (from the gazetteer, with today's %) and,
    separately, the GLOBAL names — so 'Enterprise software' isn't confused for Indian IT."""
    pct = {}
    for key in ("quotes_idx", "quotes_stk", "it_quotes", "sector_quotes", "theme_quotes", "univ_quotes"):
        for q in snap.get(key, []) or []:
            if q.get("pct_change") is not None:
                if q.get("symbol"):
                    pct[q["symbol"]] = q["pct_change"]
                pct[(q.get("name") or "").lower()] = q["pct_change"]
    gaz = getattr(core.ms, "COMPANY_GAZETTEER", [])
    out = {}
    for target, lean in (target_reads or {}).items():
        cfg = _SEMIS_TARGETS.get(target, {})
        india = []
        if cfg.get("india_sectors"):
            seen = set()
            for kw, disp, sym, sec in gaz:
                if any(h in sec.lower() for h in cfg["india_sectors"]) and disp not in seen:
                    seen.add(disp)
                    india.append({"company": disp, "pct": pct.get(sym, pct.get(disp.lower()))})
            india.sort(key=lambda c: (c["pct"] is None, ))
        out[target] = {"lean": lean, "india": india[:6],
                       "global": cfg.get("global", []), "note": cfg.get("note")}
    return out


def enrich_mio(mio: dict, core) -> dict:
    """Apply the overlay. Each step is independently guarded so a failure in one
    (e.g. a live-data edge case) records a warning instead of dropping the whole overlay."""
    warnings: list[str] = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:
            warnings.append(f"{name}: {type(e).__name__}: {e}")

    eng = core.run_engine()
    drivers = eng.get("drivers", {}) or {}
    raw = eng.get("raw", {}) or {}
    regime = core.detect_regime()
    dominance = core.driver_dominance("Nifty 50")
    validations = core.validate_relationships()
    companies = core.company_intelligence()
    sectors = core.sector_intelligence()
    news = core._ensure().get("news", [])
    ai_regime = regime.get("ai_regime")
    dom_vec = dominance.get("vector", {}) or {}

    # -- 0. NEWS ACQUISITION: targeted topic search + FULL-BODY enrichment, so every scanner
    #       reads article BODIES (where "IBM redirects budgets", cloud growth, NIM figures,
    #       USFDA actions live), not just RSS headlines. Robust + keyless (trafilatura →
    #       Playwright → plain requests fallback). Config: NEWSAGENT_SEARCH=0 disables search,
    #       NEWSAGENT_FULLTEXT=0 disables bodies, NEWSAGENT_FULLTEXT_LIMIT caps body fetches.
    import news_fetch as newsfetch  # noqa: E402
    def _acquire():
        summ = newsfetch.augment_snapshot(core)
        # `news` was bound before augmentation; re-read the (in-place enriched) snapshot list
        nonlocal news
        news = core._ensure().get("news", [])
        mio["news_acquisition"] = summ
        mio["fulltext_pulled"] = sum(1 for n in news if n.get("fulltext"))
    step("news_acquisition", _acquire)

    # -- 1b. NARRATIVE DISPATCHER — "which narratives are active today?" ----
    # The missing orchestration layer. Runs immediately after acquisition so every
    # stage below can ASK it instead of re-deriving activation from keywords:
    #
    #     if nd.active(mio["narratives"], "Oil"): build_oil_chain()
    #
    # NON-INVASIVE ON PURPOSE: this only ADDS mio["narratives"]. No existing step reads
    # it yet, so behaviour is unchanged. Detectors (is_it_ai_headline, india_cpi_hot, …)
    # get retired one at a time against a stable interface, rather than in one cut where
    # a regression would be untraceable.
    def _narratives():
        news = core._ensure().get("news", []) or []
        snap = core._ensure()
        import sys as _sys
        from pathlib import Path as _Path
        _shared = _Path(__file__).resolve().parents[2]
        if str(_shared) not in _sys.path:
            _sys.path.insert(0, str(_shared))
        import narrative_dispatcher as nd
        out = nd.dispatch(news, snap)
        mio["narratives"] = out
        # Log unmodelled co-activations for later review. Records only — affects nothing.
        try:
            import interaction_discovery as idisc
            out["discovery"] = idisc.record(out.get("relationships", []))
        except Exception:
            pass
    step("narrative_dispatcher", _narratives)

    # -- 1. multi-hop transmission chains (economic priors) -----------------
    def _chains():
        multihop = []
        for dkey, dval in drivers.items():
            for br in chainlib.expand(dkey, dval, regime=ai_regime):
                label = _DRIVER_LABEL.get(dkey, dkey)
                multihop.append({
                    "driver": label, "branch": br["branch"], "chain": br["chain"],
                    "path": " → ".join(br["chain"]), "mechanisms": br["mechanisms"],
                    "sub_sectors": br["sub_sectors"],
                    "activation": round(dom_vec.get(label, 0.0), 3),
                })
        multihop.sort(key=lambda x: -x["activation"])
        mio["transmission_multihop"] = multihop
    step("chains", _chains)

    # -- 2. level amplifiers: oil (exists) + USDINR + VIX -------------------
    def _amps():
        snap = core._ensure()
        usdinr_px = core.ms._last_of(snap.get("quotes_macro", []), "USD/INR")
        vix_px = core.ms._last_of(snap.get("quotes_idx", []), "India VIX")
        amps = amplifiers.all_amplifiers(
            eng.get("brent_price"), usdinr_px, vix_px, core.oil_level)
        mc = mio.setdefault("market_context", {})
        mc["amplifiers"] = amps
        mc["usdinr_level"] = amps["usdinr"]
        mc["vix_level"] = amps["vix"]
    step("amplifiers", _amps)

    # -- 3. explicit interaction terms -------------------------------------
    inter_holder = {}
    def _inter():
        inter = interlib.compute(drivers)
        mio["interactions"] = inter
        inter_holder["v"] = inter
    step("interactions", _inter)

    # -- 4. interaction-adjusted dominance ---------------------------------
    def _adj():
        boost = interlib.dominance_boost(inter_holder.get("v", []))
        adj = dict(dom_vec)
        for dkey, extra in boost.items():
            label = _DRIVER_LABEL.get(dkey, dkey)
            adj[label] = round(adj.get(label, 0.0) + extra, 3)
        tot = sum(adj.values()) or 1.0
        adj = {k: round(v / tot, 3) for k, v in sorted(adj.items(), key=lambda kv: -kv[1])}
        mio["driver_dominance_adjusted"] = {
            "vector": adj,
            "note": "base dominance + interaction boosts, renormalized — a driver's effective "
                    "importance rises through interaction terms, not a bigger direct coefficient.",
            "dominant_driver": next(iter(adj), None),
        }
    step("dominance_adjusted", _adj)

    # -- 5. Energy split + company->sector bridge --------------------------
    def _sectors():
        split = sectorlib.split_energy(sectors, drivers.get("oil_pct"))
        bridged = sectorlib.bridge_companies(split, companies)
        mio["affected_sectors_enriched"] = [
            {"sector": s["sector"], "direction": s["verdict"], "net": s.get("net"),
             "top_rows": s.get("rows", [])[:3], "company_bridge": s.get("company_bridge"),
             "overlay": s.get("overlay", False), "note": s.get("note")}
            for s in bridged
        ]
    step("sectors", _sectors)

    # -- 6. institutional terminology on validations -----------------------
    def _terms():
        for v in mio.get("validation", []):
            st = v.get("status")
            v["status_label"] = terminology.label(st)
            v["prior_phrase"] = terminology.phrase(st)
            v["edge"] = terminology.rename_relationship(v.get("edge", ""))
    step("terminology", _terms)

    # -- 7. the live causal graph ------------------------------------------
    def _graph():
        mio["causal_graph"] = causal_graph.build(
            core, eng, dominance, regime, validations, news)
    step("causal_graph", _graph)

    # -- 8. numeric fundamentals parsed from crawled text + IMPACT scoring --
    #      (runs first so the semis + sub-sector steps can consume the numbers)
    ex_holder: dict = {"scored": []}
    def _extract():
        metrics = extractlib.extract_from_news(news)
        if not metrics:
            return
        wt_by_company = {(c.get("company") or "").lower(): c.get("nifty_wt") for c in companies}
        scored = []
        for m in metrics:
            src = (m.get("source") or "").lower()
            wt = next((w for name, w in wt_by_company.items() if name and name in src), None)
            s = impactlib.score_metric(m, nifty_weight=wt)
            s.update({"unit": m.get("unit"), "direction": m.get("direction"),
                      "source": m.get("source")})
            scored.append(s)
        scored = impactlib.rank(scored)
        ex_holder["scored"] = scored
        mio["extracted_fundamentals"] = scored
    step("extract_fundamentals", _extract)

    # -- 9. conditional semis-regime read (why did SOX move → Indian IT) ----
    def _semis():
        snap = core._ensure()
        it_pct = core.ms._pct_of(snap.get("quotes_idx", []), "Nifty IT")
        sig = {
            "sox": raw.get("sox"), "kospi": raw.get("kospi"), "nifty_it": it_pct,
            "us10y": drivers.get("us10y_pct"), "dxy": drivers.get("dxy_pct"),
            "usdinr": raw.get("usdinr"), "vix": drivers.get("vix_pct"),
        }
        # route the parsed hyperscaler capex guidance into the classifier
        capex = next((m for m in ex_holder["scored"]
                      if m.get("metric") == "Hyperscaler capex"), None)
        read = semis_regime.classify(sig, news, capex=capex)
        if read:
            # name the actual Indian stocks per target + attach today's move
            read["target_companies"] = _semis_target_companies(core, snap, read.get("target_reads", {}))
            mio["semis_regime"] = read
    step("semis_regime", _semis)

    # -- 10. deep sub-sector factor models, magnitude-scaled by impact -------
    def _subsectors():
        snap = core._ensure()
        usdinr_move = raw.get("usdinr")
        sig = {
            "oil_pct": drivers.get("oil_pct"),
            "us10y_pct": drivers.get("us10y_pct"),
            "copper_pct": core.ms._pct_of(snap.get("quotes_macro", []), "Copper"),
            "usdinr_sign": (1.0 if (usdinr_move or 0) > 0 else -1.0 if (usdinr_move or 0) < 0 else 0.0),
        }
        mio["subsector_factors"] = subseclib.build(sig, news, extracted=ex_holder["scored"])
    step("subsectors", _subsectors)

    # -- 10. graduate reliability PRIOR->CALIBRATED from events.db ----------
    def _calibrate():
        mio["calibration_source"] = ("events.db (build_events.py linkage_conf + event_stats)"
                                     if calib.available() else "events.db not populated — run build_events.py")
        # a) validations get their real historical hit-rate
        for v in mio.get("validation", []):
            rel = calib.reliability_for(v.get("edge", ""))
            if rel:
                v["reliability"] = rel
        # b) MIO confidence: dominant driver's calibrated linkage, else the mean of the
        #    linkages ACTIVE today (some drivers like FII have no historical series).
        dom_key = dominance.get("dominant_driver_key")
        rel = calib.reliability_for_driver(dom_key) if dom_key else None
        if not (rel and rel.get("value") is not None):
            active = [v["reliability"] for v in mio.get("validation", [])
                      if v.get("reliability") and v["reliability"].get("value") is not None]
            if active:
                mean_val = round(sum(r["value"] for r in active) / len(active), 3)
                tot_n = sum(r["n"] for r in active)
                rel = {"value": mean_val, "hit_rate_pct": round(mean_val * 100, 1),
                       "n": tot_n, "tag": "CALIBRATED" if tot_n >= 60 else "PRIOR",
                       "source": "events.db (mean of active linkages)",
                       "basis": f"{len(active)} active calibrated relationships"}
        if rel and rel.get("value") is not None:
            conf = mio.setdefault("confidence", {})
            conf["historical_reliability"] = rel["value"]
            conf["reliability_tag"] = rel["tag"]
            conf["reliability_detail"] = rel
        # c) causal-graph nodes: attach calibrated reliability where a linkage matches
        for nd in mio.get("causal_graph", {}).get("nodes", []):
            rel = calib.reliability_for(nd.get("id", ""))
            if rel and rel.get("value") is not None:
                nd["historical_reliability"] = {"value": rel["value"], "n": rel["n"],
                                                "tag": rel["tag"], "matched": rel.get("matched")}
        # d) semis regime: SOX->IT linkage (57%) + sox_drop event analogue
        sr = mio.get("semis_regime")
        if sr:
            sr["reliability"] = calib.reliability_for_driver("sox_pct")
            analogue = calib.event_analogue("sox_drop_3")
            if analogue:
                sr["historical_analogue"] = {"condition": "sox_drop_3", "stats": analogue}
    step("calibration", _calibrate)

    # -- 11. standing macro-expectations block (Fed / jobs / inflation) -----
    def _macro():
        sig = {"us10y_pct": drivers.get("us10y_pct"), "dxy_pct": drivers.get("dxy_pct"),
               "oil_pct": drivers.get("oil_pct")}
        mio["macro_expectations"] = macroexp.build(sig, news)
    step("macro_expectations", _macro)

    # -- 12. policy-scheme beneficiaries + broker views --------------------
    def _policy():
        gaz = getattr(core.ms, "COMPANY_GAZETTEER", [])
        pc = polcat.build(news, gaz)
        if pc:
            mio["policy_catalysts"] = pc
    step("policy_catalysts", _policy)

    # -- 13. per-sector factor library (each sector its OWN drivers) --------
    def _sector_library():
        snap = core._ensure()
        sig = {
            "oil_pct": drivers.get("oil_pct"), "oil_mult": eng.get("oil_mult"),
            "vix_pct": drivers.get("vix_pct"), "us10y_pct": drivers.get("us10y_pct"),
            "dxy_pct": drivers.get("dxy_pct"), "sox_pct": drivers.get("sox_pct"),
            "kospi_pct": drivers.get("kospi_pct"), "fii_kcr": drivers.get("fii_kcr"),
            "copper_pct": core.ms._pct_of(snap.get("quotes_macro", []), "Copper"),
            "usdinr_move": raw.get("usdinr"),
            "india_cpi_hot": drivers.get("india_cpi_hot"),
            "us_cpi_cool": drivers.get("us_cpi_cool"),
        }
        risk_off = "risk-off" in (regime.get("observed_tone", "") or "").lower()
        mio["sector_factor_library"] = secfac.compute(
            sig, news, regime.get("ai_regime"), extracted=mio.get("extracted_fundamentals"),
            risk_off=risk_off)
    step("sector_factor_library", _sector_library)

    # -- 13.5 broad base-metals sentiment (global copper/aluminium + India MCX) ----
    import metals_sentiment as metsent  # noqa: E402
    def _metals():
        mio["metals_sentiment"] = metsent.compute(core, core._ensure())
    step("metals_sentiment", _metals)

    # -- 13a. add NEW validated relationships (rates→banks, AI-infra→EMS/power, rupee→metals)
    import extra_validations as exval  # noqa: E402
    def _extra_val():
        snap = core._ensure()
        extra = exval.build(core, snap, drivers, raw, regime.get("ai_regime")) or []
        hv = exval.heavyweight_nifty(core, snap)          # Index-level: heavyweights → Nifty
        mc = exval.global_metals_cycle(core, snap, mio.get("metals_sentiment"))  # sector cycle read
        rows = extra + ([hv] if hv else []) + ([mc] if mc else [])
        if rows:
            mio["validation"] = (mio.get("validation", []) + rows)
    step("extra_validations", _extra_val)

    # -- 13b. relationship-validation states + splits + economic reasons ----
    import validation_states as vstates  # noqa: E402
    def _vstates():
        snap = core._ensure()
        tone = (regime.get("observed_tone", "") or "").lower()

        def _flow(cat):
            for f in snap.get("flows", []) or []:
                if cat in (f.get("category", "") or "").lower():
                    n = f.get("net")
                    return n if isinstance(n, (int, float)) and not isinstance(n, bool) else None
            return None

        context = {
            "risk_on": "risk-on" in tone, "risk_off": "risk-off" in tone,
            "fii_kcr": drivers.get("fii_kcr"), "dii_net": _flow("dii"),
            "oil_pct": drivers.get("oil_pct"), "us10y_pct": drivers.get("us10y_pct"),
            "metals": mio.get("metals_sentiment"),
        }
        mio["validation"] = vstates.transform(
            mio.get("validation", []), semis=mio.get("semis_regime"),
            drivers=drivers, context=context)
    step("validation_states", _vstates)

    # -- 13c. Reason Discovery: retrieve the evidence-backed override from today's news ----
    import reason_discovery as rdisc  # noqa: E402
    def _reason_discovery():
        news = core._ensure().get("news", []) or []
        if not news:
            return
        # optional targeted per-stock search ("<stock> why up today") — bounded + net-timeout-wrapped
        do_search = os.environ.get("NEWSAGENT_REASON_SEARCH", "1") != "0"
        try:
            import news_fetch as _nf
            import net_timeout as _nt
        except Exception:
            do_search = False
        broken = [v for v in mio.get("validation", []) if v.get("state") in ("🔄", "⚠️") and v.get("broke")]
        searched = 0
        for v in broken:
            broke = v.get("broke", []) or []
            names = [c.get("name") for c in broke if c.get("name")]
            avg = sum(c.get("pct", 0) or 0 for c in broke) / max(1, len(broke))
            obs = 1 if avg >= 0 else -1
            pool = news
            # Record HOW HARD we actually looked. "No evidence found" must not be
            # reported the same way whether we searched the web and came back empty,
            # or never searched at all (budget spent / disabled / timed out). The
            # timeout flag from run_with_timeout used to be discarded, so a search
            # cut off at 12s was indistinguishable from one that completed empty.
            srch = {"attempted": False, "timed_out": False,
                    "pool": len(news), "why": ""}
            if not do_search:
                srch["why"] = "live search disabled (NEWSAGENT_REASON_SEARCH=0 or " \
                              "news_fetch/net_timeout unavailable)"
            elif searched >= 5:
                srch["why"] = "live-search budget spent (5 per run) — snapshot news only"
            else:
                pool, _to = _nt.run_with_timeout(
                    lambda: rdisc.gather_news(names, obs, _nf.search_google_news, base_news=news),
                    12.0, news)
                searched += 1
                srch["attempted"] = True
                srch["timed_out"] = bool(_to)
                srch["pool"] = len(pool or news)
                srch["why"] = ("web search timed out at 12s — fell back to snapshot news"
                               if _to else
                               f"web search ran ({srch['pool'] - len(news)} extra articles)")
            v["override_search"] = srch
            res = rdisc.discover(names, pool, observed_dir=obs)
            if res and res.get("candidates"):
                v["override_discovered"] = res
                v["override"] = res["primary_override"]      # evidence-backed (econ 'why' stays as mechanism)
                v["override_evidenced"] = res["confidence"] >= 0.30
                v["override_note"] = (f"evidence-discovered · conf {res['confidence']:.2f} · "
                                      f"coverage {int(res['coverage']*100)}%")
    step("reason_discovery", _reason_discovery)

    # -- 14. three-tier relationship hierarchy + decoupling ----------------
    def _tiers():
        mio["relationship_tiers"] = reltiers.build(core, mio)
    step("relationship_tiers", _tiers)

    # -- 15. executive dashboard (reads all other MIO fields; runs LAST) ----
    def _dashboard():
        mio["macro_dashboard"] = dashboard.build(core, mio)
    step("macro_dashboard", _dashboard)

    # engine stats stored on the MIO so a report can be rendered from the saved JSON alone
    mio["engine_stats"] = {
        "agreement": eng.get("agreement", 0.0),
        "n_bull": eng.get("n_bull", 0), "n_bear": eng.get("n_bear", 0),
        "conviction": eng.get("conviction"),
        "dissenters": eng.get("dissenters", []),
    }
    mio["overlay_applied"] = True
    if warnings:
        mio["overlay_warnings"] = warnings
    return mio
