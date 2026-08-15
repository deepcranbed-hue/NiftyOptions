#!/usr/bin/env python3
"""wings_test -- naked strangle vs iron condor: what does the insurance actually buy?

THE SAMPLE PROBLEM, AND THE FIX. The captured chain covers six cycles. ES(5%) on six
observations is the minimum of six numbers -- it cannot describe a tail, and a hedging
study that cannot describe a tail is answering nothing. So the design is hybrid:

  PRICES  come from a Black-Scholes model driven by the observed India VIX, VALIDATED
          against the six cycles of real LTPs. If the model reproduces traded prices, it
          can be trusted to price wings on days the chain does not cover.
  OUTCOMES come from the actual 2018-2026 return distribution, ~2,100 six-session paths,
          which is enough to estimate a 5% and a 1% tail.

That gives real premium economics against a real tail, instead of six anecdotes.

METRICS, as specified: expected P&L, ES(5%), ES(1%), worst case, win rate, THETA RETAINED
(hedged mean / naked mean) and ES REDUCTION -- because "the hedge costs P&L" is not the
question; the exchange rate between the two is.
"""
import sqlite3, math, json
import numpy as np, pandas as pd

H = 6                       # sessions held
SHORT_PCT = 0.62            # short strikes at +/-0.62% of spot == +/-150 on 24,300
WINGS = [0.0, 0.41, 0.82, 1.23, 1.65]     # extra distance, % of spot (~100/200/300/400 pts)

def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs(S, K, T, sig, call=True):
    if T <= 0 or sig <= 0: return max(S - K, 0) if call else max(K - S, 0)
    d1 = (math.log(S / K) + 0.5 * sig * sig * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    return (S * _cdf(d1) - K * _cdf(d2)) if call else (K * _cdf(-d2) - S * _cdf(-d1))

con = sqlite3.connect("option_chains.db")
# ---- validate the pricer against the six real cycles ----
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
vx = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
vx["d"] = pd.to_datetime(vx.ts.str[:10]); VX = vx.set_index("d")["close"].sort_index()
print("=== 0. PRICER VALIDATION against real LTPs ===")
print("   %-12s %7s %8s %9s %9s %7s" % ("expiry", "spot", "VIX", "real prem", "model", "err"))
errs = []
for exp, g in ch.groupby("exp"):
    f = g[g.dt == g.dt.min()]
    S = float(f.spot.iloc[0])
    day = pd.Timestamp(f.dt.iloc[0].date())
    v = VX.asof(day)
    if not (v == v): continue
    dte = max((exp - f.dt.iloc[0].date()).days, 1)
    T = dte / 365.0
    ks = np.asarray(sorted(f.strike.unique()), dtype=float)
    ck = float(ks[np.argmin(np.abs(ks - S * (1 + SHORT_PCT / 100)))])
    pk = float(ks[np.argmin(np.abs(ks - S * (1 - SHORT_PCT / 100)))])
    rc = f[f.strike == ck].call_ltp, f[f.strike == pk].put_ltp
    if rc[0].empty or rc[1].empty: continue
    real = float(rc[0].iloc[0]) + float(rc[1].iloc[0])
    mod = bs(S, ck, T, v / 100, True) + bs(S, pk, T, v / 100, False)
    if real <= 0: continue
    errs.append(mod / real - 1)
    print("   %-12s %7.0f %8.1f %9.1f %9.1f %6.0f%%"
          % (exp, S, v, real, mod, (mod / real - 1) * 100))
print("   median model/real error: %+.0f%%   %s"
      % (np.median(errs) * 100,
         "pricer is usable" if abs(np.median(errs)) < 0.35 else
         "pricer MISPRICES -- results below inherit that bias"))

# ---- 8-year simulation ----
o = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                "('NIFTY','INDIAVIX')", con)
o["d"] = pd.to_datetime(o.ts.str[:10])
O = o.pivot_table(index="d", columns="symbol", values="close").sort_index().dropna()
O["fwd"] = O.NIFTY.shift(-H) / O.NIFTY - 1
O["r"] = O.NIFTY.pct_change() * 100
D = O.dropna()
T = H / 252.0

def sim(extra_pct, m=None):
    d = D if m is None else D[m]
    S = 100.0
    out = []
    for v, f in zip(d.INDIAVIX.values, d.fwd.values):
        sig = v / 100.0
        ck, pk = S * (1 + SHORT_PCT / 100), S * (1 - SHORT_PCT / 100)
        credit = bs(S, ck, T, sig, True) + bs(S, pk, T, sig, False)
        cost = 0.0
        if extra_pct > 0:
            cw, pw = S * (1 + (SHORT_PCT + extra_pct) / 100), S * (1 - (SHORT_PCT + extra_pct) / 100)
            cost = bs(S, cw, T, sig, True) + bs(S, pw, T, sig, False)
        ST = S * (1 + f)
        pay = max(ST - ck, 0) + max(pk - ST, 0)
        if extra_pct > 0:
            pay -= max(ST - cw, 0) + max(pw - ST, 0)
        out.append(credit - cost - pay)
    return np.array(out)

def stats(a):
    n5 = max(int(len(a) * 0.05), 1); n1 = max(int(len(a) * 0.01), 1)
    return {"mean": a.mean(), "es5": np.sort(a)[:n5].mean(), "es1": np.sort(a)[:n1].mean(),
            "worst": a.min(), "win": (a > 0).mean()}

print("\n=== 1. NAKED vs WINGS  (%d paths, all regimes; values in %% of spot) ===" % len(D))
print("%-16s %9s %9s %9s %9s %7s %9s %9s %8s"
      % ("structure", "mean P/L", "ES(5%)", "ES(1%)", "worst", "win", "theta ret", "ES cut", "P/L / ES"))
print("-" * 96)
base = None
for w in WINGS:
    a = sim(w); s = stats(a)
    if base is None: base = s
    lab = "naked" if w == 0 else "wings +%.2f%%" % w
    print("%-16s %+9.4f %+9.4f %+9.4f %+9.4f %6.0f%% %8.0f%% %8.0f%% %8.2f"
          % (lab, s["mean"], s["es5"], s["es1"], s["worst"], s["win"] * 100,
             s["mean"] / base["mean"] * 100,
             (1 - s["es5"] / base["es5"]) * 100, s["mean"] / abs(s["es5"])))

print("\n=== 2. BY REGIME (theta retained / ES cut / efficiency) ===")
prev = D.r.abs()
REG = [("VIX<=13 quiet", (D.INDIAVIX <= 13) & (prev < 0.5)),
       ("VIX 13-17 quiet", (D.INDIAVIX > 13) & (D.INDIAVIX <= 17) & (prev < 0.5)),
       ("VIX 13-17 moved", (D.INDIAVIX > 13) & (D.INDIAVIX <= 17) & (prev > 1)),
       ("VIX>17", D.INDIAVIX > 17)]
for lab, m in REG:
    if m.sum() < 60: continue
    b = stats(sim(0.0, m))
    print("   %-18s n=%-5d naked: mean %+.3f  ES5 %+.3f  eff %.2f"
          % (lab, int(m.sum()), b["mean"], b["es5"], b["mean"] / abs(b["es5"])))
    for w in WINGS[1:]:
        s = stats(sim(w, m))
        print("      %-14s mean %+.3f (%3.0f%% kept)  ES5 %+.3f (%3.0f%% cut)  eff %.2f  HE %.1f"
              % ("wings +%.2f%%" % w, s["mean"], s["mean"] / b["mean"] * 100, s["es5"],
                 (1 - s["es5"] / b["es5"]) * 100, s["mean"] / abs(s["es5"]),
                 (b["es5"] - s["es5"]) / max(b["mean"] - s["mean"], 1e-9)))
print("\n   HE = hedge efficiency = ES points removed per point of premium sacrificed")
