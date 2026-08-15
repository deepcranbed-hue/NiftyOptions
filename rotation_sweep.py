#!/usr/bin/env python3
"""rotation_sweep -- sweep the horizons, and control for the fact that we swept them.

WHY A SWEEP NEEDS A CONTROL. The single 15/30 test found nothing. Trying 25 horizon
pairs across 11 sectors and 2 hypotheses is 550 correlations, and at a 2-sigma bar
roughly 27 of them will breach by chance ALONE. Reporting "IT leads at 10/60, r=0.19!"
off such a sweep is the purest form of the mistake this repo keeps trying not to make:
searching until the noise agrees with you.

So the sweep is scored against a NULL BUILT FROM THE SAME DATA. Within each session the
forward-return series is circularly shifted by a random offset, which destroys any true
lead-lag alignment while preserving each series' own autocorrelation, volatility and
intraday shape. The entire sweep is then re-run on the shuffled data, and the statistic
kept is MAX |r| ACROSS THE WHOLE SWEEP. Repeating that many times gives the distribution
of "best result obtainable from noise by trying this hard."

The real sweep's best result is then read against that distribution:
    p = fraction of null sweeps whose best |r| >= the observed best |r|
If p is large, the best cell of the sweep is what searching 550 times buys you and
nothing more. That is the only honest way to report a parameter sweep.

Also reported: for each horizon pair, how many cells breach the naive 2-sigma bar versus
how many are EXPECTED to breach by chance. If observed ~= expected, the grid is noise.
"""
import sqlite3, json
import numpy as np, pandas as pd

DB = "option_chains.db"
CACHE = ".state/nifty50_view_cache_v17.json"
LOOKBACKS = [5, 10, 15, 30, 60]
FORWARDS = [5, 10, 15, 30, 60]
N_PERM = 300
RNG = np.random.default_rng(20260811)

v = json.load(open(CACHE)); v = v.get("view") or v
meta = {r["symbol"]: (r.get("weight") or 0.0, r.get("sector") or "?") for r in v["rows"]}
syms = [s for s in meta if meta[s][0] > 0]

con = sqlite3.connect(DB)
df = pd.read_sql("SELECT symbol, ts, close FROM price_bars WHERE timeframe='1m' "
                 "AND symbol IN (%s)" % ",".join("?" * len(syms)), con, params=syms)
df["ts"] = pd.to_datetime(df["ts"].str.replace("Z", "", regex=False))
df["day"] = df["ts"].dt.date

# ---- per-session panels: index return path R, and sector relative-strength paths ----
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
        if len(cols) < 2:
            continue
        ws = w[cols] / w[cols].sum()
        sec[s] = r[:, cols] @ ws - R
    sessions.append({"day": str(day), "R": R, "sec": sec, "n": len(R)})

SECTORS = sorted(set().union(*[set(s["sec"]) for s in sessions]))
print("sessions %d   sectors %d   bars/session %d-%d"
      % (len(sessions), len(SECTORS), min(s["n"] for s in sessions),
         max(s["n"] for s in sessions)))


def corr(a, b):
    if len(a) < 30:
        return np.nan
    sa, sb = a.std(), b.std()
    if sa <= 0 or sb <= 0:
        return np.nan
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def sweep(shift=False):
    """Return {(lb,fw,sector): (persist_r, lead_r, n)}. shift=True builds the null."""
    res = {}
    for lb in LOOKBACKS:
        for fw in FORWARDS:
            acc = {s: [[], [], []] for s in SECTORS}   # dRS, fRS, fIndex
            for S in sessions:
                n, R, sec = S["n"], S["R"], S["sec"]
                if n < lb + fw + 10:
                    continue
                idx = np.arange(lb, n - fw, fw)         # NON-overlapping
                if len(idx) < 3:
                    continue
                if shift:
                    # circular shift kills alignment, keeps each path's own structure
                    k = RNG.integers(1, n)
                    Rf, secf = np.roll(R, k), {s: np.roll(v_, k) for s, v_ in sec.items()}
                else:
                    Rf, secf = R, sec
                fR = Rf[idx + fw] - Rf[idx]
                for s in SECTORS:
                    if s not in sec:
                        continue
                    d = sec[s][idx] - sec[s][idx - lb]        # past change: never shifted
                    acc[s][0].append(d)
                    acc[s][1].append(secf[s][idx + fw] - secf[s][idx])
                    acc[s][2].append(fR)
            for s in SECTORS:
                if not acc[s][0]:
                    continue
                d = np.concatenate(acc[s][0]); f = np.concatenate(acc[s][1])
                fi = np.concatenate(acc[s][2])
                res[(lb, fw, s)] = (corr(d, f), corr(d, fi), len(d))
    return res


real = sweep(False)
cells = [(k, v_) for k, v_ in real.items() if not np.isnan(v_[0])]
best_p = max(cells, key=lambda kv: abs(kv[1][0]))
best_l = max(cells, key=lambda kv: abs(kv[1][1]))
obs_max = max(max(abs(v_[0]), abs(v_[1])) for _, v_ in cells if not np.isnan(v_[1]))

print("\n=== PERSISTENCE grid: mean r across sectors (dRS -> forward dRS) ===")
print("      fwd " + "".join("%8d" % f for f in FORWARDS))
for lb in LOOKBACKS:
    row = "lb %3d  " % lb
    for fw in FORWARDS:
        vals = [real[(lb, fw, s)][0] for s in SECTORS
                if (lb, fw, s) in real and not np.isnan(real[(lb, fw, s)][0])]
        row += "%8s" % (("%+.3f" % np.mean(vals)) if vals else "  --")
    print(row)

print("\n=== LEAD grid: mean r across sectors (dRS -> forward INDEX return) ===")
print("      fwd " + "".join("%8d" % f for f in FORWARDS))
for lb in LOOKBACKS:
    row = "lb %3d  " % lb
    for fw in FORWARDS:
        vals = [real[(lb, fw, s)][1] for s in SECTORS
                if (lb, fw, s) in real and not np.isnan(real[(lb, fw, s)][1])]
        row += "%8s" % (("%+.3f" % np.mean(vals)) if vals else "  --")
    print(row)

# naive 2-sigma breaches vs how many chance alone predicts
brk = exp = 0
for k, (p, l, n) in real.items():
    bar = 1.96 / np.sqrt(max(n, 2))
    for x in (p, l):
        if not np.isnan(x):
            exp += 0.05
            brk += abs(x) > bar
print("\ncells breaching naive 2-sigma: %d      expected by chance alone: %.1f" % (brk, exp))
print("best |persistence| : %s lb=%d fw=%d  r=%+.3f (n=%d)"
      % (best_p[0][2], best_p[0][0], best_p[0][1], best_p[1][0], best_p[1][2]))
print("best |lead|        : %s lb=%d fw=%d  r=%+.3f (n=%d)"
      % (best_l[0][2], best_l[0][0], best_l[0][1], best_l[1][1], best_l[1][2]))
print("observed BEST |r| anywhere in the sweep: %.3f" % obs_max)

print("\nrunning %d null sweeps (circular-shift)..." % N_PERM)
null = []
for i in range(N_PERM):
    nr = sweep(True)
    null.append(max(max(abs(a), abs(b)) for a, b, _ in nr.values()
                    if not (np.isnan(a) or np.isnan(b))))
    if (i + 1) % 60 == 0:
        print("  %d/%d" % (i + 1, N_PERM), flush=True)
null = np.array(null)
p_val = float((null >= obs_max).mean())
print("\n=== SWEEP-WIDE TEST ===")
print("null best |r|: median %.3f   90th %.3f   95th %.3f   max %.3f"
      % (np.median(null), np.percentile(null, 90), np.percentile(null, 95), null.max()))
print("observed best |r| = %.3f   ->   p = %.3f" % (obs_max, p_val))
print(">>> %s" % ("SURVIVES: the best cell beats what searching this hard buys from noise"
                  if p_val < 0.05 else
                  "FAILS: the best cell is what searching 550 times buys from noise alone"))

json.dump({"lookbacks": LOOKBACKS, "forwards": FORWARDS, "sessions": len(sessions),
           "n_permutations": N_PERM, "observed_best_abs_r": round(obs_max, 4),
           "null_median": round(float(np.median(null)), 4),
           "null_p95": round(float(np.percentile(null, 95)), 4),
           "sweep_p_value": p_val, "survives": bool(p_val < 0.05),
           "naive_2sigma_breaches": int(brk), "expected_by_chance": round(exp, 1),
           "best_persistence": {"sector": best_p[0][2], "lookback": best_p[0][0],
                                "forward": best_p[0][1], "r": round(best_p[1][0], 4)},
           "best_lead": {"sector": best_l[0][2], "lookback": best_l[0][0],
                         "forward": best_l[0][1], "r": round(best_l[1][1], 4)},
           "grid": {"%d_%d_%s" % k: {"persist": None if np.isnan(v_[0]) else round(v_[0], 4),
                                     "lead": None if np.isnan(v_[1]) else round(v_[1], 4),
                                     "n": v_[2]} for k, v_ in real.items()}},
          open("rotation_sweep_result.json", "w"), indent=1)
print("\nwrote rotation_sweep_result.json")
