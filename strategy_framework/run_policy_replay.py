"""
strategy_framework/run_policy_replay.py
=======================================
BACKTEST OF THE DECISION HIERARCHY ITSELF — replay every labelled morning in
chronological order and let the engine decide, walk-forward honest:

  * gates + regime use only that morning's features (always legitimate);
  * the ranking's analogue days are restricted to STRICTLY EARLIER dates — the
    engine never sees the future, so early days mostly STAND ASIDE (no history);
  * familiarity < 40% or fewer than 3 usable neighbours → STAND ASIDE (the
    unfamiliar-state rule);
  * chosen structure's P&L comes from the same simulator as every study
    (1 lot, ₹85/leg all-in costs).

Compared against: always-condor, always-straddle, and hindsight-best over the SAME
days. HONESTY CAVEAT, printed loudly: the gate thresholds (coverage 0.05/0.10)
were chosen AFTER seeing July — so this replay contains look-back flavour and
OVERSTATES the gates' skill. It demonstrates mechanics; only prospective weeks
(the decision journal) measure real performance.

    python -m strategy_framework.run_policy_replay
"""
from __future__ import annotations
import csv
import os

import numpy as np

from strategy_framework.run_analogue_days import _FEATS, _f

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_STRUCTS = ["condor", "fly", "strangle", "straddle"]
_CAPPED = {"condor", "fly"}
_REGIME_ALLOWED = {"expansion": [], "compressed-gamma": [],
                   "pin": ["fly", "straddle"],
                   "compression": ["straddle", "strangle", "fly", "condor"],
                   "mixed": ["straddle", "strangle", "fly", "condor"]}


def _regime(cov, er, chop, schg, pin):
    if cov is not None and cov < 0.05:
        return "compressed-gamma"
    if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
        return "expansion"
    if pin is not None and pin >= 0.15 and (chop is not None and chop >= 50):
        return "pin"
    if (chop is not None and chop >= 55) or (er is not None and er <= 0.20):
        return "compression"
    return "mixed"


def main():
    ms = list(csv.DictReader(open(os.path.join(_KNOW, "market_state.csv"))))
    cs = {r["entry"]: r for r in csv.DictReader(open(os.path.join(_KNOW, "chain_structure.csv")))}
    idx = {r["date"]: i for i, r in enumerate(ms)}
    days = [d for d in idx if d in cs]
    days.sort()

    X = np.array([[(_f(r, c) if _f(r, c) is not None else np.nan) for c in _FEATS] for r in ms], float)
    mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd

    print(f"{'date':<12}{'regime':<17}{'gate':<7}{'fam%':>5}{'chosen':>10}{'P&L':>8}"
          f"{'best':>10}{'regret':>8}")
    print("-" * 78)
    tot = {"policy": 0.0, "condor": 0.0, "straddle": 0.0, "best": 0.0, "regret": 0.0}
    n_trade = n_stand = 0
    for k, d in enumerate(days):
        t = ms[idx[d]]
        cov, er = _f(t, "coverage_ratio"), _f(t, "er30")
        chop, schg = _f(t, "chop_index"), _f(t, "straddle_chg_30m_pct")
        pin = _f(t, "pin_share")
        reg = _regime(cov, er, chop, schg, pin)
        fatal = (cov is not None and cov < 0.05) or \
                (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55)
        restrict = (not fatal) and (cov is not None and cov < 0.10)

        # walk-forward neighbours: labelled days STRICTLY BEFORE d
        hist = [idx[p] for p in days[:k]]
        sims = []
        ti = idx[d]
        for j in hist:
            m = ~np.isnan(Z[ti]) & ~np.isnan(Z[j])
            if m.sum() >= 8:
                sims.append((float(np.sqrt(np.mean((Z[ti][m] - Z[j][m]) ** 2))), j))
        sims.sort()
        top = sims[:7]
        avg_d = float(np.mean([x[0] for x in top])) if top else None
        fam = max(0.0, min(100.0, (1.6 - avg_d) / 1.6 * 100)) if avg_d is not None else 0.0

        allowed = [] if fatal else [s for s in _REGIME_ALLOWED[reg]
                                    if not (restrict and s in _CAPPED)]
        chosen = "STAND"
        if allowed and len(top) >= 3 and fam >= 40:
            w = np.array([1.0 / (x[0] + 0.25) for x in top]); w /= w.sum()
            scores = {}
            for s in allowed:
                pnl = np.array([_f(cs[ms[j]["date"]], f"{s}_pnl") or 0.0 for _, j in top])
                scores[s] = float((w * pnl).sum())
            chosen = max(scores, key=scores.get)

        pnls = {s: _f(cs[d], f"{s}_pnl") or 0.0 for s in _STRUCTS}
        got = pnls.get(chosen, 0.0) if chosen != "STAND" else 0.0
        best_s = max(pnls, key=pnls.get)
        best = max(pnls[best_s], 0.0)              # standing aside is an option in hindsight too
        regret = best - max(got, 0.0) if chosen == "STAND" else pnls[best_s] - got
        gate_tag = "L1" if fatal else ("L2" if restrict else "-")
        print(f"{d:<12}{reg:<17}{gate_tag:<7}{fam:>5.0f}{chosen:>10}{got:>8.0f}"
              f"{best_s:>10}{max(regret,0):>8.0f}")
        tot["policy"] += got
        tot["condor"] += pnls["condor"]
        tot["straddle"] += pnls["straddle"]
        tot["best"] += pnls[best_s]
        tot["regret"] += max(regret, 0)
        n_trade += chosen != "STAND"
        n_stand += chosen == "STAND"

    print("-" * 78)
    print(f"POLICY (hierarchy):  ₹{tot['policy']:>8.0f}   ({n_trade} trades, {n_stand} stand-asides)")
    print(f"always-condor:       ₹{tot['condor']:>8.0f}")
    print(f"always-straddle:     ₹{tot['straddle']:>8.0f}")
    print(f"hindsight-best:      ₹{tot['best']:>8.0f}   cumulative regret ₹{tot['regret']:.0f}")
    print("\nHONESTY: gate thresholds were set AFTER seeing this month — the replay contains")
    print("look-back flavour and flatters the gates. Early days stand aside for lack of")
    print("history (correct walk-forward behaviour). Real performance = the journal, forward.")


if __name__ == "__main__":
    main()
