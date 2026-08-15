"""
semis_regime.py — conditional causal engine for SOX / KOSPI → Indian IT.

A SOX drop does NOT uniquely mean "AI demand is weak" — that label is usually WRONG. Today's
reality (IBM/Accenture, Gartner, "AI deflation" in Indian IT) is that AI demand is STRONG, but
(a) enterprise budgets are ROTATING from software/consulting into GPUs/servers/networking, and
(b) AI changes the ECONOMICS of service delivery (fewer billable hours) — so Indian IT feels
near-term pressure via the revenue model, not via collapsing technology demand.

So the causes are separated into distinct regimes, each with different implications, and each
mapped SEPARATELY to: AI infrastructure (semis/EMS/power) · Enterprise software · Indian IT
services · Cloud providers.

    ① Enterprise AI budget rotation   — demand strong, spend shifts HW↑/SW↓ (the IBM case)
    ② AI productivity / services deflation — demand strong, fewer billable hours → services down
    ③ AI demand accelerating          — infra wins, services lag
    ④ Semiconductor valuation correction — profit-taking / PE reset, no demand change
    ⑤ True AI demand slowdown (RARE)  — needs multi-layer confirmation (capex cut + cloud + orders)
    ⑥ Macro derating                  — rates/USD/risk-off compress tech valuations

Evidence is built by LAYER (news 30% · market 30% · fundamentals 25% · guidance 15%) so no
single headline decides a regime. Confidence = evidence_quality × agreement × historical.
"""
from __future__ import annotations

# ---- cause map: each regime → its separate reads --------------------------
CAUSES = {
    "enterprise_budget_rotation": {
        "label": "Enterprise AI budget rotation",
        "ai_demand": "Strong", "allocation": "Infrastructure (from software/services)",
        "indian_it": "Bearish (near-term)",
        "targets": {"AI infrastructure (semis/EMS/power)": "🟢 Bullish", "Enterprise software": "🔴 Bearish",
                    "Indian IT services": "🔴 Bearish", "Cloud providers": "🟢 Bullish"},
        "note": "budget constant but redirected to GPUs/servers/networking — software & consulting "
                "squeezed. AI demand is NOT weak (Reuters: IBM says spend redirected, not abandoned)."},
    "ai_productivity_deflation": {
        "label": "AI productivity / services deflation",
        "ai_demand": "Strong", "allocation": "Productivity",
        "indian_it": "Bearish (revenue model, not demand)",
        "targets": {"AI infrastructure (semis/EMS/power)": "🟡 Neutral", "Enterprise software": "🟡 Neutral",
                    "Indian IT services": "🔴 Bearish", "Cloud providers": "🟢 Bullish"},
        "note": "customers still buy AI, but a project bills fewer engineer-hours → services REVENUE "
                "deflation (Moneycontrol: 'AI deflation'), not falling technology demand."},
    "ai_demand_accelerating": {
        "label": "AI demand accelerating",
        "ai_demand": "Strong", "allocation": "Infrastructure",
        "indian_it": "Mixed (infra wins, services lag)",
        "targets": {"AI infrastructure (semis/EMS/power)": "🟢 Bullish", "Enterprise software": "🟡 Neutral",
                    "Indian IT services": "🔴 Bearish", "Cloud providers": "🟢 Bullish"},
        "note": "AI capex accelerating (Gartner: cloud/AI-infra spend growing) — infra beneficiaries "
                "win; services relatively lag."},
    "semiconductor_valuation_correction": {
        "label": "Semiconductor valuation correction / profit-taking",
        "ai_demand": "Unchanged", "allocation": "Unchanged",
        "indian_it": "Neutral→Bullish",
        "targets": {"AI infrastructure (semis/EMS/power)": "🔴 Bearish (positioning)", "Enterprise software": "🟡 Neutral",
                    "Indian IT services": "🟡 Neutral", "Cloud providers": "🟡 Neutral"},
        "note": "PE reset / profit-taking after a rally; no change in AI demand → the services thesis "
                "is intact, a dip is more positioning than a demand problem."},
    "true_ai_demand_slowdown": {
        "label": "True AI demand slowdown (RARE)",
        "ai_demand": "Weak", "allocation": "Contracting",
        "indian_it": "Bearish",
        "targets": {"AI infrastructure (semis/EMS/power)": "🔴 Bearish", "Enterprise software": "🔴 Bearish",
                    "Indian IT services": "🔴 Bearish", "Cloud providers": "🔴 Bearish"},
        "note": "genuine demand contraction — only when hyperscaler capex CUT + cloud slowdown + chip-"
                "order cut + enterprise-spend all confirm. Rarely the right call."},
    "macro_derating": {
        "label": "Macro-driven tech derating",
        "ai_demand": "Unchanged", "allocation": "Unchanged",
        "indian_it": "Bearish (valuation, not fundamentals)",
        "targets": {"AI infrastructure (semis/EMS/power)": "🔴 Bearish", "Enterprise software": "🔴 Bearish",
                    "Indian IT services": "🔴 Bearish", "Cloud providers": "🔴 Bearish"},
        "note": "rates/USD/risk-off compress long-duration tech valuations — a discount-rate story, "
                "not a demand or allocation story."},
}

# ---- keyword banks (per evidence layer) -----------------------------------
_CHIP = ["samsung", "sk hynix", "hynix", "micron", "tsmc", "nvidia", "amd", "intel", "asml", "sox", "semiconductor"]
_HYPER = ["microsoft", "amazon", "aws", "google", "alphabet", "meta", "hyperscaler", "oracle"]
_IT = ["accenture", "ibm", "infosys", "tcs", "wipro", "hcltech", "cognizant", "tech mahindra", "coforge"]

_KW = {
    "rotation":   ["redirect", "reallocat", "shift to infrastructure", "shift toward ai", "budget rotation",
                   "software budget", "squeez", "diverting", "prioritis", "from software", "into ai infra",
                   "mainframe", "consulting budget"],
    "deflation":  ["ai deflation", "billable hours", "fewer engineers", "pricing pressure", "productivity gain",
                   "deflationary", "revenue per", "smaller deals", "efficiency", "do more with less"],
    "accelerate": ["ai capex", "record capex", "data centre", "data center", "gpu demand", "capex raise",
                   "ai investment", "capacity expansion", "ramp"],
    "valuation":  ["profit taking", "profit-taking", "profit booking", "overvalued", "stretched valuation",
                   "valuation reset", "pe reset", "correction after", "priced in", "cooling off", "rotation out of semi"],
    "true_slow":  ["ai demand slowdown", "demand slowdown", "ai bubble", "demand collapse", "cooling ai",
                   "ai fatigue", "spending cut", "capex cut", "orders cut", "cloud slowdown"],
    "cloud_strong": ["cloud demand", "cloud growth", "cloud spending", "azure grew", "aws grew"],
    "orders_strong": ["chip orders", "record orders", "order backlog", "book-to-bill"],
    "guidance_cut": ["guidance cut", "lowered guidance", "revenue below", "warns", "warning", "miss", "trims guidance"],
    "guidance_up": ["raised guidance", "guidance raise", "above estimates", "beat"],
}


def _hits(news, kws):
    out = []
    for n in news or []:
        t = (n.get("title", "") + " " + n.get("tags", "") + " " + n.get("summary", "") + " " + n.get("fulltext", "")).lower()
        if any(k in t for k in kws):
            out.append(n.get("title", ""))
    return out


def _clip(x):
    return max(0.0, min(1.0, x))


def classify(signals: dict, news: list[dict], capex: dict | None = None) -> dict | None:
    """signals: {sox, kospi, nifty_it, us10y, dxy, usdinr, vix}. Returns the conditional read."""
    sox, kospi, it = signals.get("sox"), signals.get("kospi"), signals.get("nifty_it")
    us10y, dxy = signals.get("us10y"), signals.get("dxy")

    move = sox if (sox is not None and abs(sox) >= 1.0) else \
        (kospi if (kospi is not None and abs(kospi) >= 1.0) else None)
    if move is None:
        return None

    # ============ EVIDENCE BY LAYER (each 0..1 per cause) ============
    rot = _hits(news, _KW["rotation"]) + [n for n in _hits(news, _IT)]
    defl = _hits(news, _KW["deflation"])
    acc = _hits(news, _KW["accelerate"])
    val = _hits(news, _KW["valuation"])
    tslow = _hits(news, _KW["true_slow"])
    cloud_ok = _hits(news, _KW["cloud_strong"])
    orders_ok = _hits(news, _KW["orders_strong"])
    g_cut = _hits(news, _KW["guidance_cut"])
    g_up = _hits(news, _KW["guidance_up"])

    it_down = it is not None and it < -0.3
    it_up = it is not None and it > 0.3
    macro_shock = (us10y is not None and us10y > 1.0) or (dxy is not None and dxy > 0.3)
    capex_cut = bool(capex) and (capex.get("quality_direction", 0) < 0 or capex.get("direction") == "down")
    capex_raise = bool(capex) and (capex.get("quality_direction", 0) > 0 or capex.get("direction") == "up")

    # layer scores per cause: (news, market, fundamentals, guidance)
    def L(news_s, mkt_s, fund_s, guid_s):
        return {"news": _clip(news_s), "market": _clip(mkt_s), "fundamentals": _clip(fund_s), "guidance": _clip(guid_s)}

    layers = {
        "enterprise_budget_rotation": L(
            0.5 * bool(rot) + 0.5 * bool(_hits(news, ["ibm", "accenture"])),
            (0.6 if it_down else 0.2) + 0.2 * (move < 0),
            0.6 * bool(cloud_ok) + 0.4 * bool(orders_ok),          # demand intact = supports rotation
            0.6 * bool(g_cut) + 0.4 * capex_raise),
        "ai_productivity_deflation": L(
            bool(defl),
            0.6 if it_down else 0.2,
            0.5 * bool(cloud_ok),                                  # demand fine, services deflating
            0.7 * bool(g_cut)),
        "ai_demand_accelerating": L(
            bool(acc),
            0.5 * (move > 0) + 0.3 * it_down,
            0.5 * bool(cloud_ok) + 0.5 * bool(orders_ok),
            0.7 * capex_raise),
        "semiconductor_valuation_correction": L(
            bool(val),
            0.6 * (move < 0) * (not it_down) + 0.3 * it_up,        # SOX down but IT NOT down
            0.5 * (not tslow) * (not g_cut),                       # no demand/guidance damage
            0.4 * (not g_cut)),
        "true_ai_demand_slowdown": L(
            bool(tslow),
            0.5 * (move < 0) * it_down,
            # REQUIRES multiple fundamental confirmations — hard to score high
            0.4 * capex_cut + 0.3 * bool(_hits(news, ["cloud slowdown"])) + 0.3 * (not cloud_ok),
            0.6 * capex_cut + 0.4 * bool(g_cut)),
        "macro_derating": L(
            0.4 * bool(_hits(news, ["fed", "yields", "rate hike", "higher for longer"])),
            0.8 * macro_shock,
            0.2,
            0.0),
    }
    W = {"news": 0.30, "market": 0.30, "fundamentals": 0.25, "guidance": 0.15}
    scores = {c: round(sum(W[k] * v for k, v in lv.items()), 3) for c, lv in layers.items()}

    # currency offset (risk-off USD strength → IT export earnings tailwind)
    usdinr = signals.get("usdinr")
    currency_offset = None
    if move < 0 and ((dxy is not None and dxy > 0.2) or (usdinr is not None and usdinr > 0.2)):
        currency_offset = ("Weaker rupee (risk-off USD strength) lifts IT export earnings — a partial "
                           "offset that can soften the services hit.")

    if not any(scores.values()):
        return None
    cause = max(scores, key=scores.get)
    meta = CAUSES[cause]

    # ============ CONFIDENCE = quality × agreement × historical ============
    total = sum(scores.values()) or 1.0
    agreement = round(scores[cause] / total, 2)                    # how dominant the winner is
    win_layers = layers[cause]
    quality = round(sum(1 for v in win_layers.values() if v > 0.05) / 4.0, 2)  # layer coverage
    try:
        import calibration as _cal
        rel = _cal.reliability_for_driver("sox_pct")
        historical = rel["value"] if rel and rel.get("value") else 0.57
    except Exception:
        historical = 0.57
    confidence = round(min(0.9, quality * agreement * historical), 2)

    # diagnostic questions (kept, evidence-tagged)
    questions = [
        {"q": "Budget rotation (IBM/Accenture: HW↑ / SW↓)?", "answer": bool(rot) or bool(_hits(news, ["ibm", "accenture"])), "evidence": rot[:2]},
        {"q": "AI productivity / services deflation?", "answer": bool(defl), "evidence": defl[:2]},
        {"q": "Valuation reset / profit-taking?", "answer": bool(val), "evidence": val[:2]},
        {"q": "TRUE demand slowdown (capex cut + cloud + orders)?", "answer": bool(tslow) and capex_cut, "evidence": tslow[:2]},
        {"q": "Macro shock (Fed / yields / dollar)?", "answer": macro_shock, "evidence": [f"US10Y {us10y:+.1f}%" if us10y is not None else "", f"DXY {dxy:+.1f}%" if dxy is not None else ""]},
    ]

    return {
        "sox_pct": sox, "kospi_pct": kospi, "nifty_it_pct": it,
        "primary_cause": cause,
        "cause_label": meta["label"],
        "cause_scores": {k: v for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        "ai_demand": meta["ai_demand"],
        "capital_allocation": meta["allocation"],
        "indian_it_expected": meta["indian_it"],
        "target_reads": meta["targets"],            # semis / enterprise software / IT services / cloud
        "reasoning": meta["note"],
        "evidence_layers": {c: layers[c] for c in [cause]}[cause],
        "confidence": confidence,
        "confidence_components": {"evidence_quality": quality, "agreement": agreement,
                                  "historical_accuracy": round(historical, 2)},
        "capex_signal": ("AI capex guidance CUT (rare true-slowdown signal)" if capex_cut
                         else "AI capex guidance RAISED → demand strong, dip is allocation/positioning" if capex_raise
                         else None),
        "currency_offset": currency_offset,
        "diagnostic_questions": questions,
        "chain": ["SOX/KOSPI move", f"cause: {meta['label']}", f"AI demand: {meta['ai_demand']}",
                  f"allocation: {meta['allocation']}", f"Indian IT: {meta['indian_it']}"],
        "supersedes": "the naive 'SOX↓ ⇒ AI demand weak ⇒ IT↓' rule — replaced by a multi-cause, "
                      "multi-target read (demand vs allocation vs revenue-model).",
        "ai_regime": "Substitution" if cause in ("enterprise_budget_rotation", "ai_productivity_deflation",
                                                 "ai_demand_accelerating", "true_ai_demand_slowdown") else "Neutral",
        "tag": "PRIOR",
    }
