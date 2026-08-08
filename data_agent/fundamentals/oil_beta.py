#!/usr/bin/env python3
"""
oil_beta.py — MARKET-ADJUSTED oil sensitivity of every Nifty stock, from daily price_bars.
Audit + compute in one. Emits oil_impact.json for the Oil Impact view.

The honest measure (per SECTOR_INTELLIGENCE_FRAMEWORK.md — orthogonalise vs the market):
each stock is regressed on BOTH the index and crude together
    r_stock = a + b_mkt·r_NIFTY + b_oil·r_CRUDE + e
so b_oil is oil sensitivity AFTER removing the market move (a ceasefire lifts equities AND
drops oil at once — raw oil-beta would just re-measure that risk-on co-move). Reports:
  • b_oil (contemporaneous)   — same-day co-movement = "impact"
  • t-stat / R²               — is it real, how much of the stock does oil explain
  • b_oil_next               — crude_t -> stock_{t+1} (does oil PREDICT tomorrow; likely gap-locked)
  • b_oil_1y                 — recent-year beta (oil beta is regime-dependent)
  • move_per_-5pct_oil       — market-neutral expected stock move if crude falls 5%
Index line: NIFTY ~ CRUDE (single factor) with the caveat it still blends risk-on.

USAGE
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python oil_beta.py
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
MKT = "NIFTY"
OIL = "CRUDEOIL"
NON_EQUITY = {"NIFTY", "BANKNIFTY", "NIFTYIT", "FINNIFTY", "INDIAVIX", "USDINR",
              "GOLD", "SILVER", "COPPER", "CRUDEOIL", "NATURALGAS"}
MIN_OBS = 250          # ~1 trading year minimum
WINSOR = 0.20          # clip |daily return| > 20% (split/bad-bar guard, not real for a stock day)


def load_close(con, sym):
    rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                       "AND close IS NOT NULL ORDER BY ts", (sym,)).fetchall()
    if not rows:
        return None
    d = pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize()
    s = pd.Series([float(r[1]) for r in rows], index=d).groupby(level=0).last()
    return s


def rets(s):
    r = s.pct_change()
    return r.clip(-WINSOR, WINSOR)


def ols(y, X):
    """return (beta, tstats, r2, n). X already has intercept column."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = max(n - k, 1)
    s2 = float(resid @ resid) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * s2, 0))
    t = beta / np.where(se > 0, se, np.nan)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(resid @ resid) / sst if sst > 0 else 0.0
    return beta, t, r2, n


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d'").fetchall()]
    equities = sorted(s for s in syms if s not in NON_EQUITY)

    mkt = load_close(con, MKT)
    oil = load_close(con, OIL)
    print("\n=== OIL IMPACT — market-adjusted oil beta per stock ===")
    if mkt is None or oil is None:
        con.close(); sys.exit(f"  ❌ missing {MKT if mkt is None else ''} {OIL if oil is None else ''} in price_bars")
    rmkt, roil = rets(mkt), rets(oil)
    print(f"  CRUDEOIL: {len(oil)} bars {oil.index.min().date()} → {oil.index.max().date()}")
    print(f"  NIFTY:    {len(mkt)} bars {mkt.index.min().date()} → {mkt.index.max().date()}")
    print(f"  equity symbols in price_bars: {len(equities)}")

    # index-level (single factor; blends risk-on — caveat)
    base = pd.concat({"m": rmkt, "o": roil}, axis=1).dropna()
    bi, ti, r2i, ni = ols(base["m"].values, np.column_stack([np.ones(len(base)), base["o"].values]))
    idx_beta = float(bi[1]); idx_t = float(ti[1])

    rows = []
    for sym in equities:
        s = load_close(con, sym)
        if s is None:
            continue
        df = pd.concat({"y": rets(s), "m": rmkt, "o": roil}, axis=1).dropna()
        if len(df) < MIN_OBS:
            continue
        y = df["y"].values
        X = np.column_stack([np.ones(len(df)), df["m"].values, df["o"].values])
        beta, t, r2, n = ols(y, X)
        b_oil, t_oil = float(beta[2]), float(t[2])
        # next-day: crude_t -> stock_{t+1}, market-adjusted with mkt_{t+1}
        dlag = df.copy()
        dlag["o_prev"] = dlag["o"].shift(1)
        dlag = dlag.dropna()
        Xl = np.column_stack([np.ones(len(dlag)), dlag["m"].values, dlag["o_prev"].values])
        bl, tl, _, _ = ols(dlag["y"].values, Xl)
        # recent 1y
        d1 = df.tail(252)
        b1 = float("nan")
        if len(d1) >= 120:
            b1, _, _, _ = ols(d1["y"].values, np.column_stack([np.ones(len(d1)), d1["m"].values, d1["o"].values]))
            b1 = float(b1[2])
        rows.append({
            "sym": sym, "n": int(n),
            "b_oil": round(b_oil, 3), "t_oil": round(t_oil, 2), "r2": round(r2, 3),
            "b_oil_next": round(float(bl[2]), 3), "t_next": round(float(tl[2]), 2),
            "b_oil_1y": round(b1, 3) if b1 == b1 else None,
            "b_mkt": round(float(beta[1]), 2),
            "move_per_-5pct_oil": round(-5.0 * b_oil, 2),  # % stock move, market held flat
        })

    con.close()
    rows.sort(key=lambda r: r["b_oil"])
    sig = [r for r in rows if abs(r["t_oil"]) >= 2]

    print(f"\n  {len(rows)} stocks with ≥{MIN_OBS} obs · {len(sig)} have a SIGNIFICANT oil beta (|t|≥2)")
    print(f"\n  INDEX: NIFTY ~ crude  b_oil={idx_beta:+.3f} (t={idx_t:.1f})  → a −5% crude move ≈ "
          f"{-5*idx_beta:+.2f}% Nifty (raw co-move; blends risk-on, NOT pure oil-cost)")

    def show(title, subset):
        print(f"\n  {title}")
        print(f"    {'sym':<12}{'b_oil':>7}{'t':>6}{'R²':>6}{'1y':>7}{'next':>7}{'−5%oil→':>9}")
        for r in subset:
            print(f"    {r['sym']:<12}{r['b_oil']:>7.3f}{r['t_oil']:>6.1f}{r['r2']:>6.2f}"
                  f"{(r['b_oil_1y'] if r['b_oil_1y'] is not None else float('nan')):>7.3f}"
                  f"{r['b_oil_next']:>7.3f}{r['move_per_-5pct_oil']:>8.1f}%")

    show("OIL WINNERS — most NEGATIVE beta (rise when oil falls) [top 12]", rows[:12])
    show("OIL LOSERS — most POSITIVE beta (rise when oil rises) [top 12]", rows[-12:][::-1])

    payload = {"as_of": str(oil.index.max().date()),
               "index": {"b_oil": round(idx_beta, 3), "t": round(idx_t, 2)},
               "n_stocks": len(rows), "n_significant": len(sig), "stocks": rows}
    outp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oil_impact.json")
    with open(outp, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\n  SAVED → {outp}")
    print("\n  READ: b_oil = %-move in the stock per +1% crude, AFTER removing the market move.")
    print("  Significant NEGATIVE = genuine oil-cost beneficiary; POSITIVE = oil producer/proxy.")
    print("  b_oil_next ≈ 0 (vs b_oil) ⇒ oil is priced into the open — no next-day edge (gap-locked).")


if __name__ == "__main__":
    main()
