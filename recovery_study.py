#!/usr/bin/env python3
"""recovery_study -- the last sub-24,000 episode: how long back to 24,400, and who moved.

Two questions, deliberately separated.
  A. TIMING  -- sessions from the first close under 24,000 to the first close at or above
                24,400, for every such episode in 2026. One episode is an anecdote; the
                set gives a range.
  B. COMPOSITION -- for the most recent episode: which names fell hardest, which rose
                DURING the fall, and -- the question that actually matters -- whether the
                recovery was a ROUND TRIP (everyone back where they started) or a
                RESHUFFLE (index level restored, leadership changed).

B is where the information is. If the index returns to its old level with the same names
at their old prices, the episode was a shock that got unwound. If the index returns while
some names stay down and others end far higher, capital was reallocated and the "recovery"
is a different market wearing the same index level.

Prices are Yahoo auto-adjusted closes, so corporate actions are already handled.
"""
import sqlite3, json
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
nif = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='NIFTY' "
                  "ORDER BY ts", con)
nif["d"] = pd.to_datetime(nif.ts.str[:10])
N = nif.set_index("d")["close"]
N = N[N.index >= "2026-01-01"]

print("A. EVERY 2026 EPISODE: first close < 24,000  ->  first close >= 24,400")
print("   %-12s %-12s %-12s %-12s %7s %9s %9s"
      % ("first <24k", "low date", "low", ">=24,400 on", "sess", "drawdown", "rebound"))
print("   " + "-" * 82)
below = N < 24000
grp = (below != below.shift()).cumsum()
episodes = []
for _, idx in N.groupby(grp).groups.items():
    seg = N.loc[idx]
    if not (seg < 24000).all():
        continue
    start = seg.index[0]
    fwd = N[N.index >= start]
    lowdate = fwd[:fwd.index[min(60, len(fwd) - 1)]].idxmin()
    low = N[lowdate]
    after = N[(N.index >= lowdate) & (N >= 24400)]
    if len(after):
        rec, sess = after.index[0], int((N.index >= start).argmax())
        sess = int(((N.index >= start) & (N.index <= after.index[0])).sum()) - 1
        pre = N[N.index < start]
        peak = pre[-20:].max() if len(pre) else np.nan
        print("   %-12s %-12s %12.0f %-12s %7d %8.1f%% %8.1f%%"
              % (start.date(), lowdate.date(), low, after.index[0].date(), sess,
                 (low / peak - 1) * 100, (N[after.index[0]] / low - 1) * 100))
        episodes.append({"start": str(start.date()), "low_date": str(lowdate.date()),
                         "low": float(low), "recovered": str(after.index[0].date()),
                         "sessions": sess})
    else:
        print("   %-12s %-12s %12.0f %-12s %7s" % (start.date(), lowdate.date(), low,
                                                   "not yet", "-"))

ep = episodes[-1]
S, L, R = (pd.Timestamp(ep["start"]), pd.Timestamp(ep["low_date"]), pd.Timestamp(ep["recovered"]))
print("\n   MOST RECENT: fell below 24,000 on %s, low %s, back above 24,400 on %s"
      % (S.date(), L.date(), R.date()))
print("   -> %d trading sessions from the break to the recovery" % ep["sessions"])

# ---------- B. composition ----------
csv = pd.read_csv("nifty-50-stock-list.csv")
syms = [s.strip() for s in csv["Symbol"].dropna()]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                 "(%s)" % ",".join("?" * len(syms)), con, params=syms)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
pre = P[P.index < S].index[-1]          # last close BEFORE the break
T = pd.DataFrame({
    "fall_pct": (P.loc[L] / P.loc[pre] - 1) * 100,
    "rebound_pct": (P.loc[R] / P.loc[L] - 1) * 100,
    "roundtrip_pct": (P.loc[R] / P.loc[pre] - 1) * 100,
    "since_pct": (P.iloc[-1] / P.loc[pre] - 1) * 100,
}).dropna()
nfall = (N[L] / N[pre] - 1) * 100
nrt = (N[R] / N[pre] - 1) * 100
print("\nB. COMPOSITION   base %s -> low %s -> recovery %s   (NIFTY %+.2f%% then %+.2f%% net)"
      % (pre.date(), L.date(), R.date(), nfall, nrt))

print("\n   WORST 10 during the fall")
print("   %-14s %9s %9s %11s" % ("", "fall", "rebound", "round-trip"))
for s, r in T.nsmallest(10, "fall_pct").iterrows():
    print("   %-14s %8.2f%% %8.2f%% %10.2f%%" % (s, r.fall_pct, r.rebound_pct, r.roundtrip_pct))
print("\n   ROSE during the fall (index was down %.2f%%)" % nfall)
up = T[T.fall_pct > 0].sort_values("fall_pct", ascending=False)
for s, r in up.iterrows():
    print("   %-14s %8.2f%% %8.2f%% %10.2f%%" % (s, r.fall_pct, r.rebound_pct, r.roundtrip_pct))

print("\n   ROUND TRIP? at the recovery date, vs the pre-fall close")
back = (T.roundtrip_pct > 0).sum()
print("   names ABOVE their pre-fall price: %d of %d (%.0f%%)   median %+.2f%%"
      % (back, len(T), 100 * back / len(T), T.roundtrip_pct.median()))
print("   dispersion of round-trip returns: std %.2f%%   range %+.1f%% .. %+.1f%%"
      % (T.roundtrip_pct.std(), T.roundtrip_pct.min(), T.roundtrip_pct.max()))
print("\n   BIGGEST WINNERS over the whole round trip")
for s, r in T.nlargest(8, "roundtrip_pct").iterrows():
    print("   %-14s fall %+7.2f%%  rebound %+7.2f%%  net %+7.2f%%" % (s, r.fall_pct, r.rebound_pct, r.roundtrip_pct))
print("\n   STILL DOWN at the recovery date")
for s, r in T.nsmallest(8, "roundtrip_pct").iterrows():
    print("   %-14s fall %+7.2f%%  rebound %+7.2f%%  net %+7.2f%%" % (s, r.fall_pct, r.rebound_pct, r.roundtrip_pct))
rho = float(np.corrcoef(T.fall_pct, T.rebound_pct)[0, 1])
print("\n   corr(fall, rebound) = %+.3f" % rho)
print("   %s" % ("STRONGLY NEGATIVE -> the names that fell hardest bounced hardest: a "
                 "V-shaped unwind of one shock" if rho < -0.5 else
                 "WEAK -> the rebound did NOT simply reverse the fall; leadership changed"))
T.round(2).to_json("recovery_study_result.json", orient="index", indent=1)
print("\nwrote recovery_study_result.json")
