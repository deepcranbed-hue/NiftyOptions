#!/usr/bin/env python3
"""rotation_bucketsize -- why the stock-built buckets showed reversal and the official
                          sector indices show nothing.

THE CONTRADICTION. rotation_diffuse (sectors built by weighting individual Nifty 50
constituents) found mean persistence r = -0.036, beating a shuffled null at p<0.003, and
it survived a 5-bar gap so bid-ask bounce looked ruled out. rotation_causes (the same
measurement on PUBLISHED NSE sector indices) found r = +0.002. Both cannot be right.

THE HYPOTHESIS. Averaging k stocks divides idiosyncratic price noise by roughly sqrt(k).
A 2-stock bucket is mostly noise; a 12-stock bucket is mostly signal. If the reversal is
an artifact of that noise, its size must scale with 1/k -- SMALL buckets strongly
negative, LARGE buckets near zero -- and the published indices, which are broad, should
show nothing. If the reversal is economic, bucket size should not matter at all.

WHY THE GAP TEST DID NOT CATCH IT. That test was built for bid-ask bounce, which is a
ONE-BAR effect and dies at gap=1. But thin or stale trading is not one bar: a name whose
last print is several minutes old carries the same stale price through many consecutive
bars, so the induced reversal survives a 5-minute gap comfortably. The gap test ruled out
the fast artifact and left the slow one standing. That was my error.

This file tests the hypothesis directly: persistence r against the number of names in
the bucket.
"""
import sqlite3, json
import numpy as np, pandas as pd

LB, FW = 15, 30
v = json.load(open(".state/nifty50_view_cache_v17.json")); v = v.get("view") or v
meta = {r["symbol"]: (r.get("weight") or 0.0, r.get("sector") or "?") for r in v["rows"]}
syms = [s for s in meta if meta[s][0] > 0]
con = sqlite3.connect("option_chains.db")
df = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1m' "
                 "AND symbol IN (%s)" % ",".join("?" * len(syms)), con, params=syms)
df["ts"] = pd.to_datetime(df["ts"].str.replace("Z", "", regex=False))
df["day"] = df["ts"].dt.date

acc, sizes = {}, {}
for day, g in df.groupby("day"):
    px = g.pivot_table(index="ts", columns="symbol", values="close").sort_index().ffill()
    px = px.dropna(axis=1, how="any")
    if px.shape[1] < 40 or px.shape[0] < 200: continue
    r = (px.divide(px.iloc[0], axis=1) - 1.0).values
    w = np.array([meta[c][0] for c in px.columns]); w = w / w.sum()
    R = r @ w
    idx = np.arange(LB, len(R) - FW, FW)
    for s in sorted({meta[c][1] for c in px.columns}):
        cols = [i for i, c in enumerate(px.columns) if meta[c][1] == s]
        if len(cols) < 2: continue
        sizes[s] = len(cols)
        ws = w[cols] / w[cols].sum()
        rs = r[:, cols] @ ws - R
        acc.setdefault(s, [[], []])
        acc[s][0].append(rs[idx] - rs[idx - LB]); acc[s][1].append(rs[idx + FW] - rs[idx])

print("%-24s %6s %12s" % ("sector (stock-built)", "names", "persist r"))
print("-" * 46)
ks, rs_ = [], []
for s in sorted(acc, key=lambda x: sizes[x]):
    d, f = np.concatenate(acc[s][0]), np.concatenate(acc[s][1])
    rr = float(np.corrcoef(d, f)[0, 1])
    ks.append(sizes[s]); rs_.append(rr)
    print("%-24s %6d %12.3f" % (s, sizes[s], rr))
ks, rs_ = np.array(ks), np.array(rs_)
c1 = float(np.corrcoef(ks, rs_)[0, 1])
c2 = float(np.corrcoef(1.0 / ks, rs_)[0, 1])
print("\ncorr(bucket size, persistence r)      = %+.3f" % c1)
print("corr(1/bucket size, persistence r)    = %+.3f" % c2)
small = rs_[ks <= 3].mean(); big = rs_[ks >= 5].mean()
print("mean persistence, buckets of <=3 names: %+.3f  (n=%d sectors)" % (small, (ks <= 3).sum()))
print("mean persistence, buckets of >=5 names: %+.3f  (n=%d sectors)" % (big, (ks >= 5).sum()))
verdict = ("ARTIFACT of thin/stale prices in small buckets -- the reversal shrinks toward "
           "zero as the bucket widens, and vanishes on the published indices"
           if c1 > 0.4 and big > small else
           "bucket size does NOT explain it -- the reversal is something else")
print("\n>>> %s" % verdict)
json.dump({"sizes": {k: int(sizes[k]) for k in acc}, "persistence": dict(zip(sorted(acc, key=lambda x: sizes[x]), [round(x,4) for x in rs_])),
           "corr_size_vs_r": round(c1, 3), "corr_inv_size_vs_r": round(c2, 3),
           "mean_small_buckets": round(float(small), 4), "mean_large_buckets": round(float(big), 4),
           "verdict": verdict}, open("rotation_bucketsize_result.json", "w"), indent=1)
print("wrote rotation_bucketsize_result.json")
