#!/usr/bin/env python3
"""
root_cause.py — the missing layer BEFORE factors: WHY did the observable move?

The problem it fixes
--------------------
factor_graph.py starts from `Oil`. But oil ↓ means opposite things depending on cause:

    Iran peace  → SUPPLY eases   → oil ↓ → inflation ↓ → risk-ON  → Growth ↑ → IT ↑
    China slump → DEMAND weakens → oil ↓ → growth   ↓ → risk-OFF → Defensives ↑

Same observable, opposite propagation. A DAG rooted at Oil can't tell these apart. The
fix is NOT to make edges flip dynamically — it is to identify the ROOT CAUSE first and
let it activate the RIGHT SET of roots. Then the SAME DAG produces opposite outcomes,
because a supply shock also eases Geopolitics while a demand shock also drags China.

    News → NARRATIVE → ROOT CAUSE (shock type) → root activations → propagate()

Shock types are classified from the news; each maps the headline move onto the roots it
actually implies. Everything downstream (factor_graph) is unchanged.
"""

from __future__ import annotations

import re

# shock_type -> (keywords, how it maps an oil move onto the root factors)
# `oil` always follows the observed move. The DISCRIMINATING roots are the extras:
#   supply-easing also eases Geopolitics (risk premium fades)  → net risk-ON
#   demand-shock also drags China/Growth (weak demand)         → net risk-OFF
SHOCK_TYPES = {
    "supply_easing": {
        "label": "Supply easing (ceasefire / OPEC+ hike / inventory build)",
        "keywords": ["ceasefire", "peace", "de-escalation", "opec+ hike", "output increase",
                     "supply rises", "inventory build", "sanctions lifted", "truce"],
        # oil down here is GOOD: risk premium fades too
        "extra_roots": lambda oil: {"Geopolitics": -abs(oil) * 0.7} if oil < 0 else
                                    {"Geopolitics": +abs(oil) * 0.7},
        "note": "oil move is a SUPPLY/risk-premium story — disinflationary, risk-supportive",
    },
    "supply_shock": {
        "label": "Supply shock (Hormuz / war / sanctions / outage)",
        "keywords": ["hormuz", "blockade", "war", "strike", "attack", "sanction", "outage",
                     "supply cut", "opec+ cut", "embargo", "tanker"],
        "extra_roots": lambda oil: {"Geopolitics": +abs(oil) * 0.8},   # risk premium ON
        "note": "oil move is a SUPPLY-disruption story — inflationary AND risk-off",
    },
    "demand_shock": {
        "label": "Demand shock (China slowdown / recession fear)",
        "keywords": ["recession", "china slowdown", "china collapse", "demand destruction",
                     "weak demand", "global slowdown", "pmi contraction", "hard landing"],
        # oil DOWN because demand is weak → China/growth also weak
        "extra_roots": lambda oil: {"China": oil},                     # same sign as oil
        "note": "oil move is a DEMAND story — growth-negative, risk-OFF despite lower inflation",
    },
    "policy": {
        "label": "Policy / pricing (windfall tax, price cap, excise)",
        "keywords": ["windfall tax", "price cap", "excise", "fuel subsidy", "retail price freeze"],
        "extra_roots": lambda oil: {},                                 # domestic redistribution
        "note": "a policy/pricing story — sector-specific, muted macro propagation",
    },
}
_DEFAULT = "supply_shock"   # when oil moves with no clear cause, assume the risk case


def classify(news: list[dict], oil_move: float) -> dict:
    """Pick the shock type from the news, then map the oil move onto root activations."""
    blob = " ".join((n.get("title", "") + " " + n.get("tags", "")).lower()
                    for n in (news or []))
    hits = {k: sum(1 for kw in c["keywords"] if kw in blob) for k, c in SHOCK_TYPES.items()}
    shock = max(hits, key=hits.get) if any(hits.values()) else _DEFAULT
    cfg = SHOCK_TYPES[shock]
    roots = {"Oil": oil_move}
    roots.update(cfg["extra_roots"](oil_move))
    return {"shock_type": shock, "label": cfg["label"], "note": cfg["note"],
            "root_activations": roots, "keyword_hits": {k: v for k, v in hits.items() if v}}


if __name__ == "__main__":
    import factor_graph as FG

    def scenario(title, news, oil):
        rc = classify(news, oil)
        out = FG.propagate(rc["root_activations"])
        print(f"\n=== {title} ===")
        print(f"  shock: {rc['label']}")
        print(f"  roots: {rc['root_activations']}")
        for f in ("InflationExp", "RiskAppetite", "Growth", "Defensives", "IT"):
            print(f"    {f:14} {out['values'][f]:+.2f}")

    # SAME oil move (-0.9), opposite cause → opposite IT
    scenario("Iran ceasefire → oil down (SUPPLY easing)",
             [{"title": "Iran ceasefire holds, oil slides as supply fears ease", "tags": "ceasefire oil"}], -0.9)
    scenario("China recession → oil down (DEMAND shock)",
             [{"title": "Oil tumbles on China recession fears, demand destruction", "tags": "recession china"}], -0.9)
