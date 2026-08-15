"""
reporter.py — render the MIO as an institutional morning note.

Structured like a sell-side macro strategy note, separating FACTS / INTERPRETATION /
VALIDATION / FORWARD-VIEW into 8 sections:

    1 Executive Summary        (one screen + "why the model could be wrong")
    2 Market Regime & Dashboard(one merged environment table)
    3 Market Narratives        (why the market is moving — causal cards)
    4 Macro Drivers            (per-driver cards: level, activation, transmission, interactions)
    5 Sector Intelligence      (one ranked row per sector)
    6 Company Intelligence     (heavyweights first, then other movers)
    7 Relationship Validation  (did the textbook relationships hold today?)
    8 Quantitative Appendix    (interactions, analogues, calibration, causal graph, trace, raw data)

Wording is deliberately PROBABILISTIC — "lean" / "bias" / "modest tailwind" with an explicit
confidence — not deterministic. All numbers originate in the Core; this only renders the MIO.
"""
from __future__ import annotations

import sys
import datetime as dt
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1] / "mcp_server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import core  # noqa: E402

ms = core.ms
REPORT_DIR = Path(getattr(ms, "REPORT_DIR", Path(__file__).resolve().parents[2] / "reports"))


# ---------------------------------------------------------------------------
# probabilistic wording helpers
# ---------------------------------------------------------------------------
def _lean(verdict_or_dir: str) -> str:
    """Soften a deterministic label into a probabilistic 'lean/bias' phrasing."""
    v = (verdict_or_dir or "").strip()
    table = {
        "🟢 Bullish": "🟢 Bullish lean", "🔴 Bearish": "🔴 Bearish bias", "🟡 Neutral": "🟡 Balanced",
        "Bullish": "Bullish lean", "Bearish": "Bearish bias", "Neutral": "Balanced",
        "Up": "mild upside", "Down": "mild downside", "Flat": "flat", "Neutral ": "balanced",
        "🟢 Mild Bullish": "🟢 Mild bullish lean", "🔴 Mild Bearish": "🔴 Mild bearish bias",
        "🟢 Risk-On": "🟢 Risk-on tilt", "🔴 Risk-Off": "🔴 Risk-off tilt",
    }
    return table.get(v, v)


def _pct(x, nd=0):
    try:
        return f"{x*100:.{nd}f}%" if abs(x) <= 1 else f"{x:.{nd}f}%"
    except Exception:
        return str(x)


def _observed_sectors(mio: dict) -> dict:
    """Observed sector index moves from the tape (the ONLY source for led/lagged)."""
    out = {}
    for q in (mio.get("quotes_idx") or []):
        nm, pc = q.get("name"), q.get("pct_change")
        if nm in ("Bank Nifty", "Nifty IT") and isinstance(pc, (int, float)) and not q.get("suspect"):
            out["Banks / Financials" if nm == "Bank Nifty" else "IT"] = pc
    return out


# Display renames for the semis target buckets. "Cloud providers" was wrong for the
# Indian list — Bharti, Indus Towers, NTPC, Power Grid and Tata Power are not cloud
# providers, they are the telecom/power layer that AI infrastructure runs ON. The
# actual cloud providers are the global hyperscalers (AWS/Azure/GCP).
_SEMIS_RENAME = {
    "Cloud providers": "AI infrastructure enablers (telecom · power · data-centre)",
}

# Explicit causal path per regime — naming beneficiaries without the chain leaves the
# reader to infer WHY power and telecom belong in a semiconductor story.
_SEMIS_CHAIN = {
    "enterprise_budget_rotation": (
        "Enterprise AI budget rotation  (budget flat, destination changes)\n"
        "        │\n"
        "        ├─► GPU / server / networking spend ↑\n"
        "        │        └─► Nvidia · Broadcom · TSMC · Micron\n"
        "        │                 └─► EMS / electronics mfg (Dixon, Kaynes, CG Power)\n"
        "        │                          └─► data centres\n"
        "        │                                   └─► POWER + TELECOM demand ↑\n"
        "        │\n"
        "        └─► enterprise software / consulting budget ↓\n"
        "                 └─► deal pricing pressure, fewer billed hours\n"
        "                          └─► INDIAN IT SERVICES revenue headwind"),
    "ai_productivity_deflation": (
        "AI productivity gains\n"
        "        └─► same project needs fewer engineer-hours\n"
        "                 └─► services REVENUE deflation (demand intact)\n"
        "                          └─► Indian IT services pricing/volume headwind"),
    "_default": (
        "Semiconductor move\n"
        "        ├─► AI infrastructure chain (semis → EMS → data centre → power/telecom)\n"
        "        └─► software/services chain (enterprise budgets → IT services)"),
}


def _semis_verdict(lean: str, moves):
    """Did today's tape agree with the STRUCTURAL lean? Returns (verdict, agree, n).

    Deliberately separate from the thesis: this scores ONE session and must never be
    read as confirming or breaking a multi-quarter view."""
    if not moves:
        return "— no tape", 0, 0
    bull = "Bullish" in (lean or "")
    agree = sum(1 for _n, p in moves if (p > 0) == bull)
    n = len(moves)
    if agree == n:
        return "✅ Confirmed", agree, n
    if agree == 0:
        return "🔄 Overridden by short-term catalyst", agree, n
    return "⚠️ Mixed", agree, n


def _semis_cond_label(cond: str) -> str:
    """sox_drop_3 -> 'SOX fell more than 3%' — readers shouldn't decode slugs."""
    m = {"sox_drop_3": "SOX fell more than 3%", "sox_drop_2": "SOX fell more than 2%",
         "sox_rise_3": "SOX rose more than 3%", "sox_rise_2": "SOX rose more than 2%"}
    if cond in m:
        return m[cond]
    import re as _re
    g = _re.match(r"(\w+?)_(drop|rise)_(\d+)", cond or "")
    if g:
        return f"{g.group(1).upper()} {'fell' if g.group(2)=='drop' else 'rose'} more than {g.group(3)}%"
    return cond or "—"


def _market_narrative(mio: dict) -> str:
    """MODEL-SCORE narrative + an OBSERVED cross-check.

    BUGFIX: this previously said sectors 'led'/'lagged' using sector_factor_library
    scores. Those are MODELLED prior-weighted scores (Σ w×signal×sign), NOT realised
    returns — so on a day when the model scored Banks negative but Bank Nifty actually
    closed UP, the report claimed 'Banks lagged' while the decoupling section flagged
    the same banks as moving against it. Modelled ≠ observed; led/lagged is observed
    language and must come from the tape.
    """
    def _clean(nm):
        return (nm or "").replace(" — Upstream", " (upstream)").replace(" — OMC (downstream)", " (OMC)")
    sfl = [s for s in (mio.get("sector_factor_library") or []) if isinstance(s.get("score"), (int, float))]
    ups = sorted([s for s in sfl if s["score"] > 0.15], key=lambda s: -s["score"])[:2]
    dns = sorted([s for s in sfl if s["score"] < -0.15], key=lambda s: s["score"])[:2]
    cats = []
    for v in mio.get("validation", []) or []:
        d = v.get("override_discovered")
        if d and d.get("primary_override"):
            cats.append(d["primary_override"])
    cats = list(dict.fromkeys(cats))[:2]
    dash = mio.get("macro_dashboard", {}) or {}
    risks = [t["theme"].lower() for t in dash.get("macro_regime_card", [])
             if t.get("theme") in ("Geopolitics", "Oil") and str(t.get("direction", "")).startswith("🔴")]
    parts = []
    if ups:
        lead = ", ".join(_clean(s["sector"]) for s in ups)
        parts.append(f"model favours {lead}"
                     + (f" ({', '.join(c.lower() for c in cats)})" if cats else ""))
    if dns:
        parts.append("model penalises " + ", ".join(_clean(s["sector"]) for s in dns))
    body = "; ".join(parts) if parts else "no clear sector tilt in the model today"
    if risks:
        body += f". {', '.join(sorted(set(risks)))} the main macro risk"
    body = body[0].upper() + body[1:] + "."

    # ---- OBSERVED cross-check: what the tape actually did, + contradiction flag ----
    obs = _observed_sectors(mio)
    if obs:
        tape = ", ".join(f"{k} {v:+.2f}%" for k, v in sorted(obs.items(), key=lambda kv: -kv[1]))
        body += f" **Observed tape:** {tape}."
        clash = []
        for s in ups:
            for k, v in obs.items():
                if k.lower() in _clean(s["sector"]).lower() and v < -0.1:
                    clash.append(f"model favoured {k} but it closed {v:+.2f}%")
        for s in dns:
            for k, v in obs.items():
                if k.lower() in _clean(s["sector"]).lower() and v > 0.1:
                    clash.append(f"model penalised {k} but it closed {v:+.2f}%")
        if clash:
            body += (" ⚠️ **Model/tape contradiction:** " + "; ".join(clash)
                     + " — the model was overridden; trust the tape for what happened.")
    return body


def _agent_layer(result: dict) -> str:
    mio = result["mio"]
    val = result.get("validation", {})
    L: list[str] = []
    A = L.append

    # engine stats: prefer the MIO (so a saved-JSON render is complete), else the live Core
    es = mio.get("engine_stats")
    if es:
        agreement = es.get("agreement", 0.0)
        n_bull, n_bear = es.get("n_bull", 0), es.get("n_bear", 0)
        dissenters = es.get("dissenters", [])
    else:
        try:
            ce = core.causal_engine()
            agreement = ce.get("agreement", 0.0)
            n_bull, n_bear = ce.get("n_bull", 0), ce.get("n_bear", 0)
            dissenters = ce.get("dissenters", [])
        except Exception:
            agreement, n_bull, n_bear, dissenters = 0.0, 0, 0, []

    dash = mio.get("macro_dashboard", {}) or {}
    mp = dash.get("market_phase", {}) or {}
    rt = mio.get("relationship_tiers", {}) or {}
    reg = mio.get("regime", {}) or {}
    conf = mio.get("confidence", {}) or {}
    dom = mio.get("driver_dominance", {}) or {}

    # ==================================================================
    A("```")
    A("===================================================")
    A("            NEWS INTELLIGENCE AGENT")
    A("          Institutional Market Intelligence")
    A("===================================================")
    A("```")
    na = mio.get("news_acquisition", {})
    cov = ""
    if na:
        cov = (f" · news: {na.get('total_news','?')} items, "
               f"**{na.get('bodies_pulled',0)} full-text**"
               + (f", +{na['added']} from search" if na.get('added') else ""))
    # ---- LIVE vs REPLAY banner ------------------------------------------
    # A `--mock` run injects mcp_server/test_offline.MOCK (USDINR 83.5, Brent 92.3,
    # VIX 14.2 …) and until now produced a report VISUALLY IDENTICAL to a live one.
    # Every number was a fixture and nothing said so. That is the worst version of the
    # error class this report is built to avoid: not a mislabelled figure, but an
    # entire document that looks like today's market and isn't.
    _live = mio.get("live")
    if _live is None:
        _live = (result.get("snapshot") or {}).get("live")
    if _live is False:
        A("> # 🧪 REPLAY / MOCK DATA — NOT TODAY'S MARKET")
        A("> **Every price, flow and level below comes from a fixture snapshot "
          "(`mcp_server/test_offline.py`), not a live fetch.** Do not read any number "
          "here as a market fact. Re-run without `--mock` for live data.\n")

    A(f"> as-of **{mio.get('as_of')}** · provider `{result.get('provider')}` · "
      f"data **{'LIVE' if _live else 'REPLAY (mock)' if _live is False else 'unknown'}** · "
      f"MIO valid {val.get('valid')}" + (" · ⚠️ degraded" if mio.get("degraded") else "") + cov + "\n")

    # ---------------- 1 · EXECUTIVE SUMMARY ----------------
    A("## 1 · Executive Summary\n")
    A("```")
    A(f" Market Phase   : {mp.get('phase','—')}")
    A(f" Risk           : {mp.get('market_bias','—').replace('🟢','').replace('🔴','').strip()}")
    A(f" Liquidity      : {mp.get('liquidity','—')}")
    A(f" Growth         : {mp.get('growth','—')}")
    A(f" Inflation      : {mp.get('inflation','—')}")
    A(f" AI             : {mp.get('ai','—')}")
    A(f" Oil            : {mp.get('oil','—')}")
    A("```\n")
    tilt = rt.get("market_tilt", {})
    conflict = "High" if (n_bull and n_bear and min(n_bull, n_bear) / max(n_bull, n_bear) > 0.5) else "Moderate" if (n_bull and n_bear) else "Low"
    A(f"- **Current market (tape)**: {_lean(reg.get('primary','—'))}")
    A(f"- **Engine bias (forward lean)**: {_lean(tilt.get('direction','—'))} "
      f"({tilt.get('expected_move_pct', 0):+.2f}% expected)")
    A(f"- **Forecast confidence**: {int(conf.get('today_confidence',0)*100)}%")
    A(f"- **Model agreement**: {int(agreement*100)}%")
    A(f"- **Driver conflict**: {conflict} ({n_bull} supporting vs {n_bear} opposing)\n")

    # Today's story — SEPARATE the evidence narrative from the model coefficients (feature
    # importance ≠ causation: a +0.009 DXY weight is NOT "the market rallied on the dollar").
    prim = sorted(rt.get("primary", []), key=lambda p: p.get("contribution", 0))
    bear = prim[0] if prim else None
    bull = prim[-1] if prim else None
    dec = rt.get("decoupling", [])
    geo = next((t for t in dash.get('macro_regime_card', []) if t['theme'] == 'Geopolitics'), None)

    A("**Today's story**\n")
    A(f"📖 **Market narrative** (model tilt + observed tape): {_market_narrative(mio)}\n")

    A("_📊 Quantitative signals — model feature weights, NOT causal claims:_")

    def _mat(c):   # flag a near-zero coefficient as a non-mover
        return " · _marginal — not a market mover_" if abs(c) < 0.05 else ""
    if bull and bull.get("contribution", 0) > 0:
        c = bull["contribution"]
        A(f"- Strongest positive signal: **{bull['relationship']}** (model contribution {c:+.3f}){_mat(c)}")
    if bear and bear.get("contribution", 0) < 0:
        c = bear["contribution"]
        A(f"- Strongest negative signal: **{bear['relationship']}** (model contribution {c:+.3f}){_mat(c)}")
    A(f"- Dominant model driver: **{dom.get('dominant_driver','—')}** "
      f"({int(dom.get('dominant_driver_score',0)*100)}% of the modeled move)")
    if geo:
        A(f"- ⚠️ Primary macro risk: {geo['drivers']}")
    if dec:
        A(f"- 🎯 Notable decouplings: {', '.join(d['company'] for d in dec[:3])} "
          f"(own catalyst vs the **model's** market tilt — not vs the realised tape)")
    A("")

    # Why the model could be wrong today — the uncertainty section
    overrides = [v for v in mio.get("validation", []) if v.get("status") == "OVERRIDDEN"]
    A("> **⚠️ Why the model could be wrong today**  ")
    if geo and geo.get("score", 0) < -0.3:
        A(f"> - Largest uncertainty: geopolitics ({geo['drivers']})  ")
    if dissenters:
        # Name the driver in English and say WHAT it contradicts — a bare "us10y_pct"
        # tells a reader nothing about which way it is pulling or why that matters.
        _dl = {"us10y_pct": "US 10Y yield", "oil_pct": "Oil", "vix_pct": "India VIX",
               "dxy_pct": "Dollar index", "kospi_pct": "Kospi", "sox_pct": "US semis (SOX)",
               "fii_kcr": "FII flow", "usdinr_pct": "Rupee", "geopolitics_hits": "Geopolitics",
               "india_cpi_hot": "India CPI", "us_cpi_cool": "US CPI"}
        _d0 = dissenters[0]
        # STATE THE CONCRETE DIRECTIONS: which way the driver moved, and which way the
        # model leans — "pushing against the net direction" is meaningless without both.
        _mc = mio.get("market_context", {}) or {}
        _dmove = {"oil_pct": _mc.get("oil_pct_move"), "vix_pct": (mio.get("driver_dominance", {}) or {}).get("_vix"),
                  "usdinr_pct": None}.get(_d0)
        _net = (mio.get("expected_direction", {}) or {}).get("Nifty 50", "")
        _net_word = ("BEARISH (Nifty Down)" if _net == "Down"
                     else "BULLISH (Nifty Up)" if _net == "Up" else "the net direction")
        _mv = ""
        if _d0 == "oil_pct" and _mc.get("oil_pct_move") is not None:
            _o = _mc["oil_pct_move"]
            _mv = f"Oil {_o:+.1f}% ({'up' if _o > 0 else 'down'}) "
        A(f"> - Largest contradictory signal: **{_dl.get(_d0, _d0)}** — {_mv}is pulling the "
          f"OPPOSITE way to the model, which is net **{_net_word}**; the verdict rests on the "
          f"other drivers outweighing it  ")
    if overrides:
        # reason_discovery (enrich.py step 13c) already attached the evidence-backed
        # WHY to this same validation object. The section used to print only the edge
        # name and throw the rest away — surface it.
        _o = overrides[0]
        _edge = _o["edge"].replace("Economic relationship — ", "")
        # state the SETUP: which way oil moved and what that implied for the target, so
        # "moved against the expected direction" is concrete, not abstract.
        _es = _o.get("expected_sign")
        _exp_arrow = "↑" if _es == 1 else "↓" if _es == -1 else "?"
        _oilm = (mio.get("market_context", {}) or {}).get("oil_pct_move")
        _setup = ""
        if "oil" in _edge.lower() and _oilm is not None:
            _setup = f" _(oil {_oilm:+.1f}% → rule expected these {_exp_arrow})_"
        A(f"> - Relationship currently breaking: **{_edge}**{_setup}  ")
        _disc = _o.get("override_discovered") or {}
        _broke = _o.get("broke") or []
        if _broke:
            _names = ", ".join(f"{c.get('name')} {c.get('pct', 0):+.1f}%"
                               for c in _broke[:3] if c.get("name"))
            if _names:
                _dir = "up" if _es == 1 else "down" if _es == -1 else "as expected"
                A(f">   - What broke: rule expected them {_exp_arrow} ({_dir}), but "
                  f"{_names} did the opposite  ")
                # NOISE GUARD: a proxy that barely moved didn't "override" anything —
                # it's flat. A -0.1% BPCL on a -3.9% oil day is within intraday noise,
                # not a contrarian signal. Say so rather than over-claiming a break.
                _mvs = [abs(c.get("pct", 0) or 0) for c in _broke[:3] if c.get("name")]
                if _mvs and max(_mvs) < 1.0:
                    A(f">     - ⚠️ _near-flat (<1%) — this is within intraday noise, not a "
                      f"meaningful override; treat the 'break' as inconclusive_  ")
                # LEVEL CONTEXT for oil: the daily dip does not change the structural
                # picture while oil sits in a stress/crisis band.
                _band = (mio.get("market_context", {}) or {}).get("oil_level_band", "")
                _bp = (mio.get("market_context", {}) or {}).get("brent_price")
                if "oil" in _edge.lower() and _band and ("crisis" in _band or "stress" in _band):
                    A(f">     - _Oil is still ${_bp:.0f} ({_band}) — a one-day dip does NOT "
                      f"restore OMC margins; the structural import-cost pressure persists. "
                      f"Level = fundamental, daily move = trading noise; a stock can also just "
                      f"be bouncing after an overshoot._  ")
        if _disc.get("primary_override"):
            _conf = _disc.get("confidence", 0)
            _mark = "evidence-backed" if _o.get("override_evidenced") else "weak evidence"
            A(f">   - Discovered reason: **{_disc['primary_override']}** "
              f"({_mark} · confidence {_conf:.2f} · coverage {int(_disc.get('coverage',0)*100)}%)  ")
            # A catalyst that explains 1 of 3 broken names is a PARTIAL reason. Say so —
            # otherwise a single-name earnings print reads as the cause of a whole
            # cross-sector break (ICICI's PAT cannot explain a DLF realty move).
            if _disc.get("partial") and _disc.get("explains"):
                A(f">     - Explains only: {', '.join(_disc['explains'])} · "
                  f"**still unexplained: {', '.join(_disc.get('unexplained', []))}**  ")
            for _c in (_disc.get("candidates") or [])[:2]:
                _ev = (_c.get("evidence") or [""])[0]
                _srcs = ", ".join(_c.get("sources") or []) or "—"
                if _ev:
                    A(f">     - {_c.get('stars','')} {_c.get('catalyst')}: \"{_ev[:110]}\" "
                      f"_({_srcs})_  ")
        elif _o.get("override") or _o.get("reason_econ"):
            # reason_discovery found no NEWS evidence, but §7 still carries a MECHANISM
            # candidate + economic rationale. Saying "no evidence found" here while §7
            # prints "Likely override: X" and Company Intelligence names the catalyst
            # makes the same report contradict itself. Surface the mechanism, clearly
            # labelled as mechanism (not tape-confirmed).
            if _o.get("override"):
                _n = f" ({_o['override_note']})" if _o.get("override_note") else ""
                A(f">   - Likely override (**mechanism**, not news-confirmed): "
                  f"**{_o['override']}**{_n}  ")
            if _o.get("reason_econ"):
                A(f">     - Why: _{_o['reason_econ']}_  ")
            A(f">   - _No direct news evidence retrieved — this is the economic mechanism "
              f"that *can* override, not a confirmed cause. See §7 for the full validation._  ")
        else:
            # Genuinely nothing. Absence of evidence ≠ evidence of absence — say which.
            _s = _o.get("override_search") or {}
            if _s.get("attempted") and not _s.get("timed_out"):
                A(f">   - **Searched and found nothing**: web search ran over "
                  f"{_s.get('pool','?')} articles and no known catalyst matched. The break "
                  f"is real but unexplained — possibly a catalyst type we don't detect, or "
                  f"flow/positioning rather than news  ")
            elif _s.get("timed_out"):
                A(f">   - **Search incomplete** — {_s.get('why')}. We may simply have missed "
                  f"the reason; do NOT read this as 'no reason exists'  ")
            elif _s.get("why"):
                A(f">   - **Not searched** — {_s.get('why')}. Cause unknown because we did not "
                  f"look, not because nothing was found  ")
            else:
                A(f">   - No news evidence found and no mechanism candidate — cause genuinely "
                  f"unknown  ")
    A(f"> - Confidence tempered by: driver conflict **{conflict}**, model agreement {int(agreement*100)}%  ")
    A("")

    # ---------------- 1b · TOP HEADLINES (the STORY, not just the count) ----------
    # The report was all model output — it showed geopolitics as "10 headlines · SEVERE"
    # (a COUNT) and as transmission chains, but never the actual headline TEXT. So a
    # reader saw Brent's price move without the ceasefire / Hormuz story behind it. This
    # surfaces the macro-tagged headlines themselves, oil/geopolitics first.
    _snap = result.get("snapshot") or {}
    _news = _snap.get("news") or []
    _macro_news = [n for n in _news if n.get("macro")]
    if _macro_news:
        # rank: oil/geopolitics keywords first, then other macro
        _HOT = ("iran", "hormuz", "oil", "crude", "brent", "opec", "ceasefire", "tanker",
                "red sea", "houthi", "war", "strike", "sanction", "gulf", "blockade")
        def _rank(n):
            t = (n.get("title", "") + " " + n.get("tags", "")).lower()
            return (0 if any(k in t for k in _HOT) else 1, n.get("source", ""))
        _macro_news.sort(key=_rank)
        A("## 1b · Top headlines today\n")
        A("_The macro / geopolitics stories moving the tape — oil & Middle-East first. "
          "This is the news itself, not the model's read of it._\n")
        for n in _macro_news[:12]:
            title = n.get("title", "").strip()
            link = n.get("link", "")
            src = n.get("source", "")
            tags = n.get("tags", "")
            hot = "🛢️ " if any(k in (title + " " + tags).lower()
                               for k in ("iran", "hormuz", "oil", "crude", "opec", "ceasefire",
                                         "tanker", "red sea", "houthi", "gulf")) else "• "
            line = f"- {hot}[{title}]({link})" if link else f"- {hot}{title}"
            if src:
                line += f"  _({src})_"
            A(line)
        A("")

    # ---------------- 2 · MARKET REGIME & DASHBOARD ----------------
    A("## 2 · Market Regime & Dashboard\n")
    A("_One environment table (macro themes · dominant themes · institutional dashboard merged)._\n")
    idash = dash.get("institutional_dashboard", [])
    if idash:
        A("| Theme | Current | Direction | Strength | Horizon |")
        A("|---|---|---|---:|---|")
        for r in idash:
            cf = r.get("confidence")
            strength = f"{cf}%" if isinstance(cf, int) else "—"
            A(f"| {r['theme']} | {r['state']} | {_lean(r['bias'])} | {strength} | {r['horizon']} |")
        A("")

    # ---------------- 3 · MARKET NARRATIVES ----------------
    A("## 3 · Market Narratives — why the market is moving\n")
    mh = mio.get("transmission_multihop", [])
    reliab = {v.get("edge", "").lower(): v.get("reliability") for v in mio.get("validation", [])}
    # precise driver → linkage substring (avoids matching generic tokens like "us"/"india")
    _LINK_HINT = {"SOX": "sox", "Kospi": "kospi", "Oil": "producers", "US 10Y": "yields",
                  "FII flow": "fii flow", "USDINR": "rupee", "Dollar Index": "rupee"}
    _seen_drivers = {}
    for i, p in enumerate(mh[:4], 1):
        # Two narratives with the SAME driver (e.g. geopolitics→oil AND geopolitics→freight)
        # both show the DRIVER's dominance %, which reads like a double-count. Flag repeats:
        # the % is the driver's total shared across its channels, not additive.
        _drv = p['driver']
        _seen_drivers[_drv] = _seen_drivers.get(_drv, 0) + 1
        _chan = (f" — channel {_seen_drivers[_drv]} of the SAME {_drv} driver; the % below "
                 f"is that driver's total, NOT additive with the other {_drv} channel"
                 if _seen_drivers[_drv] > 1 else "")
        A(f"**Narrative {i} — {p['driver']}** _({p['branch']})_{_chan}  ")
        A(f"  {p['path']}  ")
        bits = [f"contribution today **{int(p.get('activation',0)*100)}%**"]
        hint = next((h for lbl, h in _LINK_HINT.items() if lbl in p['driver']), None)
        rel = next((r for name, r in reliab.items() if hint and hint in name and r), None)
        if rel and rel.get("value") is not None:
            bits.append(f"historical reliability {int(rel['hit_rate_pct'])}% (n={rel['n']})")
        A(f"  {' · '.join(bits)}\n")

    # ---------------- 4 · MACRO DRIVERS ----------------
    A("## 4 · Macro Drivers\n")
    amps = (mio.get("market_context", {}) or {}).get("amplifiers", {})
    inter = mio.get("interactions", [])
    dom_vec = dom.get("vector", {})
    # driver → its multihop chain + level + interactions
    shown = set()
    for p in mh[:6]:
        drv = p["driver"]
        if drv in shown:
            continue
        shown.add(drv)
        share = dom_vec.get(drv, 0.0)
        level = ""
        low = drv.lower()
        if "oil" in low and amps.get("oil"):
            a = amps["oil"]; level = f"level ${a.get('price')} ({a.get('band')}) ×{a.get('multiplier')}"
        elif "vix" in low and amps.get("vix"):
            a = amps["vix"]; level = f"level {a.get('price')} ({a.get('band')}) ×{a.get('multiplier')}"
        rel_int = [it["term"] for it in inter if drv.split()[0].lower() in " ".join(it["legs"]).lower()
                   or drv.split()[0] in it["term"]]
        A(f"**{drv}** — importance {int(share*100)}%" + (f" · {level}" if level else ""))
        A(f"  Transmission: {p['path']}")
        if rel_int:
            A(f"  Interactions: {', '.join(rel_int[:3])}")
        A("")

    # ---------------- 5 · SECTOR INTELLIGENCE ----------------
    A("## 5 · Sector Intelligence\n")
    lib = mio.get("sector_factor_library", [])
    if lib:
        rc = lib[0].get("regime_context", {})
        # 'risk-off' here is the OBSERVED TAPE (did Nifty actually fall today), used to
        # size the sector regime multiplier — it is NOT the §1 market-phase, which is the
        # model's FORWARD environment read. They can differ (defensive setup, calm tape)
        # without contradiction. Labelled so a reader doesn't read §1 vs §5 as a clash.
        A(f"_Effective weight = base × activation × regime. Context: AI **{rc.get('ai_regime')}**, "
          f"risk-off (today's tape) **{rc.get('risk_off')}**, inflation **{rc.get('inflation_on')}**. "
          f"(Tape risk-off ≠ §1 forward 'Risk-Off/Defensive' phase — different questions.)_\n")
        # Sub-sector divergence: the parent score can hide a sub-sector pulling the
        # OTHER way — e.g. Auto scores bearish on an ICE-weighted oil factor while its
        # EV sub-sector is strongly bullish (oil is +0.30 for EV, -0.30 for PV). The
        # sub-sector model already knows this; it just never reached the headline
        # verdict, so §5 and §8 contradicted each other with no reconciliation.
        _sub = {}
        for _p in (mio.get("subsector_factors") or []):
            _t = max(_p.get("sub_sectors") or [],
                     key=lambda s: s.get("net_computed", 0), default=None)
            if _t:
                _sub[_p["parent"]] = _t
        A("| Sector | Lean | Score | Confidence | Top live drivers | Sub-sector check |")
        A("|---|---|---:|---:|---|---|")
        for s in lib:
            cov = s.get("coverage", "")
            top = "; ".join(a["factor"].split("(")[0].strip() for a in s.get("active_factors", [])[:3]) or "—"
            # Match a sub-sector parent to this sector row. Names differ cosmetically
            # between the two models ("Banks/Financials" vs "Banks / Financials"), so
            # compare on alphanumerics only — a raw substring test silently missed them.
            def _norm(x):
                return "".join(ch for ch in (x or "").lower() if ch.isalnum())
            _sn = _norm(s["sector"])
            _m = next((v for k, v in _sub.items()
                       if _norm(k) and (_norm(k) in _sn or _sn.startswith(_norm(k)[:8]))),
                      None)
            _chk = "—"
            if _m:
                _pv, _sv = s.get("score", 0), _m.get("net_computed", 0)
                if (_pv > 0) != (_sv > 0) and abs(_sv) > 0.2:
                    _chk = (f"⚠️ **{_m['sub_sector']} {_sv:+.2f}** disagrees — "
                            f"sector score is mix-weighted")
                else:
                    _chk = f"{_m['sub_sector']} {_sv:+.2f} ✓"
            A(f"| {s['sector']} | {_lean(s['verdict'])} | {s['score']:+.2f} | {cov} | {top} | {_chk} |")
        A("")
        if any("disagrees" in r for r in L[-len(lib) - 1:]):
            A("_⚠️ A sector lean is a **weighted mix**. Where a sub-sector disagrees, the "
              "relationship's sign flips inside the sector (oil hurts ICE autos but helps EV "
              "running-cost economics), so the parent score averages away a real split. "
              "Trade the sub-sector, not the sector, on those rows._\n")

    # ---------------- 6 · COMPANY INTELLIGENCE ----------------
    A("## 6 · Company Intelligence\n")
    HEAVY = {"reliance", "hdfc bank", "icici", "infosys", "tcs", "bharti", "itc", "l&t", "larsen",
             "kotak", "axis", "sbi", "state bank", "bajaj fin", "hindustan unilever", "hul"}
    comps = mio.get("affected_companies", [])
    def _is_heavy(c):
        n = (c.get("company") or "").lower()
        return (c.get("nifty_weight") or 0) >= 0.02 or any(h in n for h in HEAVY)
    heavy = [c for c in comps if _is_heavy(c)]
    other = [c for c in comps if not _is_heavy(c)]
    if heavy:
        A("**Heavyweights** (largest index impact first):  ")
        A("  " + " · ".join(f"{c['company']} {_lean(c.get('direction'))}" for c in heavy) + "\n")
    if other:
        A("**Other movers:**  ")
        A("  " + " · ".join(f"{c['company']} {_lean(c.get('direction'))}" for c in other[:10]) + "\n")
    if dec:
        A("**Decoupling** (own catalyst overriding the market):  ")
        for d in dec:
            A(f"- **{d['company']}** — tape {d['market_says'].lower()}, stock {d['stock_expected'].lower()} "
              f"· _{d['catalyst'] if 'catalyst' in d else ''}_")
        A("")
    # broker views + scheme beneficiaries, if any
    pc = mio.get("policy_catalysts", {}) or {}
    for b in pc.get("broker_views", [])[:5]:
        stk = b.get("stock"); subj = stk["company"] if stk else b.get("subject", "market")
        A(f"- 🏦 {b['broker']} — {b['stance']} on {subj}"
          + (f" · target ₹{b['target_price']}" if b.get("target_price") else ""))
    if pc.get("broker_views"):
        A("")

    # ---------------- 7 · RELATIONSHIP VALIDATION ----------------
    A("## 7 · Relationship Validation — state, evidence & why\n")
    A("_Macro drives sectors; sectors drive companies. "
      "States: ✅ Confirmed · ⚠️ Partially · 🔄 Overridden · ⏸️ Inactive · ❓ Inconclusive._\n")

    vals = mio.get("validation", [])

    def _disp(e):
        return (e or "").replace("Economic relationship —", "").strip(" —")

    def _rel_str(v):
        rel = v.get("reliability")
        if rel and rel.get("value") is not None:
            return f" · reliability {int(rel['hit_rate_pct'])}% (n={rel['n']}, `{rel['tag']}`)"
        return ""

    # -- (a) summary table: one line per relationship, ranked (Overridden/Partial first) --
    any_candidate = False
    if vals:
        A("| Relationship | Level | Status | Likely override |")
        A("|---|---|---|---|")
        for v in vals:
            stl = v.get("state_label", v.get("status"))
            over = "—"
            if v.get("state") in ("🔄", "⚠️"):
                rk = v.get("override_ranking")
                over = rk[0][0] if rk else v.get("override", "—")
                if not v.get("override_evidenced"):
                    over += " \\*"          # structural candidate, not confirmed by today's tape
                    any_candidate = True
            A(f"| {_disp(v.get('edge'))} | {v.get('level', '—')} | {v.get('state','')} {stl} | {over} |")
        A("")
        if any_candidate:
            A("_\\* structural candidate — the mechanism that *can* override, not confirmed by "
              "today's tape. We don't hold per-stock flow/ownership data, so single-name "
              "attribution (e.g. \"DII bought X\") is never asserted._\n")

    # -- (b) expanded detail, grouped by level (Index → Macro → Sector → Company) --
    def _render_row(v):
        stl = v.get("state_label", v.get("status"))
        A(f"- {v.get('state','')} **{_disp(v.get('edge'))}** — {stl}{_rel_str(v)}")
        # Index / breadth rows report contribution, not a proxy list
        if v.get("contribution_pct") is not None:
            leaders = v.get("held") or v.get("broke") or []
            names = ", ".join(c["name"] for c in leaders[:3])
            n_tr = v.get("n_tracked") or 0
            top = v.get("top_contribution")
            rest = v.get("rest_contribution")
            net = v.get("net_contribution")

            # SHOW THE DECOMPOSITION, don't make the reader invert a ratio.
            # "127% of the net weighted move" is arithmetically fine and cognitively
            # awful: it is a percentage OF A NET, so the reader has to work backwards to
            # see that the remainder pushed the other way. Two rows and a total say it
            # outright.
            if top is not None and rest is not None and net is not None:
                pull = "pulled the index DOWN" if top < 0 else "pushed the index UP"
                push = "pushed UP" if rest > 0 else "pulled DOWN"
                fought = (top < 0) != (rest < 0)          # did they oppose each other?
                A(f"    - Evidence: **{names}** {pull}; the other {max(0, n_tr-3)} tracked "
                  f"names {push}. Index weight × move, in index-percentage points:")
                A("")
                A("        | Group | Contribution |")
                A("        |---|---:|")
                A(f"        | {names} (top 3) | **{top:+.2f}%** |")
                A(f"        | Other {max(0, n_tr-3)} heavyweights | {rest:+.2f}% |")
                A(f"        | **Net (25 tracked)** | **{net:+.2f}%** |")
                A("")
                if fought:
                    A(f"        → The two groups **worked against each other**. Three names "
                      f"outweighed the other {max(0, n_tr-3)} combined, so the "
                      f"{'decline' if net < 0 else 'gain'} is **narrow** — breadth was "
                      f"{'better' if net < 0 else 'worse'} than the headline suggests.")
                else:
                    A(f"        → Both groups moved the **same way** — a broad, "
                      f"well-participated move.")
                A(f"        _(Nifty itself {v.get('nifty_pct', 0):+.2f}%. The table covers the "
                  f"{n_tr} heavyweights we track, so it approximates rather than reproduces "
                  f"the official index move.)_")
                return

            # fallback if the decomposition isn't available
            share = v["contribution_pct"]
            A(f"    - Evidence: {names} account for ~{share}% of the net weighted move of "
              f"{n_tr or '?'} tracked heavyweights (Nifty {v.get('nifty_pct', 0):+.1f}%)."
              + ("  ⚠️ Above 100% means the rest of the basket moved the other way and "
                 "offset them — a narrow move." if share > 100 else ""))
            return
        # Inactive: the trigger was absent/too weak — don't imply the relationship fired
        if v.get("state") == "⏸️":
            A("    - _Trigger absent or too weak today — relationship not expected to fire._")
            return
        mb = v.get("metals_basket")
        if mb:
            comp = mb.get("composite")
            head = f"overall {mb.get('overall_label', 'n/a')}"
            if comp is not None:
                head += f" · tape composite {comp:+.2f}%"
            A(f"    - Metal sentiment: {head} · coverage {mb.get('coverage', '')}")
            parts = [f"{c['name']} {c['pct']:+.1f}%" for c in mb.get("components", []) if c.get("available")]
            if parts:
                A("        · tape: " + " · ".join(parts[:5]))
            nw = mb.get("news")
            if nw:
                A(f"        · news: {nw['label']} ({nw['bullish']}↑/{nw['bearish']}↓ of {nw['n_items']} items)")
                for e in nw.get("evidence", [])[:2]:
                    A(f"            – _{e}_")
        held, broke = v.get("held", []), v.get("broke", [])
        if held or broke:
            subj = _disp(v.get("edge")).split("→")[-1].strip()
            A(f"    - Expected: {subj} {v.get('exp_dir', '→')}")
            tot = len(held) + len(broke)
            if tot >= 2:   # sector-confirmation ratio first (macro→sector→company readability)
                A(f"    - Confirmation: {len(held)}/{tot} held ({round(100*len(held)/tot)}%)")
            obs = [f"{c['name']} {c['pct']:+.1f}% ✗" for c in broke[:6]] + \
                  [f"{c['name']} {c['pct']:+.1f}% ✓" for c in held[:6]]
            A("    - Observed: " + ", ".join(obs))
        disc = v.get("override_discovered")
        if disc and disc.get("candidates"):
            llm = disc.get("llm")
            primary = (llm or {}).get("primary") or disc["primary_override"]
            tag = "evidence-discovered + LLM-reranked" if llm else "evidence-discovered"
            _cov = f" · covers {int(disc.get('coverage', 0) * 100)}% of the break"
            A(f"    - Override ({tag} · conf {disc['confidence']:.2f}{_cov}): **{primary}**")
            if disc.get("partial") and disc.get("unexplained"):
                A(f"        - ⚠️ **Partial reason** — explains "
                  f"{', '.join(disc.get('explains', []))}; "
                  f"**{', '.join(disc['unexplained'])} still unexplained** "
                  f"(confidence already discounted for this)")
            for c in disc["candidates"][:3]:
                ev = f" — _{c['evidence'][0]}_" if c.get("evidence") else ""
                src = f" ({', '.join(c['sources'][:2])})" if c.get("sources") else ""
                exp = f" [explains: {', '.join(c['explains'])}]" if c.get("explains") else ""
                A(f"        - {c['stars']} {c['catalyst']} ({c['mentions']}×){exp}{ev}{src}")
            if llm and llm.get("rationale"):
                A(f"        - _LLM: {llm['rationale'][:160]}_")
        elif v.get("override_ranking"):
            A("    - Candidate overrides (ranked): " +
              " · ".join(f"{n} {s}" for n, s in v["override_ranking"]))
        elif v.get("override") and v.get("state") in ("🔄", "⚠️"):
            if v.get("override_evidenced"):
                note = f" ({v['override_note']})" if v.get("override_note") else ""
                A(f"    - Override: **{v['override']}**{note} — evidenced")
            else:
                note = f" — _{v['override_note']}_" if v.get("override_note") else ""
                A(f"    - Likely override: {v['override']}{note}")
        if v.get("reason_econ"):
            A(f"    - **Why:** _{v['reason_econ']}_")

    for lvl, title in (("Index", "Index / breadth"), ("Macro", "Macro relationships"),
                       ("Sector", "Sector relationships"), ("Company", "Company relationships"),
                       ("Structural", "Structural theme validation")):
        rows = [v for v in vals if v.get("level") == lvl]
        if not rows:
            continue
        A(f"\n### {title}\n")
        if lvl == "Structural":
            A("_Multi-year AI-infrastructure themes — context, not a daily trading signal; a single "
              "day's move does not confirm or break these._\n")
        for v in rows:
            _render_row(v)
    A("")

    # ---------------- 8 · QUANTITATIVE APPENDIX ----------------
    A("## 8 · Quantitative Appendix\n")

    if inter:
        A("### Interaction terms\n")
        A("| Term | Magnitude | Sign | Mechanism |")
        A("|---|---:|---:|---|")
        for it in inter:
            A(f"| {it['term']} | {it['magnitude']} `{it['tag']}` | {it['sign']:+d} | {it['mechanism']} |")
        A("")

    sr = mio.get("semis_regime")
    if sr:
        A("### Semiconductor cause analysis — why chips moved, mapped to 4 targets\n")
        A(f"- **Cause**: {sr.get('cause_label', sr['primary_cause'])} — _{sr.get('reasoning','')}_")
        A(f"- **AI demand**: {sr['ai_demand']} · **Allocation**: {sr['capital_allocation']}")
        # ---- transmission chain: make the causal path explicit, not just the names ----
        A("")
        A("**Transmission chain**\n")
        A("```")
        A(_SEMIS_CHAIN.get(sr.get("primary_cause"), _SEMIS_CHAIN["_default"]))
        A("```")

        tr = sr.get("target_reads", {})
        tc = sr.get("target_companies", {})
        if tr:
            # ---------------------------------------------------------------
            # HORIZON SPLIT. The old block printed one line per target mixing a
            # multi-QUARTER thesis with one DAY of prices, producing lines like
            # "🔴 Bearish Indian IT services → TCS +3.1%" — which reads as a
            # self-contradiction. A one-day rally cannot disprove a multi-quarter
            # thesis, so the two are now reported separately and reconciled in a
            # table at the end.
            # ---------------------------------------------------------------
            A("")
            A("#### A · Structural thesis (multi-quarter — where AI spend is flowing)\n")
            A("_Who benefits over coming quarters. No prices here by design._\n")
            for k, v in tr.items():
                info = tc.get(k, {})
                disp = _SEMIS_RENAME.get(k, k)
                names = [c["company"] for c in info.get("india", [])]
                parts = []
                if names:
                    parts.append("🇮🇳 " + ", ".join(names))
                if info.get("global"):
                    parts.append("🌐 " + ", ".join(info["global"]))
                A(f"- {v} **{disp}** → {' · '.join(parts) if parts else (info.get('note') or '—')}")
                if info.get("note"):
                    A(f"    _{info['note']}_")

            A("")
            A("#### B · Today's validation (one session — did the tape agree?)\n")
            A("_Whether today's price action was consistent with the thesis above. "
              "A disagreement flags a competing short-term catalyst, **not** a broken thesis._\n")
            _val = {}
            for k, v in tr.items():
                info = tc.get(k, {})
                moves = [(c["company"], c["pct"]) for c in info.get("india", [])
                         if c.get("pct") is not None]
                verdict, agree, n = _semis_verdict(v, moves)
                _val[k] = (verdict, agree, n)
                if not moves:
                    A(f"- **{_SEMIS_RENAME.get(k, k)}** → _no Indian tape (global-only bucket)_")
                    continue
                mv = ", ".join(f"{nm} {p:+.1f}%" for nm, p in moves[:6])
                A(f"- **{_SEMIS_RENAME.get(k, k)}** → {mv}  \n    → {verdict} ({agree}/{n} consistent)")

            # ---- reconciliation table: structural vs today, side by side ----
            A("")
            A("| Theme | Structural view (quarters) | Today's validation (1 session) |")
            A("|---|---|---|")
            for k, v in tr.items():
                verdict, agree, n = _val.get(k, ("— no tape", 0, 0))
                A(f"| {_SEMIS_RENAME.get(k, k)} | {v} | {verdict} |")
            A("")
            A("_The two columns answer different questions and are expected to diverge. "
              "'Overridden' means a stronger short-term catalyst (earnings, rupee, flows) "
              "dominated today — the structural thesis is unchanged until the **evidence** "
              "changes, not until a single day disagrees._")

        # ---- confidence, explained in words (a bare 0.14 is uninterpretable) ----
        cc = sr.get("confidence_components", {})
        _c = sr.get("confidence", 0)
        _band = ("High" if _c >= 0.5 else "Moderate" if _c >= 0.25 else "Low")
        A("")
        A(f"- **Confidence: {_band} ({_c})** = evidence quality {cc.get('evidence_quality')} × "
          f"agreement {cc.get('agreement')} × historical {cc.get('historical_accuracy')}")
        for _lbl, _key, _hi, _lo in (
                ("supporting evidence (company guidance / news)", "evidence_quality", 0.7, 0.4),
                ("agreement from today's market action", "agreement", 0.6, 0.35),
                ("historical support for this linkage", "historical_accuracy", 0.65, 0.5)):
            _v = cc.get(_key)
            if _v is None:
                continue
            _w = "Strong" if _v >= _hi else "Moderate" if _v >= _lo else "Low"
            A(f"    - {_w} {_lbl} ({_v})")
        A(f"    - _Low agreement drags the product down even when the evidence is strong — "
          f"that is the formula working, not a weak thesis. Read it as: good reason to "
          f"believe the mechanism, little confirmation from today's tape._")

        if sr.get("capex_signal"):
            A(f"- **Capex signal**: {sr['capex_signal']}")

        # ---- historical analogue, spelled out ----
        an = sr.get("historical_analogue")
        if an and an.get("stats"):
            tgt = (an["stats"].get("targets") or {}).get("Nifty IT") or next(
                iter((an["stats"].get("targets") or {}).values()), {})
            if tgt:
                A("")
                A(f"- **Historical analogue** — condition `{an['condition']}` "
                  f"({_semis_cond_label(an['condition'])})")
                A("")
                A("| Occurrences | Median next-day move | Fell next day |")
                A("|---:|---:|---:|")
                A(f"| {tgt.get('n')} | {tgt.get('median')}% | {tgt.get('hit_down')}% of the time |")
        A("")

    me = mio.get("macro_expectations")
    if me and me.get("us"):
        us, ind = me["us"], me.get("india", {})
        A("### Macro expectations (US · India · Europe)\n")
        def _ev(block, key):
            e = [x for x in (block.get(key, {}) or {}).get("evidence", []) if "no fresh" not in x.lower()]
            return f" · _{e[0]}_" if e else ""
        fed = us.get("rate_expectation", {})
        A(f"- 🇺🇸 **Fed {fed.get('expectation')}** · inflation {us.get('inflation',{}).get('trend')} "
          f"· jobs {us.get('labor',{}).get('trend')}{_ev(us,'rate_expectation')}")
        A(f"    - inflation:{_ev(us,'inflation') or ' no fresh print'}")
        A(f"    - jobs:{_ev(us,'labor') or ' no fresh print'}")
        rbi = ind.get("rate_expectation", {})
        A(f"- 🇮🇳 **RBI {rbi.get('expectation')}** · inflation {ind.get('inflation',{}).get('trend')}"
          f"{_ev(ind,'inflation')}")
        # ECB — a HOLD is read by its guidance, not the rate. A hawkish hold (oil-wary)
        # is an amplifier of the oil shock via global liquidity, not neutral.
        eu = (me.get("europe") or {}).get("rate_expectation", {})
        if eu:
            # expectation text already carries the tone; don't print "(neutral) (neutral)"
            _etone = eu.get("tone", "")
            _etail = "" if _etone in (eu.get("expectation", "") or "").lower() else f" · {_etone}"
            A(f"- 🇪🇺 **ECB {eu.get('expectation')}**{_etail}"
              f"{_ev({'x':eu},'x') if eu.get('evidence') else ''}")
            if eu.get("nifty_transmission"):
                A(f"    - _Nifty read: {eu['nifty_transmission']}_")
        A("")

    ssf = mio.get("subsector_factors")
    if ssf:
        A("### Sub-sector factor detail\n")
        for parent in ssf:
            top = max(parent["sub_sectors"], key=lambda s: s["net_computed"], default=None)
            if top:
                A(f"- **{parent['parent']}**: strongest sub-sector {top['sub_sector']} {top['verdict']} ({top['net_computed']:+.2f})")
        A("")

    amps = (mio.get("market_context", {}) or {}).get("amplifiers", {})
    if amps:
        A("### Level amplifiers\n")
        A("| Driver | Level | Band | ×Amplifier |")
        A("|---|---|---|---:|")
        for k, disp in (("oil", "Brent"), ("usdinr", "USDINR"), ("vix", "India VIX")):
            a = amps.get(k, {})
            if a.get("price") is not None:
                A(f"| {disp} | {a['price']} | {a['band']} | ×{a['multiplier']} |")
        A("")

    met = mio.get("metals_sentiment")
    if met and met.get("components"):
        A("### Global industrial metals cycle (feeds the Indian-steel relationship)\n")
        comp = met.get("composite")
        comp_str = f"{comp:+.2f}% ({met.get('label', 'n/a')})" if comp is not None else "n/a"
        A(f"_Overall: **{met.get('overall_label', met.get('label', 'n/a'))}** · "
          f"tape composite {comp_str} · coverage {met.get('coverage', '')}._\n")
        A("| Signal | Bucket | Read | Source |")
        A("|---|---|---:|---|")
        for c in met["components"]:
            mv = f"{c['pct']:+.2f}%" if c.get("available") else "n/a"
            A(f"| {c['name']} | {c['bucket']} | {mv} | {c.get('source', 'n/a')} |")
        nw = met.get("news")
        if nw:
            A(f"| Metal news sentiment | news | {nw['label']} ({nw['bullish']}↑/{nw['bearish']}↓) "
              f"| {nw['n_items']} items |")
        A("")
        if nw and nw.get("evidence"):
            A("_News evidence:_ " + " · ".join(f"_{e}_" for e in nw["evidence"][:3]) + "\n")
        if met.get("note"):
            A(f"_{met['note']}_\n")

    imp = mio.get("impact", {})
    if imp:
        A("### Directional lean by horizon (not blended)\n")
        A("| Horizon | Lean | Magnitude |")
        A("|---|---|---:|")
        for h in ("immediate", "short", "medium", "structural"):
            b = imp.get(h, {})
            A(f"| {h} | {_lean(b.get('direction'))} | {b.get('magnitude')} {b.get('unit','')} |")
        A("")

    g = mio.get("causal_graph")
    if g and g.get("nodes"):
        A(f"### Causal graph — {g['node_count']} nodes · {g['edge_count']} edges "
          f"(regime {g.get('ai_regime')})\n")
        active = sorted(g["nodes"], key=lambda n: -n["today_activation"])[:8]
        A("| Node | State | Activation | Confirmation |")
        A("|---|---|---:|---|")
        for n in active:
            A(f"| {n['id']} | {n.get('current_state') or '—'} | {n['today_activation']} | "
              f"{n.get('observed_confirmation') or '—'} |")
        A("")

    A(f"### Calibration source\n\n_{mio.get('calibration_source','—')}_\n")

    trace = result.get("agent_trace", [])
    if trace:
        A("### Agent execution trace\n")
        A("| Agent | Mode |")
        A("|---|---|")
        for t in trace:
            A(f"| {t['agent']} | `{t['mode']}` |")
        A("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
def _market_data_appendix() -> str:
    """Raw market-data (part of §8): price tables, flows, standout movers, earnings."""
    try:
        s = core._ensure()
    except Exception:
        # rendering from a saved MIO with no live snapshot — skip the raw-data appendix
        return "### Raw market data\n\n_(rendered from saved MIO — live price tables unavailable; " \
               "re-run the pipeline for the raw-data appendix)_\n"
    L, A = [], None
    out = ["### Raw market data\n"]
    A = out.append

    def _price_table(title, rows):
        A(f"**{title}**\n")
        A("| Instrument | Last | % chg |")
        A("|---|---:|---:|")
        for q in rows:
            if q.get("last") is None:
                continue
            pc = q.get("pct_change")
            A(f"| {q['name']} | {q['last']:g} | {pc:+.2f}% |" if pc is not None
              else f"| {q['name']} | {q['last']:g} | — |")
        A("")

    if s.get("quotes_idx"):
        _price_table("Indices & volatility", s["quotes_idx"])
    if s.get("quotes_macro"):
        _price_table("Cross-asset / macro", s["quotes_macro"])
    try:
        mv = core.standout_movers(4)
        if mv.get("gainers") or mv.get("losers"):
            A("**Standout movers (weight-adjusted)**\n")
            A("- 🟢 " + ", ".join(f"{x['name']} {x['pct_change']:+.1f}%" for x in mv["gainers"]))
            A("- 🔴 " + ", ".join(f"{x['name']} {x['pct_change']:+.1f}%" for x in mv["losers"]) + "\n")
    except Exception:
        pass
    flows = s.get("flows") or []
    if flows:
        A("**FII / DII flows**\n")
        A("| Category | Net (₹cr) |")
        A("|---|---:|")
        # ORDER MUST MATCH THE DASHBOARD. NSE returns these in its own order (DII first),
        # while §2 states them "FII …, DII …". Two tables listing the same two numbers in
        # opposite order reads as if the labels were swapped. Pin the order: FII, DII.
        def _forder(f):
            c = (f.get("category") or "").lower()
            return (0 if "fii" in c or "fpi" in c else 1 if "dii" in c else 2, c)
        _net_total = 0.0
        for f in sorted(flows, key=_forder):
            net = f.get("net")
            is_num = isinstance(net, (int, float)) and not isinstance(net, bool)
            if is_num:
                _net_total += net
            A(f"| {f.get('category')} | {net:+,.0f} |" if is_num else f"| {f.get('category')} | {net} |")
        if any(isinstance(f.get("net"), (int, float)) for f in flows):
            A(f"| **Net (FII+DII)** | **{_net_total:+,.0f}** |")
        A("")
        A("_Positive = net buying. FII and DII are stated in this order everywhere in the "
          "report; the net line is what actually hits the tape._\n")
    earn = s.get("earnings") or []
    if earn:
        A("**Earnings / results calendar**\n")
        for e in earn[:15]:
            nm = e.get("company") or e.get("symbol") or e.get("name") or "—"
            when = e.get("date") or e.get("when") or ""
            A(f"- {nm} {('· ' + str(when)) if when else ''}")
        A("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
def render_report(result: dict) -> str:
    """The 8-section institutional note (raw market data folded into §8)."""
    return _agent_layer(result) + "\n" + _market_data_appendix()


def save_report(result: dict, out_dir: str | Path | None = None) -> Path:
    d = Path(out_dir) if out_dir else REPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    path = d / f"news_agent_{stamp}.md"
    path.write_text(render_report(result), encoding="utf-8")
    return path
