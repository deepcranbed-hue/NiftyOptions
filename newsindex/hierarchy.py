#!/usr/bin/env python3
"""
hierarchy.py — the exposure registry + the missing SPINE: top-down hierarchical
resolution with sequential override.

What this adds (and what it deliberately REUSES)
-------------------------------------------------
The engine already has every ingredient of a sell-side hierarchical model — it just
runs them as parallel modules, so Oil, Earnings and Treasury each produce their own
override for the same stock. This file is the one missing layer: it walks the ontology
top-down and produces ONE explanation per company, built incrementally.

  REUSED, not rebuilt:
    taxonomy.py           → the ontology (company → subsector → sector), single source
    narrative dispatcher  → the active macro/narrative impulses (Oil +0.9, …)
    NarrativeSignal       → the unit company-specific signals arrive in
    reason_discovery      → the evidence behind a company override
  ADDED here:
    EXPOSURES             → company (or bucket) → {factor: sensitivity}, defined ONCE
    resolve_company()     → Country→Macro→Sector→Subsector→Company, with precedence

Sequential override — the precedence rule
-----------------------------------------
More specific information wins. A macro tailwind is the INHERITED prior; a company fact
(earnings, guidance, regulator) can offset or flip it. The final number is additive so
levels compound, but the EXPLANATION names which level dominated:

    Oil +5% → ONGC inherits +0.80 (macro)
    ONGC earnings beat → +0.50 (company)
    Final +1.30 — "macro tailwind, reinforced by the earnings beat"

    Oil +5% → ONGC inherits +0.80 (macro)
    ONGC cuts production guidance → -1.00 (company)
    Final -0.20 — "crude was supportive, but weak guidance OFFSET the macro tailwind"

One company, one path, one explanation — no duplicate override sections.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
import taxonomy as TAX  # noqa: E402
import factors as F      # noqa: E402  Layer 2 — canonical factor IDs


# ── EXPOSURE REGISTRY ─────────────────────────────────────────────────────────
# company/bucket → {factor: sensitivity in -1..+1}. Sensitivity is how strongly the
# name moves when that factor rises. DEFINED ONCE. Defaults are per-BUCKET (so all
# steel names share a profile); a per-company override refines a single name.
#
# factor names MUST match the narrative dispatcher's outputs + exposure drivers:
#   oil, dxy, usdinr, treasury, us10y, ai_infra, ai_substitution, us_tech_spend,
#   china_demand, iron_ore, copper, india_cpi, credit_growth, fii, ev_theme,
#   govt_capex, usfda, tariff, windfall_tax, marketing_margin, demand
# All PRIOR (judgement), consistent in sign with the calibrated betas.
BUCKET_EXPOSURES: dict[str, dict] = {
    "upstream_oil":   {"FACTOR_OIL_PRICE": +1.0, "FACTOR_RUPEE": +0.2, "FACTOR_WINDFALL_TAX": -0.6, "FACTOR_FII_FLOW": +0.2},
    "omc_downstream": {"FACTOR_OIL_PRICE": -0.4, "FACTOR_MARKETING_MARGIN": +0.6, "FACTOR_MARKETING_MARGIN": -0.3},
    "refiner_conglomerate": {"FACTOR_OIL_PRICE": +0.2, "FACTOR_RUPEE": +0.1, "FACTOR_FII_FLOW": +0.4},
    "it_services":    {"FACTOR_RUPEE": +0.8, "FACTOR_US_TECH_SPENDING": +0.9, "FACTOR_AI_SUBSTITUTION": -0.5,
                       "FACTOR_FINANCIAL_CONDITIONS": -0.3, "FACTOR_FII_FLOW": +0.4},
    # EMS is a semiconductor SUPPLY CHAIN → exposed to FACTOR_SEMI_CYCLE (so BOTH SOX and
    # Kospi reach it), plus AI capex. Deliberately NOT us_tech_spending — that's the IT
    # services demand channel, a different factor. This is the whole SOX/Kospi/IT/EMS
    # disambiguation, expressed as exposure wiring instead of relationship rows.
    "ems":            {"FACTOR_SEMI_CYCLE": +0.9, "FACTOR_AI_CAPEX": +0.4, "FACTOR_FII_FLOW": +0.3},
    "steel":          {"FACTOR_CHINA_CONSTRUCTION": +0.9, "FACTOR_IRON_ORE": -0.4, "FACTOR_GOVT_CAPEX": +0.6,
                       "FACTOR_OIL_PRICE": -0.1},
    "base_metals":    {"FACTOR_COPPER": +0.8, "FACTOR_USD": -0.6, "FACTOR_CHINA_CONSTRUCTION": +0.5, "FACTOR_FII_FLOW": +0.3},
    "private_bank":   {"FACTOR_INDIA_INFLATION": +0.1, "FACTOR_FINANCIAL_CONDITIONS": -0.2, "FACTOR_CREDIT_GROWTH": +0.6,
                       "FACTOR_FII_FLOW": +0.5, "FACTOR_GLOBAL_RISK": -0.4},
    "psu_bank":       {"FACTOR_INDIA_INFLATION": +0.1, "FACTOR_CREDIT_GROWTH": +0.5, "FACTOR_FII_FLOW": +0.4},
    "nbfc":           {"FACTOR_FINANCIAL_CONDITIONS": -0.4, "FACTOR_CREDIT_GROWTH": +0.6, "FACTOR_FII_FLOW": +0.4},
    "auto_ice":       {"FACTOR_OIL_PRICE": -0.4, "FACTOR_INDIA_INFLATION": -0.3, "FACTOR_FINANCIAL_CONDITIONS": -0.3, "FACTOR_CONSUMER_DEMAND": +0.5},
    "auto_ev":        {"FACTOR_OIL_PRICE": +0.2, "FACTOR_EV_THEME": +0.7, "FACTOR_FINANCIAL_CONDITIONS": -0.2, "FACTOR_CONSUMER_DEMAND": +0.4},
    "power":          {"FACTOR_AI_CAPEX": +0.5, "FACTOR_GOVT_CAPEX": +0.3},
    "telecom":        {"FACTOR_TELECOM_TARIFF": +0.6, "FACTOR_AI_CAPEX": +0.3, "FACTOR_FII_FLOW": +0.3},
    "oil_user_aviation": {"FACTOR_OIL_PRICE": -0.7, "FACTOR_CONSUMER_DEMAND": +0.5},
    "oil_user_paints":   {"FACTOR_OIL_PRICE": -0.5, "FACTOR_CONSUMER_DEMAND": +0.4},
    "pharma_export":  {"FACTOR_RUPEE": +0.6, "FACTOR_USFDA": -0.4},
    "fmcg":           {"FACTOR_CONSUMER_DEMAND": +0.6, "FACTOR_OIL_PRICE": -0.2},
    "capital_goods":  {"FACTOR_GOVT_CAPEX": +0.7, "FACTOR_FINANCIAL_CONDITIONS": -0.3},
}
# per-company refinements (only where a name differs from its bucket default)
COMPANY_EXPOSURES: dict[str, dict] = {
    "RELIANCE": {"FACTOR_OIL_PRICE": +0.3, "FACTOR_RUPEE": +0.1, "FACTOR_FII_FLOW": +0.4, "FACTOR_AI_CAPEX": +0.2},  # refiner + Jio + retail
    "INFY":     {"FACTOR_RUPEE": +0.8, "FACTOR_US_TECH_SPENDING": +0.9, "FACTOR_AI_SUBSTITUTION": -0.5, "FACTOR_FINANCIAL_CONDITIONS": -0.5},
}

# Converting a narrative's ACTIVATION (a magnitude) into per-factor SIGNED impacts.
# This is where regime direction lives — a single "AI +0.8" cannot say "infra up AND
# services-spend down" at once, which is the whole structural-split point. So a narrative
# maps to {factor: sign}; the resolver multiplies sign × activation × company exposure.
# Under AI-Substitution the caller flips us_tech_spend to negative (spend redirected
# AWAY from services), which is what makes IT services correctly bearish on a chip day.
NARRATIVE_FACTOR_SIGNS = {
    "Oil": {"oil": +1},
    "Geopolitics": {"oil": +1},             # reaches equities via the oil channel
    "Semiconductors / AI": {"ai_infra": +1, "ai_substitution": +1, "us_tech_spend": +1},
    "Treasury": {"treasury": +1, "us10y": +1},
    "RBI": {"india_cpi": +1, "treasury": +1},
    # "Earnings" is COMPANY-level, never a macro factor.
}


def factor_impacts(narrative_activations: dict, ai_regime: str = "Neutral",
                   observable_moves: dict | None = None) -> dict:
    """Signed {FACTOR_ID: impact} from BOTH channels, via the canonical factors.py:
      - observable price moves  (SOX/Kospi/Brent → factors, incl. the SOX-means-two split)
      - narrative activations    (news → factors that have no clean price series)
    Regime-aware. This replaces the local NARRATIVE_FACTOR_SIGNS map — factor wiring now
    lives in ONE place (factors.py), so nothing re-declares which observable hits which
    factor."""
    out = dict(F.activate_from_narratives(narrative_activations, ai_regime))
    if observable_moves:
        for fid, v in F.activate_from_observables(observable_moves).items():
            out[fid] = out.get(fid, 0.0) + v
    return out

# level weights: more specific information carries more, so a company fact can offset an
# inherited macro prior (the precedence rule, as additive weights).
LEVEL_WEIGHT = {"macro": 1.0, "sector": 0.8, "subsector": 0.7, "company": 1.4}


def exposures_of(sym: str) -> dict:
    """STRUCTURAL exposures: bucket default + per-company refinement. Always-true
    economic sensitivity, independent of today."""
    base = dict(BUCKET_EXPOSURES.get(TAX.bucket_of(sym), {}))
    base.update(COMPANY_EXPOSURES.get(sym, {}))
    return base


# ── CONTEXTUAL exposure — condition-dependent multipliers ─────────────────────
# The insight (yours): what we called "overrides" are really CONTEXT-DEPENDENT
# MODIFICATIONS TO EXPOSURE. ONGC's structural oil exposure is 1.0; when a windfall tax
# is active it only KEEPS ~0.6 of the crude tailwind. TCS captures 1.2× the tech-spend
# tailwind on a deal-win, 0.7× on weak guidance. These are multipliers on a factor's
# transmission, not separate additive rows — so the model is one equation:
#     impact = Σ(activation × structural_exposure × Π context_multipliers)
# condition flags are detected upstream (earnings plugin, news) and passed as a set.
CONTEXT_RULES: dict[str, list] = {
    "ONGC": [
        {"cond": "windfall_tax", "factor": "FACTOR_OIL_PRICE", "mult": 0.6,
         "note": "windfall tax skims the upstream gain"},
        {"cond": "production_outage", "factor": "FACTOR_OIL_PRICE", "mult": 0.5,
         "note": "can't capitalise on high crude if output is down"},
    ],
    "TCS": [
        {"cond": "deal_win", "factor": "FACTOR_US_TECH_SPENDING", "mult": 1.2,
         "note": "large deal wins amplify the tech-spend tailwind"},
        {"cond": "weak_guidance", "factor": "FACTOR_US_TECH_SPENDING", "mult": 0.7,
         "note": "weak guidance means less of the tailwind is captured"},
    ],
    "INFY": [
        {"cond": "weak_guidance", "factor": "FACTOR_US_TECH_SPENDING", "mult": 0.7,
         "note": "weak guidance dampens the tech-spend capture"},
    ],
}
# bucket-level context rules (apply to every member) — e.g. OMC margin regime
BUCKET_CONTEXT_RULES: dict[str, list] = {
    "omc_downstream": [
        {"cond": "price_freeze", "factor": "FACTOR_OIL_PRICE", "mult": 1.6,
         "note": "govt retail-price freeze turns crude into pure under-recovery"},
    ],
}


def context_multipliers(sym: str, conditions: set) -> dict:
    """{factor: product-of-multipliers} for the conditions active today. 1.0 = unaffected."""
    rules = list(BUCKET_CONTEXT_RULES.get(TAX.bucket_of(sym), [])) + list(CONTEXT_RULES.get(sym, []))
    out: dict[str, float] = {}
    applied: dict[str, list] = {}
    for r in rules:
        if r["cond"] in (conditions or set()):
            out[r["factor"]] = out.get(r["factor"], 1.0) * r["mult"]
            applied.setdefault(r["factor"], []).append((r["cond"], r["mult"], r["note"]))
    return {"mult": out, "applied": applied}


# ── THE SPINE: resolve one company top-down ───────────────────────────────────
def resolve_company(sym: str, factor_impacts: dict,
                    sector_signal: float = 0.0,
                    company_signals: list | None = None,
                    conditions: set | None = None) -> dict:
    """
    Build ONE hierarchical view for a company from the core equation:
        impact = Σ(factor activation × structural exposure × context multiplier)

    factor_impacts  : signed {factor: impact}   (from factor_impacts(); regime-aware)
    sector_signal   : net signed score for its sector   (from sector_analyzer)
    company_signals : [{"label","signed"}]  — idiosyncratic events with NO factor to
                      modify (fraud, M&A). Context-modifying events go via `conditions`.
    conditions      : set of active condition flags (windfall_tax, weak_guidance, …)

    Returns per-level contributions, final score, dominant level, and a plain-English
    explanation naming any context modifier or override.
    """
    exp = exposures_of(sym)
    ctx = context_multipliers(sym, conditions or set())
    levels = []
    ctx_notes = []
    # UNCERTAINTY: factor_impacts values may be Activation objects (strength+probability)
    # or bare floats (certain). expected_impacts folds probability in, so a 35%-likely
    # tariff rumor moves companies ~a third as hard as a certain oil print — no
    # special-case code in the resolver.
    impacts = F.expected_impacts(factor_impacts or {})

    # -- SYSTEMATIC: Σ(expected activation × structural exposure × context multiplier) --
    macro_terms = []
    macro_raw = 0.0
    for factor, impact in impacts.items():
        s = exp.get(factor)
        if not s:
            continue
        m = ctx["mult"].get(factor, 1.0)          # CONTEXTUAL multiplier
        c = s * impact * m
        if abs(c) < 0.02:
            continue
        label = factor + (f" ×{m:.1f}" if m != 1.0 else "")
        macro_terms.append((label, round(c, 3)))
        macro_raw += c
        if m != 1.0:
            for cond, mm, note in ctx["applied"].get(factor, []):
                ctx_notes.append(f"{cond} (×{mm}: {note})")
    macro = macro_raw * LEVEL_WEIGHT["macro"]
    levels.append(("Macro", round(macro, 3), macro_terms))

    # -- SECTOR / SUBSECTOR: net sector view, inherited ------------------------
    sec = sector_signal * LEVEL_WEIGHT["sector"]
    levels.append(("Sector", round(sec, 3),
                   [(TAX.sector_of(sym) or "—", round(sector_signal, 3))] if sector_signal else []))
    # subsector left as an explicit slot (0 unless a subsector narrative fires) so the
    # report structure is identical for every company — the hierarchy is always shown.
    levels.append(("Subsector", 0.0, []))

    # -- COMPANY: the override level (highest precedence) ----------------------
    comp_terms, comp_raw = [], 0.0
    for cs in (company_signals or []):
        v = cs.get("signed", 0.0)
        comp_terms.append((cs.get("label", "company"), round(v, 3)))
        comp_raw += v
    company = comp_raw * LEVEL_WEIGHT["company"]
    levels.append(("Company", round(company, 3), comp_terms))

    final = round(macro + sec + company, 3)
    contribs = {"Macro": macro, "Sector": sec, "Subsector": 0.0, "Company": company}
    dominant = max(contribs, key=lambda k: abs(contribs[k]))

    # explanation — sequential override + any context modifier
    expl = _explain(sym, contribs, final, macro, company)
    if ctx_notes:
        expl += " Context: " + "; ".join(sorted(set(ctx_notes))) + "."

    return {"symbol": sym, "sector": TAX.sector_of(sym),
            "subsector": TAX.subsector_of(sym), "bucket": TAX.bucket_of(sym),
            "levels": [{"level": n, "score": s, "terms": t} for n, s, t in levels],
            "final": final, "dominant_level": dominant,
            "context_applied": ctx["applied"],
            "verdict": ("🟢 Bullish" if final > 0.15 else "🔴 Bearish" if final < -0.15
                        else "🟡 Neutral"),
            "explanation": expl}


# Conceptual rename: the resolver PRICES a company (Expected Impact = Systematic +
# Idiosyncratic). resolve_company kept as an alias so existing callers/tests don't break.
price_company = resolve_company


def _explain(sym, c, final, macro, company) -> str:
    name = sym
    macro_word = "supportive" if macro > 0 else "a headwind"
    if macro and company and (macro > 0) != (company > 0):
        # macro and company disagree — the sequential-override case
        if abs(company - -macro) < 0.05:     # they (near-)exactly cancel
            return (f"{name}: the macro backdrop was {macro_word}, but company-specific news "
                    f"fully OFFSET it — net {final:+.2f} (neutral).")
        if abs(company) > abs(macro):
            return (f"{name}: the macro backdrop was {macro_word}, but company-specific news was "
                    f"stronger and OVERRODE it — the more specific signal wins, net {final:+.2f}.")
        return (f"{name}: company news leaned {'positive' if company > 0 else 'negative'}, but the "
                f"macro {'tailwind' if macro > 0 else 'headwind'} was larger — it only PARTLY "
                f"offset, net {final:+.2f}.")
    if macro and company:                    # same direction — reinforcement
        return (f"{name}: macro backdrop {macro_word} and company news agreed — "
                f"reinforcing to {final:+.2f}.")
    if company and not macro:
        return f"{name}: no material macro read today; driven by company-specific news ({final:+.2f})."
    if macro and not company:
        return f"{name}: inherited from the macro/sector backdrop ({final:+.2f}); no company override."
    return f"{name}: net {final:+.2f} across the hierarchy."


def to_markdown(view: dict) -> str:
    L = [f"### {view['symbol']} — {view['verdict']} ({view['final']:+.2f})",
         f"_{view['sector']} › {view['subsector']} · dominant level: **{view['dominant_level']}**_\n",
         "| Level | Score | Driver(s) |", "|---|---:|---|"]
    for lv in view["levels"]:
        terms = ", ".join(f"{lab} {v:+.2f}" for lab, v in lv["terms"]) or "—"
        L.append(f"| {lv['level']} | {lv['score']:+.2f} | {terms} |")
    L.append(f"| **Final** | **{view['final']:+.2f}** | |")
    L.append(f"\n_{view['explanation']}_")
    return "\n".join(L)
