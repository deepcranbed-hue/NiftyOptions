#!/usr/bin/env python3
"""rotation_probe -- does intraday sector rotation LEAD the index, or merely describe it?

THE PROBLEM WITH THE IDEA AS STATED. Nifty is the weighted sum of the same constituent
prices the rotation score is built from: R_index = sum(w_i * r_i). A sector's relative
strength, RS_s = r_s - R_index, is therefore an ALGEBRAIC REARRANGEMENT of the index,
not an independent observation of it. It cannot "know" something the index does not.
The only way rotation leads is if the CHANGE in relative strength over the last k
minutes predicts the index's move over the NEXT m minutes. That is an empirical claim
and this file tests it, reporting the answer whether or not it is flattering.

THREE TESTS
  1. PERSISTENCE  -- does a sector gaining relative strength keep gaining it?
                     (momentum) or hand it straight back? (reversal)
                     If reversal, "rotation" is noise and chasing it loses money.
  2. LEAD         -- does dRS predict the INDEX's next move?
  3. DISPERSION   -- does cross-sectional spread predict the SIZE of the next move?
                     Dispersion predicting volatility is a far weaker claim than
                     dispersion predicting direction, and much more likely true.

Non-overlapping windows only. Overlapping samples would inflate n by ~30x and make
noise look significant.
"""
import sqlite3, json, sys
import numpy as np, pandas as pd

DB = "option_chains.db"
CACHE = ".state/nifty50_view_cache_v17.json"
LOOKBACK, FORWARD = 15, 30      # minutes

v = json.load(open(CACHE)); v = v.get("view") or v
meta = {r["symbol"]: (r.get("weight") or 0.0, r.get("sector") or "?") for r in v["rows"]}
syms = [s for s in meta if meta[s][0] > 0]

con = sqlite3.connect(DB)
q = ("SELECT symbol, ts, close FROM price_bars WHERE timeframe='1m' AND symbol IN (%s)"
     % ",".join("?" * len(syms)))
df = pd.read_sql(q, con, params=syms)
df["ts"] = pd.to_datetime(df["ts"].str.replace("Z", "", regex=False))
df["day"] = df["ts"].dt.date
print("loaded %d 1m bars, %d symbols, %d days" % (len(df), df.symbol.nunique(), df.day.nunique()))

good = [d for d, g in df.groupby("day") if g.symbol.nunique() >= 40 and len(g) > 4000]
df = df[df.day.isin(good)]
print("sessions with >=40 names: %d" % len(good))

recs = []
for day, g in df.groupby("day"):
    px = g.pivot_table(index="ts", columns="symbol", values="close").sort_index().ffill()
    px = px.dropna(axis=1, how="any")
    if px.shape[1] < 40 or px.shape[0] < LOOKBACK + FORWARD + 5:
        continue
    r = px.divide(px.iloc[0], axis=1) - 1.0
    w = np.array([meta[c][0] for c in px.columns]); w = w / w.sum()
    R = r.values @ w
    sec = {}
    for s in sorted({meta[c][1] for c in px.columns}):
        cols = [i for i, c in enumerate(px.columns) if meta[c][1] == s]
        if len(cols) < 2:
            continue
        ws = w[cols] / w[cols].sum()
        sec[s] = r.values[:, cols] @ ws - R
    disp = r.values.std(axis=1)
    n = len(R)
    for t in range(LOOKBACK, n - FORWARD, FORWARD):
        row = {"day": str(day), "t": t,
               "fwd_index": (R[t + FORWARD] - R[t]) * 100,
               "d_disp": (disp[t] - disp[t - LOOKBACK]) * 100,
               "disp": disp[t] * 100}
        for s, v_ in sec.items():
            row["d::" + s] = (v_[t] - v_[t - LOOKBACK]) * 100
            row["f::" + s] = (v_[t + FORWARD] - v_[t]) * 100
        recs.append(row)

D = pd.DataFrame(recs)
print("non-overlapping observations: %d\n" % len(D))
secs = sorted(c[3:] for c in D.columns if c.startswith("d::"))

def corr(a, b):
    m = a.notna() & b.notna()
    if m.sum() <= 30:
        return None, int(m.sum())
    return round(float(np.corrcoef(a[m], b[m])[0, 1]), 3), int(m.sum())

print("%-26s %9s %9s %6s" % ("sector", "PERSIST", "LEAD idx", "n"))
print("%-26s %9s %9s" % ("", "dRS->fRS", "dRS->fR"))
print("-" * 54)
out = {}
for s in secs:
    p, n1 = corr(D["d::" + s], D["f::" + s])
    l, _ = corr(D["d::" + s], D["fwd_index"])
    out[s] = {"persistence_r": p, "lead_index_r": l, "n": n1}
    print("%-26s %9s %9s %6d" % (s, p, l, n1))

dd, n = corr(D["d_disp"], D["fwd_index"].abs())
lv, _ = corr(D["disp"], D["fwd_index"].abs())
print("\nDISPERSION -> SIZE of next index move (not direction):")
print("  d(dispersion) vs |fwd index move| : r = %s   (n=%s)" % (dd, n))
print("    dispersion  vs |fwd index move| : r = %s" % lv)
allp = [x["persistence_r"] for x in out.values() if x["persistence_r"] is not None]
mp = float(np.mean(allp))
verdict = ("MOMENTUM - rotation continues" if mp > 0.1 else
           "REVERSAL - rotation is given back" if mp < -0.1 else "NEITHER - inside noise")
print("\nmean persistence across sectors: %+.3f   (%s)" % (mp, verdict))
json.dump({"lookback_min": LOOKBACK, "forward_min": FORWARD, "sessions": len(good),
           "observations": len(D), "sectors": out,
           "dispersion_vs_abs_move_r": dd, "level_dispersion_vs_abs_move_r": lv,
           "mean_persistence_r": round(mp, 3), "verdict": verdict},
          open("rotation_probe_result.json", "w"), indent=1)
print("\nwrote rotation_probe_result.json")
