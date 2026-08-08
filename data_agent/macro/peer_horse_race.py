#!/usr/bin/env python3
"""
peer_horse_race.py — horse-race attribution engine comparing local INFY to global peers.

Attributes daily INFY equity returns against daily changes in:
  - Accenture (ACN)
  - Cognizant (CTSH)
  - Salesforce (CRM)
  - Infosys ADR (INFY_ADR)
  - USD/INR Currency rate (USDINR)

Lagging: US equities (ACN, CTSH, CRM, INFY_ADR) close overnight before India's session,
so their changes are lagged by 1 India session to be causal.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

try:
    import numpy as np
    import pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas: pip install numpy pandas")
try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db",
)

TARGET = "INFY"

# Define sources and transformations
SOURCES = {
    "INFY":      {"kind": "sqlite", "symbol": "INFY",      "timeframe": "1d"},                     # target
    "USDINR":    {"kind": "sqlite", "symbol": "USDINR",    "timeframe": "1d", "transform": "pct"},
    "ACN":       {"kind": "pg",     "factor": "ACN",       "transform": "pct"},
    "CTSH":      {"kind": "pg",     "factor": "CTSH",      "transform": "pct"},
    "CRM":       {"kind": "pg",     "factor": "CRM",       "transform": "pct"},
    "INFY_ADR":  {"kind": "pg",     "factor": "INFY_ADR",  "transform": "pct"},
}

FACTORS = {k: v["transform"] for k, v in SOURCES.items() if k != TARGET}
US_FACTORS = [k for k, v in SOURCES.items() if v["kind"] == "pg" and k != TARGET]

def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()

def _sqlite_series(symbol, timeframe):
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite db not found: {SQLITE_DB} (set OPTION_CHAINS_DB)")
    con = sqlite3.connect(SQLITE_DB)
    try:
        rows = con.execute(
            "SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe=? ORDER BY ts",
            (symbol, timeframe)).fetchall()
    finally:
        con.close()
    return {_dkey(ts): float(v) for ts, v in rows if v is not None}

def _pg_series(conn, factor):
    with conn.cursor() as cur:
        cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date",
                    (factor,))
        return {_dkey(d): float(v) for d, v in cur.fetchall() if v is not None}

def load_panel(pgconn):
    series = {}
    for name, spec in SOURCES.items():
        s = (_sqlite_series(spec["symbol"], spec.get("timeframe", "1d"))
             if spec["kind"] == "sqlite" else _pg_series(pgconn, spec["factor"]))
        if not s:
            print(f"[warn] no rows for {name}", file=sys.stderr)
        series[name] = pd.Series(s, dtype=float)
    wide = pd.DataFrame(series).sort_index()
    return wide

def build_changes(wide, us_lag=1):
    have = [c for c in [TARGET] + list(FACTORS) if c in wide.columns and wide[c].notna().any()]
    missing = [c for c in [TARGET] + list(FACTORS) if c not in have]
    cols = {}
    if TARGET in wide.columns:
        cols[TARGET] = wide[TARGET].pct_change() * 100.0
    for f, how in FACTORS.items():
        if f not in wide.columns:
            continue
        chg = wide[f].diff() if how == "diff" else wide[f].pct_change() * 100.0
        if f in US_FACTORS and us_lag:
            chg = chg.shift(us_lag)
        cols[f] = chg
    changes = pd.DataFrame(cols).dropna()
    return changes, have, missing

def ols(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return beta, (1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"))

def rolling_regression(changes, window):
    facs = [f for f in FACTORS if f in changes.columns]
    y_all = changes[TARGET].to_numpy()
    X_all = np.column_stack([np.ones(len(changes))] + [changes[f].to_numpy() for f in facs])
    dates = changes.index
    out = []
    for end in range(window, len(changes) + 1):
        sl = slice(end - window, end)
        beta, r2 = ols(y_all[sl], X_all[sl])
        rec = {"date": dates[end - 1].strftime("%Y-%m-%d"), "alpha": beta[0], "r2": r2}
        rec.update({f: beta[i + 1] for i, f in enumerate(facs)})
        out.append(rec)
    return facs, out

def attribution(betas, changes_latest, facs):
    contribs = {f: betas[f] * changes_latest[f] for f in facs}
    total = sum(abs(v) for v in contribs.values()) or 1.0
    dist = {f: round(abs(v) / total * 100, 1) for f, v in contribs.items()}
    return contribs, dist, sum(contribs.values()), max(contribs, key=lambda f: abs(contribs[f]))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=120)
    ap.add_argument("--us-lag", type=int, default=1, dest="us_lag",
                    help="lag US stocks by N India sessions (default 1)")
    ap.add_argument("--diag", action="store_true", help="print each series' date range and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    wide = load_panel(conn)
    conn.close()

    if args.diag:
        print("=== per-series coverage ===")
        for c in [TARGET] + list(FACTORS):
            s = wide[c].dropna() if c in wide.columns else pd.Series([], dtype=float)
            if len(s):
                print(f"  {c:<9} n={len(s):<5} {s.index.min().date()} -> {s.index.max().date()}")
            else:
                print(f"  {c:<9} MISSING")
        aligned = wide[[c for c in [TARGET] + list(FACTORS) if c in wide.columns]].dropna()
        if len(aligned):
            print(f"  ALIGNED (all present): n={len(aligned)}  "
                  f"{aligned.index.min().date()} -> {aligned.index.max().date()}")
        return

    changes, have, missing = build_changes(wide, us_lag=args.us_lag)
    if TARGET not in changes.columns:
        sys.exit(f"Target {TARGET} missing. Present: {have}")
    facs = [f for f in FACTORS if f in changes.columns]
    if len(changes) < args.window + 5:
        sys.exit(f"Only {len(changes)} aligned rows; need > {args.window}. "
                 f"Present: {[TARGET] + facs}; missing: {missing}")

    uni_corr = {f: round(float(changes[TARGET].corr(changes[f])), 3) for f in facs}

    facs, series = rolling_regression(changes, args.window)
    latest = series[-1]
    betas = {f: latest[f] for f in facs}
    chg = {f: float(changes[f].iloc[-1]) for f in facs}
    contribs, dist, net, dominant = attribution(betas, chg, facs)

    result = {
        "target": TARGET, "window": args.window, "us_lag": args.us_lag,
        "as_of": latest["date"], "n_obs": len(changes), "r2": round(latest["r2"], 3),
        "macro_explained_pct": round(latest["r2"] * 100, 1),
        "betas": {f: round(betas[f], 4) for f in facs},
        "univariate_corr": uni_corr,
        "latest_factor_change": {f: round(chg[f], 4) for f in facs},
        "contribution": {f: round(contribs[f], 4) for f in facs},
        "driver_distribution_pct": dist,
        "net_expected_from_macro": round(net, 3),
        "dominant_driver": dominant, "missing_factors": missing,
    }
    if args.json:
        print(json.dumps(result, indent=2)); return
        
    print(f"\n=== {TARGET} Peer Horse Race Model (window {args.window}, as of {latest['date']}) ===")
    print(f"  aligned obs {result['n_obs']}   R² {result['r2']} -> {result['macro_explained_pct']}% peer-explained")
    print(f"  {'factor':<10}{'beta':>10}{'chg(latest)':>14}{'contrib':>10}{'share%':>9}")
    for f in facs:
        print(f"  {f:<10}{betas[f]:>10.4f}{chg[f]:>14.4f}{contribs[f]:>10.4f}{dist[f]:>9}")
    print(f"  net expected move from peers: {result['net_expected_from_macro']}%   dominant: {dominant}")
    if missing:
        print(f"  NOTE missing: {missing}")
    print("\n--- JSON ---"); print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
