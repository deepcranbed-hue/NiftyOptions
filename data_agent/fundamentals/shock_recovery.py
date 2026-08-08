#!/usr/bin/env python3
"""
shock_recovery.py — does a macro shock-down day BOUNCE the next day, and in which names?
Tests the "buy the macro dip, recovers within a day" hypothesis RIGOROUSLY — full history,
per-regime, with the tail risk shown (the strategy has negative skew: small frequent wins,
occasional big loss). Emits shock_recovery.json.

Method (close-to-close, no gap/intraday split — per your spec):
  shock day  = NIFTY daily return < THRESH (macro down-shock)
  recovery   = the NEXT day's return (and cumulative next-3-day)
For the INDEX: mean/median/%positive/worst of next-day return after shocks, vs the base rate,
  broken out BY YEAR (does the bounce hold across regimes or only in calm ones?).
For each STOCK: shock-day drop (how much it absorbs) + next-day bounce + %positive.
  Ranked — the reliable dip-buys. b_mkt shown (high-beta fall more; do they bounce more?).
Also: oil-shock variant (big crude move day) and a VIX-regime split if INDIAVIX is present.

USAGE
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python shock_recovery.py            # default THRESH=-1.5%
"""
from __future__ import annotations
import os, sqlite3, sys, json
try:
    import numpy as np, pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
MKT, OIL, VIX = "NIFTY", "CRUDEOIL", "INDIAVIX"
NON_EQUITY = {"NIFTY", "BANKNIFTY", "NIFTYIT", "FINNIFTY", "INDIAVIX", "USDINR",
              "GOLD", "SILVER", "COPPER", "CRUDEOIL", "NATURALGAS"}
THRESH = float(os.getenv("SHOCK_THRESH", "-1.5"))   # % Nifty day that counts as a macro shock
MIN_OBS = 250


def load_close(con, sym):
    rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                       "AND close IS NOT NULL ORDER BY ts", (sym,)).fetchall()
    if not rows:
        return None
    d = pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize()
    return pd.Series([float(r[1]) for r in rows], index=d).groupby(level=0).last()


def stats(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) == 0:
        return None
    return {"n": int(len(x)), "mean": round(float(x.mean()), 2), "median": round(float(np.median(x)), 2),
            "pos": round(float((x > 0).mean()), 2), "std": round(float(x.std()), 2),
            "worst": round(float(x.min()), 2), "best": round(float(x.max()), 2)}


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    mkt = load_close(con, MKT)
    oil = load_close(con, OIL)
    vix = load_close(con, VIX)
    if mkt is None:
        con.close(); sys.exit("no NIFTY in price_bars")
    rmkt = mkt.pct_change() * 100
    roil = oil.pct_change() * 100 if oil is not None else None

    print("\n=== SHOCK → RECOVERY  (macro down-day, then next day) ===")
    print(f"  shock day = NIFTY < {THRESH:.1f}%  ·  NIFTY history {mkt.index.min().date()} → {mkt.index.max().date()}")

    # index next-day after shocks
    df = pd.DataFrame({"r": rmkt}).dropna()
    df["r_next"] = df["r"].shift(-1)
    df["r_next3"] = df["r"].shift(-1) + df["r"].shift(-2) + df["r"].shift(-3)
    df["yr"] = df.index.year
    shock = df[df["r"] < THRESH]
    base = df["r_next"].dropna()

    print(f"\n  INDEX — {len(shock)} shock days in {df['yr'].nunique()} yrs")
    s_next = stats(shock["r_next"]); b_next = stats(base)
    print(f"    next-day AFTER shock : mean {s_next['mean']:+.2f}%  median {s_next['median']:+.2f}%  "
          f"%positive {s_next['pos']:.0%}  worst {s_next['worst']:+.1f}%  best {s_next['best']:+.1f}%")
    print(f"    next-day BASE RATE   : mean {b_next['mean']:+.2f}%  median {b_next['median']:+.2f}%  "
          f"%positive {b_next['pos']:.0%}")
    edge = s_next["mean"] - b_next["mean"]
    print(f"    → mean-reversion EDGE over base rate: {edge:+.2f}%/day  "
          f"({'bounce' if edge > 0 else 'no bounce / continuation'})")
    s3 = stats(shock["r_next3"])
    print(f"    next-3-day cumulative: mean {s3['mean']:+.2f}%  %positive {s3['pos']:.0%}")

    # BY YEAR — does the bounce survive every regime?
    print(f"\n  BY YEAR (does the 1-day bounce hold, or break in stress years?)")
    print(f"    {'yr':<6}{'shocks':>7}{'next mean':>11}{'%pos':>7}{'worst':>8}")
    per_year = {}
    for yr, g in shock.groupby("yr"):
        st = stats(g["r_next"])
        if st:
            per_year[int(yr)] = st
            print(f"    {yr:<6}{st['n']:>7}{st['mean']:>+10.2f}%{st['pos']:>7.0%}{st['worst']:>+7.1f}%")

    # oil-shock variant
    oil_block = None
    if roil is not None:
        dfo = pd.DataFrame({"o": roil, "rn": rmkt.shift(-1)}).dropna()
        up = dfo[dfo["o"] > 4]      # crude spike (bad for importer India)
        dn = dfo[dfo["o"] < -4]     # crude crash (good)
        su, sd = stats(up["rn"]), stats(dn["rn"])
        oil_block = {"crude_up>4pct_next": su, "crude_dn>4pct_next": sd}
        print(f"\n  OIL-SHOCK variant (next-day NIFTY):")
        if su: print(f"    after crude +>4% : next-day {su['mean']:+.2f}%  %pos {su['pos']:.0%}  (n={su['n']})")
        if sd: print(f"    after crude −>4% : next-day {sd['mean']:+.2f}%  %pos {sd['pos']:.0%}  (n={sd['n']})")

    # VIX regime split
    vix_block = None
    if vix is not None:
        vlev = vix.reindex(shock.index).median()
        hi = shock[vix.reindex(shock.index) >= vlev]
        lo = shock[vix.reindex(shock.index) < vlev]
        vix_block = {"vix_median": round(float(vlev), 1), "hi_vix_next": stats(hi["r_next"]), "lo_vix_next": stats(lo["r_next"])}
        print(f"\n  VIX-REGIME split of the bounce (median VIX on shock days = {vlev:.1f}):")
        if vix_block["lo_vix_next"]: print(f"    calm (low VIX) shocks : next-day {vix_block['lo_vix_next']['mean']:+.2f}%  %pos {vix_block['lo_vix_next']['pos']:.0%}")
        if vix_block["hi_vix_next"]: print(f"    stressed (high VIX)   : next-day {vix_block['hi_vix_next']['mean']:+.2f}%  %pos {vix_block['hi_vix_next']['pos']:.0%}  ← does it still bounce?")

    # per-stock: shock-day drop (absorb) + next-day bounce
    equities = sorted(s for s in {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d'").fetchall()} if s not in NON_EQUITY)
    shock_days = set(shock.index)
    rows = []
    for sym in equities:
        s = load_close(con, sym)
        if s is None:
            continue
        r = s.pct_change() * 100
        rn = r.shift(-1)
        idx = [d for d in shock_days if d in r.index and d in rn.index and not np.isnan(rn.get(d, np.nan))]
        if len(idx) < 20:
            continue
        drop = stats([r[d] for d in idx])           # how much it falls on the shock
        bounce = stats([rn[d] for d in idx])         # next-day recovery
        rows.append({"sym": sym, "n_shocks": len(idx),
                     "shock_drop_mean": drop["mean"], "next_mean": bounce["mean"],
                     "next_pos": bounce["pos"], "next_worst": bounce["worst"]})
    con.close()
    rows.sort(key=lambda x: x["next_mean"], reverse=True)

    print(f"\n  PER-STOCK next-day bounce after an index shock (top 12 bouncers):")
    print(f"    {'sym':<12}{'shocks':>7}{'drop':>8}{'bounce':>8}{'%pos':>7}{'worst':>8}")
    for x in rows[:12]:
        print(f"    {x['sym']:<12}{x['n_shocks']:>7}{x['shock_drop_mean']:>+7.2f}%{x['next_mean']:>+7.2f}%"
              f"{x['next_pos']:>7.0%}{x['next_worst']:>+7.1f}%")
    print(f"\n  WEAKEST bouncers (dip may keep dipping — do NOT buy these on a shock):")
    for x in rows[-6:]:
        print(f"    {x['sym']:<12}{x['n_shocks']:>7}{x['shock_drop_mean']:>+7.2f}%{x['next_mean']:>+7.2f}%"
              f"{x['next_pos']:>7.0%}{x['next_worst']:>+7.1f}%")

    payload = {"as_of": str(mkt.index.max().date()), "thresh": THRESH,
               "index": {"shock_next": s_next, "base_next": b_next, "edge": round(edge, 2),
                         "next3": s3, "by_year": per_year, "oil": oil_block, "vix": vix_block},
               "stocks": rows}
    outp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shock_recovery.json")
    with open(outp, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\n  SAVED → {outp}")
    print("\n  READ: 'edge' > 0 = real next-day mean-reversion beyond the base rate. Check BY YEAR —")
    print("  if the bounce vanishes / goes negative in a stress year, the strategy breaks exactly when")
    print("  it matters (negative skew). 'worst' per stock = the day buy-the-dip failed; size for THAT.")


if __name__ == "__main__":
    main()
