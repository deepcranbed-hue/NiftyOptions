#!/usr/bin/env python3
"""wings_pnl -- P&L of naked vs symmetric vs ASYMMETRIC structures, priced off the
                measured smile rather than a flat VIX.

Every leg is priced at ITS OWN implied vol, taken from the smile inverted from 20,990
traded prices in smile.py, expressed as a multiplier on ATM and scaled by each day's VIX.
Outcomes come from the 2,097 actual six-session paths, 2018-2026.

Slippage 1 point per leg (0.0041% of spot at 24,300), as specified.

CAVEAT ON THE SMILE: it was measured at VIX 12-14 on weeklies. Skew steepens in stress, so
holding the multiplier constant across VIX buckets understates the cost of put wings in the
high-VIX rows. The call-side conclusion is unaffected -- that side is flat.
"""
import sqlite3, math
import numpy as np, pandas as pd

H, SHORT, SLIP = 6, 0.62, 0.0041
# measured smile: moneyness (%) -> IV as a multiple of ATM
SM_X = np.array([-2.30, -1.75, -1.25, -0.75, -0.275, 0.275, 0.75, 1.25, 1.75, 2.30])
SM_Y = np.array([14.89, 14.09, 13.47, 13.00, 12.60, 12.91, 12.73, 12.56, 12.64, 12.89]) / 12.75

def smile(m): return float(np.interp(m, SM_X, SM_Y, left=SM_Y[0], right=SM_Y[-1]))
def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
def bs(S, K, T, s, call):
    if T <= 0 or s <= 0: return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return (S * _cdf(d1) - K * _cdf(d2)) if call else (K * _cdf(-d2) - S * _cdf(-d1))
def price(S, mny, T, vix, call):
    K = S * (1 + mny / 100.0)
    return bs(S, K, T, vix / 100.0 * smile(mny), call), K

con = sqlite3.connect("option_chains.db")
o = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                "('NIFTY','INDIAVIX')", con)
o["d"] = pd.to_datetime(o.ts.str[:10])
O = o.pivot_table(index="d", columns="symbol", values="close").sort_index().dropna()
O["fwd"] = O.NIFTY.shift(-H) / O.NIFTY - 1
O["r"] = O.NIFTY.pct_change() * 100
D = O.dropna(); T = H / 252.0

# structure = (call wing extra %, put wing extra %); None = no wing that side
STRUCTS = [("naked",                 None, None),
           ("condor +0.82 both",     0.82, 0.82),
           ("condor +1.65 both",     1.65, 1.65),
           ("CALL wing +0.82 only",  0.82, None),
           ("CALL wing +1.65 only",  1.65, None),
           ("PUT wing +0.82 only",   None, 0.82),
           ("CALL+1.65 / PUT+0.41",  1.65, 0.41)]

def sim(cw, pw, m=None):
    d = D if m is None else D[m]
    S, out = 100.0, []
    for vix, f in zip(d.INDIAVIX.values, d.fwd.values):
        legs = 2
        cP, cK = price(S, SHORT, T, vix, True)
        pP, pK = price(S, -SHORT, T, vix, False)
        cash = cP + pP
        ST = S * (1 + f)
        pay = max(ST - cK, 0) + max(pK - ST, 0)
        if cw is not None:
            w, wK = price(S, SHORT + cw, T, vix, True); cash -= w; pay -= max(ST - wK, 0); legs += 1
        if pw is not None:
            w, wK = price(S, -(SHORT + pw), T, vix, False); cash -= w; pay -= max(wK - ST, 0); legs += 1
        out.append(cash - pay - legs * SLIP)
    return np.array(out)

def stats(a):
    n5, n1 = max(int(len(a) * .05), 1), max(int(len(a) * .01), 1)
    s = np.sort(a)
    return {"mean": a.mean(), "es5": s[:n5].mean(), "es1": s[:n1].mean(),
            "worst": a.min(), "win": (a > 0).mean()}

print("=== P&L BY STRUCTURE, all regimes (n=%d paths, %% of spot per 6-session cycle) ===" % len(D))
print("%-24s %9s %9s %9s %9s %6s %9s %8s %9s"
      % ("structure", "mean", "ES(5%)", "ES(1%)", "worst", "win", "theta ret", "ES cut", "P/L / ES"))
print("-" * 100)
base = None
for lab, cw, pw in STRUCTS:
    s = stats(sim(cw, pw))
    if base is None: base = s
    print("%-24s %+9.4f %+9.4f %+9.4f %+9.4f %5.0f%% %8.0f%% %7.0f%% %9.3f"
          % (lab, s["mean"], s["es5"], s["es1"], s["worst"], s["win"] * 100,
             s["mean"] / base["mean"] * 100, (1 - s["es5"] / base["es5"]) * 100,
             s["mean"] / abs(s["es5"])))

print("\n=== BY REGIME  (P/L per unit of ES -- higher is better) ===")
prev = D.r.abs()
REG = [("VIX<=13 quiet", (D.INDIAVIX <= 13) & (prev < 0.5)),
       ("VIX 13-17 quiet", (D.INDIAVIX > 13) & (D.INDIAVIX <= 17) & (prev < 0.5)),
       ("VIX 13-17 moved", (D.INDIAVIX > 13) & (D.INDIAVIX <= 17) & (prev > 1)),
       ("VIX > 17", D.INDIAVIX > 17)]
hdr = "%-24s" % "structure" + "".join("%18s" % r[0] for r in REG)
print(hdr); print("-" * len(hdr))
for lab, cw, pw in STRUCTS:
    line = "%-24s" % lab
    for _, m in REG:
        if m.sum() < 60: line += "%18s" % "-"; continue
        s = stats(sim(cw, pw, m))
        line += "%18s" % ("%+.3f / %.2f" % (s["mean"], s["mean"] / abs(s["es5"])))
    print(line)
print("\n   cells show  mean P/L  /  P&L-per-ES")
