#!/usr/bin/env python3
"""rotation_diffuse -- the sweep's max|r| test was insensitive to the effect that showed up.

WHAT THE SWEEP FOUND, AND WHY ITS TEST MISSED IT. rotation_sweep scored the single BEST
cell against a shuffled null and the best cell failed (p=0.137). But max|r| is a test for
ONE strong cell. What the persistence grid actually showed was different in shape: all 25
horizon pairs mildly NEGATIVE, none of them individually impressive. That is a DIFFUSE
effect, and max|r| is close to blind to it.

So this file tests the right statistics against the same circular-shift null:
  A. MEAN persistence r pooled across every cell   -- is the grid's centre really below 0?
  B. FRACTION of cells with negative persistence   -- is 25/25 beyond what noise gives?
  C. COUNT of naive 2-sigma breaches               -- 100 observed vs 27.5 "expected";
                                                      but cells are heavily DEPENDENT
                                                      (nested horizons, shared bars), so
                                                      27.5 is the wrong yardstick. The
                                                      null supplies the right one.

C is the important correction. The 27.5 figure assumes 550 independent tests. They are
not independent: lb=5/fw=30 and lb=10/fw=30 read almost the same bars. Under dependence
the breach count has far higher variance than the binomial suggests, so 100 breaches may
be entirely ordinary. Only the shuffled null can say.
"""
import sqlite3, json
import numpy as np, pandas as pd

DB, CACHE = "option_chains.db", ".state/nifty50_view_cache_v17.json"
LOOKBACKS, FORWARDS = [5, 10, 15, 30, 60], [5, 10, 15, 30, 60]
N_PERM = 300
RNG = np.random.default_rng(11082026)

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
    if px.shape[1] < 40 or px.shape[0] < 200:
        continue
    r = (px.divide(px.iloc[0], axis=1) - 1.0).values
    w = np.array([meta[c][0] for c in px.columns]); w = w / w.sum()
    R = r @ w
    sec = {}
    for s in sorted({meta[c][1] for c in px.columns}):
        cols = [i for i, c in enumerate(px.columns) if meta[c][1] == s]
        if len(cols) >= 2:
            ws = w[cols] / w[cols].sum()
            sec[s] = r[:, cols] @ ws - R
    sessions.append({"R": R, "sec": sec, "n": len(R)})
SECTORS = sorted(set().union(*[set(s["sec"]) for s in sessions]))


def corr(a, b):
    if len(a) < 30: return np.nan
    sa, sb = a.std(), b.std()
    if sa <= 0 or sb <= 0: return np.nan
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def stats(shift):
    """(mean persistence, frac negative cells, 2-sigma breach count) for one sweep."""
    rs, ns = [], []
    for lb in LOOKBACKS:
        for fw in FORWARDS:
            acc = {s: [[], []] for s in SECTORS}
            for S in sessions:
                n, sec = S["n"], S["sec"]
                if n < lb + fw + 10: continue
                idx = np.arange(lb, n - fw, fw)
                if len(idx) < 3: continue
                secf = ({s: np.roll(v_, RNG.integers(1, n)) for s, v_ in sec.items()}
                        if shift else sec)
                for s in SECTORS:
                    if s not in sec: continue
                    acc[s][0].append(sec[s][idx] - sec[s][idx - lb])
                    acc[s][1].append(secf[s][idx + fw] - secf[s][idx])
            for s in SECTORS:
                if not acc[s][0]: continue
                d, f = np.concatenate(acc[s][0]), np.concatenate(acc[s][1])
                c = corr(d, f)
                if not np.isnan(c):
                    rs.append(c); ns.append(len(d))
    rs, ns = np.array(rs), np.array(ns)
    breaches = int((np.abs(rs) > 1.96 / np.sqrt(np.maximum(ns, 2))).sum())
    return float(rs.mean()), float((rs < 0).mean()), breaches


obs_mean, obs_neg, obs_brk = stats(False)
print("OBSERVED   mean persistence r = %+.4f   negative cells = %.0f%%   2-sigma breaches = %d"
      % (obs_mean, obs_neg * 100, obs_brk))
print("running %d null sweeps..." % N_PERM, flush=True)
nm, nn, nb = [], [], []
for i in range(N_PERM):
    a, b, c = stats(True)
    nm.append(a); nn.append(b); nb.append(c)
    if (i + 1) % 100 == 0: print("  %d/%d" % (i + 1, N_PERM), flush=True)
nm, nn, nb = np.array(nm), np.array(nn), np.array(nb)

p_mean = float((nm <= obs_mean).mean())          # one-sided: is it MORE negative?
p_neg = float((nn >= obs_neg).mean())
p_brk = float((nb >= obs_brk).mean())
print("\n=== A. mean persistence ===")
print("  null: median %+.4f   5th %+.4f   |   observed %+.4f   ->  p = %.3f"
      % (np.median(nm), np.percentile(nm, 5), obs_mean, p_mean))
print("=== B. fraction of cells negative ===")
print("  null: median %.0f%%   95th %.0f%%   |   observed %.0f%%   ->  p = %.3f"
      % (np.median(nn) * 100, np.percentile(nn, 95) * 100, obs_neg * 100, p_neg))
print("=== C. 2-sigma breach count ===")
print("  null: median %.0f   95th %.0f   max %.0f   |   observed %d   ->  p = %.3f"
      % (np.median(nb), np.percentile(nb, 95), nb.max(), obs_brk, p_brk))
print("  (binomial 'expected 27.5' assumed independence; the null's median above is the"
      " honest yardstick)")
sig = [n_ for n_, p_ in (("mean persistence", p_mean), ("share negative", p_neg),
                         ("breach count", p_brk)) if p_ < 0.05]
print("\n>>> survives the shuffled null: %s" % (", ".join(sig) if sig else "NOTHING"))
json.dump({"observed": {"mean_persistence_r": round(obs_mean, 4),
                        "frac_cells_negative": round(obs_neg, 3), "breaches": obs_brk},
           "null_median": {"mean_persistence_r": round(float(np.median(nm)), 4),
                           "frac_cells_negative": round(float(np.median(nn)), 3),
                           "breaches": float(np.median(nb))},
           "p_values": {"mean_persistence": p_mean, "frac_negative": p_neg,
                        "breach_count": p_brk},
           "n_permutations": N_PERM, "survives": sig},
          open("rotation_diffuse_result.json", "w"), indent=1)
print("wrote rotation_diffuse_result.json")
