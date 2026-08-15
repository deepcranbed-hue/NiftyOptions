"""
strategy_framework/signals/straddle_flow.py
============================================
ATM straddle compression / expansion — a VOLATILITY-REGIME read, NOT a direction.

S = C_ATM + P_ATM is the market's priced expected move. Its change over the window:

    compression  S↓  (premium being sold, expected move shrinking)  → pin / range
    expansion    S↑  (premium bid up, expected move growing)         → breakout brewing

This is registered as a GATE (kind='gate'), deliberately, because a straddle is
call+put — it is symmetric and carries NO directional information by construction. Any
"bullish/bearish" score squeezed from it would be fabricated. Its real job is exactly
the one your Layer-0/Layer-20 architecture gives it: a REGIME that tells the directional
signals WHEN a move is even likely, so downstream trust can be modulated (compression →
fade/expect chop; expansion → a real move is coming, respect momentum). The `score` here
encodes expansion(+)/compression(−) MAGNITUDE, not market direction.

Next step (not done here): expose this as a regime axis (regime_by='straddle') so the
P(edge) study can measure whether the directional signals actually work better during
expansion vs compression — that's how the weight it deserves gets EARNED, not typed in.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp
from . import option_oi

_SC_SCALE = 0.15       # a ~15% straddle move → a firm regime reading (PRIOR)


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("straddle_flow", "no option chain as-of now")
    S, k_atm = option_oi.atm_straddle(chain)
    prev = option_oi.prior_chain(da, chain, now)
    if prev is None or S <= 0:
        return Signal("straddle_flow", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"atm_straddle": round(S, 1),
                              "note": "no earlier straddle to measure compression against"})
    S0, _ = option_oi.atm_straddle(prev)
    if S0 <= 0:
        return Signal.no_data("straddle_flow", "no prior straddle")
    change = (S - S0) / S0                        # + = expansion, − = compression
    score = clamp(float(np.tanh(change / _SC_SCALE)))   # magnitude of vol regime, NOT direction
    confidence = clamp(0.30 + 0.5 * min(1.0, abs(change) / _SC_SCALE), 0.30, 0.85)
    regime = ("expansion — move brewing" if change > 0.05 else
              "compression — pinning/range" if change < -0.05 else "stable")
    return Signal("straddle_flow", float(score), float(confidence), "PRIOR",
                  detail={"atm_straddle": round(S, 1), "prev_straddle": round(S0, 1),
                          "change_pct": round(change * 100, 1), "atm_strike": k_atm,
                          "regime": regime,
                          "note": "vol-regime gate (expansion+/compression−), not a direction"})
