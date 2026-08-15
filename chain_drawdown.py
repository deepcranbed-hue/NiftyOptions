#!/usr/bin/env python3
"""chain_drawdown -- mark-to-market drawdown of a short strangle on REAL option prices.

Marked every captured minute from chain_rows LTPs, not simulated from index returns.
Timestamps converted to IST. Covers the six expiry cycles in the capture window
(2026-06-29 .. 2026-08-10) -- the only period where minute-level option prices exist.

Equity = credit received - current value of the two short legs.
Drawdown = running peak equity - current equity, in index points.
"""
import sqlite3
import numpy as np, pandas as pd

X = 150.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")

print("=== PER-CYCLE MAX DRAWDOWN, marked on real LTPs (all times IST) ===")
print("%-11s %-16s %7s %7s %7s | %-16s %8s %9s | %8s"
      % ("expiry", "entry", "spot", "CE K", "PE K", "MAX DD at", "spot", "drawdown", "final"))
print("-" * 118)
allpts = []
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    C = g.pivot_table(index="dt", columns="strike", values="call_ltp").sort_index()
    P = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) < 60: continue
    S0 = float(sp.iloc[0])
    ks = np.asarray(sorted(set(C.columns) & set(P.columns)), dtype=float)
    ck = float(ks[np.argmin(np.abs(ks - (S0 + X)))]); pk = float(ks[np.argmin(np.abs(ks - (S0 - X)))])
    c0, p0 = C[ck].iloc[0], P[pk].iloc[0]
    if not (c0 > 0 and p0 > 0): continue
    val = C[ck].ffill() + P[pk].ffill()
    eq = (c0 + p0) - val
    eq = eq.dropna()
    peak = eq.cummax(); dd = eq - peak
    t = dd.idxmin()
    allpts.append(pd.DataFrame({"dt": eq.index, "dd": dd.values, "eq": eq.values,
                                "spot": sp.reindex(eq.index).values, "exp": str(exp)}))
    print("%-11s %-16s %7.0f %7.0f %7.0f | %-16s %8.0f %+9.1f | %+8.1f"
          % (exp, eq.index[0].strftime("%d-%b %H:%M"), S0, ck, pk,
             t.strftime("%d-%b %H:%M"), sp.asof(t), dd.min(), eq.iloc[-1]))

A = pd.concat(allpts, ignore_index=True)
print("\n=== 12 DEEPEST DRAWDOWN MINUTES ACROSS ALL CYCLES ===")
print("%-18s %-11s %9s %10s %10s" % ("timestamp IST", "expiry", "spot", "drawdown", "equity"))
print("-" * 62)
for r in A.nsmallest(12, "dd").itertuples():
    print("%-18s %-11s %9.0f %+10.1f %+10.1f"
          % (r.dt.strftime("%a %d-%b %H:%M"), r.exp, r.spot, r.dd, r.eq))

print("\n=== WHEN DOES THE DAMAGE HAPPEN? drawdown deepening by half-hour (IST) ===")
A = A.sort_values(["exp", "dt"])
A["ddelta"] = A.groupby("exp").dd.diff()
A["slot"] = A.dt.dt.floor("30min").dt.strftime("%H:%M")
w = A[A.ddelta < 0].groupby("slot").ddelta.sum()
tot = w.sum()
for slot, v in w.sort_values().head(8).items():
    print("   %-8s %+9.1f pts  (%2.0f%% of all deepening)" % (slot, v, v / tot * 100))
print("\n   first 30 min of the session: %.0f%% of all drawdown deepening"
      % (w[w.index.isin(["09:00", "09:15", "09:30"])].sum() / tot * 100))
gap = A[A.dt.dt.strftime("%H:%M") <= "09:20"]
print("   note: the open print carries the overnight gap -- it appears here as one minute.")
A.to_json("chain_drawdown_result.json", orient="records", indent=1)
print("\nwrote chain_drawdown_result.json")
