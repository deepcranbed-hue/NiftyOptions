#!/usr/bin/env python3
"""wings_real -- replace the modelled wing prices with observed ones, and measure the skew.

The hedging study priced wings with Black-Scholes at the VIX. BS assumes a single
volatility across strikes, and Indian index options do not trade that way: puts carry a
premium to equidistant calls. Every hedged result therefore flattered the put wing. The
chain carries call_iv and put_iv per strike per minute, so the correction is measurable
rather than assumed.

  1. SKEW      put IV minus call IV at equidistant OTM strikes, by distance from spot
  2. COST      real wing cost / real strangle credit, against the same ratio under BS.
               This ratio is what drives the hedging economics and it is scale-free.
  3. IMPACT    apply the measured correction to the theta-retained figures.
"""
import sqlite3, math, json
import numpy as np, pandas as pd

SHORT = 0.62
WINGS = [0.41, 0.82, 1.23, 1.65]
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp,call_iv,put_iv "
                 "FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
ch["dte"] = (pd.to_datetime(ch.exp) - ch.dt).dt.days
ch = ch[(ch.dte >= 1) & (ch.dte <= 12)]
print("captures usable: %d   strikes: %d rows" % (ch.capture_id.nunique(), len(ch)))

# ---- 1. SKEW: put IV vs call IV at equidistant strikes ----
ch["mny"] = (ch.strike / ch.spot - 1) * 100
print("\n=== 1. OBSERVED SKEW: put IV minus call IV at equal distance from spot ===")
print("   %-14s %8s %10s %10s %9s" % ("distance", "n", "put IV", "call IV", "skew"))
print("   " + "-" * 56)
sk = {}
for lo, hi in ((0.4, 0.85), (0.85, 1.3), (1.3, 1.75), (1.75, 2.3)):
    p = ch[(ch.mny < -lo) & (ch.mny >= -hi) & (ch.put_iv > 1) & (ch.put_iv < 200)]
    c = ch[(ch.mny > lo) & (ch.mny <= hi) & (ch.call_iv > 1) & (ch.call_iv < 200)]
    if len(p) < 200 or len(c) < 200: continue
    piv, civ = p.put_iv.median(), c.call_iv.median()
    sk["%.2f-%.2f" % (lo, hi)] = {"put_iv": float(piv), "call_iv": float(civ),
                                  "skew": float(piv - civ)}
    print("   %-14s %8d %9.2f%% %9.2f%% %+8.2f%%"
          % ("%.2f-%.2f%% OTM" % (lo, hi), min(len(p), len(c)), piv, civ, piv - civ))

# ---- 2. real wing cost / credit, vs BS ----
def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
def bs(S, K, T, s, call):
    if T <= 0 or s <= 0: return max(S - K, 0) if call else max(K - S, 0)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return (S * _cdf(d1) - K * _cdf(d2)) if call else (K * _cdf(-d2) - S * _cdf(-d1))

vx = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
vx["d"] = pd.to_datetime(vx.ts.str[:10]); VX = vx.set_index("d")["close"].sort_index()

def leg(sub, S, pct, side):
    """nearest strike to S*(1+/-pct), returns its LTP."""
    tgt = S * (1 + pct / 100) if side == "C" else S * (1 - pct / 100)
    ks = np.asarray(sorted(sub.strike.unique()), dtype=float)
    k = float(ks[np.argmin(np.abs(ks - tgt))])
    r = sub[sub.strike == k]
    col = "call_ltp" if side == "C" else "put_ltp"
    if r.empty: return None, None
    v = float(r[col].iloc[0])
    return (v, k) if v > 0.05 else (None, k)

rows = []
for cid, sub in ch.groupby("capture_id"):
    S = float(sub.spot.iloc[0]); dte = int(sub.dte.iloc[0])
    day = pd.Timestamp(sub.dt.iloc[0].date()); v = VX.asof(day)
    if not (v == v): continue
    cS, kc = leg(sub, S, SHORT, "C"); pS, kp = leg(sub, S, SHORT, "P")
    if not (cS and pS): continue
    T = dte / 365.0; sig = v / 100.0
    credit_r, credit_m = cS + pS, bs(S, kc, T, sig, True) + bs(S, kp, T, sig, False)
    for w in WINGS:
        cw, kcw = leg(sub, S, SHORT + w, "C"); pw, kpw = leg(sub, S, SHORT + w, "P")
        if not (cw and pw): continue
        cost_r = cw + pw
        cost_m = bs(S, kcw, T, sig, True) + bs(S, kpw, T, sig, False)
        if credit_r <= 0 or credit_m <= 0: continue
        rows.append({"w": w, "dte": dte, "real_ratio": cost_r / credit_r,
                     "model_ratio": cost_m / credit_m,
                     "put_share_real": pw / cost_r, "put_share_model": bs(S, kpw, T, sig, False) / cost_m})
R = pd.DataFrame(rows)
print("\n=== 2. WING COST AS A SHARE OF THE STRANGLE CREDIT: observed vs modelled ===")
print("   %-14s %8s %11s %11s %9s %12s" % ("wing width", "n", "REAL", "BS model", "error", "put share"))
print("   " + "-" * 70)
corr = {}
for w in WINGS:
    s = R[R.w == w]
    if len(s) < 100: continue
    rr, mr = s.real_ratio.median(), s.model_ratio.median()
    corr[w] = rr / mr
    print("   %-14s %8d %10.3f %10.3f %+8.0f%% %11.0f%%"
          % ("+%.2f%%" % w, len(s), rr, mr, (rr / mr - 1) * 100, s.put_share_real.median() * 100))
print("\n   (put share = fraction of the wing cost paid for the PUT wing; BS with a flat")
print("    vol would put it near 50%%. Above that is the skew you are paying.)")

print("\n=== 3. IMPACT on the hedging conclusion ===")
print("   BS-based theta-retained figures from the previous run, rescaled by the measured")
print("   cost error. naked mean and ES are unchanged; only the wing cost moves.")
BASE = {"VIX<=13 quiet": (0.059, 2.567), "VIX 13-17 quiet": (0.185, 3.232),
        "VIX>17": (0.287, 6.340)}
MODEL_KEEP = {"VIX<=13 quiet": {0.82: 0.01, 1.65: 0.10},
              "VIX 13-17 quiet": {0.82: 0.12, 1.65: 0.47},
              "VIX>17": {0.82: 0.12, 1.65: 0.27}}
for reg, (mean, es) in BASE.items():
    out = []
    for w in (0.82, 1.65):
        if w not in corr: continue
        kept_m = MODEL_KEEP[reg][w]
        cost_m = mean * (1 - kept_m)                 # premium given up under BS
        cost_r = cost_m * corr[w]                    # scaled to observed cost
        kept_r = (mean - cost_r) / mean
        out.append("+%.2f%%: %.0f%% -> %.0f%%" % (w, kept_m * 100, kept_r * 100))
    print("   %-18s theta retained  %s" % (reg, "   |   ".join(out)))
json.dump({"skew": sk, "cost_correction": {str(k): float(v) for k, v in corr.items()}},
          open("wings_real_result.json", "w"), indent=1)
print("\nwrote wings_real_result.json")
