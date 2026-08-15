"""
sector_factors.py — a per-sector ECONOMIC FACTOR LIBRARY.

The critique (macro strategist): the base engine assigns roughly the same 5-6 macro drivers
to every sector, so VIX dominates Banks and Kospi drives Metals — economically wrong. Real
desks give EACH sector its own factor model with the drivers that actually determine its
earnings and valuation. This module does that.

Each sector = a weighted list of factors, each factor tagged by KIND:
    macro       — computed from today's tape (oil, yields, USDINR, copper, DXY, FII, VIX...)
    regime      — the AI regime (Complement/Substitution) for IT
    catalyst    — detected from crawled news (USFDA, monsoon, govt capex, china stimulus...)
    fundamental — from parsed numbers (credit growth, NIM...) or listed "needs data" with sign

Score = Σ weightᵢ × signalᵢ × signᵢ over the ACTIVE factors, renormalized by the active
weight so a sector isn't penalised for factors with no data today. Weights/signs are PRIOR
(judgement priors from the review) until calibrated. No engine edits.
"""
from __future__ import annotations

import common
_CAPS = common.CAPS            # single source of truth (overlay/common.py)
_clamp = common.clamp


# ===========================================================================
# THE FACTOR LIBRARY.  factor = (label, weight, sign, kind, key)
#   sign: economic direction of the factor's effect on the SECTOR when the
#         underlying signal is POSITIVE (e.g. USDINR up = weaker rupee).
#   key:  how to source it — a macro key, "regime", or a catalyst keyword group.
# ===========================================================================
# catalyst keyword groups (news-detected), with an inherent direction handled below
CAT = {
    "earnings":        ["result", "q1", "q2", "q3", "q4", "profit", "pat ", "earnings", "beat", "miss"],
    "enterprise_it":   ["enterprise spending", "it spending", "tech spending", "deal win", "tcv",
                        "discretionary spend", "accenture", "cloud demand", "order book"],
    "windfall_policy": ["windfall tax", "opm", "gas price", "apm", "oil policy", "excise", "cess"],
    "govt_pricing":    ["price cut", "price hike", "retail price", "fuel price", "under-recovery"],
    "usfda":           ["usfda", "us fda", "483", "warning letter", "import alert", "anda", "approval"],
    "generic_pricing": ["price erosion", "generic pricing", "pricing pressure"],
    "china_api":       ["china api", "api cost", "key starting material"],
    "china_demand":    ["china pmi", "china stimulus", "china demand", "china property", "beijing stimulus"],
    "steel_ironore":   ["steel price", "iron ore", "hrc", "coking coal"],
    "monsoon_rural":   ["monsoon", "rural", "kharif", "msp", "rural wage", "rural demand"],
    "food_inflation":  ["food inflation", "cpi food", "wheat", "sugar", "edible oil price"],
    "palm_oil":        ["palm oil", "edible oil", "vegetable oil"],
    "consumer_demand": ["consumer demand", "festive demand", "discount", "volume growth", "footfall"],
    "ev_theme":        ["ev ", "electric vehicle", "e-scooter", "fame", "pli auto", "battery"],
    "govt_capex":      ["capex", "budget", "infrastructure", "order inflow", "l&t", "capital goods", "pli"],
    "ai_infra":        ["data centre", "data center", "ai infrastructure", "ai capex", "hyperscaler"],
    "power_demand":    ["peak demand", "electricity demand", "power demand", "grid"],
    "coal_gas":        ["coal price", "gas price", "lng", "fuel cost"],
    "spectrum":        ["spectrum", "trai", "tariff hike", "arpu"],
    "data_growth":     ["data usage", "5g", "subscriber", "broadband"],
    "credit_growth":   ["credit growth", "advances", "loan growth"],
    "mortgage":        ["home loan", "mortgage", "housing demand"],
    "cement_steel":    ["cement price", "steel price"],
    # --- new factors from the coefficient review ---
    "china_property":  ["china property", "evergrande", "china real estate", "property sector", "china developer"],
    "mfg_pmi":         ["manufacturing pmi", "global pmi", "ism manufacturing", "factory activity", "industrial activity"],
    "rural_wage":      ["rural wage", "mgnrega", "rural income", "farm income", "agri income"],
    "office_demand":   ["office demand", "commercial real estate", "office leasing", "grade a office", "office absorption", "reit"],
}

SECTOR_LIBRARY = {
    "Banks / Financials": [
        ("RBI / rate stress (hot CPI + yields)", 0.25, -1, "macro", "rate_stress"),
        ("Yield curve / treasury MTM", 0.20, -1, "macro", "us10y_pct"),
        ("Credit growth", 0.20, +1, "fundamental", "credit_growth"),
        ("Earnings", 0.15, +1, "catalyst", "earnings"),
        ("Deposit growth / CASA", 0.10, +1, "fundamental", "deposit_growth"),
        ("FII / DII flow", 0.05, +1, "macro", "fii_kcr"),
        ("Oil (indirect via inflation)", 0.03, -1, "macro", "oil_pct"),
        ("VIX (secondary)", 0.02, -1, "macro", "vix_pct"),
    ],
    "IT Services": [
        ("AI regime (Complement/Substitution)", 0.30, +1, "regime", "ai_regime"),
        ("US enterprise IT spend", 0.25, +1, "catalyst", "enterprise_it"),
        ("USDINR (weak rupee = export tailwind)", 0.20, +1, "macro", "usdinr_move"),
        ("Earnings", 0.15, +1, "catalyst", "earnings"),
        ("FII", 0.05, +1, "macro", "fii_kcr"),
        ("SOX (proxy only, secondary)", 0.02, +1, "macro", "sox_pct"),
    ],
    "Energy — Upstream": [
        ("Govt policy / windfall tax", 0.30, -1, "catalyst", "windfall_policy"),
        ("Brent LEVEL (realisations)", 0.30, +1, "macro", "oil_level"),
        ("Brent daily move", 0.20, +1, "macro", "oil_pct"),
        ("Gas prices", 0.10, +1, "catalyst", "coal_gas"),
        ("Earnings", 0.10, +1, "catalyst", "earnings"),
    ],
    "Energy — OMC (downstream)": [
        ("GRM / marketing margin", 0.35, +1, "fundamental", "needs_grm"),
        ("Govt retail pricing", 0.20, +1, "catalyst", "govt_pricing"),
        ("Brent (input cost, inverse)", 0.25, -1, "macro", "oil_pct"),
        ("Inventory gains/losses", 0.10, +1, "fundamental", "needs_inventory"),
        ("FII", 0.10, +1, "macro", "fii_kcr"),
    ],
    "Auto": [
        ("Consumer demand", 0.20, +1, "catalyst", "consumer_demand"),
        ("Earnings / execution", 0.20, +1, "catalyst", "earnings"),
        ("EV theme (structural)", 0.15, +1, "catalyst", "ev_theme"),
        ("Interest rates (financing)", 0.15, -1, "macro", "rate_stress"),
        ("Rural income / monsoon", 0.10, +1, "catalyst", "monsoon_rural"),
        ("Fuel prices (oil, inverse)", 0.10, -1, "macro", "oil_pct"),
        ("Commodity cost — steel/rubber/alu", 0.10, -1, "catalyst", "steel_ironore"),
    ],
    "Pharma": [
        ("USDINR (export)", 0.30, +1, "macro", "usdinr_move"),
        ("USFDA action", 0.30, -1, "catalyst", "usfda"),
        ("Generic drug pricing", 0.18, -1, "catalyst", "generic_pricing"),
        ("Earnings", 0.10, +1, "catalyst", "earnings"),
        ("China API cost (inverse)", 0.07, -1, "catalyst", "china_api"),
        ("FII (secondary)", 0.05, +1, "macro", "fii_kcr"),
    ],
    "Metals": [
        ("China demand / stimulus", 0.25, +1, "catalyst", "china_demand"),
        ("China property", 0.20, +1, "catalyst", "china_property"),
        ("Copper", 0.15, +1, "macro", "copper_pct"),
        ("Steel / iron ore", 0.15, +1, "catalyst", "steel_ironore"),
        ("Global manufacturing PMI", 0.15, +1, "catalyst", "mfg_pmi"),
        ("USD (strong USD = headwind)", 0.05, -1, "macro", "dxy_pct"),
        ("FII", 0.05, +1, "macro", "fii_kcr"),
    ],
    "FMCG": [
        ("Monsoon / rural demand", 0.20, +1, "catalyst", "monsoon_rural"),
        ("Palm / edible oil (input, inverse)", 0.20, -1, "catalyst", "palm_oil"),
        ("Rural wage", 0.15, +1, "catalyst", "rural_wage"),
        ("Food inflation", 0.15, -1, "catalyst", "food_inflation"),
        ("Crude derivatives — packaging (inverse)", 0.15, -1, "macro", "oil_pct"),
        ("Earnings", 0.10, +1, "catalyst", "earnings"),
        ("Consumer confidence", 0.05, +1, "fundamental", "needs_confidence"),
    ],
    "Realty": [
        ("RBI rates / mortgage cost (inverse)", 0.30, -1, "macro", "rate_stress"),
        ("Credit availability / mortgage demand", 0.20, +1, "catalyst", "mortgage"),
        ("Commercial office demand", 0.15, +1, "catalyst", "office_demand"),
        ("FII", 0.15, +1, "macro", "fii_kcr"),
        ("Income growth", 0.10, +1, "fundamental", "needs_income"),
        ("Cement / steel input (inverse)", 0.10, -1, "catalyst", "cement_steel"),
    ],
    "Capital Goods": [
        ("Government capex", 0.30, +1, "catalyst", "govt_capex"),
        ("AI infrastructure", 0.20, +1, "catalyst", "ai_infra"),
        ("Manufacturing PMI", 0.20, +1, "fundamental", "needs_pmi"),
        ("Power / copper (electrification)", 0.15, +1, "macro", "copper_pct"),
        ("China (secondary)", 0.05, +1, "catalyst", "china_demand"),
        ("FII", 0.10, +1, "macro", "fii_kcr"),
    ],
    "Telecom": [
        ("Data growth / AI usage", 0.40, +1, "catalyst", "data_growth"),
        ("Spectrum / tariff policy", 0.20, +1, "catalyst", "spectrum"),
        ("AI infrastructure", 0.15, +1, "catalyst", "ai_infra"),
        ("Capex intensity (inverse)", 0.15, -1, "catalyst", "govt_capex"),
        ("FII", 0.10, +1, "macro", "fii_kcr"),
    ],
    "Power & Utilities": [
        ("AI data centres / electricity demand", 0.35, +1, "catalyst", "ai_infra"),
        ("Peak / power demand", 0.15, +1, "catalyst", "power_demand"),
        ("Coal / gas prices (inverse)", 0.20, -1, "catalyst", "coal_gas"),
        ("Government policy", 0.15, +1, "catalyst", "govt_capex"),
        ("FII", 0.15, +1, "macro", "fii_kcr"),
    ],
}


_news_dir = common.news_direction     # single source of truth (overlay/common.py)


def _macro_value(key, sig):
    """Normalized macro signal in [-1, 1] for a factor key."""
    if key == "rate_stress":
        # tighter conditions = hot India CPI + rising US yields
        cpi = 1.0 if sig.get("india_cpi_hot") else 0.0
        y = _clamp((sig.get("us10y_pct") or 0) / _CAPS["us10y_pct"])
        return _clamp(0.6 * cpi + 0.4 * max(0, y))
    if key == "oil_level":
        # level amplifier (0.6..2.2) mapped to ~[-1,1] around the 1.0 normal band
        mult = sig.get("oil_mult") or 1.0
        return _clamp((mult - 1.0) / 1.2)
    if key == "usdinr_move":
        return _clamp((sig.get("usdinr_move") or 0) / _CAPS["usdinr_move"])
    v = sig.get(key)
    cap = _CAPS.get(key)
    if v is None or cap is None:
        return None
    return _clamp(v / cap)


# fundamental factor key -> the parsed metric that activates it
_FUND_METRIC = {"credit_growth": "Credit / advances growth", "deposit_growth": "Deposit growth"}


def _regime_mult(key: str, kind: str, ai_regime: str, risk_off: bool, inflation_on: bool) -> float:
    """Regime multiplier — the SAME factor matters more in some regimes. Base relationships
    stay fixed; only their EFFECTIVE influence adapts to the active regime."""
    m = 1.0
    if ai_regime == "Substitution" and key in ("ai_regime", "sox_pct", "ai_infra"):
        m *= 1.8            # AI-substitution makes AI/chip factors dominate the IT read
    elif ai_regime == "Complement" and key in ("ai_regime", "sox_pct", "ai_infra"):
        m *= 1.4
    if risk_off and key in ("vix_pct", "fii_kcr"):
        m *= 1.5            # in risk-off, volatility & flows drive everything
    if inflation_on and key in ("rate_stress", "oil_pct", "oil_level"):
        m *= 1.3            # in an inflation regime, rate/oil factors reinforce
    return m


def compute(signals: dict, news: list[dict], ai_regime: str, extracted: list[dict] | None = None,
            risk_off: bool = False) -> list[dict]:
    """Score every sector from its OWN factor library, using
        effective_weight = base_weight × activation × regime_multiplier
    so the relationships stay stable but their INFLUENCE adapts to the regime.
    Returns rich per-sector rows with the base/activation/regime decomposition."""
    extracted = extracted or []
    fund_by_metric = {m.get("metric"): m for m in extracted}
    # is an inflation regime active? (oil rising OR India CPI hot)
    inflation_on = (signals.get("oil_pct") or 0) > 1.5 or bool(signals.get("india_cpi_hot"))

    out = []
    for sector, factors in SECTOR_LIBRARY.items():
        active, needs_data, eff_sum, contrib_sum = [], [], 0.0, 0.0
        for label, base, sign, kind, key in factors:
            activation, direction = None, 0     # activation ∈ [0,1]; direction ∈ {-1,0,+1}
            if kind == "macro":
                val = _macro_value(key, signals)
                if val is not None and abs(val) >= 0.02:
                    activation = abs(val); direction = 1 if val > 0 else -1
            elif kind == "regime":
                r = {"Complement": 1.0, "Substitution": -1.0}.get(ai_regime, 0.0)
                if r != 0.0:
                    activation = 1.0; direction = 1 if r > 0 else -1
            elif kind == "catalyst":
                d = _news_dir(news, CAT.get(key, []))
                if d is not None:
                    activation = 1.0 if d != 0 else 0.4      # strong hit vs ambiguous mention
                    direction = d if d != 0 else 1
            elif kind == "fundamental":
                m = fund_by_metric.get(_FUND_METRIC.get(key)) if key in _FUND_METRIC else None
                if m and m.get("quality_direction"):
                    activation = min(1.0, m.get("impact_score", 0.3)); direction = m["quality_direction"]
                else:
                    needs_data.append({"factor": label, "base_weight": base, "sign": _s(sign)})
                    continue
            if activation is None:
                needs_data.append({"factor": label, "base_weight": base, "sign": _s(sign)})
                continue
            reg = _regime_mult(key, kind, ai_regime, risk_off, inflation_on)
            effective = base * activation * reg          # ← Base × Activation × Regime
            contrib = effective * sign * direction
            eff_sum += effective
            contrib_sum += contrib
            active.append({
                "factor": label, "base_weight": base, "activation": round(activation, 2),
                "regime_mult": round(reg, 2), "effective_weight": round(effective, 3),
                "contrib": round(contrib, 3),
            })
        denom = max(eff_sum, 0.5)                          # floor so thin coverage doesn't over-amplify
        score = round(contrib_sum / denom, 3) if eff_sum else 0.0
        verdict = ("🟢 Bullish" if score > 0.12 else "🔴 Bearish" if score < -0.12 else "🟡 Neutral")
        active.sort(key=lambda a: -abs(a["contrib"]))
        out.append({
            "sector": sector, "score": score, "verdict": verdict,
            "active_factors": active,
            "primary_drivers": [f[0] for f in factors[:4]],
            "needs_data": needs_data,
            "coverage": f"{len(active)}/{len(factors)} factors live",
            "regime_context": {"ai_regime": ai_regime, "risk_off": risk_off, "inflation_on": inflation_on},
        })
    out.sort(key=lambda s: -s["score"])
    return out


def _s(sign):
    return "＋" if sign > 0 else "－"
