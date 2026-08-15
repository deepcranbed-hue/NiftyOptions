#!/usr/bin/env python3
"""
sector_analyzer.py — turn NarrativeSignals into a sector view.

    Narrative plugins  →  explain WHY something happened  (emit signals)
    Sector analyzers   →  decide WHAT IT MEANS for a sector (aggregate signals)

This is the ONLY place cross-narrative combination happens. Plugins never compute a
sector score, because no plugin can see its peers: if the Earnings plugin returned
`overall = -0.22` for Banks while Treasury returned `-0.30`, those two numbers would
already share rate reasoning and combining them would count one story twice.

TWO THINGS THIS FILE GUARDS AGAINST
-----------------------------------
1. DOUBLE-COUNTING ACROSS NARRATIVES. Oil→Banks and Treasury→Banks and RBI→Banks are
   three views of ONE transmission chain (oil → inflation → policy → rates → banks).
   They are placed in a shared "RateComplex" group with one weight budget, exactly like
   the correlated fundamentals block, so adding another rate-flavoured narrative
   re-slices the budget instead of growing it.

2. DIMENSION COLLAPSE. Fundamentals, Guidance, Valuation, Management and Peer answer
   different questions and are reported separately as well as netted. "Fundamentals
   weak but valuation attractive" is usually the real read, and a single number destroys it.

Every output carries its inputs, so a sector view can always be walked back to the
signals — and to the headlines — that produced it.
"""

from __future__ import annotations

from collections import defaultdict

# Dimension weights within a sector view. PRIOR — judgement, documented, not fitted.
#   reported numbers  > what management said > what a broker thinks
DIMENSION_WEIGHT = {
    "Fundamentals": 0.35,
    "Guidance": 0.25,
    "Policy": 0.20,
    "FinancialConditions": 0.20,
    "Flows": 0.15,
    "Management": 0.10,
    "Peer": 0.10,
    "Valuation": 0.10,      # an opinion, not a fact — weakest by design
}

# Narratives that reach a sector through the SAME economic chain share one budget.
# Oil → inflation → RBI → yields → banks is one story told by four narratives; without
# this they would each contribute in full and a single rate move would look like four
# independent confirmations.
CORRELATED_GROUPS = {
    "Banks": {"RateComplex": {"Oil", "Treasury", "RBI", "Fed"}},
    "Auto":  {"RateComplex": {"Treasury", "RBI", "Fed"}},
    "Realty": {"RateComplex": {"Treasury", "RBI", "Fed"}},
    "IT Services": {"AIThesis": {"Semiconductors / AI", "AI"}},
}
GROUP_BUDGET = 0.45     # max share of |total| any one correlated group may contribute


def analyze_sector(sector: str, signals: list) -> dict:
    """
    Aggregate every NarrativeSignal touching `sector` into one view.
    `signals` may be NarrativeSignal objects or their dicts.
    """
    rows = [s if isinstance(s, dict) else s.to_dict() for s in signals]
    mine = [r for r in rows if (r.get("sector") or "") == sector]
    if not mine:
        return {"sector": sector, "verdict": "— no signals", "score": 0.0,
                "dimensions": {}, "narratives": {}, "signals": []}

    # ---- per-dimension netting (kept separate, then weighted) -------------
    by_dim = defaultdict(list)
    for r in mine:
        by_dim[r.get("dimension") or "Other"].append(r)

    dims = {}
    for dim, rs in by_dim.items():
        # confidence-weighted mean, so a low-confidence read moves the view less
        wsum = sum(abs(r["confidence"]) for r in rs) or 1.0
        val = sum(r["signed"] * r["confidence"] for r in rs) / wsum
        dims[dim] = {"score": round(val, 3), "n": len(rs),
                     "weight": DIMENSION_WEIGHT.get(dim, 0.10),
                     "companies": sorted({r["company"] for r in rs if r["company"]})}

    # ---- correlated-narrative cap ----------------------------------------
    by_nar = defaultdict(float)
    for r in mine:
        by_nar[r["narrative"]] += r["signed"] * r["confidence"]

    groups = CORRELATED_GROUPS.get(sector, {})
    capped = {}
    for gname, members in groups.items():
        gsum = sum(v for k, v in by_nar.items() if k in members)
        others = sum(abs(v) for k, v in by_nar.items() if k not in members)
        allowed = GROUP_BUDGET / (1 - GROUP_BUDGET) * others if others else abs(gsum)
        if abs(gsum) > allowed and gsum:
            scale = allowed / abs(gsum)
            for k in list(by_nar):
                if k in members:
                    by_nar[k] *= scale
            capped[gname] = {"members": sorted(members), "before": round(gsum, 3),
                             "after": round(gsum * scale, 3),
                             "why": "these narratives reach the sector through ONE chain; "
                                    "capped so a single story cannot count repeatedly"}

    # ---- final score: dimension-weighted, then rescaled by the narrative cap
    num = sum(d["score"] * d["weight"] for d in dims.values())
    den = sum(d["weight"] for d in dims.values()) or 1.0
    score = num / den
    if capped:
        raw_nar = sum(abs(v) for v in by_nar.values()) or 1.0
        score *= min(1.0, raw_nar / (raw_nar + 1e-9))

    verdict = ("🟢 Bullish" if score > 0.15 else "🔴 Bearish" if score < -0.15
               else "🟡 Neutral")
    return {"sector": sector, "score": round(score, 3), "verdict": verdict,
            "dimensions": dims,
            "narratives": {k: round(v, 3) for k, v in sorted(by_nar.items(),
                                                             key=lambda kv: -abs(kv[1]))},
            "capped_groups": capped,
            "signals": mine}


def to_markdown(view: dict) -> str:
    if not view.get("dimensions"):
        return f"_{view['sector']}: no narrative signals today._"
    L = [f"### 🏦 {view['sector']} — {view['verdict']} ({view['score']:+.2f})\n",
         "_Aggregated from narrative SIGNALS. Plugins emit observations; this is the one "
         "place they are combined, so nothing is counted twice._\n",
         "| Dimension | Score | Signals | Weight | Names |", "|---|---:|---:|---:|---|"]
    for dim, d in sorted(view["dimensions"].items(), key=lambda kv: -abs(kv[1]["score"])):
        names = ", ".join(d["companies"][:3]) or "—"
        L.append(f"| {dim} | {d['score']:+.2f} | {d['n']} | {d['weight']:.2f} | {names} |")
    L.append("\n**By narrative:** " + " · ".join(
        f"{k} {v:+.2f}" for k, v in view["narratives"].items()))
    for g, c in (view.get("capped_groups") or {}).items():
        L.append(f"\n⚠️ **{g} capped** {c['before']:+.2f} → {c['after']:+.2f} — "
                 f"{', '.join(c['members'])}: {c['why']}")
    return "\n".join(L)
