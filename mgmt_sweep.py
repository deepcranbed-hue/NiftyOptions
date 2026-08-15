#!/usr/bin/env python3
"""mgmt_sweep -- how little management can the strategy get away with?

The rolling test showed the management OVERLAY was net -446 points while the strategy as
a whole made +591. That isolates the overlay as the problem but leaves the real question
open: is the right amount of management zero, or merely less? Six variants, same six
cycles, same real LTPs.

    STATIC     sell the strangle, touch nothing until settlement
    H25/H50/H75  buy back a leg once it has lost 25/50/75% of its value; do NOT replace
                 it and do NOT touch the other leg
    H50-R/H75-R  as above, but also chase the threatened leg outward on a breach

METRICS, because total P&L alone hides the mechanism:
    P&L per execution   the +591 headline is ~234 leg-trades; per-execution is what
                        actually has to clear the bid/ask and the taxes
    theta efficiency    gross premium captured divided by what adjustment cost to get it
    worst cycle         six cycles cannot show a tail, but they can show which variant
                        is closest to finding one

SIX CYCLES. Nothing here is evidence. It is a ranking on one favourable regime, and the
point is to have the sweep ready for when capture resumes.
"""
import sqlite3, json
import numpy as np, pandas as pd

X = 150.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
PANELS = {}
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    C = g.pivot_table(index="dt", columns="strike", values="call_ltp").sort_index()
    P_ = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) < 50: continue
    st = max(1, len(sp) // 600)
    PANELS[exp] = (C.iloc[::st], P_.iloc[::st], sp.iloc[::st])

def _px(row, k):
    if k not in row.index: return None
    v = row[k]
    return float(v) if (v == v and v is not None and v > 0.05) else None

def _near(cols, t):
    a = np.asarray(list(cols), dtype=float)
    return float(a[np.argmin(np.abs(a - t))])

def run(harvest=None, resell=False, roll=False, slip=1.0):
    rows = []
    for exp, (C, P_, sp) in PANELS.items():
        S0 = float(sp.iloc[0])
        ck, pk = _near(C.columns, S0 + X), _near(P_.columns, S0 - X)
        c0, p0 = _px(C.iloc[0], ck), _px(P_.iloc[0], pk)
        if not (c0 and p0): continue
        cash = c0 + p0 - 2 * slip
        legs = {"C": {"k": ck, "e": c0, "live": True}, "P": {"k": pk, "e": p0, "live": True}}
        nex = harv_gross = roll_cost = 0
        nh = nr = 0
        for i in range(1, len(sp)):
            S = float(sp.iloc[i])
            for side, panel, sgn in (("C", C, +1), ("P", P_, -1)):
                L = legs[side]
                if not L["live"]: continue
                row = panel.iloc[i]
                cur = _px(row, L["k"])
                if cur is None: continue
                if harvest is not None and cur <= harvest * L["e"]:
                    cash -= cur + slip; harv_gross += L["e"] - cur; nh += 1; nex += 1
                    if resell:
                        nk = _near(panel.columns, S + sgn * X); npx = _px(row, nk)
                        if npx and abs(nk - S) > 40:
                            cash += npx - slip; nex += 1
                            legs[side] = {"k": nk, "e": npx, "live": True}; continue
                    L["live"] = False; continue
                if roll and ((S > L["k"]) if side == "C" else (S < L["k"])):
                    nk = _near(panel.columns, S + sgn * X); npx = _px(row, nk)
                    if npx and nk != L["k"]:
                        cash -= cur + slip; cash += npx - slip
                        roll_cost += cur - npx; nr += 1; nex += 2
                        legs[side] = {"k": nk, "e": npx, "live": True}
        ST = float(sp.iloc[-1])
        settle = sum((max(ST - L["k"], 0) if s == "C" else max(L["k"] - ST, 0))
                     for s, L in legs.items() if L["live"])
        rows.append({"pnl": cash - settle, "nex": nex, "nh": nh, "nr": nr,
                     "harv": harv_gross, "rollc": roll_cost})
    return pd.DataFrame(rows)

VARIANTS = [("STATIC", dict(harvest=None, roll=False)),
            ("H25", dict(harvest=0.75, roll=False)), ("H50", dict(harvest=0.50, roll=False)),
            ("H75", dict(harvest=0.25, roll=False)),
            ("H50-R", dict(harvest=0.50, roll=True)), ("H75-R", dict(harvest=0.25, roll=True)),
            ("H50-R+resell", dict(harvest=0.50, roll=True, resell=True))]
for slip in (1.0, 2.0):
    print("\n=== MANAGEMENT SWEEP  (6 cycles, X=%.0f, slippage %.1f pt/leg) ===" % (X, slip))
    print("%-14s %9s %9s %9s %7s %6s %6s %12s"
          % ("variant", "total P/L", "mean/cyc", "worst", "execs", "#harv", "#roll", "P/L per exec"))
    print("-" * 84)
    out = {}
    for name, kw in VARIANTS:
        t = run(slip=slip, **kw)
        if t.empty: continue
        ex = int(t.nex.sum())
        out[name] = {"total": float(t.pnl.sum()), "mean": float(t.pnl.mean()),
                     "worst": float(t.pnl.min()), "execs": ex,
                     "per_exec": float(t.pnl.sum() / ex) if ex else None,
                     "harv": float(t.harv.sum()), "rollc": float(t.rollc.sum())}
        print("%-14s %+9.1f %+9.1f %+9.1f %7d %6d %6d %12s"
              % (name, t.pnl.sum(), t.pnl.mean(), t.pnl.min(), ex, int(t.nh.sum()),
                 int(t.nr.sum()), ("%+.2f" % (t.pnl.sum() / ex)) if ex else "n/a"))
    if slip == 1.0:
        print("\n   theta efficiency = gross harvested / adjustment cost paid")
        for name, v in out.items():
            if v["rollc"] > 0:
                print("      %-14s harvested %+8.1f  roll cost %8.1f  ratio %.2fx"
                      % (name, v["harv"], v["rollc"], v["harv"] / v["rollc"]))
            elif v["harv"] > 0:
                print("      %-14s harvested %+8.1f  roll cost      0.0  (no rolling)"
                      % (name, v["harv"]))
        json.dump(out, open("mgmt_sweep_result.json", "w"), indent=1)
print("\nwrote mgmt_sweep_result.json")
