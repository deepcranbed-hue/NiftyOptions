#!/usr/bin/env python3
"""
shock_recovery_v2.py — the TRADEABLE version of the dip-buy: filter by VIX regime AND by each
stock's own health, so we separate "healthy oversold" (bounces) from "broken, keeps falling".

v1 found: after a −1.5% Nifty day the next day is +0.31% over base — but the edge lives in
HIGH-VIX shocks, and stressed names (IndusInd/Bandhan) DON'T bounce. v2 turns that into a rule:

  SETUP = macro shock (NIFTY < THRESH) + VIX ≥ HIVIX + stock is HEALTHY at the close
  HEALTH (two lenses):
    • trend (universal, all names): close > its own 200-day MA  (in an uptrend, not broken)
    • ROE (the 22 bank/IT names we have fundamentals for) — confirmation that quality bounces
  Measures the next-day close→close bounce in each cell of {VIX} × {health}, pooled and per-stock,
  with the tail (worst) shown. Emits shock_recovery_v2.json for the Shock-Recovery view.

USAGE
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python shock_recovery_v2.py
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
FUND_DIR = os.path.dirname(os.path.abspath(__file__))
MKT, VIX = "NIFTY", "INDIAVIX"
NON_EQUITY = {"NIFTY", "BANKNIFTY", "NIFTYIT", "FINNIFTY", "INDIAVIX", "USDINR",
              "GOLD", "SILVER", "COPPER", "CRUDEOIL", "NATURALGAS"}
THRESH = float(os.getenv("SHOCK_THRESH", "-1.5"))
HIVIX = float(os.getenv("HIVIX", "20"))     # VIX at/above this = elevated fear
MA = 200                                     # own-trend health window
MIN_CELL = 8                                 # min obs to report a stock's setup


def load_close(con, sym):
    rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                       "AND close IS NOT NULL ORDER BY ts", (sym,)).fetchall()
    if not rows:
        return None
    d = pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize()
    return pd.Series([float(r[1]) for r in rows], index=d).groupby(level=0).last()


def load_roe():
    import psycopg2
    roe = {}
    try:
        conn = psycopg2.connect("postgresql://localhost/niftyoptions")
        c = conn.cursor()
        c.execute("""
            WITH latest_yearly AS (
                SELECT isin, statement, line_item, value,
                       ROW_NUMBER() OVER(PARTITION BY isin, line_item ORDER BY period_end DESC) as rn
                FROM fundamentals.financials
                WHERE time_period = 'yearly' 
                  AND line_item IN ('net_profit', 'Profit After Tax', 'equity_capital', 'reserves')
            ),
            latest_vals AS (
                SELECT isin, line_item, value
                FROM latest_yearly
                WHERE rn = 1
            ),
            pvt AS (
                SELECT isin,
                       MAX(CASE WHEN line_item IN ('net_profit', 'Profit After Tax') THEN value END) as pat,
                       MAX(CASE WHEN line_item = 'equity_capital' THEN value END) as eq,
                       MAX(CASE WHEN line_item = 'reserves' THEN value END) as res
                FROM latest_vals
                GROUP BY isin
            )
            SELECT c.symbol, p.pat, p.eq, p.res
            FROM pvt p
            JOIN fundamentals.companies c ON c.isin = p.isin;
        """)
        for sym, pat, eq, res in c.fetchall():
            try:
                if pat is not None and eq is not None:
                    res_val = res if res is not None else 0
                    net_worth = float(eq) + float(res_val)
                    if net_worth > 0:
                        roe[sym] = round((float(pat) / net_worth) * 100, 1)
            except Exception:
                pass
        conn.close()
    except Exception as e:
        print(f"Failed to load ROE from Postgres: {e}")
    return roe


def agg(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) == 0:
        return None
    return {"n": int(len(x)), "mean": round(float(x.mean()), 2), "pos": round(float((x > 0).mean()), 2),
            "worst": round(float(x.min()), 1)}


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    mkt = load_close(con, MKT); vix = load_close(con, VIX)
    if mkt is None or vix is None:
        con.close(); sys.exit("need NIFTY and INDIAVIX in price_bars")
    rmkt = mkt.pct_change() * 100
    shock_days = set(rmkt[rmkt < THRESH].index)
    vix_al = vix.reindex(sorted(shock_days)).to_dict()
    roe = load_roe()

    print("\n=== SHOCK-RECOVERY v2 — VIX + own-health filtered dip-buy ===")
    print(f"  shock = NIFTY < {THRESH}%  ·  elevated VIX ≥ {HIVIX}  ·  health = above own {MA}DMA")
    print(f"  {len(shock_days)} shock days  ·  ROE loaded for {len(roe)} names")

    equities = sorted(s for s in {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d'").fetchall()} if s not in NON_EQUITY)

    pool = {("hi", "healthy"): [], ("hi", "broken"): [], ("lo", "healthy"): [], ("lo", "broken"): []}
    roe_rows = []       # (roe, bounce) for the fundamental overlay
    stocks = []
    for sym in equities:
        s = load_close(con, sym)
        if s is None:
            continue
        r = s.pct_change() * 100
        rn = r.shift(-1)
        ma = s.rolling(MA).mean()
        setupA = []      # healthy + hi-VIX next-day bounces (the tradeable setup)
        for d in shock_days:
            if d not in r.index or d not in rn.index or np.isnan(rn.get(d, np.nan)):
                continue
            v = vix_al.get(d)
            m = ma.get(d, np.nan)
            if np.isnan(m) or v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            vk = "hi" if v >= HIVIX else "lo"
            hk = "healthy" if s[d] > m else "broken"
            b = float(rn[d])
            pool[(vk, hk)].append(b)
            if vk == "hi" and hk == "healthy":
                setupA.append(b)
        a = agg(setupA)
        if a and a["n"] >= MIN_CELL:
            row = {"sym": sym, "setupA_n": a["n"], "setupA_mean": a["mean"],
                   "setupA_pos": a["pos"], "setupA_worst": a["worst"], "roe": roe.get(sym)}
            stocks.append(row)
            if sym in roe:
                roe_rows.append((roe[sym], a["mean"]))
    con.close()

    # pooled 2x2
    print("\n  POOLED next-day bounce by {VIX regime} × {stock health}:")
    print(f"    {'':<10}{'HEALTHY (>200DMA)':>22}{'BROKEN (<200DMA)':>22}")
    for vk, vlabel in (("hi", f"VIX≥{HIVIX:g}"), ("lo", f"VIX<{HIVIX:g}")):
        h, b = agg(pool[(vk, "healthy")]), agg(pool[(vk, "broken")])
        hs = f"{h['mean']:+.2f}% ({h['pos']:.0%},n{h['n']})" if h else "—"
        bs = f"{b['mean']:+.2f}% ({b['pos']:.0%},n{b['n']})" if b else "—"
        print(f"    {vlabel:<10}{hs:>22}{bs:>22}")
    A = agg(pool[("hi", "healthy")]); B = agg(pool[("hi", "broken")])
    if A and B:
        print(f"\n  ► THE SETUP (healthy + VIX≥{HIVIX:g}): {A['mean']:+.2f}%/day, {A['pos']:.0%} positive, worst {A['worst']:.1f}%")
        print(f"    vs broken + high-VIX:              {B['mean']:+.2f}%/day, {B['pos']:.0%} positive, worst {B['worst']:.1f}%")
        print(f"    → health filter adds {A['mean']-B['mean']:+.2f}%/day and lifts hit-rate {A['pos']-B['pos']:+.0%}")

    # ROE overlay
    if len(roe_rows) >= 6:
        xr = np.array([r for r, _ in roe_rows]); yr = np.array([b for _, b in roe_rows])
        c = float(np.corrcoef(xr, yr)[0, 1])
        print(f"\n  FUNDAMENTAL OVERLAY (n={len(roe_rows)} names with ROE): corr(ROE, setup-A bounce) = {c:+.2f}")
        print(f"    {'higher ROE → bigger bounce (quality recovers)' if c > 0.15 else 'weak/none — trend health matters more than ROE here'}")

    # per-stock ranked (the dip-buy list)
    stocks.sort(key=lambda x: x["setupA_mean"], reverse=True)
    print(f"\n  BEST healthy-dip-buys (VIX≥{HIVIX:g} + above 200DMA) [top 12]:")
    print(f"    {'sym':<12}{'bounce':>8}{'%pos':>7}{'worst':>8}{'n':>5}{'ROE':>7}")
    for x in stocks[:12]:
        roestr = f"{x['roe']:.0f}" if x['roe'] is not None else "—"
        print(f"    {x['sym']:<12}{x['setupA_mean']:>+7.2f}%{x['setupA_pos']:>7.0%}{x['setupA_worst']:>+7.1f}%"
              f"{x['setupA_n']:>5}{roestr:>7}")

    payload = {"as_of": str(mkt.index.max().date()), "thresh": THRESH, "hivix": HIVIX, "ma": MA,
               "pooled": {f"{vk}_{hk}": agg(pool[(vk, hk)]) for vk in ("hi", "lo") for hk in ("healthy", "broken")},
               "roe_corr": (round(float(np.corrcoef(*zip(*roe_rows))[0, 1]), 2) if len(roe_rows) >= 6 else None),
               "stocks": stocks}
    outp = os.path.join(FUND_DIR, "shock_recovery_v2.json")
    with open(outp, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\n  SAVED → {outp}")
    print("\n  RULE: only take the dip when VIX≥threshold AND the name is above its 200DMA.")
    print("  The broken-name column is the trap — those keep falling. Size for 'worst', not 'mean'.")


if __name__ == "__main__":
    main()
