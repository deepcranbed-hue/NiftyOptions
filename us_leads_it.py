#!/usr/bin/env python3
"""us_leads_it -- does US tech actually lead Indian IT, and is any of it capturable?

THREE QUESTIONS, in increasing order of how much they matter.

1. SAME-DAY vs NEXT-DAY. The US close stamped D prints after NIFTY's D close, so a
   same-day correlation cannot be a lead. Both columns are shown so the difference is
   visible rather than argued.

2. IS IT AI-CAPEX, OR JUST GLOBAL BETA? SOX predicting NIFTYIT proves nothing on its own
   if SOX merely proxies world risk appetite. Two controls settle it:
     a. the target is NIFTYIT RELATIVE TO NIFTY -- if US tech only moves the whole Indian
        market, the relative version goes to zero and the story is beta, not semis;
     b. SOX is regressed alongside SP500 -- if SOX's coefficient dies once the broad
        market is in the equation, "AI capex" was the S&P wearing a lab coat.

3. THE ONE THAT DECIDES USABILITY: WHERE DOES THE MOVE LAND? A US signal that Indian
   markets absorb entirely in the OPENING PRINT is real and useless -- by the time you
   can act, the gap has happened. So the next Indian session is split:
        gap      = open(D+1) / close(D) - 1        cannot be traded from D's close
        intraday = close(D+1) / open(D+1) - 1      CAN be traded at D+1's open
   If the predictability is all gap and none intraday, US tech genuinely leads Indian IT
   and there is still nothing to do about it. That distinction is invisible in a
   close-to-close correlation, which is how this kind of signal usually gets oversold.

Alignment is the strict backward as-of from backfill_us_indices: for Indian session T,
the latest US close STRICTLY BEFORE T, with staleness recorded so holiday gaps can be
excluded rather than silently forward-filled.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(2026)
con = sqlite3.connect("option_chains.db")
US = ["SP500", "NASDAQ", "NDX100", "SOX", "VIX_US"]
IN = ["NIFTY", "NIFTYIT"]
df = pd.read_sql("SELECT symbol,ts,open,close FROM price_bars WHERE timeframe='1d' AND "
                 "symbol IN (%s)" % ",".join("?" * (len(US) + len(IN))), con, params=US + IN)
df["d"] = pd.to_datetime(df.ts.str[:10])
C = df.pivot_table(index="d", columns="symbol", values="close").sort_index()
O = df.pivot_table(index="d", columns="symbol", values="open").sort_index()

usr = (C[US].dropna(how="all").pct_change(fill_method=None) * 100).dropna()
ind_dates = C[IN].dropna().index
# strict backward as-of: US row must predate the Indian session
left = pd.DataFrame({"ind": ind_dates}).sort_values("ind")
right = usr.reset_index().rename(columns={"d": "us_date"})
M = pd.merge_asof(left, right, left_on="ind", right_on="us_date",
                  direction="backward", allow_exact_matches=False)
M["age"] = (M["ind"] - M["us_date"]).dt.days
M = M.set_index("ind")
M = M[M.age <= 4]                                   # drop stale-across-holiday joins
print("aligned observations: %d   (%s .. %s)  median staleness %d day(s)"
      % (len(M), M.index.min().date(), M.index.max().date(), int(M.age.median())))

for t in IN:
    M["ret_" + t] = (C[t] / C[t].shift(1) - 1).reindex(M.index) * 100
    M["gap_" + t] = (O[t] / C[t].shift(1) - 1).reindex(M.index) * 100
    M["itd_" + t] = (C[t] / O[t] - 1).reindex(M.index) * 100
M["rel"] = M.ret_NIFTYIT - M.ret_NIFTY
M["rel_gap"] = M.gap_NIFTYIT - M.gap_NIFTY
M["rel_itd"] = M.itd_NIFTYIT - M.itd_NIFTY
M = M.dropna()

def cc(a, b):
    return float(np.corrcoef(a, b)[0, 1])

print("\n1. SAME-DAY (invalid as a lead) vs NEXT-SESSION (valid)")
print("   %-8s %14s %14s %14s" % ("US", "same-day NIFTY", "next NIFTY", "next NIFTYIT"))
print("   " + "-" * 56)
sd = pd.concat([usr, (C[IN] / C[IN].shift(1) - 1) * 100], axis=1).dropna()
for u in US:
    print("   %-8s %14.3f %14.3f %14.3f"
          % (u, cc(sd[u], sd.NIFTY), cc(M[u], M.ret_NIFTY), cc(M[u], M.ret_NIFTYIT)))

print("\n2. IS IT SEMIS, OR JUST BETA?  target = NIFTYIT RELATIVE to NIFTY")
band = 1.96 / np.sqrt(len(M))
print("   noise band +/-%.3f" % band)
print("   %-8s %12s %12s %12s" % ("US", "-> IT rel", "-> IT abs", "-> NIFTY"))
print("   " + "-" * 48)
for u in US:
    r = cc(M[u], M.rel)
    print("   %-8s %12.3f %12.3f %12.3f%s"
          % (u, r, cc(M[u], M.ret_NIFTYIT), cc(M[u], M.ret_NIFTY),
             "  *" if abs(r) > band else ""))

X = np.column_stack([np.ones(len(M)), M.SP500.values, M.SOX.values])
for tgt, lab in ((M.ret_NIFTYIT.values, "NIFTYIT absolute"), (M.rel.values, "NIFTYIT relative")):
    b = np.linalg.lstsq(X, tgt, rcond=None)[0]
    e = tgt - X @ b
    se = np.sqrt(np.diag(np.linalg.pinv(X.T @ X) * (e @ e) / (len(tgt) - 3)))
    print("   horse-race on %-17s SP500 b=%+.3f (t=%+.1f)   SOX b=%+.3f (t=%+.1f)"
          % (lab, b[1], b[1] / se[1], b[2], b[2] / se[2]))

print("\n3. WHERE DOES IT LAND?  gap (untradeable) vs intraday (tradeable)")
print("   %-8s %14s %14s %14s" % ("US", "IT rel: GAP", "IT rel: INTRA", "IT abs: GAP"))
print("   " + "-" * 56)
res = {}
for u in US:
    g, i2, ga = cc(M[u], M.rel_gap), cc(M[u], M.rel_itd), cc(M[u], M.gap_NIFTYIT)
    res[u] = {"next_it_rel": cc(M[u], M.rel), "gap": g, "intraday": i2}
    print("   %-8s %14.3f %14.3f %14.3f" % (u, g, i2, ga))

print("\n   block-shift null on the largest INTRADAY correlation:")
best = max(US, key=lambda u: abs(res[u]["intraday"]))
obs = res[best]["intraday"]; n = len(M)
null = [abs(cc(np.roll(M[best].values, int(RNG.integers(20, n - 20))), M.rel_itd.values))
        for _ in range(2000)]
p = float((np.array(null) >= abs(obs)).mean())
print("   %s intraday r=%+.3f   null p95 %.3f   p=%.3f  ->  %s"
      % (best, obs, np.percentile(null, 95), p,
         "SURVIVES" if p < 0.05 else "inside noise"))
json.dump({"n": int(len(M)), "results": res, "best_intraday": best,
           "best_intraday_r": obs, "p": p}, open("us_leads_it_result.json", "w"), indent=1)
print("\nwrote us_leads_it_result.json")
