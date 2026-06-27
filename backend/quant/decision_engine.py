"""
decision_engine.py
------------------
The v1 synthesis: turn weighted news sentiment into a directional bias score,
a (pseudo-)probability, and a concrete strategy decision.

    sentiment (per sector)
        |  weight by NIFTY index weight
        v
    index_bias  in [-1, +1]
        |  logistic squash (PARAMS ASSUMED NOW, FIT LATER)
        v
    P(up)  pseudo-probability
        |  + regime vol-state + conviction + coverage gate
        v
    decision: {structure, direction, size_mult, rationale}

HONESTY CONTRACT
----------------
* P(up) is a *pseudo-probability* until `logit_a/logit_b` are fitted to realised
  outcomes (see calibrate()). Ordered, not calibrated. Do NOT feed it into the
  EV engine as a true probability yet.
* This is structure-selection + sizing support, not an EV or a trade call.
* If index-weight coverage of the sentiment is low, the score is unreliable
  (news covered the loud mover, not the heavyweights) -> STAND ASIDE.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .index_attribution import NIFTY50_WEIGHTS, SECTOR_OF


# --------------------------------------------------------------------------- #
# Calibration parameters -- ASSUMPTIONS until fitted. See calibrate().
# --------------------------------------------------------------------------- #
LOGIT_A = 0.0      # intercept (drift bias of the index)
LOGIT_B = 2.5      # slope: how strongly bias maps to up-probability

# Gates / bands (tune to taste, then to data)
COVERAGE_MIN = 0.35    # need >=35% of index weight to carry sentiment
NEUTRAL_BAND = 0.15    # |bias| below this = no directional view


# --------------------------------------------------------------------------- #
# 1. sentiment -> weighted bias score + coverage
# --------------------------------------------------------------------------- #
def sector_weights(weights=None, sector_of=None) -> dict[str, float]:
    weights = weights or NIFTY50_WEIGHTS
    sector_of = sector_of or SECTOR_OF
    sw: dict[str, float] = defaultdict(float)
    for sym, w in weights.items():
        sw[sector_of.get(sym, "Other")] += w
    return dict(sw)


def index_bias(sector_sentiment: dict[str, float],
               sw: dict[str, float] | None = None) -> tuple[float, float]:
    """Weighted-average sentiment across sectors that have a reading.
    Returns (bias in [-1,1], coverage = covered weight / total weight)."""
    sw = sw or sector_weights()
    total_weight = sum(sw.values()) or 1.0
    covered = {s: sw[s] for s in sector_sentiment if s in sw}
    covered_weight = sum(covered.values())
    if covered_weight == 0:
        return 0.0, 0.0
    bias = sum(sector_sentiment[s] * covered[s] for s in covered) / covered_weight
    return bias, covered_weight / total_weight


# --------------------------------------------------------------------------- #
# 2. bias -> pseudo-probability  (the step that must be calibrated)
# --------------------------------------------------------------------------- #
def prob_up(bias: float, a: float = LOGIT_A, b: float = LOGIT_B) -> float:
    """Pseudo-probability of an up day. Ordered, NOT calibrated until a,b fit."""
    return 1.0 / (1.0 + math.exp(-(a + b * bias)))


def calibrate(history: list[tuple[float, int]]):
    """Fit (a, b) by logistic regression of realised direction on past bias.
    history = [(bias_t, up_t in {0,1}), ...]. Returns (a, b) to replace the
    assumed constants. Needs ~a few hundred sessions to mean anything; until
    then prob_up() stays a heuristic ranking, not a frequency.
    (Implementation left as the next deliverable -- one sklearn LogisticRegression
    fit, plus a reliability curve / Brier score to prove it calibrated.)"""
    raise NotImplementedError("fit with sklearn LogisticRegression + check Brier")


# --------------------------------------------------------------------------- #
# 3. score -> decision
# --------------------------------------------------------------------------- #
def conviction(bias: float, coverage: float, regime_conviction: float) -> float:
    """0..~1. Loud-but-narrow news and low regime conviction both shrink it."""
    return abs(bias) * coverage * (0.5 + 0.5 * regime_conviction)


# (vol_expansion, direction) -> structure. vol_expansion=True means realised
# vol is expected ABOVE implied (buy optionality); False means range (sell it).
STRUCTURE_MATRIX = {
    (True,  "bearish"): "Bear put spread / long puts (defined-risk long vol)",
    (True,  "bullish"): "Bull call spread / long calls (defined-risk long vol)",
    (True,  "neutral"): "Long straddle / strangle (pure long vol)",
    (False, "bearish"): "Bear call spread (sell premium above spot)",
    (False, "bullish"): "Bull put spread (sell premium below spot)",
    (False, "neutral"): "Iron condor (defined-risk premium sell)",
}


def decide(sector_sentiment: dict[str, float], *, vol_expansion: bool,
           regime_conviction: float, base_units: float = 1.0) -> dict:
    bias, coverage = index_bias(sector_sentiment)
    p_up = prob_up(bias)

    if coverage < COVERAGE_MIN:
        return {
            "action": "STAND ASIDE",
            "reason": f"coverage {coverage:.0%} < {COVERAGE_MIN:.0%}: heavyweights "
                      f"are quiet, weighted score is unreliable.",
            "index_bias": round(bias, 3), "coverage": round(coverage, 3),
            "prob_up_pseudo": round(p_up, 3),
        }

    if bias < -NEUTRAL_BAND:
        direction = "bearish"
    elif bias > NEUTRAL_BAND:
        direction = "bullish"
    else:
        direction = "neutral"

    conv = conviction(bias, coverage, regime_conviction)
    structure = STRUCTURE_MATRIX[(vol_expansion, direction)]
    size_mult = round(min(conv, 1.0) * base_units, 2)

    return {
        "action": "TRADE",
        "structure": structure,
        "direction": direction,
        "vol_state": "expansion (long vol)" if vol_expansion else "range (short vol)",
        "index_bias": round(bias, 3),
        "coverage": round(coverage, 3),
        "conviction": round(conv, 3),
        "size_mult": size_mult,
        "prob_up_pseudo": round(p_up, 3),
        "caveat": "prob_up is UNCALIBRATED; use for ranking/sizing, not EV. "
                  "Size small until calibrate() proves the mapping.",
    }


# --------------------------------------------------------------------------- #
# 4. Demo: the 23-Jun-2026 episode
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # sector sentiment as the news layer would produce it that day:
    # IT loud and bearish; banks mildly soft; Reliance/energy quiet (~0).
    sector_sentiment = {
        "IT": -0.60, "Banks": -0.20, "Telecom": -0.10, "Energy": 0.0,
    }

    sw = sector_weights()
    print("Sector weights (from constituent weights):")
    for s, w in sorted(sw.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {s:<14} {w:5.2f}%")

    bias, cov = index_bias(sector_sentiment)
    print(f"\nindex_bias = {bias:+.3f} | coverage = {cov:.0%} | "
          f"P(up) pseudo = {prob_up(bias):.0%}")

    decision = decide(sector_sentiment, vol_expansion=True, regime_conviction=0.73)
    print("\nDECISION:")
    for k, v in decision.items():
        print(f"  {k:<16}: {v}")

    # contrast: if the loud IT story had NO bank reading (heavyweights silent)
    print("\n--- same IT story, banks silent (coverage trap) ---")
    d2 = decide({"IT": -0.60, "Telecom": -0.10}, vol_expansion=True,
                regime_conviction=0.73)
    for k, v in d2.items():
        print(f"  {k:<16}: {v}")
