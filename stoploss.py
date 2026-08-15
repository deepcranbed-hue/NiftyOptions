#!/usr/bin/env python3
"""stoploss -- cut the position at a loss threshold and re-enter at the new level.

Rule: mark the short strangle every captured minute. If the unrealised loss on the CURRENT
position exceeds L points, buy both legs back (realising the loss), then immediately sell a
fresh strangle +/-150 around the prevailing spot. Repeat to expiry, settle at intrinsic.

Real LTPs, 1 point slippage per leg traded. Six cycles -- a mechanics check, not evidence.

WHAT TO WATCH: the stop caps the loss on any ONE position, but each re-entry sells fresh
premium at a level the market has just moved to. Whether that limits total loss or simply
manufactures a sequence of small realised losses is the whole question, and it is the same
question the roll test answered badly.
"""
import sqlite3
import numpy as np, pandas as pd

X, SLIP = 150.0, 1.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
PAN = {}
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    C = g.pivot_table(index="dt", columns="strike", values="call_ltp").sort_index().ffill()
    P = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index().ffill()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) >= 60: PAN[exp] = (C, P, sp)

def near(cols, t):
    a = np.asarray(list(cols), dtype=float); return float(a[np.argmin(np.abs(a - t))])
def px(row, k):
    if k not in row.index: return None
    v = row[k]; return float(v) if (v == v and v > 0.05) else None

def run(L):
    res = []
    for exp, (C, P, sp) in PAN.items():
        realised, stops = 0.0, 0
        i = 0
        S = float(sp.iloc[0])
        ck, pk = near(C.columns, S + X), near(P.columns, S - X)
        c0, p0 = px(C.iloc[0], ck), px(P.iloc[0], pk)
        if not (c0 and p0): continue
        entry = c0 + p0 - 2 * SLIP
        worst = 0.0
        for i in range(1, len(sp)):
            cv, pv = px(C.iloc[i], ck), px(P.iloc[i], pk)
            if cv is None or pv is None: continue
            unreal = entry - (cv + pv)
            worst = min(worst, realised + unreal)
            if L is not None and unreal < -L:
                realised += entry - (cv + pv) - 2 * SLIP      # close both legs
                stops += 1
                S = float(sp.iloc[i])
                ck, pk = near(C.columns, S + X), near(P.columns, S - X)
                nc, np_ = px(C.iloc[i], ck), px(P.iloc[i], pk)
                if not (nc and np_): entry = None; break
                entry = nc + np_ - 2 * SLIP
        ST = float(sp.iloc[-1])
        final = realised + (entry - (max(ST - ck, 0) + max(pk - ST, 0)) if entry is not None else 0.0)
        res.append({"exp": str(exp), "pnl": final, "stops": stops, "worst": worst})
    return pd.DataFrame(res)

print("=== STOP-LOSS AND RE-ENTER, six cycles, real LTPs, 1pt/leg ===")
print("%-14s %9s %9s %9s %8s %9s %8s"
      % ("stop level", "total P/L", "mean/cyc", "worst cyc", "wins", "worst DD", "#stops"))
print("-" * 74)
out = {}
for L in (None, 200, 150, 100, 75, 50):
    t = run(L)
    lab = "none (hold)" if L is None else "%d pts" % L
    out[L] = t
    print("%-14s %+9.1f %+9.1f %+9.1f %5d/%d %+9.1f %8d"
          % (lab, t.pnl.sum(), t.pnl.mean(), t.pnl.min(), int((t.pnl > 0).sum()), len(t),
             t.worst.min(), int(t.stops.sum())))

print("\n=== PER-CYCLE DETAIL ===")
print("%-12s" % "expiry" + "".join("%14s" % ("hold" if L is None else "stop %d" % L)
                                    for L in (None, 150, 100, 75, 50)))
print("-" * 82)
for e in out[None].exp:
    line = "%-12s" % e
    for L in (None, 150, 100, 75, 50):
        r = out[L][out[L].exp == e]
        line += "%14s" % ("%+.1f (%d)" % (r.pnl.iloc[0], r.stops.iloc[0]) if len(r) else "-")
    print(line)
print("\n   cells: P/L (number of stops triggered)")
