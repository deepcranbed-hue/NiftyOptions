"""Follow-up: the only thing that survived multiple testing was the OVERNIGHT GAP.

fii_deriv.py found that across 8 FII signals and 3 targets, the max-|t| null clears
only for r_gap (p=0.022) -- not close-to-close (0.202), not intraday (0.220). This
script interrogates that one survivor before anyone believes it.

Four questions, in the order that can kill it fastest:
  1. Is the variance decomposition in the first script even right? (No -- shares summed
     to 117%, which means gap and intraday are negatively correlated. Fixed here.)
  2. Does it survive splitting the sample in half? A one-year window with 217 rows can
     produce a 2.86 t-stat from a single quarter.
  3. Does it survive controlling for the previous day's return? Overnight gaps reverse
     intraday moves; if FII option activity is just a proxy for "yesterday fell", the
     signal is the reversal, not the flow.
  4. Is it tradable at all? The participant file publishes AFTER the close on day D.
     Capturing close(D)->open(D+1) requires entering before that file exists.
"""
import sqlite3, json
import numpy as np
import pandas as pd

NLAG, NSIM = 5, 4000
RNG = np.random.default_rng(20260813)
con = sqlite3.connect("option_chains.db")

px = pd.read_sql("select substr(ts,1,10) d, open, close from price_bars "
                 "where symbol='NIFTY' and timeframe='1d' and ts>='2025-07-01' order by ts", con)
px["d"] = pd.to_datetime(px.d); px = px.drop_duplicates("d").sort_values("d").reset_index(drop=True)
px["open_n"] = px.open.shift(-1); px["close_n"] = px.close.shift(-1)
px["open_n2"] = px.open.shift(-2)
px["r_prev"] = px.close / px.close.shift(1) - 1.0          # day D's own return
px["r_cc"] = px.close_n / px.close - 1.0
px["r_oc"] = px.close_n / px.open_n - 1.0
px["r_gap"] = px.open_n / px.close - 1.0
px["r_gap2"] = px.open_n2 / px.close_n - 1.0               # the NEXT gap, D+1 -> D+2

fii = pd.read_sql("select flow_date, idx_opt_call_long, idx_opt_call_short, "
                  "idx_opt_put_long, idx_opt_put_short from participant_flows "
                  "where participant_type='FII' order by flow_date", con)
fii["d"] = pd.to_datetime(fii.flow_date)
fii["call_net"] = fii.idx_opt_call_long - fii.idx_opt_call_short
fii["put_net"] = fii.idx_opt_put_long - fii.idx_opt_put_short
fii["opt_dir"] = fii.call_net - fii.put_net
fii["act_pcr"] = ((fii.idx_opt_put_long + fii.idx_opt_put_short)
                  / (fii.idx_opt_call_long + fii.idx_opt_call_short).replace(0, np.nan))
for c in ["opt_dir", "put_net", "act_pcr"]:
    m = fii[c].rolling(60, min_periods=30).mean().shift(1)
    s = fii[c].rolling(60, min_periods=30).std().shift(1)
    fii[c + "_z"] = (fii[c] - m) / s

SIG = ["opt_dir_z", "put_net_z", "act_pcr_z"]
df = fii[["d"] + SIG].merge(
    px[["d", "r_prev", "r_cc", "r_oc", "r_gap", "r_gap2"]], on="d", how="inner").dropna()
print(f"n = {len(df)}   {df.d.min().date()} -> {df.d.max().date()}\n")


def nw_t(X, y, lag=NLAG):
    """Newey-West t-stats on every slope in a multivariate regression."""
    X = np.column_stack([np.ones(len(y))] + [np.asarray(c, float) for c in X])
    y = np.asarray(y, float)
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        w = 1.0 - L / (lag + 1.0)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return b, b / np.sqrt(np.diag(V))


out = {"n": len(df)}

# ---------- 1. correct variance decomposition ----------
# Var(cc) = Var(gap) + Var(oc) + 2 Cov(gap, oc). The first script printed the two
# variance shares and they summed to 117% -- the covariance term was missing and it
# is NEGATIVE, i.e. overnight moves get partially given back during the session.
vg, vo = df.r_gap.var(), df.r_oc.var()
cov = df.r_gap.cov(df.r_oc)
vcc = df.r_cc.var()
out["variance"] = dict(
    gap_share=round(vg / vcc, 3), intraday_share=round(vo / vcc, 3),
    cov_share=round(2 * cov / vcc, 3), check_sums_to_one=round((vg + vo + 2 * cov) / vcc, 3),
    corr_gap_intraday=round(float(df.r_gap.corr(df.r_oc)), 3))
print("1. VARIANCE DECOMPOSITION of close-to-close")
print(f"   gap {out['variance']['gap_share']:+.3f}  intraday {out['variance']['intraday_share']:+.3f}"
      f"  2*cov {out['variance']['cov_share']:+.3f}   sums to {out['variance']['check_sums_to_one']}")
print(f"   corr(gap, intraday) = {out['variance']['corr_gap_intraday']}"
      "   <- negative means the session partly REVERSES the gap\n")

# ---------- 2. split-half stability ----------
mid = len(df) // 2
halves = {}
for name, sub in [("first_half", df.iloc[:mid]), ("second_half", df.iloc[mid:])]:
    row = {}
    for s in SIG:
        r = float(np.corrcoef(sub[s], sub.r_gap)[0, 1])
        _, t = nw_t([sub[s].values], sub.r_gap.values)
        row[s] = dict(r=round(r, 4), t=round(float(t[1]), 2))
    row["span"] = f"{sub.d.min().date()} -> {sub.d.max().date()}  n={len(sub)}"
    halves[name] = row
out["split_half"] = halves
print("2. SPLIT-HALF STABILITY on r_gap")
for k, v in halves.items():
    print(f"   {k:12s} {v['span']}")
    for s in SIG:
        print(f"      {s:12s} r={v[s]['r']:+.4f}  t={v[s]['t']:+.2f}")
print()

# ---------- 3. control for the previous day's return ----------
print("3. CONTROLLING FOR DAY D's OWN RETURN (gaps reverse prior moves)")
ctrl = {}
for s in SIG:
    b1, t1 = nw_t([df[s].values], df.r_gap.values)
    b2, t2 = nw_t([df[s].values, df.r_prev.values], df.r_gap.values)
    ctrl[s] = dict(t_alone=round(float(t1[1]), 2), t_with_control=round(float(t2[1]), 2),
                   t_on_r_prev=round(float(t2[2]), 2),
                   beta_shrink=round(float(b2[1] / b1[1]), 3) if b1[1] else None)
    print(f"   {s:12s} t alone {ctrl[s]['t_alone']:+.2f} -> with r_prev {ctrl[s]['t_with_control']:+.2f}"
          f"   (r_prev's own t = {ctrl[s]['t_on_r_prev']:+.2f}, beta keeps {ctrl[s]['beta_shrink']:.0%})")
out["prev_return_control"] = ctrl
print()

# ---------- 4. is anything left to trade? ----------
# The file lands after the close on D, so close(D)->open(D+1) is unreachable. The only
# reachable versions are: enter at open(D+1) and hold the session (r_oc, already dead),
# or wait and take the NEXT gap, open(D+2) vs close(D+1) -- one day staler.
print("4. TRADABILITY -- what is left once the unreachable gap is removed")
trad = {}
for s in SIG:
    for tgt in ["r_gap", "r_oc", "r_gap2"]:
        sub = df.dropna(subset=[tgt])
        r = float(np.corrcoef(sub[s], sub[tgt])[0, 1])
        _, t = nw_t([sub[s].values], sub[tgt].values)
        n = len(sub)
        null = np.array([abs(np.corrcoef(np.roll(sub[s].values, RNG.integers(1, n)), sub[tgt].values)[0, 1])
                         for _ in range(NSIM)])
        trad[f"{s}|{tgt}"] = dict(r=round(r, 4), t=round(float(t[1]), 2),
                                  p=round(float((null >= abs(r)).mean()), 4),
                                  reachable=(tgt != "r_gap"), n=n)
out["tradability"] = trad
print(f"   {'signal':12s}{'target':9s}{'r':>9}{'t':>7}{'p':>8}  reachable?")
for k, v in trad.items():
    s, tgt = k.split("|")
    print(f"   {s:12s}{tgt:9s}{v['r']:9.4f}{v['t']:7.2f}{v['p']:8.4f}  "
          f"{'YES' if v['reachable'] else 'NO - file publishes after the close'}")

json.dump(out, open("fii_gap_result.json", "w"), indent=1)
print("\nwrote fii_gap_result.json")
