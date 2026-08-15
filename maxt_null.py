#!/usr/bin/env python3
"""maxt_null -- the count test can hide a single strong effect. Test the max too.

horserace's survivor COUNT (18 vs a null median of 15, p=0.225) says the panel as a
whole is unremarkable. But a count is insensitive to ONE large effect buried among many
null ones -- the exact mirror of the mistake made earlier, when a max test hid a diffuse
one. Crude's t-stats reach +5.9, so the max deserves its own null.

Also reported PER PREDICTOR, because the survivors are not spread evenly: nearly all of
them sit in the crude column. If crude alone beats its own null while USDINR and VIX do
not, the honest summary is "crude, and nothing else" rather than "nothing".
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
HOR = [1, 2, 5]; NAMES = ["USDINR", "CRUDE", "VIX", "own_ret"]
con = sqlite3.connect("option_chains.db")
need = SECT + ["NIFTY", "USDINR", "CRUDEOIL", "INDIAVIX"]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN (%s)"
                 % ",".join("?" * len(need)), con, params=need)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
P = P[P.index >= "2018-01-01"].ffill(); R = P.pct_change() * 100
X0 = pd.DataFrame({"USDINR": R["USDINR"], "CRUDE": R["CRUDEOIL"],
                   "VIX": P["INDIAVIX"].pct_change() * 100})

def nw(X, y, lag):
    X = np.column_stack([np.ones(len(X)), X]); XtXi = np.linalg.pinv(X.T @ X)
    b = XtXi @ X.T @ y; e = y - X @ b
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1.0)
        A = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None]); S += w * (A + A.T)
    V = XtXi @ S @ XtXi
    return b / np.sqrt(np.maximum(np.diag(V), 1e-30))

panel = {}
for h in HOR:
    for s in SECT:
        rel = R[s] - R["NIFTY"]
        fwd = (P[s].shift(-h) / P[s] - 1) * 100 - (P["NIFTY"].shift(-h) / P["NIFTY"] - 1) * 100
        D = pd.concat([fwd.rename("y"), X0, rel.rename("own")], axis=1).dropna()
        if len(D) >= 200: panel[(h, s)] = (D[NAMES[:3] + ["own"]].values, D["y"].values, h + 2)

def stats(rng=None):
    T = {n: [] for n in NAMES}
    for (h, s), (Xv, yv, lag) in panel.items():
        Xs = np.roll(Xv, int(rng.integers(50, len(Xv) - 50)), axis=0) if rng is not None else Xv
        t = nw(Xs, yv, lag)
        for i, n in enumerate(NAMES): T[n].append(abs(t[i + 1]))
    return {n: (max(v), int(sum(x > 2 for x in v))) for n, v in T.items()}

obs = stats()
rng = np.random.default_rng(11)
null = [stats(rng) for _ in range(200)]
print("%-9s %8s %8s %8s   %8s %8s %8s" %
      ("predictor", "max|t|", "null50", "p(max)", "n|t|>2", "null50", "p(cnt)"))
print("-" * 62)
res = {}
for n in NAMES:
    om, oc = obs[n]
    nm = np.array([x[n][0] for x in null]); nc = np.array([x[n][1] for x in null])
    pm = float((nm >= om).mean()); pc = float((nc >= oc).mean())
    res[n] = {"max_t": round(float(om), 2), "null_median_max_t": round(float(np.median(nm)), 2),
              "p_max": pm, "count": oc, "null_median_count": float(np.median(nc)), "p_count": pc}
    print("%-9s %8.2f %8.2f %8.3f   %8d %8.0f %8.3f%s"
          % (n, om, np.median(nm), pm, oc, np.median(nc), pc,
             "  <<<" if (pm < 0.05 or pc < 0.05) else ""))
json.dump(res, open("maxt_null_result.json", "w"), indent=1)
print("\n%d of 33 crude coefficients clear |t|>2 (11 sectors x 3 horizons)." % obs["CRUDE"][1])
print("wrote maxt_null_result.json")
