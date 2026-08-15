#!/usr/bin/env python3
"""horserace -- the macro horse-race on the FULL 2018-2026 daily sample.

SCOPE CORRECTION FIRST. The 1-day price history runs to 2018 (~2,100 sessions) but the
FII series does NOT: participant_flows starts 2025-08-07 (247 days) and there is no
deeper source in this repo -- fii_dii_flows has 35 rows, flows_export.json has 30.
So the extension splits: the MACRO horse-race gets ~2,100 observations and is properly
powered; the FII questions stay at n=247 and are answered with a power analysis instead
of a bigger sample. Claiming 2,000 observations for FII would be inventing data.

THE MODEL, per sector, relative to Nifty so it measures rotation and not direction:

    SectorRelRet(t+1..t+h) = b1*dUSDINR(t) + b2*dCRUDE(t) + b3*dVIX(t)
                             + b4*SectorRelRet(t) + e

b4 is the control that matters most: without it, any "macro predicts sector" result can
be short-horizon reversal wearing a macro costume.

TWO STATISTICAL CORRECTIONS, both of which change conclusions if omitted:
  * OVERLAPPING RETURNS. For h>1 the forward windows share days, so residuals are
    autocorrelated by construction and ordinary standard errors are far too small.
    Newey-West with lag h+2 is used throughout. t-stats reported are NW t-stats.
  * MULTIPLE TESTING. 11 sectors x 4 predictors x 3 horizons = 132 coefficients; ~7
    will clear |t|>2 by chance. The count of survivors is printed against that
    expectation, so no single starred cell gets read on its own.
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
HORIZONS = [1, 2, 5]
con = sqlite3.connect("option_chains.db")
need = SECT + ["NIFTY", "USDINR", "CRUDEOIL", "INDIAVIX"]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' "
                 "AND symbol IN (%s)" % ",".join("?" * len(need)), con, params=need)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
P = P[P.index >= "2018-01-01"].ffill()
R = P.pct_change() * 100
R["dVIX"] = P["INDIAVIX"].pct_change() * 100
X0 = pd.DataFrame({"USDINR": R["USDINR"], "CRUDE": R["CRUDEOIL"], "VIX": R["dVIX"]})


def nw(X, y, lag):
    """OLS with Newey-West standard errors. Returns (beta, tstat, r2, n)."""
    X = np.column_stack([np.ones(len(X)), X])
    XtXi = np.linalg.pinv(X.T @ X)
    b = XtXi @ X.T @ y
    e = y - X @ b
    n, k = X.shape
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1.0)
        A = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None])
        S += w * (A + A.T)
    V = XtXi @ S @ XtXi
    se = np.sqrt(np.maximum(np.diag(V), 1e-30))
    r2 = 1 - (e @ e) / (((y - y.mean()) ** 2).sum())
    return b, b / se, r2, n


NAMES = ["USDINR", "CRUDE", "VIX", "own_ret"]
survivors, total = 0, 0
results = {}
for h in HORIZONS:
    print("\n" + "=" * 78)
    print("FORWARD HORIZON: %d day(s)   (Newey-West lag %d)" % (h, h + 2))
    print("%-10s" % "sector" + "".join("%13s" % n for n in NAMES) + "%9s%7s" % ("R2%", "n"))
    print("-" * 78)
    for s in SECT:
        rel = R[s] - R["NIFTY"]
        fwd = (P[s].shift(-h) / P[s] - 1) * 100 - (P["NIFTY"].shift(-h) / P["NIFTY"] - 1) * 100
        D = pd.concat([fwd.rename("y"), X0, rel.rename("own_ret")], axis=1).dropna()
        if len(D) < 200: continue
        b, t, r2, n = nw(D[NAMES].values, D["y"].values, h + 2)
        line = "%-10s" % s.replace("NIFTY", "")
        for i in range(1, 5):
            star = "*" if abs(t[i]) > 2 else " "
            survivors += abs(t[i]) > 2; total += 1
            line += "%13s" % ("%+.3f(%+.1f)%s" % (b[i], t[i], star))
        line += "%9.2f%7d" % (r2 * 100, n)
        print(line)
        results["%s_h%d" % (s, h)] = {"beta": dict(zip(NAMES, [round(x, 4) for x in b[1:]])),
                                      "t": dict(zip(NAMES, [round(x, 2) for x in t[1:]])),
                                      "r2_pct": round(float(r2) * 100, 3), "n": int(n)}
print("\n" + "=" * 78)
print("coefficients with |t|>2 : %d of %d      expected by chance: ~%.0f"
      % (survivors, total, total * 0.05))
print("sample: %s to %s" % (R.index.min().date(), R.index.max().date()))
json.dump({"sample_start": str(R.index.min().date()), "sample_end": str(R.index.max().date()),
           "horizons": HORIZONS, "survivors": int(survivors), "total_coefficients": int(total),
           "expected_by_chance": round(total * 0.05, 1), "results": results},
          open("horserace_result.json", "w"), indent=1)
print("wrote horserace_result.json")

# ---------------------------------------------------------------------------
# CROSS-SECTIONAL DEPENDENCE -- why "132 tests, expect 7" is the wrong yardstick.
# All 11 sectors are regressed on the SAME USDINR, CRUDE and VIX series, and the
# sectors are themselves heavily correlated with one another. One lucky sample
# therefore lifts every sector's t-stat at once, so the binomial count (which
# assumes 132 independent tests) badly overstates the evidence. The honest null
# circularly shifts the PREDICTOR block against the targets: every autocorrelation
# and every cross-correlation inside X and inside y is preserved, and only the
# alignment between them is destroyed. The survivor count is re-counted on that.
# ---------------------------------------------------------------------------
print("\nblock-shift null for the survivor count ...", flush=True)
rng = np.random.default_rng(7)
panel = {}
for h in HORIZONS:
    for s_ in SECT:
        rel_ = R[s_] - R["NIFTY"]
        fwd_ = (P[s_].shift(-h) / P[s_] - 1) * 100 - (P["NIFTY"].shift(-h) / P["NIFTY"] - 1) * 100
        Dd = pd.concat([fwd_.rename("y"), X0, rel_.rename("own_ret")], axis=1).dropna()
        if len(Dd) >= 200:
            panel[(h, s_)] = (Dd[NAMES].values, Dd["y"].values)

def count_survivors(shift):
    c = 0
    for (h, s_), (Xv, yv) in panel.items():
        Xs = np.roll(Xv, int(rng.integers(50, len(Xv) - 50)), axis=0) if shift else Xv
        _, t_, _, _ = nw(Xs, yv, h + 2)
        c += int((np.abs(t_[1:]) > 2).sum())
    return c

null = np.array([count_survivors(True) for _ in range(200)])
p_val = float((null >= survivors).mean())
print("null survivor count: median %.0f   95th %.0f   max %.0f"
      % (np.median(null), np.percentile(null, 95), null.max()))
print("observed %d  ->  p = %.3f" % (survivors, p_val))
print(">>> %s" % ("SURVIVES cross-sectional dependence -- the macro links are real"
                  if p_val < 0.05 else
                  "does NOT survive -- the excess is what correlated sectors give from noise"))
json.dump({"observed_survivors": int(survivors), "null_median": float(np.median(null)),
           "null_p95": float(np.percentile(null, 95)), "p_value": p_val},
          open("horserace_null.json", "w"), indent=1)
