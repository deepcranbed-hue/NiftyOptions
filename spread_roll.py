#!/usr/bin/env python3
"""spread_roll -- bull put spread on the FAR expiry, rolled up as spot rises.

Entry: sell PE at spot-200, buy PE at spot-400.
Roll:  when spot has risen TRIG points from the entry spot, close BOTH legs and reopen
       the same -200/-400 structure around the new spot. 4 legs per roll, 1pt each.
Exit:  settle whatever is open at the expiry.

Run on the FAR expiry (the second one quoted at entry, which is what is actually traded)
and on the NEAR expiry for contrast, since the far expiry pays 39% less theta per day and
the question is whether the roll mechanic makes up for it.

A symmetric variant (roll on a move either way) is included, because rolling only on
rallies means a fall is simply held -- which is where the losses were.
"""
import sqlite3
import numpy as np, pandas as pd

SO, LO, SLIP = 200.0, 400.0, 1.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
ch = pd.read_sql("SELECT capture_id,expiry,strike,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
nexp = ch.groupby("capture_id").exp.nunique()

def series(which):
    """for each expiry, the captures where it is the NEAR (0) or FAR (1) quoted expiry."""
    out = {}
    for cid, g in ch[ch.capture_id.isin(nexp[nexp > 1].index)].groupby("capture_id"):
        exps = sorted(g.exp.unique())
        if which >= len(exps): continue
        out.setdefault(exps[which], []).append(cid)
    return out

def build(cids, exp):
    g = ch[(ch.capture_id.isin(cids)) & (ch.exp == exp)]
    P = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index().ffill()
    sp = g.groupby("dt")["spot"].first().sort_index()
    return P, sp

def near_k(cols, t):
    a = np.asarray(list(cols), dtype=float); return float(a[np.argmin(np.abs(a - t))])
def v(row, k):
    if k not in row.index: return None
    x = row[k]; return float(x) if (x == x and x > 0.05) else None

def run(which, trig, symmetric=False):
    res = []
    for exp, cids in series(which).items():
        P, sp = build(cids, exp)
        if len(sp) < 60: continue
        S = float(sp.iloc[0])
        sk, lk = near_k(P.columns, S - SO), near_k(P.columns, S - LO)
        sv, lv = v(P.iloc[0], sk), v(P.iloc[0], lk)
        if not (sv and lv): continue
        credit = sv - lv - 2 * SLIP
        anchor, realised, rolls, dd = S, 0.0, 0, 0.0
        live = True
        for i in range(1, len(sp)):
            row = P.iloc[i]; Sn = float(sp.iloc[i])
            a, b = v(row, sk), v(row, lk)
            if a is None or b is None: continue
            dd = min(dd, realised + credit - (a - b))
            hit = (Sn - anchor >= trig) if not symmetric else (abs(Sn - anchor) >= trig)
            if trig and hit:
                realised += credit - (a - b) - 2 * SLIP
                rolls += 1
                nsk, nlk = near_k(P.columns, Sn - SO), near_k(P.columns, Sn - LO)
                nsv, nlv = v(row, nsk), v(row, nlk)
                if not (nsv and nlv) or nsk <= nlk: live = False; break
                sk, lk, anchor = nsk, nlk, Sn
                credit = nsv - nlv - 2 * SLIP
        ST = float(sp.iloc[-1])
        fin = realised + (credit - (max(sk - ST, 0) - max(lk - ST, 0)) if live else 0.0)
        res.append({"exp": str(exp), "pnl": fin, "rolls": rolls, "dd": dd})
    return pd.DataFrame(res)

for which, lab in ((1, "FAR EXPIRY"), (0, "NEAR EXPIRY")):
    print("\n=== %s : sell PE spot-200 / buy PE spot-400, rolled up ===" % lab)
    print("%-22s %6s %10s %9s %9s %6s %9s"
          % ("rule", "cycles", "total P/L", "mean", "worst", "wins", "rolls"))
    print("-" * 76)
    for trig, rl in ((0, "static (no roll)"), (100, "roll on +100"), (150, "roll on +150"),
                     (200, "roll on +200"), (-1, "roll on +/-100")):
        t = run(which, 100 if trig == -1 else trig, symmetric=(trig == -1))
        if t.empty: continue
        print("%-22s %6d %+10.1f %+9.1f %+9.1f %4d/%d %9d"
              % (rl, len(t), t.pnl.sum(), t.pnl.mean(), t.pnl.min(),
                 int((t.pnl > 0).sum()), len(t), int(t.rolls.sum())))
