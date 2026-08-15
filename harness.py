"""
harness.py
----------
Forward-logging + deferred-evaluation harness. You have NO historical labels yet,
so this cannot score the model on day 1. Instead it:

  1. log_signal(...)   -> append every live signal (bias, momentum, regime, RND
                          reads, prob_up, suggested structure) with a timestamp.
  2. settle(...)       -> next session, attach the realized NIFTY outcome to the
                          matching logged row (close, % move, direction).
  3. evaluate(...)     -> once enough settled rows accrue, score whether the
                          signals had edge: Brier score + reliability bins for
                          prob_up, hit-rate of the directional call, and whether
                          the news+RND view beat a naive baseline.

The harness BUILDS its own calibration set over time. Run it from day one; it
starts producing trustworthy numbers after a few hundred settled sessions.

Storage: JSONL (one row per signal). Swap for a DB later; same schema.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

LOG = os.getenv("HARNESS_LOG", "harness_log.jsonl")


# ── 1. log a live signal ──────────────────────────────────────────────────────
def log_signal(result: dict, spot: float, expiry: str, path: str = LOG) -> dict:
    """result = the dict from run_pipeline(). Logs the decision-relevant fields."""
    row = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "spot_at_signal": spot,
        "expiry": expiry,
        # signal features
        "regime": result["regime"]["dominant"],
        "regime_conviction": result["regime"]["conviction"],
        "vol_expansion": result["regime"]["vol_expansion"],
        "bias": result["bias"],
        "coverage": result["coverage"],
        "momentum": result["momentum"],
        "rnd_p_below": result["rnd"]["p_below_spot"],
        "rnd_move": result["rnd"]["sd"],
        "rnd_skew": result["rnd"]["skew"],
        "relation": result["comparison"]["relation"],
        "suggested": result["suggestion"].get("structure")
                     or result["suggestion"].get("action"),
        # to be filled by settle()
        "realized_close": None,
        "realized_move_pts": None,
        "realized_dir": None,      # 1 up / 0 down vs spot_at_signal
    }
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# ── 2. settle: attach the realized outcome next session ───────────────────────
def settle(signal_id: str, realized_close: float, path: str = LOG):
    """Fill realized_* on the row whose id == signal_id (call once you have the
    next close). Rewrites the file; fine at JSONL scale, swap for a DB later."""
    rows = _read(path)
    hit = False
    for r in rows:
        if r["id"] == signal_id and r["realized_close"] is None:
            r["realized_close"] = realized_close
            r["realized_move_pts"] = realized_close - r["spot_at_signal"]
            r["realized_dir"] = int(realized_close > r["spot_at_signal"])
            hit = True
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return hit


# ── 3. evaluate: only meaningful once enough settled rows exist ───────────────
def evaluate(path: str = LOG, min_n: int = 30, bins: int = 5) -> dict:
    rows = [r for r in _read(path) if r.get("realized_dir") is not None]
    n = len(rows)
    if n < min_n:
        return {"status": "INSUFFICIENT_DATA", "settled": n, "need": min_n,
                "note": "harness is accruing; come back after more settled sessions."}

    # prob_up here is derived from bias via the same logistic the app uses;
    # we recompute a simple monotone prob so the harness is self-contained.
    def prob_up(bias):       # mirror decision_engine default (a=0,b=2.5)
        return 1 / (1 + math.exp(-(2.5 * bias)))

    p = [prob_up(r["bias"]) for r in rows]
    y = [r["realized_dir"] for r in rows]

    # Brier score (lower better; 0.25 = coin flip baseline at p=0.5)
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / n

    # reliability bins: predicted vs observed frequency
    reliability = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p) if lo <= pi < hi or (b == bins - 1 and pi == 1.0)]
        if idx:
            reliability.append({
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": len(idx),
                "pred_mean": round(sum(p[i] for i in idx) / len(idx), 3),
                "obs_freq": round(sum(y[i] for i in idx) / len(idx), 3),
            })

    # directional hit-rate vs a naive 'always up' baseline (markets drift up)
    calls = [(prob_up(r["bias"]) > 0.5, r["realized_dir"]) for r in rows
             if abs(r["bias"]) > 0.15]          # only when the signal took a side
    hit = (sum(int(c == bool(d)) for c, d in calls) / len(calls)) if calls else None
    base_up = sum(y) / n                          # observed up-rate (naive baseline)

    # does the RND-implied direction beat the signal? (sanity: market vs news)
    rnd_calls = [(r["rnd_p_below"] < 0.5, r["realized_dir"]) for r in rows]
    rnd_hit = sum(int(c == bool(d)) for c, d in rnd_calls) / n

    return {
        "status": "OK",
        "settled": n,
        "brier": round(brier, 4),
        "brier_baseline_coinflip": 0.25,
        "reliability": reliability,
        "signal_hit_rate": round(hit, 3) if hit is not None else None,
        "signal_decisions": len(calls),
        "naive_up_rate": round(base_up, 3),
        "rnd_hit_rate": round(rnd_hit, 3),
        "verdict": _verdict(brier, hit, base_up),
    }


def _verdict(brier, hit, base_up):
    if brier >= 0.25:
        return "NO EDGE: Brier ≥ coinflip. Signals are not informative yet."
    if hit is not None and hit <= max(base_up, 1 - base_up):
        return "WEAK: beats Brier but not the naive directional baseline. Treat as risk filter, not alpha."
    return "PROMISING: beats coinflip and naive baseline. Validate with more data before sizing up."


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


if __name__ == "__main__":
    # tiny synthetic demo: log a few signals, settle them, evaluate.
    import random
    random.seed(0)
    demo = "harness_demo.jsonl"
    open(demo, "w").close()
    for i in range(40):
        bias = random.uniform(-0.5, 0.5)
        # synthetic 'reality' with a weak true edge so evaluate has signal
        up = int(random.random() < 1 / (1 + math.exp(-(1.2 * bias))))
        fake = {"regime": {"dominant": "ai_semiconductor", "conviction": 0.6,
                           "vol_expansion": False},
                "bias": bias, "coverage": 0.7, "momentum": 0.4,
                "rnd": {"p_below_spot": 0.5 - bias * 0.3, "sd": 340, "skew": -0.3},
                "comparison": {"relation": "NEUTRAL"},
                "suggestion": {"action": "RANGE"}}
        row = log_signal(fake, spot=24000, expiry="2026-06-30", path=demo)
        settle(row["id"], realized_close=24000 + (50 if up else -50), path=demo)
    from pprint import pprint
    pprint(evaluate(demo))
    os.remove(demo)
