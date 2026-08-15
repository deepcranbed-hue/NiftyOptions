#!/usr/bin/env python3
"""rotation_bounce -- is the reversal a real effect, or the oldest artifact in the book?

THE PROBLEM. rotation_diffuse found intraday sector relative strength REVERSES, and the
finding crushed a shuffled null (p<0.003 on all three statistics). Before believing it,
one alternative has to be killed, because it predicts precisely this result and it is not
tradeable: BID-ASK BOUNCE plus NON-SYNCHRONOUS TRADING (Roll, 1984).

The mechanism. A 1-minute "close" is a last-trade price, which lands at the bid or the
ask essentially at random. Write the observed price as true + e(t), where e is that
measurement error. Then:
    return INTO  t  contains  +e(t)
    return OUT OF t  contains  -e(t)
so the same noise enters the past window positively and the forward window negatively,
manufacturing negative correlation between them out of nothing. Thinly traded members of
a sector make it worse: a stale last trade at t behaves the same way. NONE of this is a
market phenomenon and none of it can be traded -- you would be crossing the very spread
that creates it.

THE TEST. Insert a GAP of g bars on both sides of the measurement point, so the past
window ends at t-g and the forward window starts at t+g. The bounce term e(t) then
appears in neither. Its contribution is a one-bar effect, so:

    if the reversal is bid-ask bounce  -> it collapses at g=1 and is gone by g=2
    if the reversal is economic        -> it survives the gap largely intact

Same circular-shift null at every gap, so each gap is scored honestly rather than by
eyeballing the decay.
"""
import sqlite3, json
import numpy as np, pandas as pd

DB, CACHE = "option_chains.db", ".state/nifty50_view_cache_v17.json"
LOOKBACKS, FORWARDS = [5, 10, 15, 30, 60], [5, 10, 15, 30, 60]
GAPS = [0, 1, 2, 5]
N_PERM = 40
RNG = np.random.default_rng(2026)

v = json.load(open(CACHE)); v = v.get("view") or v
meta = {r["symbol"]: (r.get("weight") or 0.0, r.get("sector") or "?") for r in v["rows"]}
syms = [s for s in meta if meta[s][0] > 0]
con = sqlite3.connect(DB)
df = pd.read_sql("SELECT symbol, ts, close FROM price_bars WHERE timeframe='1m' "
                 "AND symbol IN (%s)" % ",".join("?" * len(syms)), con, params=syms)
df["ts"] = pd.to_datetime(df["ts"].str.replace("Z", "", regex=False))
df["day"] = df["ts"].dt.date

sessions = []
for day, g in df.groupby("day"):
    px = g.pivot_table(index="ts", columns="symbol", values="close").sort_index().ffill()
    px = px.dropna(axis=1, how="any")
    if px.shape[1] < 40 or px.shape[0] < 200: continue
    r = (px.divide(px.iloc[0], axis=1) - 1.0).values
    w = np.array([meta[c][0] for c in px.columns]); w = w / w.sum()
    R = r @ w
    sec = {}
    for s in sorted({meta[c][1] for c in px.columns}):
        cols = [i for i, c in enumerate(px.columns) if meta[c][1] == s]
        if len(cols) >= 2:
            ws = w[cols] / w[cols].sum()
            sec[s] = r[:, cols] @ ws - R
    sessions.append({"sec": sec, "n": len(R)})
SECTORS = sorted(set().union(*[set(s["sec"]) for s in sessions]))


def corr(a, b):
    if len(a) < 30: return np.nan
    sa, sb = a.std(), b.std()
    if sa <= 0 or sb <= 0: return np.nan
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def mean_persist(gap, shift):
    rs = []
    for lb in LOOKBACKS:
        for fw in FORWARDS:
            acc = {s: [[], []] for s in SECTORS}
            for S in sessions:
                n, sec = S["n"], S["sec"]
                if n < lb + fw + 2 * gap + 10: continue
                idx = np.arange(lb + gap, n - fw - gap, max(fw, 1))
                if len(idx) < 3: continue
                secf = ({s: np.roll(v_, RNG.integers(1, n)) for s, v_ in sec.items()}
                        if shift else sec)
                for s in SECTORS:
                    if s not in sec: continue
                    # past window ENDS at t-gap ; forward window STARTS at t+gap
                    acc[s][0].append(sec[s][idx - gap] - sec[s][idx - gap - lb])
                    acc[s][1].append(secf[s][idx + gap + fw] - secf[s][idx + gap])
            for s in SECTORS:
                if not acc[s][0]: continue
                c = corr(np.concatenate(acc[s][0]), np.concatenate(acc[s][1]))
                if not np.isnan(c): rs.append(c)
    return float(np.mean(rs))


import sys
print("%4s  %12s  %12s  %10s  %s" % ("gap", "observed r", "null median", "null 5th", "p"))
print("-" * 60)
res = {}
for g in GAPS:
    obs = mean_persist(g, False)
    null = np.array([mean_persist(g, True) for _ in range(N_PERM)])
    p = float((null <= obs).mean())
    res[g] = {"observed_r": round(obs, 4), "null_median": round(float(np.median(null)), 4),
              "null_p5": round(float(np.percentile(null, 5)), 4), "p": p}
    print("%4d  %12.4f  %12.4f  %10.4f  %.3f %s"
          % (g, obs, np.median(null), np.percentile(null, 5), p,
             "<-- survives" if p < 0.05 else "<-- gone")); sys.stdout.flush()

r0, r1 = res[0]["observed_r"], res[1]["observed_r"]
decay = 1 - (abs(r1) / abs(r0)) if r0 else 0
print("\ncollapse from gap 0 -> gap 1: %.0f%% of the effect" % (decay * 100))
verdict = ("BID-ASK BOUNCE / stale prices -- an artifact of last-trade measurement, "
           "not a market effect, and not tradeable"
           if (res[1]["p"] >= 0.05 or decay > 0.6) else
           "SURVIVES the gap -- the reversal is not purely a measurement artifact")
print(">>> %s" % verdict)
json.dump({"gaps": res, "collapse_gap0_to_gap1": round(decay, 3), "verdict": verdict,
           "n_permutations": N_PERM, "sessions": len(sessions)},
          open("rotation_bounce_result.json", "w"), indent=1)
print("wrote rotation_bounce_result.json")
