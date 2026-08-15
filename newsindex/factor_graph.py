#!/usr/bin/env python3
"""
factor_graph.py — the MISSING MIDDLE: factor→factor propagation as a sparse DAG.

The gap this closes
-------------------
factors.py is a FLAT list — companies are exposed directly to root factors (Oil, SOX).
So the engine could say "oil moved" and "IT moved" but never "IT moved BECAUSE oil →
inflation → rates → risk appetite → growth". Today's tape was exactly that chain, and a
flat model can't express it.

This adds the four layers you specified and the edges between them:

    L1 ROOT          Oil, SOX, US10Y, USD, China, Geopolitics   (activated by evidence)
    L2 DERIVED-MACRO Inflation Exp, Financial Conditions, Risk Appetite, Growth Exp
                     (INFERRED — nobody prints "Risk Appetite +0.7"; the engine computes it)
    L3 STYLE         Growth, Value, Cyclicals, Defensives        (leadership)
    L4 SECTOR        IT, Banks, …                                 (companies inherit)

propagate() walks the DAG in topological order: root activations flow up through the
derived and style layers, so a single Oil move lands on IT *through* the chain, with the
path recorded. Competing pathways (a factor reached two ways) are summed at the node, so
"oil ↓ helps IT via rates BUT the energy-weight drag offsets" falls out naturally.

Sparse by discipline (your rule): every node has 2–5 parents, each edge carries a signed
weight AND an economic mechanism string. Dense graphs are opaque and uncalibratable.

Edges/weights are PRIOR — judgement, documented, unfitted. The attribution loop is how
they'd become posteriors (given the news-history store).
"""

from __future__ import annotations

# (child_factor, [(parent, sign, weight, mechanism)]). Parents must be defined ABOVE the
# child (roots first) so a simple pass resolves in order — it's a DAG, no cycles.
LAYERS = {
    "root": ["Oil", "SOX", "US10Y", "USD", "China", "Geopolitics", "VIX"],
    # ORDER = topological: a node's parents must precede it. GrowthExp before
    # RiskAppetite, because RiskAppetite depends on GrowthExp (the demand-shock channel).
    "derived_macro": ["InflationExp", "FinancialConditions", "GrowthExp", "RiskAppetite"],
    "style": ["Growth", "Value", "Cyclicals", "Defensives"],
    "sector": ["IT", "Banks", "Metals", "Energy", "Auto", "FMCG"],
}

EDGES: dict[str, list] = {
    # ---- L2 derived-macro (inferred from roots) --------------------------------
    "InflationExp": [
        ("Oil", +1, 0.6, "crude → energy CPI → headline inflation expectations"),
        ("China", +1, 0.2, "China demand → global commodity prices"),
        ("Geopolitics", +1, 0.3, "supply-corridor risk → energy premium"),
    ],
    "FinancialConditions": [   # higher = TIGHTER
        ("US10Y", +1, 0.5, "US yields → global funding cost"),
        ("USD", +1, 0.3, "stronger dollar → tighter EM conditions"),
        ("InflationExp", +1, 0.4, "inflation → central banks stay restrictive"),
    ],
    "RiskAppetite": [          # higher = MORE risk-on
        ("FinancialConditions", -1, 0.6, "tighter conditions → less risk appetite"),
        ("VIX", -1, 0.5, "fear → risk-off"),
        ("Geopolitics", -1, 0.4, "conflict → risk-off"),
        ("GrowthExp", +1, 0.7, "weak growth → risk-OFF (the demand-shock channel)"),
    ],
    "GrowthExp": [
        ("FinancialConditions", -1, 0.4, "tighter conditions → lower growth"),
        ("China", +1, 0.7, "China is the marginal driver of global growth — a slump bites hard"),
    ],
    # ---- L3 style (leadership) ------------------------------------------------
    "Growth": [
        ("RiskAppetite", +1, 0.6, "risk-on favours long-duration growth"),
        ("FinancialConditions", -1, 0.5, "lower discount rate lifts growth multiples"),
    ],
    "Value": [
        ("RiskAppetite", -1, 0.3, "value leads when risk appetite fades"),
        ("FinancialConditions", +1, 0.4, "higher rates favour value/financials"),
    ],
    "Cyclicals": [
        ("GrowthExp", +1, 0.7, "cyclicals track the growth cycle"),
        ("China", +1, 0.3, "China demand for industrial cyclicals"),
    ],
    "Defensives": [
        ("RiskAppetite", -1, 0.5, "defensives bid when risk appetite falls"),
    ],
    # ---- L4 sector (companies inherit these) ----------------------------------
    "IT": [
        ("Growth", +1, 0.6, "IT is a long-duration growth style"),
        ("SOX", +1, 0.2, "global tech-spend read"),
    ],
    "Banks": [
        ("Value", +1, 0.5, "banks are the core value/rate-sensitive block"),
        ("FinancialConditions", +1, 0.2, "steeper curve helps NIM early"),
    ],
    "Metals": [("Cyclicals", +1, 0.7, "metals are China-cyclicals")],
    "Energy": [("Oil", +1, 0.8, "upstream tracks crude directly")],
    "Auto": [("RiskAppetite", +1, 0.3, "discretionary demand"),
             ("Oil", -1, 0.3, "fuel cost / running cost")],
    "FMCG": [("Defensives", +1, 0.6, "staples are defensive")],
}

# topological order = roots, then each layer (parents always precede children)
_ORDER = LAYERS["root"] + LAYERS["derived_macro"] + LAYERS["style"] + LAYERS["sector"]


def propagate(root_activations: dict) -> dict:
    """
    root_activations: {root_factor: signed value}  e.g. {"Oil": -0.9}
    Returns {factor: {"value", "contributors": [(parent, contribution, mechanism)]}}
    for EVERY node — roots pass through, derived/style/sector computed from parents.
    """
    val: dict[str, float] = {}
    trace: dict[str, list] = {}
    for f in _ORDER:
        if f in EDGES:
            v, contribs = 0.0, []
            for parent, sign, w, mech in EDGES[f]:
                pv = val.get(parent, root_activations.get(parent, 0.0))
                c = sign * w * pv
                if abs(c) > 1e-6:
                    contribs.append((parent, round(c, 3), mech))
                    v += c
            val[f] = round(v, 3)
            trace[f] = contribs
        else:
            val[f] = round(root_activations.get(f, 0.0), 3)
            trace[f] = []
    return {"values": val, "trace": trace}


def explain_path(out: dict, target: str, depth: int = 0, seen=None) -> list[str]:
    """Walk back from a target node to the roots, printing the dominant chain."""
    seen = seen or set()
    if target in seen or depth > 8:
        return []
    seen.add(target)
    lines, v = [], out["values"].get(target, 0.0)
    arrow = "↑" if v > 0.02 else "↓" if v < -0.02 else "→"
    lines.append("    " * depth + f"{target} {arrow} ({v:+.2f})")
    top = sorted(out["trace"].get(target, []), key=lambda c: -abs(c[1]))[:2]
    for parent, contrib, mech in top:
        lines.append("    " * (depth + 1) + f"↑ via {parent} ({contrib:+.2f}) — {mech}")
        lines += explain_path(out, parent, depth + 2, seen)
    return lines


if __name__ == "__main__":
    # TODAY'S CHAIN: oil ↓, no other shock → watch it reach IT and Banks through the DAG
    print("=== Oil ↓ (-0.9) propagating through the DAG ===\n")
    out = propagate({"Oil": -0.9})
    for f in ("InflationExp", "FinancialConditions", "RiskAppetite", "Growth", "IT", "Banks", "Energy"):
        print(f"  {f:20} {out['values'][f]:+.2f}")
    print("\n=== why did IT move? (path back to roots) ===")
    print("\n".join(explain_path(out, "IT")))
