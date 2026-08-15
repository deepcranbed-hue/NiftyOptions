#!/usr/bin/env python3
"""gamma_break -- do OI-wall breaks persist or mean-revert, and does confirmation matter?

THE HYPOTHESIS. A strike with heavy call OI can act as a positioning barrier rather than
an economic level. When spot breaks it, either (a) fundamentals changed and the options
market is reacting, or (b) dealer hedging mechanically pushed it through. (b) should be
more likely to mean-revert. Testable: label breaks, split by whether the rest of the
market confirmed, measure what happens next.

WHAT THE DATA SUPPORTS, AND WHAT IT DOES NOT -- stated before any result.
  * chain_rows covers 31 TRADING DAYS (2026-06-29 .. 2026-08-10), at genuine 1-minute
    cadence (376 captures/day, 09:15-15:30 IST), 21 strikes per capture. The GRANULARITY
    is excellent; the HISTORY is one and a half months.
  * So this is an intraday study by necessity. Day-level break events would number in the
    low tens -- not a study, an anecdote. Minute-level crossings give more events, and
    that is the only version worth running.
  * Even so, splitting the events by confirmation halves an already small sample. Expect
    to be able to see only large effects. The null below says how large.

NO LOOK-AHEAD. The walls for day D are computed from the LAST capture of day D-1, so the
levels are known before D's session opens. Using D's own chain would let the day's own
OI build leak into the level that supposedly predicted the day.

CONFIRMATION is measured at the break minute from data that exists at that minute:
breadth (share of the 50 constituents up on the day) and Bank Nifty's relative move.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(24500)
con = sqlite3.connect("option_chains.db")

cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
cap["day"] = cap.dt.dt.date
days = sorted(cap.day.unique())
print("chain days: %d   %s .. %s   %.0f captures/day"
      % (len(days), days[0], days[-1], cap.groupby("day").size().mean()))

# --- walls from the PREVIOUS day's final capture ---
last_cap = cap.groupby("day").tail(1).set_index("day")
walls = {}
for i in range(1, len(days)):
    prev, today = days[i - 1], days[i]
    cid = int(last_cap.loc[prev, "capture_id"])
    ch = pd.read_sql("SELECT expiry,strike,call_oi,put_oi FROM chain_rows WHERE capture_id=?",
                     con, params=(cid,))
    if ch.empty: continue
    exp = sorted(ch.expiry.unique())[0]                 # nearest expiry
    ch = ch[ch.expiry == exp].dropna(subset=["strike"])
    if len(ch) < 8: continue
    g = ch.groupby("strike")[["call_oi", "put_oi"]].sum()
    ks = g.index.to_numpy(dtype=float)
    pain = [(float((np.clip(K - ks, 0, None) * g.call_oi.to_numpy()).sum()
                   + (np.clip(ks - K, 0, None) * g.put_oi.to_numpy()).sum()), float(K))
            for K in ks]
    walls[today] = {"call_wall": float(g.call_oi.idxmax()),
                    "put_wall": float(g.put_oi.idxmax()),
                    "max_pain": float(min(pain)[1]),
                    "prev_close": float(last_cap.loc[prev, "spot"])}
print("days with a usable prior-day wall: %d" % len(walls))

# --- breadth at 1m, for confirmation ---
pb = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1m' AND symbol "
                 "NOT LIKE 'NIFTY%' AND symbol NOT LIKE '%_20%'", con)
pb["dt"] = pd.to_datetime(pb.ts.str.replace("Z", "", regex=False))
pb["day"] = pb.dt.dt.date
bank = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1m' AND symbol='BANKNIFTY'", con)
bank["dt"] = pd.to_datetime(bank.ts.str.replace("Z", "", regex=False))
bank = bank.set_index("dt")["close"]

events = []
for day, w in walls.items():
    s = cap[cap.day == day].set_index("dt")["spot"].sort_index()
    if len(s) < 100: continue
    day_px = pb[pb.day == day]
    wide = day_px.pivot_table(index="dt", columns="symbol", values="close").sort_index().ffill()
    if wide.shape[1] < 30: wide = None
    bk = bank[bank.index.date == day]
    for name, lvl, updown in (("call_wall", w["call_wall"], +1), ("put_wall", w["put_wall"], -1)):
        rel = np.sign(s.values - lvl)
        for k in range(1, len(s)):
            if rel[k] == rel[k - 1] or rel[k] == 0: continue
            if rel[k] != updown: continue                  # only breaks in the barrier's direction
            t = s.index[k]
            fwd = {}
            for h in (15, 30, 60):
                nxt = s[s.index <= t + pd.Timedelta(minutes=h)]
                fwd["f%d" % h] = (nxt.iloc[-1] / s.iloc[k] - 1) * 100 * updown
            fwd["fclose"] = (s.iloc[-1] / s.iloc[k] - 1) * 100 * updown
            br = np.nan
            if wide is not None:
                sub = wide[wide.index <= t]
                if len(sub) > 2:
                    # BUG FIXED: sub.iloc[0] is NaN for any symbol whose first bar comes
                    # later than the session's first timestamp -- ffill does not backfill.
                    # Those ratios went NaN, NaN>0 is False, and breadth collapsed to a
                    # 0-1.7% range instead of 0-100%. Base off each column's FIRST VALID
                    # value. (Third instance of this same base/alignment class of bug in
                    # this session -- worth a shared helper.)
                    base = sub.bfill().iloc[0]
                    chg = (sub.iloc[-1] / base - 1).dropna()
                    br = float((chg > 0).mean() * 100) if len(chg) >= 25 else np.nan
            bkr = np.nan
            if len(bk) > 2:
                bs = bk[bk.index <= t]
                if len(bs) > 1:
                    bkr = float((bs.iloc[-1] / bs.iloc[0] - 1) * 100 * updown)
            events.append({"day": str(day), "level": name, "t": t, "dir": updown,
                           "breadth": br, "bank_rel": bkr, **fwd})
E = pd.DataFrame(events)
print("\nbreak events detected: %d  (call-wall up-breaks %d, put-wall down-breaks %d)"
      % (len(E), (E.level == "call_wall").sum(), (E.level == "put_wall").sum()))
if len(E) < 15:
    print("TOO FEW to test. Stopping rather than reporting a number from single digits.")
    raise SystemExit(0)

print("\nmove AFTER the break, signed so + = the break CONTINUED:")
print("  %-22s %5s %9s %9s %9s %9s" % ("group", "n", "+15m", "+30m", "+60m", "to close"))
print("  " + "-" * 66)
def row(lab, d):
    print("  %-22s %5d %8.3f%% %8.3f%% %8.3f%% %8.3f%%"
          % (lab, len(d), d.f15.mean(), d.f30.mean(), d.f60.mean(), d.fclose.mean()))
row("ALL breaks", E)
# BUG FIXED: confirmation must be SIGNED BY THE BREAK DIRECTION. The original test
# demanded majority-up breadth for DOWNSIDE breaks too, which is unsatisfiable -- it
# put all 106 events in "unconfirmed" and silently emptied the comparison. For a
# down-break, confirmation means breadth BELOW 50 and Bank Nifty falling.
E["breadth_signed"] = (E.breadth - 50.0) * E.dir      # >0 = breadth agrees with the break
ok = (E.breadth_signed > 0) & (E.bank_rel > 0)        # bank_rel is already dir-signed
conf, unconf = E[ok], E[~ok]
row("CONFIRMED (breadth+bank)", conf)
row("UNCONFIRMED", unconf)

print("\n  null: random minutes on the SAME days (controls for intraday drift/vol)")
# Precompute each day's spot path once -- re-slicing `cap` inside the draw loop made this
# O(draws x events x captures) and it did not finish.
paths = {d: cap[cap.day == d].sort_values("dt")["spot"].to_numpy(dtype=float)
         for d in walls}
paths = {d: a for d, a in paths.items() if len(a) >= 80}
keys = list(paths)
n = len(E)
null = []
for _ in range(1000):
    tot = np.empty(n)
    for i in range(n):
        a = paths[keys[int(RNG.integers(len(keys)))]]
        k = int(RNG.integers(5, len(a) - 65))
        tot[i] = (a[k + 60] / a[k] - 1) * 100 * (1 if RNG.random() < 0.5 else -1)
    null.append(tot.mean())
null = np.array(null)
obs = E.f60.mean()
p = float((np.abs(null) >= abs(obs)).mean())
print("  observed +60m continuation %.3f%%   null 5th/95th %.3f%% / %.3f%%   p=%.3f  -> %s"
      % (obs, np.percentile(null, 5), np.percentile(null, 95), p,
         "SURVIVES" if p < 0.05 else "inside noise"))
if len(conf) >= 8 and len(unconf) >= 8:
    diff = conf.f60.mean() - unconf.f60.mean()
    nd = []
    lab = np.array([1] * len(conf) + [0] * len(unconf))
    vals = np.concatenate([conf.f60.values, unconf.f60.values])
    for _ in range(3000):
        pm = RNG.permutation(lab)
        nd.append(vals[pm == 1].mean() - vals[pm == 0].mean())
    pd_ = float((np.abs(np.array(nd)) >= abs(diff)).mean())
    print("  confirmed minus unconfirmed at +60m: %+.3f%%  (n=%d vs %d)  permutation p=%.3f"
          % (diff, len(conf), len(unconf), pd_))
else:
    print("  confirmation split: too few in one arm to compare (n=%d / %d)" % (len(conf), len(unconf)))
E.drop(columns=["t"]).to_json("gamma_break_events.json", orient="records", indent=1)
print("\nwrote gamma_break_events.json")
