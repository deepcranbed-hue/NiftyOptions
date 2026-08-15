"""
strategy_framework/run_chain_structure_study.py
===============================================
CHAIN-STATE → STRUCTURE SELECTION: does the 10:00 option chain tell you WHICH
premium structure (or none) to deploy?

One row per expiry-week entry. Features are CHAIN-ONLY (no external market data),
including the CHANGE features that matter more than levels:

  atm_straddle_pts, straddle_chg_30m_pct           priced move + its 30-min drift
  pcr, d_pcr_1d                                    put/call OI ratio + Δ vs yesterday 10:00
  put_wall_dist_pts, call_wall_dist_pts            wall geometry vs spot
  put_wall_migration_pts, call_wall_migration_pts  where the walls MOVED since yesterday
  coverage_ratio                                   min(wall dist) / (0.8·straddle) — are the
                                                   short strikes OUTSIDE the priced 1σ move?
                                                   (<1 = selling inside the expected move,
                                                   the 21-Jul red flag, formalised)
  pin_share, oi_std_pts                            concentration of the OI distribution

Outcomes per entry (same simulator/costs as the structure race, 1pt slippage):
  condor / fly / strangle / straddle final P&L (hold to expiry), each structure's
  worst mark, best_structure label, and condor_inside (settled inside the walls?).

Output: knowledge/chain_structure.csv + descriptive correlations. ~15 entries =
DESCRIPTIVE ONLY; the value is the harness — every new expiry adds 4 rows.

    python -m strategy_framework.run_chain_structure_study --db option_chains_full.db
"""
from __future__ import annotations
import argparse
import csv
import os
import sqlite3

import numpy as np

from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import option_oi
from strategy_framework.run_condor_study import run_static_structure

_ENTRY = "04:30"
_STRUCTS = ["condor", "fly", "strangle", "straddle"]


def _chain_feats(da, ts, exp):
    ch = da.chain_as_of(ts, exp)
    if ch is None or ch.ts[:10] != ts[:10]:
        return None
    S, _ = option_oi.atm_straddle(ch)
    prior = option_oi.prior_chain(da, ch, ts, lookback_min=30)
    S0 = option_oi.atm_straddle(prior)[0] if prior else None
    tot_p = sum(v or 0 for v in ch.put_oi.values())
    tot_c = sum(v or 0 for v in ch.call_oi.values())
    below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
    above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
    if not below or not above or not S:
        return None
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]
    conc = option_oi.oi_concentration(ch)
    _, _, pin_share = option_oi.pin_strike(ch)
    return {"spot": ch.spot, "atm_straddle_pts": round(S, 1),
            "straddle_chg_30m_pct": (round((S / S0 - 1) * 100, 2) if S0 else None),
            "pcr": round(tot_p / tot_c, 3) if tot_c else None,
            "put_wall": pw, "call_wall": cw,
            "put_wall_dist_pts": round(ch.spot - pw, 0),
            "call_wall_dist_pts": round(cw - ch.spot, 0),
            "coverage_ratio": round(min(ch.spot - pw, cw - ch.spot) / (0.8 * S), 2),
            "pin_share": round(pin_share, 3),
            "oi_std_pts": round(conc["std"], 0) if conc else None}


def build(db):
    da = DataAccess(db)
    con = sqlite3.connect(db)
    exps = [r[0] for r in con.execute(
        "SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE '2026-%' ORDER BY expiry")]
    rows = []
    for exp in exps:
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c "
            "JOIN chain_rows r ON r.capture_id=c.capture_id AND r.expiry=? ORDER BY 1", (exp,))]
        entries = [d for d in days if d < exp[:10]][-4:]
        for i, d in enumerate(entries):
            ts = f"{d}T{_ENTRY}:00Z"
            f = _chain_feats(da, ts, exp)
            if not f:
                continue
            # Δ vs yesterday 10:00 (same expiry) — CHANGES beat levels
            prev_day = entries[i - 1] if i > 0 else None
            fp = _chain_feats(da, f"{prev_day}T{_ENTRY}:00Z", exp) if prev_day else None
            f["d_pcr_1d"] = (round(f["pcr"] - fp["pcr"], 3) if fp and fp.get("pcr") else None)
            f["put_wall_migration_pts"] = (round(f["put_wall"] - fp["put_wall"], 0) if fp else None)
            f["call_wall_migration_pts"] = (round(f["call_wall"] - fp["call_wall"], 0) if fp else None)

            outs = {}
            ok = True
            for st in _STRUCTS:
                r = run_static_structure(db, exp, ts, structure=st, wing=100, slippage_pts=1.0)
                if r.get("error"):
                    ok = False
                    break
                outs[f"{st}_pnl"] = r["final_pnl"]
                outs[f"{st}_worst"] = r["worst_mark"]
                if st == "condor":
                    outs["condor_kp"], outs["condor_kc"] = r["kp"], r["kc"]
            if not ok:
                continue
            outs["best_structure"] = max(_STRUCTS, key=lambda s: outs[f"{s}_pnl"])
            row = {"expiry": exp[:10], "entry": d, **f, **outs}
            rows.append(row)
    con.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "knowledge", "chain_structure.csv"))
    args = ap.parse_args()
    rows = build(args.db)
    if not rows:
        print("no rows")
        return
    cols = list(rows[0].keys())
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows → {args.out}\n")

    print(f"{'entry':<7}{'covg':>6}{'sΔ30':>7}{'ΔPCR':>7}{'pwMig':>7}{'best':>10}"
          f"{'condor':>8}{'straddle':>9}")
    print("-" * 62)
    for r in rows:
        sc = r["straddle_chg_30m_pct"]
        dp = r["d_pcr_1d"]
        pm = r["put_wall_migration_pts"]
        sc_s = f"{sc:+.1f}" if sc is not None else "   ·"
        dp_s = f"{dp:+.2f}" if dp is not None else "   ·"
        pm_s = f"{pm:+.0f}" if pm is not None else "  ·"
        print(f"{r['entry'][5:]:<7}{r['coverage_ratio']:>6}{sc_s:>7}{dp_s:>7}{pm_s:>7}"
              f"{r['best_structure']:>10}{r['condor_pnl']:>8.0f}{r['straddle_pnl']:>9.0f}")
    print("-" * 62)

    feats = ["coverage_ratio", "atm_straddle_pts", "straddle_chg_30m_pct", "pcr", "d_pcr_1d",
             "put_wall_dist_pts", "call_wall_dist_pts", "put_wall_migration_pts",
             "call_wall_migration_pts", "pin_share", "oi_std_pts"]
    labels = [f"{s}_pnl" for s in _STRUCTS] + ["condor_worst", "straddle_worst"]
    print(f"\nchain-feature ↔ structure-outcome |corr|  (n={len(rows)} — DESCRIPTIVE ONLY):")
    out = []
    for lab in labels:
        y = np.array([r.get(lab) if r.get(lab) is not None else np.nan for r in rows], float)
        for ft in feats:
            x = np.array([r.get(ft) if r.get(ft) is not None else np.nan for r in rows], float)
            m = ~np.isnan(x) & ~np.isnan(y)
            if m.sum() >= 8 and x[m].std() > 0 and y[m].std() > 0:
                out.append((abs(float(np.corrcoef(x[m], y[m])[0, 1])),
                            float(np.corrcoef(x[m], y[m])[0, 1]), ft, lab, int(m.sum())))
    out.sort(reverse=True)
    for a, r_, ft, lab, n in out[:10]:
        print(f"  {ft:<26} ↔ {lab:<16} r={r_:+.2f}  (n={n})")


if __name__ == "__main__":
    main()
