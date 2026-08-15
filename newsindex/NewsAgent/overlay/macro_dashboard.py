"""
macro_dashboard.py — the institutional executive-summary layer.

Aggregates signals the overlay already computed into the top-of-report dashboards a trading
desk reads first:

  1. MARKET PHASE card   — one-glance environment (phase, liquidity, growth, inflation, AI,
                           oil, market bias, confidence).
  2. MACRO REGIME card   — 7 macro themes with a score + view + drivers (Liquidity, Growth,
                           Inflation, Monetary Policy, Geopolitics, Technology/AI, Valuation/Risk).
  3. DOMINANT THEMES     — ranked narratives with strength% + beneficiaries / losers.
  4. INSTITUTIONAL DASH  — per-theme Current State · Market Bias · Confidence · Horizon · Transmission.

Everything is derived from existing MIO fields + the Core, so it never invents numbers.
Scores are PRIOR (judgement weights) until calibrated.
"""
from __future__ import annotations


import common
_norm = common.norm            # single source of truth (overlay/common.py)


def _view(score, pos="🟢 Bullish", neu="🟡 Neutral", neg="🔴 Bearish", band=0.12):
    return pos if score > band else neg if score < -band else neu


def _conf(score, base=55, span=40, cap=92):
    return int(min(cap, base + span * abs(score)))


_cat_dir = common.news_direction     # single source of truth (overlay/common.py)


# geopolitical severity: routine tension << shipping disruption << war/closure/nuclear
_GEO_SEVERE = ["hormuz", "strait clos", "closes strait", "war declar", "invasion", "nuclear",
               "missile strike", "attack on", "blockade", "full-scale"]
_GEO_MED = ["sanction", "escalat", "strike", "drone", "red sea", "tanker", "conflict"]


def build(core, mio: dict) -> dict:
    eng = core.run_engine()
    d = eng.get("drivers", {})
    raw = eng.get("raw", {})
    news = core._ensure().get("news", [])
    fii = raw.get("fii")
    dii = raw.get("dii")
    vix = d.get("vix_pct") or 0.0
    oilp = d.get("oil_pct") or 0.0
    brent = eng.get("brent_price")
    agreement = eng.get("agreement", 0.0)
    # transparency: if the Brent quote was corrected (yfinance stale vs TradingEconomics),
    # note both so the reader can see WHY the level is what it is.
    _brent_note = ""
    for _q in (core._ensure().get("quotes_macro", []) or []):
        if _q.get("symbol") == "BZ=F" and _q.get("yfinance_raw") is not None:
            _brent_note = (f" _(yfinance read ${_q['yfinance_raw']:.0f} — stale; "
                           f"using TradingEconomics ${brent:.0f})_")
            break

    me = mio.get("macro_expectations", {})
    us = me.get("us", {})
    india = me.get("india", {})
    regime = mio.get("regime", {})
    ai_regime = (mio.get("semis_regime", {}) or {}).get("ai_regime") or \
        (regime.get("active", [None])[0] if regime.get("active") else None)
    observed_tone = regime.get("primary", "")
    lib = mio.get("sector_factor_library", [])

    # ---- helper reads from macro_expectations -----------------------------
    def infl_sign(block):
        t = (block.get("inflation", {}) or {}).get("trend", "Stable")
        return 1 if t == "Cooling" else -1 if t == "Rising" else 0

    def rate_sign(block, cb_key):
        exp = (block.get("rate_expectation", {}) or {}).get("expectation", "")
        e = exp.lower()
        if "cut" in e or "room to cut" in e:
            return 1
        if "hike" in e or "hawkish" in e or "higher-for-longer" in e:
            return -1
        return 0

    # ================= 7 MACRO THEMES =================
    # 1. Liquidity — DII vs FII + VIX
    liq = round(0.4 * _norm(dii, 4000) + 0.3 * _norm(fii, 4000) - 0.3 * _norm(vix, 12), 3)
    liq_drivers = (f"DII ₹{dii:+,.0f}cr {'cushioning' if (dii or 0) > 0 and (fii or 0) < 0 else 'vs'} "
                   f"FII ₹{fii:+,.0f}cr, VIX {vix:+.1f}%") if fii is not None else "flows n/a"

    # 2. Growth — explicit composition: 40% PMI, 30% earnings, 20% IIP, 10% freight
    #    (only active components count; renormalized by live weight)
    g_comp = [("PMI", 0.40, _cat_dir(news, ["pmi", "manufacturing pmi", "services pmi"])),
              ("Earnings", 0.30, _cat_dir(news, ["result", "profit", "earnings", "guidance"])),
              ("IIP", 0.20, _cat_dir(news, ["iip", "industrial production", "factory output"])),
              ("Freight", 0.10, _cat_dir(news, ["freight", "e-way", "cargo", "port volume", "gst collection"]))]
    g_live = [(lbl, w, v) for lbl, w, v in g_comp if v is not None]
    if g_live:
        gw = sum(w for _, w, _ in g_live)
        growth = round(sum(w * v for _, w, v in g_live) / gw, 3)
        growth_drivers = ", ".join(f"{lbl} {'＋' if v > 0 else '－' if v < 0 else '0'} ({int(w*100)}%)"
                                   for lbl, w, v in g_live)
    else:
        growth = round(0.2 * (1 if d.get("us_cpi_cool") else 0), 3)
        growth_drivers = "PMI 40% · Earnings 30% · IIP 20% · Freight 10% (no fresh prints today)"

    # 3. Inflation (market-positive when cooling)
    infl = round(0.4 * infl_sign(us) + 0.4 * infl_sign(india) - 0.2 * _norm(oilp, 4), 3)
    infl_drivers = f"US {us.get('inflation',{}).get('trend','?')}, India {india.get('inflation',{}).get('trend','?')}, oil {oilp:+.1f}%"

    # 4. Monetary policy (dovish = positive)
    mp = round(0.5 * rate_sign(us, "Fed") + 0.5 * rate_sign(india, "RBI"), 3)
    mp_drivers = f"Fed {us.get('rate_expectation',{}).get('expectation','?')}, RBI {india.get('rate_expectation',{}).get('expectation','?')}"

    # 5. Geopolitics — scale by SEVERITY, not just headline count. A shipping disruption is
    #    not a war; -1.0 is reserved for genuine escalation (Hormuz closure / war / nuclear).
    geo_hits = d.get("geopolitics_hits") or 0
    geo_text = " ".join((n.get("title", "") + " " + n.get("fulltext", "")).lower() for n in news)
    if any(k in geo_text for k in _GEO_SEVERE):
        sev_mult, sev_label = 2.5, "SEVERE (closure/war/nuclear risk)"
    elif any(k in geo_text for k in _GEO_MED):
        sev_mult, sev_label = 1.25, "elevated (sanctions/strikes/shipping)"
    elif geo_hits:
        sev_mult, sev_label = 0.55, "routine tension"
    else:
        sev_mult, sev_label = 0.0, "no fresh escalation"
    geo = round(-min(1.0, 0.08 * min(geo_hits, 5) * sev_mult), 3) if geo_hits else 0.05
    geo_drivers = f"{int(geo_hits)} headline(s) · {sev_label}"

    # 6. Technology / AI (infrastructure vs services)
    regime_val = 0.3 if ai_regime == "Complement" else -0.1 if ai_regime == "Substitution" else 0.0
    tech = round(0.4 * _norm(d.get("sox_pct"), 3) + 0.3 * regime_val + 0.3 * 0.2, 3)  # +0.2 infra bias
    tech_drivers = f"SOX {d.get('sox_pct',0):+.1f}%, AI regime {ai_regime} (infra > services)"

    # 7. Valuation / Risk (risk-on when VIX falling)
    risk = round(-_norm(vix, 12), 3)
    risk_drivers = f"VIX {vix:+.1f}% ({'falling → risk-on' if vix < 0 else 'rising → risk-off' if vix > 0 else 'flat'})"

    regime_card = [
        {"theme": "Liquidity",       "score": liq,   "view": _view(liq),  "drivers": liq_drivers},
        {"theme": "Growth",          "score": growth, "view": _view(growth), "drivers": growth_drivers},
        {"theme": "Inflation",       "score": infl,  "view": _view(infl, neu="🟡 Watch"), "drivers": infl_drivers},
        {"theme": "Monetary Policy", "score": mp,    "view": _view(mp, neu="🟡 Neutral"), "drivers": mp_drivers},
        {"theme": "Geopolitics",     "score": geo,   "view": _view(geo, pos="🟢 Improving"), "drivers": geo_drivers},
        {"theme": "Technology / AI", "score": tech,  "view": _view(tech, pos="🟢 Selective"), "drivers": tech_drivers},
        {"theme": "Valuation / Risk","score": risk,  "view": _view(risk, pos="🟢 Risk-On", neg="🔴 Risk-Off"), "drivers": risk_drivers},
    ]

    # ================= MARKET PHASE =================
    overall = round(sum(t["score"] for t in regime_card) / len(regime_card), 3)
    if liq > 0.1 and risk > 0.05 and overall > 0.05:
        phase = "Early Risk-On Recovery"
    elif risk < -0.1 or liq < -0.1 or overall < -0.1:
        phase = "Risk-Off / Defensive"
    else:
        phase = "Range / Consolidation"
    bias = ("🟢 Bullish" if overall > 0.15 else "🟢 Mild Bullish" if overall > 0.05 else
            "🔴 Bearish" if overall < -0.15 else "🔴 Mild Bearish" if overall < -0.05 else "🟡 Neutral")
    phase_conf = int(min(90, 50 + 100 * abs(overall) * 0.5 + agreement * 20))

    market_phase = {
        "phase": phase,
        "liquidity": _word(liq), "growth": _word(growth),
        "inflation": ("Contained" if infl >= 0 else "Watch"),
        "ai": ("Infrastructure Leadership" if tech > 0 else "Mixed"),
        # A bare "n/a" hid a real degradation: when the Brent LEVEL is missing, the band
        # table, the level amplifier and every level-scoped sector go dormant while the
        # % move still flows. Say so, and say what we still have.
        # Three distinct states, previously collapsed into "n/a" then into a fake "+0.0%":
        #   level present            → normal
        #   level from prev close    → usable, flagged
        #   NO BRENT DATA AT ALL     → an OUTAGE, not a 0.0% oil day
        "oil": (f"Brent ${brent:.0f} — {core.ms._oil_band_label(brent)}"
                + (f" (day {oilp:+.1f}%)" if oilp is not None else "") + _brent_note
                + (" _(prev close — live level unavailable)_"
                   if eng.get("brent_src") == "prev_close" else "")
                if brent else
                ("❌ **NO BRENT DATA** — quote fetch failed and no fallback source returned "
                 "a price. Oil shows 0.0% because it is MISSING, not because it was flat; "
                 "band table, ×level amplifier and all oil-driven sector scores are inactive "
                 "this run."
                 if "Brent" in (eng.get("missing_drivers") or []) else
                 f"{oilp:+.1f}% today · ⚠️ level unavailable — band/amplifier inactive")),
        "market_bias": bias, "confidence": phase_conf, "overall_score": overall,
    }

    # ================= DOMINANT THEMES (ranked) =================
    THEME_BENEF = {
        "Liquidity":       (["Banks", "Large Caps"], ["Gold"]),
        "Growth":          (["Cyclicals", "Capital Goods"], []),
        "Inflation":       (["Consumer", "Auto", "Banks"], ["Upstream Energy"]),
        "Monetary Policy": (["Rate-sensitives", "Realty", "Banks"], ["USD exporters"]),
        "Geopolitics":     (["Airlines", "Paints"], ["Upstream Energy"]),
        "Technology / AI": (["EMS", "Power", "Telecom", "Capital Goods"], ["IT Services"]),
        "Valuation / Risk":(["High-beta", "Banks"], ["Gold", "Defensives"]),
    }
    ranked = sorted(regime_card, key=lambda t: -abs(t["score"]))[:5]
    dominant = []
    for i, t in enumerate(ranked, 1):
        benef, losers = THEME_BENEF.get(t["theme"], ([], []))
        # flip benef/losers if the theme score is negative
        if t["score"] < 0:
            benef, losers = losers, benef
        direction = ("improving" if t["score"] > 0 else "deteriorating" if t["score"] < 0 else "stable")
        dominant.append({
            "rank": i, "theme": f"{t['theme']} {direction}",
            "strength": _conf(t["score"], base=58, span=37),
            "beneficiaries": benef or ["—"], "losers": losers or ["—"],
        })

    # ================= INSTITUTIONAL DASHBOARD (Version 1) =================
    usdinr_px = None
    try:
        usdinr_px = core.ms._last_of(core._ensure().get("quotes_macro", []), "USD/INR")
    except Exception:
        pass
    rows = [
        {"theme": "Institutional Flows",
         "state": (f"FII ₹{fii:+,.0f}cr, DII ₹{dii:+,.0f}cr" if fii is not None else "n/a"),
         "bias": _view(liq), "confidence": _conf(liq), "horizon": "1–5 Days",
         "transmission": "Liquidity → Large Caps"},
        {"theme": "Global Rates (Fed)",
         "state": us.get("rate_expectation", {}).get("expectation", "?"),
         "bias": _view(rate_sign(us, "Fed"), neu="🟡 Neutral"), "confidence": us.get("rate_expectation", {}).get("confidence", 0.5) and int(us["rate_expectation"]["confidence"] * 100),
         "horizon": "Weeks",
         # direction-aware: a CUT lowers yields+USD → EM/FII INFLOWS (not outflows)
         "transmission": ("Fed cut → US yields ↓ → USD ↓ → EM/FII inflows ↑ → Nifty ↑"
                          if rate_sign(us, "Fed") > 0 else
                          "Fed hike → US yields ↑ → USD ↑ → FII outflows → Nifty ↓"
                          if rate_sign(us, "Fed") < 0 else
                          "On hold → rate path data-dependent")},
        {"theme": "RBI Policy",
         "state": india.get("rate_expectation", {}).get("expectation", "?"),
         "bias": _view(rate_sign(india, "RBI"), neu="🟡 Neutral"), "confidence": 65,
         "horizon": "Weeks", "transmission": "Rates → Banks / Realty"},
        {"theme": "Oil",
         "state": (f"Brent ${brent:.0f} ({oilp:+.1f}%)" if brent else f"{oilp:+.1f}%"),
         "bias": ("🟡 Inflation Watch" if oilp > 0 else "🟢 Positive"), "confidence": 70,
         "horizon": "Days–Weeks", "transmission": "Inflation → RBI → Banks"},
        {"theme": "Geopolitics",
         "state": (f"{int(geo_hits)} headline(s)" if geo_hits else "Stable"),
         "bias": _view(geo, pos="🟢 Positive"), "confidence": _conf(geo), "horizon": "Days",
         "transmission": "Oil Risk"},
        {"theme": "Inflation",
         "state": f"India {india.get('inflation',{}).get('trend','?')}, US {us.get('inflation',{}).get('trend','?')}",
         "bias": _view(infl, neu="🟡 Watch"), "confidence": _conf(infl, base=60), "horizon": "Months",
         "transmission": "Consumer → Banks"},
        {"theme": "USDINR",
         "state": (f"₹{usdinr_px:.1f}" if usdinr_px else "n/a"),
         "bias": ("🟢 IT Positive" if (raw.get("usdinr") or 0) > 0 else "🟡 Neutral"), "confidence": 68,
         "horizon": "Days", "transmission": "Exporters ↑"},
        {"theme": "AI Theme (Infra)",
         "state": "AI Infrastructure " + ("Strong" if tech > 0 else "Mixed")
                  + " — Semis · EMS · Power · Telecom · Software vs IT-Services",
         "bias": "🟢 EMS / Power / Telecom", "confidence": _conf(tech, base=60), "horizon": "Quarters",
         "transmission": "Capex Cycle → Semis/EMS/Power/Data-centres"},
        {"theme": "AI Services",
         "state": f"AI {ai_regime}",
         "bias": ("🔴 IT Services" if ai_regime == "Substitution" else "🟢 IT Services" if ai_regime == "Complement" else "🟡 Neutral"),
         "confidence": 70, "horizon": "Quarters", "transmission": "Consulting Pressure"},
        {"theme": "Market Regime",
         "state": observed_tone or "Mixed",
         "bias": _view(overall), "confidence": phase_conf, "horizon": "Intraday",
         "transmission": "Broad Participation"},
    ]

    return {
        "market_phase": market_phase,
        "macro_regime_card": regime_card,
        "dominant_themes": dominant,
        "institutional_dashboard": rows,
    }


def _word(score):
    return "Positive" if score > 0.05 else "Negative" if score < -0.05 else "Neutral"
