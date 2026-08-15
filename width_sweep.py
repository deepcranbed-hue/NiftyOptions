#!/usr/bin/env python3
"""width_sweep -- the untested parameter.

Every experiment in this thread has varied the MANAGEMENT rule with the strike distance
frozen at 150 points. But X was never chosen on evidence -- it was an opening assumption,
and it sets the delta, the gamma, the premium and the breach probability all at once.
Before optimising when to roll, it is worth knowing how much the roll rule matters
relative to the parameter it is applied to.

Same six cycles, same real LTPs, STATIC management throughout so nothing but width varies.
"""
import sqlite3, json
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
PAN = {}
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    C = g.pivot_table(index="dt", columns="strike", values="call_ltp").sort_index()
    P_ = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) >= 50:
        PAN[exp] = (C, P_, sp)

def _px(row, k):
    if k not in row.index: return None
    v = row[k]
    return float(v) if (v == v and v is not None and v > 0.05) else None

def _near(cols, t):
    a = np.asarray(list(cols), dtype=float)
    return float(a[np.argmin(np.abs(a - t))])

def static(X, slip=1.0):
    out = []
    for exp, (C, P_, sp) in PAN.items():
        S0, ST = float(sp.iloc[0]), float(sp.iloc[-1])
        ck, pk = _near(C.columns, S0 + X), _near(P_.columns, S0 - X)
        c0, p0 = _px(C.iloc[0], ck), _px(P_.iloc[0], pk)
        if not (c0 and p0): continue
        credit = c0 + p0 - 2 * slip
        settle = max(ST - ck, 0) + max(pk - ST, 0)
        # widest adverse excursion relative to the short strikes during the cycle
        worst = max((sp.max() - ck) if sp.max() > ck else 0,
                    (pk - sp.min()) if sp.min() < pk else 0)
        out.append({"expiry": str(exp), "credit": c0 + p0, "settle": settle,
                    "pnl": credit - settle, "breached": float(worst > 0), "excursion": worst})
    return pd.DataFrame(out)

print("=== STRIKE-WIDTH SWEEP, static management, 6 cycles, real LTPs ===")
print("%-8s %9s %10s %10s %9s %9s %11s"
      % ("width", "credit", "total P/L", "mean/cyc", "worst", "wins", "cycles breached"))
print("-" * 74)
rows = {}
for X in (50, 100, 150, 200, 250, 300, 400):
    t = static(X)
    if t.empty: continue
    rows[X] = {"credit": float(t.credit.mean()), "total": float(t.pnl.sum()),
               "mean": float(t.pnl.mean()), "worst": float(t.pnl.min()),
               "wins": int((t.pnl > 0).sum()), "breach": float(t.breached.mean())}
    print("%-8d %9.1f %+10.1f %+10.1f %+9.1f %5d/%d %10.0f%%"
          % (X, t.credit.mean(), t.pnl.sum(), t.pnl.mean(), t.pnl.min(),
             int((t.pnl > 0).sum()), len(t), t.breached.mean() * 100))

best = max(rows.items(), key=lambda kv: kv[1]["total"])
worstw = min(rows.items(), key=lambda kv: kv[1]["total"])
print("\n   spread across WIDTH      : %+.1f pts  (best %d wide vs worst %d wide)"
      % (best[1]["total"] - worstw[1]["total"], best[0], worstw[0]))
print("   spread across MANAGEMENT : %+.1f pts  (STATIC +689.6 vs H25 +12.7, from mgmt_sweep)"
      % (689.6 - 12.7))
print("\n   -> width moves the result by %.0f%% of what the management rule moves it by."
      % ((best[1]["total"] - worstw[1]["total"]) / (689.6 - 12.7) * 100))
json.dump(rows, open("width_sweep_result.json", "w"), indent=1)
print("\nwrote width_sweep_result.json")
