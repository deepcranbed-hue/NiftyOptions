"""
strategy_framework/run_analogue_days.py
=======================================
ANALOGUE DAYS — "which historical days look most like today, and what worked then?"

Instead of predicting returns, find the nearest historical market states and read
their outcomes. No fitted weights anywhere: features are z-scored in-sample and
compared with plain Euclidean distance (Mahalanobis once the sample can support a
covariance estimate). This is deliberately non-parametric — the honest version of a
regime engine while n is small.

Feature vector spans the four state layers (chain + tape only, no external data):
  trend     er30, chop_index, overnight_gap_pct, ret_open_to_1000_pct, prev_day_ret_pct
  premium   atm_straddle_pts, straddle_chg_30m_pct
  dealer    pcr, coverage_ratio, cov_adj_premium, pin_share, oi_std_pts,
            put/call wall distances
(sector layer joins automatically as those columns exist in market_state.csv)

For the target date it prints the top-k analogues with what HAPPENED on them (same-day
move, range, next gap, straddle expansion, and — where the structure race covered that
day — which structure won). ~25 sessions = neighbours are suggestive, never proof.

    python -m strategy_framework.run_analogue_days --date 2026-07-29 --k 5
"""
from __future__ import annotations
import argparse
import csv
import os

import numpy as np

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_FEATS = ["er30", "chop_index", "overnight_gap_pct", "ret_open_to_1000_pct",
          "prev_day_ret_pct", "atm_straddle_pts", "straddle_chg_30m_pct",
          "pcr", "coverage_ratio", "cov_adj_premium", "pin_share", "oi_std_pts",
          "put_wall_dist_pts", "call_wall_dist_pts"]
_OUTS = ["day_ret_1000_close_pct", "day_range_pts", "next_gap_pct",
         "straddle_exp_close_pct", "crush_excess_pct"]


def _f(r, k):
    v = r.get(k)
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Nearest historical market states.")
    ap.add_argument("--date", default=None, help="target date (default: latest row)")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(os.path.join(_KNOW, "market_state.csv"))))
    best = {}
    cs_path = os.path.join(_KNOW, "chain_structure.csv")
    if os.path.exists(cs_path):
        for r in csv.DictReader(open(cs_path)):
            best[r["entry"]] = (r.get("best_structure"),
                                _f(r, "condor_pnl"), _f(r, "straddle_pnl"))

    dates = [r["date"] for r in rows]
    target = args.date or dates[-1]
    if target not in dates:
        print(f"no row for {target}; have {dates[0]}..{dates[-1]}")
        return
    ti = dates.index(target)

    X = np.array([[(_f(r, c) if _f(r, c) is not None else np.nan) for c in _FEATS]
                  for r in rows], float)
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd

    d = []
    for j in range(len(rows)):
        if j == ti:
            continue
        m = ~np.isnan(Z[ti]) & ~np.isnan(Z[j])
        if m.sum() < 8:
            continue
        d.append((float(np.sqrt(np.mean((Z[ti][m] - Z[j][m]) ** 2))), j))
    d.sort()

    t = rows[ti]
    print(f"TARGET {target}: er30={t.get('er30')} chop={t.get('chop_index')} "
          f"straddle={t.get('atm_straddle_pts')} coverage={t.get('coverage_ratio')} "
          f"pcr={t.get('pcr')} covAdjPrem={t.get('cov_adj_premium')}")
    print(f"\nnearest {args.k} analogue days (z-distance over {len(_FEATS)} features):")
    print(f"{'date':<12}{'dist':>6}{'dayRet%':>9}{'range':>7}{'nextGap%':>9}"
          f"{'crush%':>8}{'best struct':>12}{'condor':>8}")
    print("-" * 72)
    for dist, j in d[:args.k]:
        r = rows[j]
        b = best.get(r["date"], (None, None, None))
        print(f"{r['date']:<12}{dist:>6.2f}"
              f"{(_f(r,'day_ret_1000_close_pct') or 0):>9.2f}"
              f"{(_f(r,'day_range_pts') or 0):>7.0f}"
              f"{(_f(r,'next_gap_pct') or 0):>9.2f}"
              f"{(_f(r,'crush_excess_pct') or 0):>8.1f}"
              f"{(b[0] or '·'):>12}"
              f"{(f'{b[1]:.0f}' if b[1] is not None else '·'):>8}")
    print("-" * 72)
    print(f"Read: what HAPPENED on the days most like {target}. n={len(rows)} sessions — "
          f"suggestive, not statistical. Re-run daily as rows accumulate.")


if __name__ == "__main__":
    main()
