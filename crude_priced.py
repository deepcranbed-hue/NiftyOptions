#!/usr/bin/env python3
"""crude_priced -- two claims, both testable on data already here.

CLAIM A "The July crude rally caught the market unprepared; the premium is now priced."
  If true, Nifty's SAME-DAY sensitivity to crude should have SHRUNK since July. Rolling
  beta of Nifty returns on WTI returns measures exactly that. A premium that is 'priced'
  means new crude moves stop moving the index.

CLAIM B "No Iran deal tomorrow either, hence downward bias."
  This is the claim worth pressing, because it sits in tension with Claim A. If the
  premium is already in the price, then the ABSENCE of a deal tomorrow is not news -- it
  is the status quo the price already reflects. Markets move on revisions to expectations,
  not on the persistence of a known condition. For a further fall you need the outlook to
  deteriorate relative to what is already discounted, not merely to stay bad.
  Testable directly: condition on days that look like today -- crude UP, Nifty DOWN -- and
  count what the NEXT session did. If 'continued bad news = continued falls' were right,
  those days should be followed by more falls than the base rate.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(1108)
con = sqlite3.connect("option_chains.db")
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol "
                 "IN ('NIFTY','CRUDEOIL','NIFTYIT','NIFTYENERGY','INDIAVIX')", con)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill().dropna()
R = P.pct_change() * 100
R["f1"] = (P.NIFTY.shift(-1) / P.NIFTY - 1) * 100
D = R.dropna()

print("A. ROLLING 60-day beta of NIFTY on WTI  (same-day sensitivity)")
cov = D.NIFTY.rolling(60).cov(D.CRUDEOIL); var = D.CRUDEOIL.rolling(60).var()
beta = (cov / var).dropna()
corr = D.NIFTY.rolling(60).corr(D.CRUDEOIL).dropna()
print("   full-sample beta %+.4f   corr %+.3f" % (
    D.NIFTY.cov(D.CRUDEOIL) / D.CRUDEOIL.var(), D.NIFTY.corr(D.CRUDEOIL)))
for lab, sl in (("2018-2024", slice("2018-01-01", "2024-12-31")),
                ("2025", slice("2025-01-01", "2025-12-31")),
                ("2026 YTD", slice("2026-01-01", None)),
                ("last 60 sessions", slice(None))):
    b = beta[sl] if lab != "last 60 sessions" else beta.tail(1)
    cc = corr[sl] if lab != "last 60 sessions" else corr.tail(1)
    print("   %-18s beta %+.4f   corr %+.3f" % (lab, b.mean(), cc.mean()))
print("   >>> %s" % ("sensitivity has FALLEN -- consistent with 'premium priced'"
                     if abs(beta.tail(1).mean()) < abs(beta[:"2026-06-30"].mean())
                     else "sensitivity has NOT fallen -- 'premium priced' unsupported"))

print("\nB. DAYS THAT LOOK LIKE TODAY: crude UP, NIFTY DOWN -> what did the NEXT day do?")
m = (D.CRUDEOIL > 0) & (D.NIFTY < 0)
strong = (D.CRUDEOIL > 2) & (D.NIFTY < -0.4)          # today: WTI +~5% Mon, NIFTY -0.46%
print("   %-34s %6s %10s %10s" % ("condition", "n", "next-day", "P(down)"))
print("   " + "-" * 62)
print("   %-34s %6d %10.3f %9.0f%%" % ("ALL days (base rate)", len(D), D.f1.mean(),
                                       (D.f1 < 0).mean() * 100))
for lab, mm in (("crude up + nifty down", m), ("crude >+2% + nifty <-0.4%", strong)):
    s = D[mm]
    print("   %-34s %6d %10.3f %9.0f%%" % (lab, len(s), s.f1.mean(), (s.f1 < 0).mean() * 100))
    k = int(mm.sum()); obs = s.f1.mean()
    b = np.array([D.f1.values[RNG.integers(0, len(D), k)].mean() for _ in range(4000)])
    p = float((np.abs(b - D.f1.mean()) >= abs(obs - D.f1.mean())).mean())
    print("   %-34s %6s   bootstrap p = %.3f%s" % ("", "", p, "  <<<" if p < 0.05 else ""))

print("\n   consecutive-fall check: after ANY down day, what does the next do?")
dn = D[D.NIFTY < 0]
print("   after a down day        n=%4d  next-day %+.3f%%  P(down) %.0f%%"
      % (len(dn), dn.f1.mean(), (dn.f1 < 0).mean() * 100))
dn2 = D[(D.NIFTY < 0) & (D.NIFTY.shift(1) < 0)]
print("   after TWO down days     n=%4d  next-day %+.3f%%  P(down) %.0f%%"
      % (len(dn2), dn2.f1.mean(), (dn2.f1 < 0).mean() * 100))

print("\nC. is NIFTY IT actually the crude-insensitive leg?  (beta on WTI, same day)")
for s in ("NIFTYIT", "NIFTYENERGY", "NIFTY"):
    print("   %-12s beta %+.4f   corr %+.3f" % (s, D[s].cov(D.CRUDEOIL) / D.CRUDEOIL.var(),
                                                D[s].corr(D.CRUDEOIL)))
json.dump({"beta_full": float(D.NIFTY.cov(D.CRUDEOIL) / D.CRUDEOIL.var()),
           "beta_2026": float(beta["2026-01-01":].mean()),
           "beta_last": float(beta.tail(1).mean()),
           "crude_up_nifty_down_n": int(m.sum()),
           "crude_up_nifty_down_next": float(D[m].f1.mean()),
           "base_next": float(D.f1.mean())},
          open("crude_priced_result.json", "w"), indent=1)
print("\nwrote crude_priced_result.json")
