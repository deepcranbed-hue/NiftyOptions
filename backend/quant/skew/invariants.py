"""
invariants.py — engine-side invariant evaluation per D-MA-07 (as amended 07-Jul-2026).

Contract:
  - evaluate() consumes the ACTUAL emission produced by skew_engine.decompose_skew
    plus optional auxiliary inputs (OI join, VIX, config recompute inputs).
  - Every invariant reports exactly one of: PASSED | FAILED | SKIPPED.
    FAILED carries measured values and the violated rule — never prose diagnoses.
    SKIPPED names the missing input. Nothing is ever silently passed.
  - The `checked` manifest lists only invariants actually evaluated (PASSED/FAILED);
    skipped ones appear in `skipped`, with reasons. A manifest that lists an
    unevaluated invariant is itself a defect.
"""
from __future__ import annotations
import numpy as np

TOL = {"leg_identity_vpt": 0.05, "iv_bound_vpt": 10.0, "vix_disagree_vpt": 1.5}


def _passed(id_):            return {"id": id_, "result": "PASSED"}
def _failed(id_, meas, rule): return {"id": id_, "result": "FAILED", "measured": meas, "rule": rule}
def _skipped(id_, missing):  return {"id": id_, "result": "SKIPPED", "missing": missing}


def evaluate(emission: dict,
             floating_legs: dict | None = None,   # {"d_call25": vpt, "d_put25": vpt} at floating deltas
             oi_join: dict | None = None,         # {"leg_strikes": [...], "oi_strikes": [...]}
             vix: dict | None = None,             # {"d_vix_vpt": x, "d_atm_vpt": y}
             config_inputs: dict | None = None    # recompute check inputs
             ) -> dict:
    res = []

    rrf = emission.get("rr_floating"); rrx = emission.get("rr_fixed")
    legs = emission.get("legs_fixed_vpt")

    # T-A: floating RR change equals floating leg difference (needs floating legs)
    if rrf is None:
        res.append(_skipped("T-A", ["rr_floating"]))
    elif floating_legs is None:
        res.append(_skipped("T-A", ["floating_legs(d_call25,d_put25)"]))
    else:
        lhs = rrf["d_vpt"]; rhs = floating_legs["d_call25"] - floating_legs["d_put25"]
        diff = abs(lhs - rhs)
        rule = "abs(d_rr_floating - (d_call25_float - d_put25_float)) <= 0.05 vpt"
        res.append(_passed("T-A") if diff <= TOL["leg_identity_vpt"]
                   else _failed("T-A", {"d_rr_floating": lhs, "leg_diff": round(rhs, 3),
                                        "abs_diff": round(diff, 3)}, rule))

    # T-B: fixed/floating sign agreement (only meaningful when both exist & non-quiet)
    art = emission.get("artifact_share", {})
    if rrf is None or rrx is None:
        res.append(_skipped("T-B", ["rr_floating" if rrf is None else "rr_fixed"]))
    elif art.get("status") == "QUIET":
        res.append(_skipped("T-B", ["d_rr_floating above quiet dead-band"]))
    else:
        a, b = rrf["d_vpt"], rrx["d_vpt"]
        # Mirrors the engine guard exactly (drift between guard and checker caused a
        # false alarm, 08-Jul): a fixed leg within the identity tolerance is zero-
        # within-noise — the pure sticky-strike case; its sign is undefined and a
        # numeric share (~1.0) is the CORRECT output, not a violation.
        if abs(b) <= TOL["leg_identity_vpt"]:
            res.append(_passed("T-B"))
        elif np.sign(a) == np.sign(b):
            res.append(_passed("T-B"))
        elif art.get("status") == "MIXED_REGIME":
            res.append(_passed("T-B"))
        else:
            res.append(_failed("T-B", {"d_rr_fixed": b, "d_rr_floating": a,
                                       "artifact_share_status": art.get("status")},
                               "sign mismatch (fixed leg above tolerance) must render "
                               "MIXED_REGIME, not a numeric share"))

    # T-C: fixed RR change equals FIXED-strike leg difference (accounting identity)
    if rrx is None or legs is None:
        res.append(_skipped("T-C", ["rr_fixed" if rrx is None else "legs_fixed_vpt"]))
    else:
        lhs = rrx["d_vpt"]; rhs = legs["d_call"] - legs["d_put"]; diff = abs(lhs - rhs)
        rule = "abs(d_rr_fixed - (d_call_fixed - d_put_fixed)) <= 0.05 vpt"
        res.append(_passed("T-C") if diff <= TOL["leg_identity_vpt"]
                   else _failed("T-C", {"d_rr_fixed": lhs, "leg_diff": round(rhs, 3),
                                        "abs_diff": round(diff, 3)}, rule))

    # T-D: leg changes bounded
    if legs is None:
        res.append(_skipped("T-D", ["legs_fixed_vpt"]))
    else:
        bad = {k: v for k, v in legs.items() if abs(v) > TOL["iv_bound_vpt"]}
        res.append(_passed("T-D") if not bad
                   else _failed("T-D", bad, "abs(leg change) <= 10 vpt"))

    # T-E: every displayed change carries its window label
    windowed = [x for x in (rrf, rrx) if x is not None]
    if not windowed:
        res.append(_skipped("T-E", ["rr_floating", "rr_fixed"]))
    else:
        missing = [i for i, x in enumerate(windowed) if "window" not in x]
        res.append(_passed("T-E") if not missing
                   else _failed("T-E", {"blocks_missing_window": missing},
                                "every Δ block carries a window label"))

    # T-F: OI join strike set equals leg-attribution strike set
    if oi_join is None:
        res.append(_skipped("T-F", ["oi_join(leg_strikes, oi_strikes)"]))
    else:
        same = sorted(oi_join["leg_strikes"]) == sorted(oi_join["oi_strikes"])
        res.append(_passed("T-F") if same
                   else _failed("T-F", {"leg_strikes": oi_join["leg_strikes"],
                                        "oi_strikes": oi_join["oi_strikes"]},
                                "OI join computed over identical strike set as leg attribution"))

    # T-G: configuration recomputes from its displayed inputs
    cfg = emission.get("configuration")
    if cfg is None or "inputs" not in cfg or config_inputs is None:
        res.append(_skipped("T-G", ["configuration.inputs", "config recompute fn"]))
    else:
        recomputed = config_inputs["classify_fn"](**config_inputs["kwargs"])
        agree = recomputed["configuration"] == cfg["configuration"]
        res.append(_passed("T-G") if agree
                   else _failed("T-G", {"displayed": cfg["configuration"],
                                        "recomputed": recomputed["configuration"]},
                                "chip label must equal classifier output on displayed inputs"))

    # T-H: ATM IV change direction vs India VIX change (independent stream)
    if vix is None:
        res.append(_skipped("T-H", ["vix(d_vix_vpt, d_atm_vpt)"]))
    else:
        a, b = vix["d_atm_vpt"], vix["d_vix_vpt"]
        disagree = np.sign(a) != np.sign(b) and abs(a - b) > TOL["vix_disagree_vpt"]
        res.append(_failed("T-H", {"d_atm_vpt": a, "d_vix_vpt": b},
                           "ATM IV and VIX direction disagree beyond tolerance — mid contamination suspected")
                   if disagree else _passed("T-H"))

    # T-I: parity gate ran and its flags are carried on the emission
    pf = emission.get("parity_flags")
    if pf is None:
        res.append(_failed("T-I", {"parity_flags": None},
                           "emission must carry parity gate output (even if empty)"))
    else:
        res.append(_passed("T-I"))

    checked = [r["id"] for r in res if r["result"] in ("PASSED", "FAILED")]
    skipped = [r for r in res if r["result"] == "SKIPPED"]
    failures = [r for r in res if r["result"] == "FAILED"]
    return {"passed": len(failures) == 0, "checked": checked,
            "failures": failures, "skipped": skipped}
