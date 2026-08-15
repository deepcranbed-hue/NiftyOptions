"""
strategy_framework/strategy/candidates.py
=========================================
Generate a ranked list of priced candidate structures for the current read —
even when the gate says NO_TRADE.

Why: the gate is deliberately conservative (it stands aside on weak conviction),
which can leave the desk blank. But the analysis still has a *lean*, and the user
wants to see the options: "given what the signals say right now, here are the
structures that fit, best first, and here's whether each clears the conviction
gate." Adding any of them is the user's call.

Each candidate is the real, priced Structure the constructor would build, tagged
with a short rationale and an `aligned` flag (does it match the current lean /
regime). The one the gate would actually fire on is marked `primary`.
"""
from __future__ import annotations
from . import constructor

_RATIONALE = {
    "bull_call_spread": "bullish, defined risk (debit vertical)",
    "bear_put_spread": "bearish, defined risk (debit vertical)",
    "bull_put_spread": "bullish, sells rich premium (credit vertical)",
    "bear_call_spread": "bearish, sells rich premium (credit vertical)",
    "long_call": "bullish, convex — cheap premium / strong conviction",
    "long_put": "bearish, convex — cheap premium / strong conviction",
    "iron_condor": "range — sell both wings, harvest premium",
    "iron_butterfly": "tight range / pin — sell ATM body, buy wings",
    "long_straddle": "expects a big move either way (long vol)",
    "long_strangle": "expects a big move, cheaper than straddle",
}


def _families_for(decision) -> list:
    """Ordered family shortlist: the gate's pick first, then lean-aligned
    directionals, then range and volatility structures."""
    primary = decision.family if decision.family != "stand_aside" else None
    lean = 1 if decision.net_score > 0 else -1
    if lean > 0:
        directional = ["bull_call_spread", "bull_put_spread", "long_call"]
    else:
        directional = ["bear_put_spread", "bear_call_spread", "long_put"]
    rng = ["iron_condor", "iron_butterfly"]
    vol = ["long_straddle"]
    order = ([primary] if primary else []) + directional + rng + vol
    seen, out = set(), []
    for f in order:
        if f and f not in seen:
            seen.add(f); out.append(f)
    return out


def generate(decision, chain, cfg, top_n: int = 6) -> list:
    """Build + price each candidate; rank primary → aligned → rest."""
    lean = 1 if decision.net_score > 0 else -1
    regime = decision.regime
    out = []
    for fam in _families_for(decision):
        st = constructor.build(fam, chain, cfg)
        if st is None:
            continue
        is_dir = fam.startswith(("bull", "bear", "long_call", "long_put"))
        is_rng = fam in ("iron_condor", "iron_butterfly")
        aligned = ((is_dir and ((fam.startswith("bull") and lean > 0) or
                                (fam.startswith("bear") and lean < 0) or
                                (fam == "long_call" and lean > 0) or
                                (fam == "long_put" and lean < 0)))
                   or (is_rng and regime == "RANGE"))
        out.append({"family": fam, "primary": fam == decision.family,
                    "aligned": aligned, "rationale": _RATIONALE.get(fam, ""),
                    "structure": st.as_dict()})
    out.sort(key=lambda c: (not c["primary"], not c["aligned"]))
    return out[:top_n]
