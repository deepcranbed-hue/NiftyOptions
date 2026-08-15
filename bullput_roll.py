#!/usr/bin/env python3
"""bullput_roll -- the ACTUAL strategy: bull put spread, take profit early, re-enter.

Sell PE at spot-150, buy PE at spot-350. Mark every captured minute. When the spread has
captured P% of its credit, close BOTH legs and immediately re-open at spot-150 / spot-350
around the NEW spot. Repeat to expiry; settle whatever is open.

This is not the hold-to-expiry version tested before, and the difference matters: taking
profit early raises the hit rate but shrinks each win, while every round trip costs four
legs of slippage. Whether that trades favourably is the question.

1 point slippage per leg. Real LTPs. Six cycles -- mechanics, not evidence.
"""
import sqlite3
import numpy as np, pandas as pd

SHORT_OFF, LONG_OFF, SLIP = 150.0, 350.0, 1.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
ch = pd.read_sql("SELECT capture_id,expiry,strike,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
PAN = {}
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    P = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index().ffill()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) >= 60: PAN[exp] = (P, sp)

def near(cols, t):
    a = np.asarray(list(cols), dtype=float); return float(a[np.argmin(np.abs(a - t))])
def val(row, k):
    if k not in row.index: return None
    v = row[k]; return float(v) if (v == v and v > 0.05) else None

def run(take):          # take = fraction of credit captured before closing; None = hold
    out = []
    for exp, (P, sp) in PAN.items():
        realised, trips, dd = 0.0, 0, 0.0
        S = float(sp.iloc[0])
        sk, lk = near(P.columns, S - SHORT_OFF), near(P.columns, S - LONG_OFF)
        sv, lv = val(P.iloc[0], sk), val(P.iloc[0], lk)
        if not (sv and lv): continue
        credit = sv - lv - 2 * SLIP
        live = True
        for i in range(1, len(sp)):
            row = P.iloc[i]
            a, b = val(row, sk), val(row, lk)
            if a is None or b is None or not live: continue
            cur = credit - (a - b)                     # mark to market
            dd = min(dd, realised + cur)
            if take is not None and cur >= take * (credit + 2 * SLIP):
                realised += cur - 2 * SLIP             # close both legs
                trips += 1
                S = float(sp.iloc[i])
                nsk, nlk = near(P.columns, S - SHORT_OFF), near(P.columns, S - LONG_OFF)
                nsv, nlv = val(row, nsk), val(row, nlk)
                if not (nsv and nlv) or nsk <= nlk: live = False; break
                sk, lk = nsk, nlk
                credit = nsv - nlv - 2 * SLIP
        ST = float(sp.iloc[-1])
        final = realised + (credit - (max(sk - ST, 0) - max(lk - ST, 0)) if live else 0.0)
        out.append({"exp": str(exp), "pnl": final, "trips": trips, "dd": dd})
    return pd.DataFrame(out)

print("=== BULL PUT SPREAD: take profit at X%% of credit, then re-enter ===")
print("%-16s %10s %9s %9s %7s %9s %10s"
      % ("rule", "total P/L", "mean/cyc", "worst cyc", "wins", "worst DD", "round trips"))
print("-" * 78)
res = {}
for take, lab in ((None, "hold to expiry"), (0.75, "take 75%"), (0.50, "take 50%"),
                  (0.35, "take 35%"), (0.25, "take 25%")):
    t = run(take); res[lab] = t
    print("%-16s %+10.1f %+9.1f %+9.1f %4d/%d %+9.1f %10d"
          % (lab, t.pnl.sum(), t.pnl.mean(), t.pnl.min(), int((t.pnl > 0).sum()), len(t),
             t.dd.min(), int(t.trips.sum())))

print("\n=== PER CYCLE ===")
print("%-12s" % "expiry" + "".join("%15s" % k for k in res))
print("-" * 90)
for e in res["hold to expiry"].exp:
    line = "%-12s" % e
    for k, t in res.items():
        r = t[t.exp == e]
        line += "%15s" % ("%+.1f (%d)" % (r.pnl.iloc[0], r.trips.iloc[0]) if len(r) else "-")
    print(line)
print("\n   cells: P/L (round trips).  Slippage 4 legs per round trip.")
