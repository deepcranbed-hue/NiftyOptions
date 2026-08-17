"""Second-source cross-check of the reconstructed index P/E.

Independent sources, 14-Aug-2026, all NSE-derived consolidated TTM:
  nifty-pe-ratio.com  20.56      indexpe.in  20.56      screener.in  20.6
Ours: 20.39 at spot 24,366  ->  agrees within 0.8%.

But indexpe.in also publishes a 5-YEAR MEDIAN of 22.06, and our reconstruction's
trailing-5y median is 23.89 — 8.3% richer. Today agrees, history does not.
That asymmetry has a mechanical explanation worth testing rather than asserting.
"""
import json, os, statistics, datetime
os.chdir(os.path.expanduser("~/mnt/NiftyOptions"))
import sys; sys.path.insert(0, "backend")

ser = json.load(open("nifty_outlook.json"))["pe"]["series"]
TODAY = 20.39
NSE_TODAY, NSE_MED_5Y = 20.56, 22.06

def med(rows): return statistics.median([r["pe"] for r in rows])
def pctile(v, x): return 100 * sum(1 for y in v if y < x) / len(v)

five = [r for r in ser if r["d"] >= "2021-08-14"]
ours_med = med(five)
bias = ours_med / NSE_MED_5Y
print(f"trailing-5y median   ours {ours_med:.2f}   NSE-derived {NSE_MED_5Y:.2f}   ratio {bias:.3f}")
print(f"today               ours {TODAY:.2f}   NSE-derived {NSE_TODAY:.2f}   ratio {TODAY/NSE_TODAY:.3f}")
print()

# ---- Hypothesis: the annual EPS step is the bias. -------------------------
# Our EPS steps ONCE a year, 92 days after 31-March. NSE's consolidated EPS
# updates every quarter as TTM rolls. In a growing market a stale-low EPS
# prints a P/E that is too HIGH, and it is too high for most of the year.
# Expected bias = mean over the year of (TTM EPS / last-annual EPS).
g = 0.129   # median FY19-FY26 annual growth on the 47-name panel, ex-COVID-adjusted
# EPS is stale by 0..1 year uniformly -> mean overstatement of P/E:
import math
exp_bias = (g / math.log1p(g))  # mean of (1+g)^u for u in [0,1] = g/ln(1+g)
print(f"expected bias from annual-vs-quarterly stepping at g={g:.1%}: {exp_bias:.3f}")
print(f"observed bias: {bias:.3f}")
print()

# ---- What the percentile becomes once history is put on NSE's footing ----
for lab, rows in [("full 2018-2026", ser), ("trailing 5y", five)]:
    raw = [r["pe"] for r in rows]
    adj = [p / bias for p in raw]           # deflate history by the measured bias
    print(f"{lab:16s}  percentile of {TODAY}:  as-published {pctile(raw,TODAY):5.1f}   "
          f"bias-adjusted {pctile(adj,TODAY):5.1f}   adj-median {statistics.median(adj):.2f}")
