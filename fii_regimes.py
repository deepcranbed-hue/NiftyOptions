#!/usr/bin/env python3
"""fii_regimes -- FII POSITIONING regimes, recovered from data already in the repo.

THE DISCOVERY THAT MAKES THIS POSSIBLE. participant_flows was believed to hold traded
volume only, which cannot answer "is FII positioning extreme?" -- a busy trading day says
nothing about which way the book leans. But checked against the 20 days of true net
position in .state/flows_cash_cache.json, the identity is exact:

    FII idx_fut_long - idx_fut_short  ==  the ONE-DAY CHANGE in FII net index futures

18 of 20 days match to the contract. Both exceptions are explained, not noise:
    2026-07-20  a 4-day gap in the anchor series, so the diff is not a one-day change
    2026-07-28  MONTHLY EXPIRY: +77,301 contracts left open interest by settlement,
                without any trade. The flow file cannot see that, by construction.

So position is recoverable by INTEGRATING the flow -- with one caveat that dictates the
whole design below.

WHY THE SERIES IS RESET EVERY MONTH. Cumulating across an expiry accumulates the
settlement discontinuity as permanent error, and there are ~12 expiries in this window
with only ONE of them observed, so they cannot be calibrated out. Cumulating anyway would
produce a series whose level is wrong by an unknown and growing amount -- confident and
false. Instead the cumulation RESETS at each monthly expiry (last Tuesday), giving:

    position accumulated SINCE THIS CYCLE BEGAN  -- exact within the cycle, by the
    identity above, and never carrying error across a settlement.

This is a narrower quantity than the absolute book, and the narrowing is the honest part:
it is what the data supports. It is also arguably the better regime variable, since it
measures the FRESH position being built rather than a legacy holding.

WHAT IS TESTED
  1. REGIME     forward NIFTY return by quintile of cycle position (the direction question)
  2. INTERACTION  FII position x USDINR direction x VIX direction -- does agreement help?
  3. Both scored against a circular-shift null, because with 5 buckets x 4 horizons (and
     12 x 4 for the interaction) the best cell is a searched maximum, not a finding.

SAMPLE: 247 days, ~12 monthly cycles. A 5-bucket split leaves ~49 days per bucket and the
interaction leaves ~20 per cell. That is thin, and the nulls below are what keep it honest.
"""
import calendar, json, sqlite3
from datetime import date, timedelta
import numpy as np, pandas as pd

HOR = [1, 2, 3, 5]
N_PERM = 400
RNG = np.random.default_rng(20260811)

con = sqlite3.connect("option_chains.db")
pf = pd.read_sql("SELECT flow_date,idx_fut_long,idx_fut_short FROM participant_flows "
                 "WHERE participant_type='FII' ORDER BY flow_date", con)
pf["flow"] = pf.idx_fut_long - pf.idx_fut_short
pf["d"] = pd.to_datetime(pf.flow_date)
pf = pf.set_index("d")[["flow"]]

px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol "
                 "IN ('NIFTY','USDINR','INDIAVIX')", con)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill()

def last_tue(y, m):
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return pd.Timestamp(d)

exp = {last_tue(d.year, d.month) for d in pf.index}
cyc, run = [], 0.0
for d in pf.index:
    run += float(pf.at[d, "flow"])
    cyc.append(run)
    if d in exp:                      # settlement: the next cycle starts from flat
        run = 0.0
pf["cycle_pos"] = cyc

D = pf.join(P, how="inner").dropna(subset=["NIFTY"])
D["dUSDINR"] = D.USDINR.pct_change() * 100
D["dVIX"] = D.INDIAVIX.pct_change() * 100
for h in HOR:
    D["f%d" % h] = (D.NIFTY.shift(-h) / D.NIFTY - 1) * 100
D = D.dropna(subset=["dUSDINR", "dVIX"])
print("days %d   cycles %d   cycle_pos range %.0f .. %.0f contracts"
      % (len(D), len(exp), D.cycle_pos.min(), D.cycle_pos.max()))

D["Q"] = pd.qcut(D.cycle_pos, 5, labels=["Q1 most short", "Q2", "Q3", "Q4", "Q5 most long"])


def spread_stats(pos_series):
    """Mean forward NIFTY return per quintile + the Q5-Q1 spread, per horizon."""
    q = pd.qcut(pos_series, 5, labels=False)
    out = {}
    for h in HOR:
        m = [D["f%d" % h][q == k].mean() for k in range(5)]
        out[h] = (m, m[4] - m[0])
    return out


obs = spread_stats(D.cycle_pos)
print("\n1. FORWARD NIFTY RETURN (%) BY FII CYCLE-POSITION QUINTILE")
print("%-16s %7s" % ("quintile", "n") + "".join("%10s" % ("+%dd" % h) for h in HOR))
print("-" * 60)
for k, lab in enumerate(["Q1 most short", "Q2", "Q3", "Q4", "Q5 most long"]):
    n = int((pd.qcut(D.cycle_pos, 5, labels=False) == k).sum())
    print("%-16s %7d" % (lab, n) + "".join("%10.3f" % obs[h][0][k] for h in HOR))
print("%-16s %7s" % ("Q5 - Q1 spread", "") + "".join("%10.3f" % obs[h][1] for h in HOR))

# ---- null: shift the position series against the returns, keeping both intact ----
n = len(D)
null = {h: [] for h in HOR}
for _ in range(N_PERM):
    sh = pd.Series(np.roll(D.cycle_pos.values, int(RNG.integers(10, n - 10))), index=D.index)
    st = spread_stats(sh)
    for h in HOR:
        null[h].append(st[h][1])
print("\n   spread vs circular-shift null (%d draws):" % N_PERM)
print("   %-8s %10s %10s %10s %8s" % ("horizon", "observed", "null p5", "null p95", "p(2-sided)"))
best_p = 1.0
for h in HOR:
    a = np.array(null[h]); o = obs[h][1]
    p = float((np.abs(a) >= abs(o)).mean()); best_p = min(best_p, p)
    print("   %-8s %10.3f %10.3f %10.3f %8.3f%s"
          % ("+%dd" % h, o, np.percentile(a, 5), np.percentile(a, 95), p,
             "  <<<" if p < 0.05 else ""))

# ---- 2. interaction ----
print("\n2. INTERACTION -- FII position tercile x USDINR x VIX, forward NIFTY %+dd" % 5)
D["Tpos"] = pd.qcut(D.cycle_pos, 3, labels=["short", "mid", "long"])
D["Tfx"] = np.where(D.dUSDINR > 0, "INR weak", "INR firm")
D["Tvix"] = np.where(D.dVIX > 0, "VIX up", "VIX down")
print("%-10s %-10s %-9s %6s %9s %9s" % ("FII", "USDINR", "VIX", "n", "fwd+5d", "t"))
print("-" * 60)
cells = []
for (a, b, c_), g in D.groupby(["Tpos", "Tfx", "Tvix"], observed=True):
    v = g["f5"].dropna()
    if len(v) < 8: continue
    t = v.mean() / (v.std() / np.sqrt(len(v))) if v.std() > 0 else 0
    cells.append((a, b, c_, len(v), v.mean(), t))
for r in sorted(cells, key=lambda x: -x[4]):
    print("%-10s %-10s %-9s %6d %9.3f %9.2f%s" % (r[0], r[1], r[2], r[3], r[4], r[5],
                                                  "  *" if abs(r[5]) > 2 else ""))
obs_max = max(abs(r[5]) for r in cells)
nullmax = []
for _ in range(N_PERM):
    k = int(RNG.integers(10, n - 10))
    sh = pd.DataFrame({"Tpos": np.roll(D.Tpos.values, k), "Tfx": np.roll(D.Tfx.values, k),
                       "Tvix": np.roll(D.Tvix.values, k), "f5": D.f5.values}, index=D.index)
    best = 0
    for _k, g in sh.groupby(["Tpos", "Tfx", "Tvix"], observed=True):
        v = g["f5"].dropna()
        if len(v) < 8 or v.std() == 0: continue
        best = max(best, abs(v.mean() / (v.std() / np.sqrt(len(v)))))
    nullmax.append(best)
nullmax = np.array(nullmax)
p_int = float((nullmax >= obs_max).mean())
print("\n   best |t| across %d cells: observed %.2f   null median %.2f   null p95 %.2f"
      % (len(cells), obs_max, np.median(nullmax), np.percentile(nullmax, 95)))
print("   p = %.3f  ->  %s" % (p_int, "SURVIVES" if p_int < 0.05 else
                               "inside what searching %d cells gives from noise" % len(cells)))
json.dump({"days": len(D), "quintile_means": {str(h): obs[h][0] for h in HOR},
           "spread_Q5_Q1": {str(h): obs[h][1] for h in HOR},
           "spread_p": {str(h): float((np.abs(np.array(null[h])) >= abs(obs[h][1])).mean())
                        for h in HOR},
           "interaction_best_t": float(obs_max), "interaction_p": p_int,
           "n_permutations": N_PERM},
          open("fii_regimes_result.json", "w"), indent=1)
print("\nwrote fii_regimes_result.json")
