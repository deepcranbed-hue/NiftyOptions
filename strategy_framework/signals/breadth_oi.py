"""
strategy_framework/signals/breadth_oi.py
========================================
Breadth (constituent advance/decline) and option open-interest positioning.

Two independent reads folded into one directional signal:

  * Breadth   : across the NIFTY constituents we have 1m bars for, what fraction
                are advancing vs declining over the lookback, weighted by the
                strength of each move? Broad participation up = bullish.
  * OI walls  : the largest put-OI strike below spot is a support wall; the
                largest call-OI strike above spot is a resistance wall. Spot's
                position between the walls, and which wall is being reinforced
                by OI change, gives a directional lean (D-MA-08 style).

Positive score = breadth up + spot pressing toward resistance with put support
building beneath it (bullish). Negative = the mirror image.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp
from ..config import constituents as K


def _breadth(da, now: str, lookback: int = 60):
    # Use ALL NIFTY-50 constituents that have bars, not a hardcoded few.
    syms = sorted((set(da.available_symbols("1m")) & set(K.symbols())) - {"NIFTY"})
    adv = dec = 0.0
    adv_w = dec_w = tot_w = 0.0                    # index-weighted advance/decline
    moves, wts, used = [], [], []
    for sym in syms:
        bars = da.bars(sym, "1m", end=now, limit=lookback + 5)
        if len(bars) < 3:
            continue
        c = np.array([b["close"] for b in bars], float)
        r = (c[-1] / c[0] - 1.0)
        w = K.weight_of(sym)
        moves.append(r); wts.append(w); used.append(sym); tot_w += w
        if r > 0:
            adv += 1; adv_w += w
        elif r < 0:
            dec += 1; dec_w += w
    if not moves:
        return None
    n = len(moves)
    mv = np.array(moves, float); wv = np.array(wts, float)
    # NIFTY is cap-weighted, so index direction ≈ the INDEX-WEIGHTED breadth; the
    # equal-weighted view is reported alongside to show heavyweight vs broad divergence.
    net_breadth_u = (adv - dec) / n                                   # equal-weighted
    net_breadth_w = ((adv_w - dec_w) / tot_w) if tot_w > 0 else net_breadth_u  # index-weighted
    avg_move_u = float(mv.mean())
    avg_move_w = float((mv * wv).sum() / wv.sum()) if wv.sum() > 0 else avg_move_u
    # score from the INDEX-WEIGHTED reads (broad AND strong AND in the heavyweights)
    score = clamp(0.6 * net_breadth_w + 0.4 * squash(avg_move_w * 100.0, scale=1.0))
    return {"score": score, "net_breadth_weighted": round(net_breadth_w, 3),
            "net_breadth_unweighted": round(net_breadth_u, 3),
            "weight_vs_equal_divergence": round(net_breadth_w - net_breadth_u, 3),
            "adv": adv, "dec": dec, "n": n,
            "avg_move_weighted_pct": round(avg_move_w * 100, 3),
            "avg_move_pct": round(avg_move_u * 100, 3), "symbols": used}


def _oi_lean(chain, doi=None):
    """Directional lean from put/call OI walls and their reinforcement.

    `doi` = reconstructed per-strike ΔOI ({"call_doi": {...}, "put_doi": {...}}) from
    option_oi.reconstruct_doi. The chain's own call_oi_chg/put_oi_chg columns are never
    populated (all zero), so the wall-reinforcement term MUST read the reconstructed
    ΔOI — otherwise build_lean is silently always 0 and 35% of this signal is dead."""
    spot = chain.spot
    below = [(k, chain.put_oi.get(k, 0)) for k in chain.strikes if k < spot]
    above = [(k, chain.call_oi.get(k, 0)) for k in chain.strikes if k > spot]
    if not below or not above:
        return None
    support_k, support_oi = max(below, key=lambda x: x[1])
    resist_k, resist_oi = max(above, key=lambda x: x[1])

    # 1) Position of spot in the [support, resistance] band. Near support ->
    #    limited downside/bounce bias (+); near resistance -> capped (-).
    band = max(resist_k - support_k, 1e-6)
    pos = (spot - support_k) / band               # 0 at support, 1 at resistance
    position_lean = (0.5 - pos) * 2.0             # +1 at support, -1 at resistance

    # 2) Which wall is being reinforced by fresh OI (writers defending)? Put building at
    #    support (+) vs call building at resistance (−). Reconstructed ΔOI, not the empty
    #    oi_chg column. No reconstruction available (day's first snapshots) → 0, neutral.
    cd = (doi or {}).get("call_doi", {})
    pd = (doi or {}).get("put_doi", {})
    support_build = pd.get(support_k, 0.0)
    resist_build = cd.get(resist_k, 0.0)
    build_lean = squash((support_build - resist_build) /
                        (abs(support_build) + abs(resist_build) + 1e-6), scale=1.0)

    # 3) Put/call OI ratio around ATM as a coarse sentiment tilt.
    tot_put = sum(chain.put_oi.values()); tot_call = sum(chain.call_oi.values())
    pcr = tot_put / (tot_call + 1e-9)
    pcr_lean = squash(np.log(pcr + 1e-9), scale=0.5)   # high PCR -> support-heavy -> +

    score = clamp(0.4 * position_lean + 0.35 * build_lean + 0.25 * pcr_lean)
    return {"score": score, "support": support_k, "resistance": resist_k,
            "pcr": round(pcr, 3), "support_oi": support_oi, "resist_oi": resist_oi,
            "spot_pos_in_band": round(pos, 3)}


def compute(da, now: str, ctx: dict) -> Signal:
    from . import option_oi
    chain = ctx.get("chain")
    breadth = _breadth(da, now)
    # reconstruct per-strike ΔOI from levels (the oi_chg columns are unpopulated)
    doi = option_oi.reconstruct_doi(da, chain, now) if chain is not None else None
    oi = _oi_lean(chain, doi) if chain is not None else None

    parts = []
    detail = {}
    if breadth:
        parts.append((breadth["score"], 0.5)); detail["breadth"] = breadth
    if oi:
        parts.append((oi["score"], 0.5)); detail["oi"] = oi
    if not parts:
        return Signal.no_data("breadth_oi", "no constituent bars and no chain")

    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)
    # confidence rises when breadth and OI agree in sign.
    if breadth and oi:
        agree = 1.0 - abs(np.sign(breadth["score"]) - np.sign(oi["score"])) / 2.0
        confidence = clamp(0.35 + 0.35 * agree, 0.0, 0.85)
    else:
        confidence = 0.35
    return Signal("breadth_oi", score, confidence, "PRIOR", detail=detail)
