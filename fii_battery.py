#!/usr/bin/env python3
"""fii_battery -- level / change / acceleration / extremes, and an honest power analysis.

WHY A POWER ANALYSIS IS THE POINT. The FII series cannot be extended: participant_flows
starts 2025-08-07 (247 days) and nothing deeper exists in this repo. So the question
"is 'FII does not forecast' structural, or just low power?" cannot be settled with more
data -- it can only be settled by stating precisely what n=247 is able to see. That is
what the last section does, and it is the most important output here.

FOUR SPECIFICATIONS
  LEVEL   idx_net, z-scored   -- is a heavily long/short standing book a regime?
  CHANGE  d(idx_net)          -- does fresh positioning move the cross-section?
  ACCEL   d2(idx_net)         -- does the RATE of change lead anything?
  EXTREME bottom/top quintile -- is the relationship nonlinear at the tails, where a
                                 linear correlation would average it away to nothing?
across forward horizons of 1, 2, 3 and 5 days, always on returns RELATIVE to Nifty.

Overlapping forward windows for h>1 make ordinary standard errors far too small, so
Newey-West (lag h+2) is used throughout.
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
HOR = [1, 2, 3, 5]
con = sqlite3.connect("option_chains.db")
need = SECT + ["NIFTY"]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN (%s)"
                 % ",".join("?" * len(need)), con, params=need)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill()
pf = pd.read_sql("SELECT flow_date,idx_fut_long,idx_fut_short FROM participant_flows "
                 "WHERE participant_type='FII' ORDER BY flow_date", con)
pf["d"] = pd.to_datetime(pf.flow_date); pf = pf.set_index("d")
net = (pf.idx_fut_long - pf.idx_fut_short).rename("lvl")
F = pd.DataFrame({"LEVEL": (net - net.mean()) / net.std(),
                  "CHANGE": net.diff(), "ACCEL": net.diff().diff()})
F["CHANGE"] = (F.CHANGE - F.CHANGE.mean()) / F.CHANGE.std()
F["ACCEL"] = (F.ACCEL - F.ACCEL.mean()) / F.ACCEL.std()

def nwt(x, y, lag):
    X = np.column_stack([np.ones(len(x)), x]); XtXi = np.linalg.pinv(X.T @ X)
    b = XtXi @ X.T @ y; e = y - X @ b
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1.0)
        A = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None]); S += w * (A + A.T)
    V = XtXi @ S @ XtXi
    return b[1], b[1] / np.sqrt(max(V[1, 1], 1e-30))

print("%-8s %-7s" % ("spec", "h") + "".join("%9s" % s.replace("NIFTY", "")[:7] for s in SECT))
print("-" * 106)
hits = tot = 0; out = {}
for spec in ("LEVEL", "CHANGE", "ACCEL"):
    for h in HOR:
        line = "%-8s %-7d" % (spec, h)
        for s in SECT:
            fwd = ((P[s].shift(-h) / P[s] - 1) - (P["NIFTY"].shift(-h) / P["NIFTY"] - 1)) * 100
            D = pd.concat([fwd.rename("y"), F[spec].rename("x")], axis=1).dropna()
            if len(D) < 60: line += "%9s" % "--"; continue
            b, t = nwt(D.x.values, D.y.values, h + 2)
            hits += abs(t) > 2; tot += 1
            out["%s_h%d_%s" % (spec, h, s)] = {"beta": round(float(b), 4), "t": round(float(t), 2)}
            line += "%9s" % ("%+.2f%s" % (t, "*" if abs(t) > 2 else ""))
        print(line)
print("\n|t|>2 : %d of %d   (expected ~%.0f by chance, and the 11 sectors are NOT independent)"
      % (hits, tot, tot * 0.05))

# ---- EXTREMES: is it nonlinear at the tails? ----
print("\nEXTREME POSITIONING -- forward 5d relative return by FII net-position quintile")
q = pd.qcut(F.LEVEL.dropna(), 5, labels=["Q1 most short", "Q2", "Q3", "Q4", "Q5 most long"])
print("%-16s %6s" % ("quintile", "n") + "".join("%9s" % s.replace("NIFTY", "")[:7] for s in SECT))
print("-" * 106)
ext = {}
for lab in q.cat.categories:
    days = q[q == lab].index
    line = "%-16s %6d" % (lab, len(days)); ext[str(lab)] = {}
    for s in SECT:
        fwd = ((P[s].shift(-5) / P[s] - 1) - (P["NIFTY"].shift(-5) / P["NIFTY"] - 1)) * 100
        v = fwd.reindex(days).dropna()
        ext[str(lab)][s] = round(float(v.mean()), 3) if len(v) else None
        line += "%9s" % ("%+.2f" % v.mean() if len(v) else "--")
    print(line)

# ---- POWER: what can n=247 actually see? ----
n = int(F.CHANGE.notna().sum())
mdr = (1.96 + 0.84) / np.sqrt(n - 3)
relstd = float(np.mean([ (( (P[s]/P[s].shift(1)-1) - (P["NIFTY"]/P["NIFTY"].shift(1)-1) )*100).std()
                         for s in SECT]))
print("\n" + "=" * 78)
print("POWER ANALYSIS -- what a null at n=%d does and does not rule out" % n)
print("=" * 78)
print("  minimum |r| detectable at 80%% power, alpha=0.05 : %.3f" % mdr)
print("  observed mean |next-day r| from the earlier test  : 0.042")
print("  mean daily std of sector relative return          : %.2f%%" % relstd)
for r in (0.05, 0.10, 0.15, mdr):
    print("    a TRUE r of %.3f would mean %.3f%% per day of expected relative return"
          "   -> %.1f%% a year, and n=%d %s see it"
          % (r, r * relstd, r * relstd * 250, n, "CAN" if r >= mdr else "CANNOT reliably"))
print("\n>>> n=247 rules out effects larger than |r|~%.2f. It does NOT rule out |r| of"
      " 0.05-0.15,\n    which at %.2f%% daily dispersion is economically large. 'FII does not"
      " forecast'\n    is UNPROVEN, not established -- and this repo cannot settle it without"
      " more history." % (mdr, relstd))
json.dump({"n": n, "min_detectable_r": round(float(mdr), 3), "rel_daily_std_pct": round(relstd, 3),
           "hits": int(hits), "tests": int(tot), "coefficients": out, "extremes_fwd5d": ext},
          open("fii_battery_result.json", "w"), indent=1)
print("\nwrote fii_battery_result.json")
