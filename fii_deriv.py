"""Does FII derivatives positioning on day D predict Nifty on day D+1?

The publication lag is the whole point. NSE puts the participant-wise F&O file out
after the close on day D, so the earliest a human can act on it is the open of D+1.
Everything here is built to respect that: the signal is dated D, the target is D+1,
and there is no same-day leakage anywhere.

Three targets, because "predict next day" is ambiguous and the difference matters:
  r_cc  close(D)   -> close(D+1)   the textbook number, NOT tradable on this signal
                                   (it includes the overnight gap you cannot enter)
  r_oc  open(D+1)  -> close(D+1)   what you can actually capture entering at the open
  r_gap close(D)   -> open(D+1)    the piece r_cc has that r_oc does not

Multiple testing is handled explicitly. Eight signals times three targets is 24 shots
at the same 247 observations; the max-|t| null below is what keeps that honest.

NOTE ON WHAT THIS DATA IS: participant_flows carries NSE's fao_participant_vol_ file --
TRADED VOLUME in contracts, not open interest. So idx_fut_long minus idx_fut_short is
the one-day CHANGE in FII net index-futures position, not the level. The level series
(fao_participant_oi_) has never been collected. The cumulative column below reconstructs
a level proxy, but it is a running sum of changes with no anchor and it drifts.
"""
import sqlite3, json
import numpy as np
import pandas as pd

DB = "option_chains.db"
NLAG = 5          # Newey-West lag
NSIM = 2000       # circular-shift null draws
RNG = np.random.default_rng(20260813)

con = sqlite3.connect(DB)

# ---------- Nifty daily OHLC ----------
px = pd.read_sql(
    "select substr(ts,1,10) d, open, close from price_bars "
    "where symbol='NIFTY' and timeframe='1d' and ts>='2025-07-01' order by ts", con)
px["d"] = pd.to_datetime(px["d"])
px = px.drop_duplicates("d").sort_values("d").reset_index(drop=True)

# next session's bar
px["open_n"] = px["open"].shift(-1)
px["close_n"] = px["close"].shift(-1)
px["r_cc"] = px["close_n"] / px["close"] - 1.0
px["r_oc"] = px["close_n"] / px["open_n"] - 1.0
px["r_gap"] = px["open_n"] / px["close"] - 1.0

# ---------- FII participant flows ----------
fii = pd.read_sql(
    "select flow_date, idx_fut_long, idx_fut_short, "
    "idx_opt_call_long, idx_opt_call_short, idx_opt_put_long, idx_opt_put_short, "
    "total_long, total_short from participant_flows where participant_type='FII' "
    "order by flow_date", con)
fii["d"] = pd.to_datetime(fii["flow_date"])
fii = fii.drop_duplicates("d").sort_values("d").reset_index(drop=True)

# ---------- signals, all dated D ----------
f = fii
f["fut_net"] = f.idx_fut_long - f.idx_fut_short
f["fut_cum"] = f["fut_net"].cumsum()
f["fut_ls"] = f.idx_fut_long / (f.idx_fut_long + f.idx_fut_short).replace(0, np.nan)
f["call_net"] = f.idx_opt_call_long - f.idx_opt_call_short
f["put_net"] = f.idx_opt_put_long - f.idx_opt_put_short
f["opt_dir"] = f["call_net"] - f["put_net"]
f["act_pcr"] = ((f.idx_opt_put_long + f.idx_opt_put_short)
                / (f.idx_opt_call_long + f.idx_opt_call_short).replace(0, np.nan))
f["tot_net"] = f.total_long - f.total_short

RAW = ["fut_net", "fut_cum", "fut_ls", "call_net", "put_net", "opt_dir", "act_pcr", "tot_net"]

# Raw contract counts trend and change scale with total market activity, so every signal
# is also z-scored on a trailing 60-day window. The z-score uses only past data --
# .shift(1) on the rolling moments -- so nothing from day D's own distribution leaks in.
for c in RAW:
    m = f[c].rolling(60, min_periods=30).mean().shift(1)
    s = f[c].rolling(60, min_periods=30).std().shift(1)
    f[c + "_z"] = (f[c] - m) / s

SIGS = [c + "_z" for c in RAW]

df = f[["d"] + SIGS].merge(px[["d", "r_cc", "r_oc", "r_gap"]], on="d", how="inner").dropna()
print(f"merged observations: {len(df)}   {df.d.min().date()} -> {df.d.max().date()}")


def nw_t(x, y, lag=NLAG):
    """Newey-West t on the slope of y ~ a + b x. Persistent regressors make the OLS
    standard error far too small; this is the same correction used elsewhere in the repo."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    X = np.column_stack([np.ones_like(x), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        w = 1.0 - L / (lag + 1.0)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return b[1], b[1] / np.sqrt(V[1, 1])


def circ_null(x, y, nsim=NSIM):
    """Circular shift breaks the signal/target pairing while preserving BOTH series'
    autocorrelation. A plain permutation would destroy the persistence and understate
    the null, which is exactly how a persistent regressor manufactures significance."""
    n = len(x)
    out = np.empty(nsim)
    for i in range(nsim):
        k = RNG.integers(1, n)
        out[i] = abs(np.corrcoef(np.roll(x, k), y)[0, 1])
    return out


results = []
maxt_obs = {t: 0.0 for t in ["r_cc", "r_oc", "r_gap"]}

for tgt in ["r_cc", "r_oc", "r_gap"]:
    for s in SIGS:
        x = df[s].values; y = df[tgt].values
        r = np.corrcoef(x, y)[0, 1]
        b, t = nw_t(x, y)
        null = circ_null(x, y)
        p = float((null >= abs(r)).mean())
        # quintile spread on the signal
        q = pd.qcut(df[s], 5, labels=False, duplicates="drop")
        qm = df.groupby(q)[tgt].mean()
        spread = float(qm.iloc[-1] - qm.iloc[0]) if len(qm) == 5 else np.nan
        hit = float((np.sign(x) == np.sign(y)).mean())
        results.append(dict(target=tgt, signal=s, r=round(float(r), 4),
                            nw_t=round(float(t), 2), p_null=round(p, 4),
                            null_p95=round(float(np.percentile(null, 95)), 4),
                            q5_minus_q1_pct=round(spread * 100, 4),
                            sign_agree=round(hit, 3)))
        maxt_obs[tgt] = max(maxt_obs[tgt], abs(t))

# ---------- max-|t| null across the whole signal family ----------
# Eight correlated signals on one target is eight shots at the same data. The right
# yardstick is not "is any |t| > 2" but "how large is the LARGEST |t| under the null",
# with the signals' cross-correlation preserved -- so the whole block is shifted together.
X = df[SIGS].values
maxt_null = {}
for tgt in ["r_cc", "r_oc", "r_gap"]:
    y = df[tgt].values
    n = len(y)
    draws = np.empty(500)
    for i in range(500):
        k = RNG.integers(1, n)
        Xs = np.roll(X, k, axis=0)
        draws[i] = max(abs(nw_t(Xs[:, j], y)[1]) for j in range(Xs.shape[1]))
    maxt_null[tgt] = dict(observed_max_t=round(maxt_obs[tgt], 2),
                          null_median=round(float(np.median(draws)), 2),
                          null_p95=round(float(np.percentile(draws, 95)), 2),
                          p=round(float((draws >= maxt_obs[tgt]).mean()), 4))

# ---------- how much of r_cc is the gap you cannot trade ----------
var_gap = float(np.var(df.r_gap)); var_oc = float(np.var(df.r_oc)); var_cc = float(np.var(df.r_cc))
decomp = dict(var_gap_share=round(var_gap / var_cc, 3), var_oc_share=round(var_oc / var_cc, 3))

out = dict(n=len(df), start=str(df.d.min().date()), end=str(df.d.max().date()),
           results=results, maxt_null=maxt_null, variance_decomp=decomp)
json.dump(out, open("fii_deriv_result.json", "w"), indent=1)

print("\n=== per-signal (z-scored, day D -> day D+1) ===")
print(f"{'target':7s}{'signal':12s}{'r':>8}{'NW t':>7}{'p(null)':>9}{'Q5-Q1 %':>10}{'sign%':>7}")
for x in results:
    print(f"{x['target']:7s}{x['signal']:12s}{x['r']:8.4f}{x['nw_t']:7.2f}"
          f"{x['p_null']:9.4f}{x['q5_minus_q1_pct']:10.3f}{x['sign_agree']:7.3f}")
print("\n=== max-|t| null across all 8 signals (multiple-testing corrected) ===")
for k, v in maxt_null.items():
    print(f"  {k}: observed {v['observed_max_t']}  null median {v['null_median']}"
          f"  null p95 {v['null_p95']}  p = {v['p']}")
print("\n=== variance decomposition of the close-to-close target ===")
print(f"  overnight gap {decomp['var_gap_share']:.1%} of close-to-close variance; "
      f"intraday {decomp['var_oc_share']:.1%}")
