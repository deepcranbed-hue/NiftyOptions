"""
strategy_framework/signals/strike_role.py
==========================================
STRIKE-ROLE-CHANGE detector — the temporal read `breadth_oi` doesn't do.

`breadth_oi` reads the OI walls as a STATIC snapshot (where is support/resistance
right now). This signal reads their EVOLUTION: is the wall defending the current move
being reinforced or dismantled? The classic role flip is a resistance strike whose
CALL open interest is UNWINDING while PUT interest builds beneath — the level is
transitioning from resistance into support (bullish). The mirror — support puts
unwinding while calls build above — is a support→resistance flip (bearish).

Mechanics (no lookahead):
  * resistance R = largest call-OI strike above spot; support S = largest put-OI below.
  * ΔOI reconstructed from LEVELS across captures (option_oi.reconstruct_doi — the
    oi_chg columns are empty), expressed as a GROWTH RATE relative to the wall's own
    level so a 10% unwind reads the same on a big or small wall.
  * bullish when the resistance call wall is unwinding (rc < 0) AND the support put
    wall is building (sp > 0); bearish on the mirror.

Weight 0 / studied CANDIDATE: this is a hypothesis (does a role flip predict the next
move?), not a trusted vote. Let the P(edge) study decide, and watch its correlation
with breadth_oi — they read the same walls, so they may be cousins.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp
from . import option_oi

# PRIOR scale on the OI growth rate: a ~15% wall change → a firm (~0.6) contribution.
# Calibratable — kept explicit so it isn't a hidden magic number.
_SCALE = 6.0


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("strike_role_change", "no option chain as-of now")
    doi = option_oi.reconstruct_doi(da, chain, now)
    if not doi:
        return Signal("strike_role_change", 0.0, 0.15, "PRIOR",
                      status="INSUFFICIENT_HISTORY",
                      detail={"note": "no earlier snapshot to difference ΔOI against yet"})

    spot = chain.spot
    above = [(k, chain.call_oi.get(k, 0) or 0) for k in chain.strikes if k > spot]
    below = [(k, chain.put_oi.get(k, 0) or 0) for k in chain.strikes if k < spot]
    if not above or not below:
        return Signal.no_data("strike_role_change", "spot outside the strike ladder")

    resist_k, resist_oi = max(above, key=lambda x: x[1])     # biggest call wall above
    support_k, support_oi = max(below, key=lambda x: x[1])   # biggest put wall below

    call_doi_R = doi["call_doi"].get(resist_k, 0.0)
    put_doi_S = doi["put_doi"].get(support_k, 0.0)
    # ALSO: is the resistance strike itself gaining PUT OI (writers switching sides)?
    put_doi_R = doi["put_doi"].get(resist_k, 0.0)

    rc = call_doi_R / (resist_oi + 1e-9)        # resistance call growth (neg = unwinding → +)
    sp = put_doi_S / (support_oi + 1e-9)        # support put growth (pos = building → +)
    flip_r = put_doi_R / (resist_oi + 1e-9)     # puts building AT resistance → flip toward support (+)

    score = clamp(0.45 * np.tanh(-rc * _SCALE)
                  + 0.35 * np.tanh(sp * _SCALE)
                  + 0.20 * np.tanh(flip_r * _SCALE))

    # confidence: bigger, more dominant walls = a more meaningful role read.
    tot_oi = float(sum(v for _, v in above) + sum(v for _, v in below)) or 1.0
    concentration = (resist_oi + support_oi) / tot_oi
    confidence = clamp(0.30 + 1.5 * concentration, 0.30, 0.80)

    role = ("resistance dissolving → support (bullish)" if rc < -0.02 and sp > 0.0 else
            "support dissolving → resistance (bearish)" if sp < -0.02 and rc > 0.0 else
            "walls holding / mixed")

    return Signal("strike_role_change", float(score), float(confidence), "PRIOR",
                  detail={"resistance_strike": resist_k, "support_strike": support_k,
                          "resist_call_growth_pct": round(rc * 100, 2),
                          "support_put_growth_pct": round(sp * 100, 2),
                          "put_build_at_resistance_pct": round(flip_r * 100, 2),
                          "resist_oi": int(resist_oi), "support_oi": int(support_oi),
                          "read": role, "window": f"{doi['prior_ts'][11:16]}→{doi['cur_ts'][11:16]}"})
