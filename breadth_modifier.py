#!/usr/bin/env python3
"""breadth_modifier -- does breadth MODIFY the index signal, rather than replace it?

THE HYPOTHESIS IS DIFFERENT FROM THE ONES ALREADY REFUTED, and the difference matters.
Earlier tests asked "does the cross-section PREDICT tomorrow" and the answer was no.
This asks something weaker and more plausible: given today's index move, does knowing
whether it was BROAD or CONCENTRATED change what tomorrow looks like? A modifier can be
real even when the modifying variable has no standalone predictive power -- that is what
an interaction is.

FEATURES, both as specified:
  breadth      = share of constituents advancing
  ew_minus_cw  = equal-weight return minus the cap-weighted index return.
                 Positive => the typical stock beat the index => the move was BROAD.
                 Negative => heavyweights carried it => CONCENTRATED.
                 This is strictly richer than advance/decline because it is weighted by
                 how much each stock moved, not just its sign.

WHAT "CONFIDENCE" HAS TO MEAN TO BE TESTABLE. "Higher confidence" is not a direction
claim, so testing next-day RETURN alone would miss it. Three separate operationalisations:
  1. CONTINUATION  does today's direction persist more often when the move was broad?
  2. MAGNITUDE     is tomorrow's absolute move different after broad vs narrow?
  3. INTERACTION   does breadth x index-return beat index-return alone in a regression?
Only 3 tests the claim as stated; 1 and 2 are the tradeable forms of it.

Cells are scored against a circular-shift null, since a 2x2 crossed with 3 statistics is
12 numbers and the largest of them is a searched maximum.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(505)
con = sqlite3.connect("option_chains.db")
csv = pd.read_csv("nifty-50-stock-list.csv")
syms = [s.strip() for s in csv["Symbol"].dropna()]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                 "(%s)" % ",".join("?" * len(syms)), con, params=syms)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
nif = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='NIFTY'", con)
nif["d"] = pd.to_datetime(nif.ts.str[:10])
N = nif.set_index("d")["close"].sort_index()
P, N = P.align(N, join="inner", axis=0)
P = P.dropna(axis=1, thresh=int(len(P) * 0.8))

R = P.pct_change(fill_method=None) * 100
idx = N.pct_change() * 100
D = pd.DataFrame({
    "idx": idx,
    "ew": R.mean(axis=1),
    "breadth": (R > 0).sum(axis=1) / R.notna().sum(axis=1) * 100,
    "n_valid": R.notna().sum(axis=1),
}).dropna()
D = D[D.n_valid >= 35]
D["ew_minus_cw"] = D.ew - D.idx
D["fwd"] = D.idx.shift(-1)
D = D.dropna()
print("sessions: %d   %s .. %s" % (len(D), D.index.min().date(), D.index.max().date()))
print("ew_minus_cw: mean %+.3f%%  std %.3f%%  -- positive means the typical stock beat the index"
      % (D.ew_minus_cw.mean(), D.ew_minus_cw.std()))
print("corr(breadth, ew_minus_cw) = %+.3f  (they are related but not the same thing)"
      % np.corrcoef(D.breadth, D.ew_minus_cw)[0, 1])

BR = D.ew_minus_cw > D.ew_minus_cw.median()     # BROAD = typical stock beat the index
UP = D.idx > 0
cells = {("bullish", "broad"): UP & BR, ("bullish", "narrow"): UP & ~BR,
         ("bearish", "broad"): ~UP & BR, ("bearish", "narrow"): ~UP & ~BR}

print("\n%-20s %6s %11s %11s %11s" % ("cell", "n", "next-day", "continued", "|next-day|"))
print("-" * 64)
print("%-20s %6d %10.3f%% %10.0f%% %10.3f%%"
      % ("ALL (base rate)", len(D), D.fwd.mean(),
         (np.sign(D.fwd) == np.sign(D.idx)).mean() * 100, D.fwd.abs().mean()))
res = {}
for (sig, br), m in cells.items():
    s = D[m]
    cont = (np.sign(s.fwd) == np.sign(s.idx)).mean() * 100
    res["%s/%s" % (sig, br)] = {"n": int(len(s)), "fwd": float(s.fwd.mean()),
                                "cont": float(cont), "absfwd": float(s.fwd.abs().mean())}
    print("%-20s %6d %10.3f%% %10.0f%% %10.3f%%"
          % (sig + " / " + br, len(s), s.fwd.mean(), cont, s.fwd.abs().mean()))

print("\n1. CONTINUATION -- does a BROAD move persist more than a NARROW one?")
for sig in ("bullish", "bearish"):
    b, n_ = res["%s/broad" % sig], res["%s/narrow" % sig]
    print("   %-9s broad %.1f%%  vs narrow %.1f%%   difference %+.1f pp"
          % (sig, b["cont"], n_["cont"], b["cont"] - n_["cont"]))

print("\n2. NEXT-DAY RETURN spread across the four cells")
best = max(res.values(), key=lambda v: v["fwd"]); worst = min(res.values(), key=lambda v: v["fwd"])
obs = best["fwd"] - worst["fwd"]
n = len(D)
null = []
for _ in range(3000):
    k = int(RNG.integers(20, n - 20))
    up_s, br_s = np.roll(UP.values, k), np.roll(BR.values, k)
    v = [D.fwd.values[(up_s == u) & (br_s == b)].mean()
         for u in (True, False) for b in (True, False)]
    null.append(max(v) - min(v))
null = np.array(null)
p = float((null >= obs).mean())
print("   best-worst cell spread %.3f%%   null median %.3f%%   p=%.3f  ->  %s"
      % (obs, np.median(null), p, "SURVIVES" if p < 0.05 else "inside noise"))

print("\n3. INTERACTION -- does breadth x index-return beat index-return alone?")
def ols(X, y):
    X = np.column_stack([np.ones(len(X))] + list(np.asarray(X).T))
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    se = np.sqrt(np.diag(np.linalg.pinv(X.T @ X) * (e @ e) / (len(y) - X.shape[1])))
    return b, b / se, 1 - (e @ e) / ((y - y.mean()) ** 2).sum()
y = D.fwd.values
z = ((D.ew_minus_cw - D.ew_minus_cw.mean()) / D.ew_minus_cw.std()).values
_, t1, r1 = ols(np.column_stack([D.idx]), y)
_, t2, r2 = ols(np.column_stack([D.idx, z]), y)
_, t3, r3 = ols(np.column_stack([D.idx, z, D.idx.values * z]), y)
print("   idx only            R2=%.3f%%   idx t=%+.2f" % (r1 * 100, t1[1]))
print("   + breadth           R2=%.3f%%   breadth t=%+.2f" % (r2 * 100, t2[2]))
print("   + interaction       R2=%.3f%%   idx x breadth t=%+.2f   dR2=%+.4f pp"
      % (r3 * 100, t3[3], (r3 - r2) * 100))
print("   >>> %s" % ("interaction is real -- breadth modifies the index signal"
                     if abs(t3[3]) > 2 else
                     "no interaction -- breadth does not modify the index signal's "
                     "forward content"))
json.dump({"cells": res, "spread": obs, "spread_p": p,
           "r2_idx": r1 * 100, "r2_breadth": r2 * 100, "r2_inter": r3 * 100,
           "t_interaction": float(t3[3])}, open("breadth_modifier_result.json", "w"), indent=1)
print("\nwrote breadth_modifier_result.json")
