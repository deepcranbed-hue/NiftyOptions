#!/usr/bin/env python3
"""
rotation_factor_study.py — quantitative sector rotation analysis.

Computes Relative Strength (RS-Ratio) and Relative Momentum (RS-Momentum)
for all major NSE sectors against the Nifty 50 benchmark, mapping them
into the 4 standard rotation quadrants:
  1. LEADING   (RS-Ratio > 100, RS-Momentum > 100)
  2. WEAKENING (RS-Ratio > 100, RS-Momentum <= 100)
  3. LAGGING   (RS-Ratio <= 100, RS-Momentum <= 100)
  4. IMPROVING (RS-Ratio <= 100, RS-Momentum > 100)
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_db_path

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

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    resolve_db_path(),
)

BENCHMARK = "NIFTY"
SECTORS = [
    "BANKNIFTY", "NIFTYIT", "NIFTYFMCG", "NIFTYMETAL", "NIFTYPHARMA",
    "NIFTYAUTO", "NIFTYREALTY", "NIFTYPSU", "NIFTYENERGY", "NIFTYINFRA"
]

US_STOCKS = ["ACN", "CTSH", "CRM", "INFY_ADR", "NVDA", "MU", "SMH", "EPAM", "WIT_ADR", "IBM", "ADBE", "MSFT", "XLK", "SPY"]

def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()

def load_data(pgconn, universe="india"):
    series = {}
    if universe == "india":
        if not os.path.exists(SQLITE_DB):
            sys.exit(f"SQLite db not found: {SQLITE_DB} (set OPTION_CHAINS_DB)")
        con = sqlite3.connect(SQLITE_DB)
        try:
            # Load benchmark
            rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' ORDER BY ts").fetchall()
            series[BENCHMARK] = pd.Series({_dkey(ts): float(c) for ts, c in rows if c is not None})
            
            # Load sectors
            for sec in SECTORS:
                s_rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY ts", (sec,)).fetchall()
                series[sec] = pd.Series({_dkey(ts): float(c) for ts, c in s_rows if c is not None})
        finally:
            con.close()
    else:
        # Load US benchmark (NASDAQ) from PG
        with pgconn.cursor() as cur:
            cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor='NASDAQ' ORDER BY obs_date")
            series["NASDAQ"] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
            
            # Load US stocks from PG
            for stock in US_STOCKS:
                cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date", (stock,))
                series[stock] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
                
    df = pd.DataFrame(series).sort_index().dropna(how="all")
    df = df.ffill().dropna()
    return df

def calculate_rrg(df, targets, benchmark, window_ratio=14, window_momentum=14):
    """
    Computes RS-Ratio and RS-Momentum for each target.
    """
    rrg = {}
    for target in targets:
        if target not in df.columns or target == benchmark:
            continue
        rp = df[target] / df[benchmark]
        
        mean_rp = rp.rolling(window_ratio).mean()
        std_rp = rp.rolling(window_ratio).std().replace(0, float("nan"))
        rs_ratio = 100.0 + ((rp - mean_rp) / std_rp) * 1.0
        
        rs_ratio_roc = rs_ratio.pct_change(3) * 100.0
        mean_roc = rs_ratio_roc.rolling(window_momentum).mean()
        std_roc = rs_ratio_roc.rolling(window_momentum).std().replace(0, float("nan"))
        rs_momentum = 100.0 + ((rs_ratio_roc - mean_roc) / std_roc) * 1.0
        
        rrg[target] = pd.DataFrame({
            "rs_ratio": rs_ratio,
            "rs_momentum": rs_momentum
        })
    return rrg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=str, choices=["india", "us"], default="india")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import psycopg
    dsn = os.getenv("DATABASE_URL")
    pgconn = psycopg.connect(dsn) if dsn else psycopg.connect()
    
    df = load_data(pgconn, args.universe)
    pgconn.close()
    
    if len(df) < 30:
        sys.exit(f"Too few aligned daily rows: {len(df)}")
        
    benchmark = BENCHMARK if args.universe == "india" else "NASDAQ"
    targets = SECTORS if args.universe == "india" else US_STOCKS
    
    rrg = calculate_rrg(df, targets, benchmark)
    
    perf_20d = (df.pct_change(20).iloc[-1] * 100.0).to_dict()
    perf_60d = (df.pct_change(60).iloc[-1] * 100.0).to_dict()
    
    active_targets = [t for t in targets if t in df.columns and t != benchmark]
    returns = df.pct_change().dropna()
    corr_matrix = returns[active_targets].corr().round(3).to_dict()
    
    as_of = df.index[-1].strftime("%Y-%m-%d")
    
    quadrants = {
        "LEADING": [],
        "WEAKENING": [],
        "LAGGING": [],
        "IMPROVING": []
    }
    
    results_list = []
    
    for target, rrg_df in rrg.items():
        latest_ratio = float(rrg_df["rs_ratio"].iloc[-1])
        latest_momo = float(rrg_df["rs_momentum"].iloc[-1])
        
        if pd.isna(latest_ratio) or pd.isna(latest_momo):
            continue
            
        if latest_ratio > 100 and latest_momo > 100:
            quad = "LEADING"
        elif latest_ratio > 100 and latest_momo <= 100:
            quad = "WEAKENING"
        elif latest_ratio <= 100 and latest_momo <= 100:
            quad = "LAGGING"
        else:
            quad = "IMPROVING"
            
        quadrants[quad].append(target)
        
        results_list.append({
            "target": target,
            "rs_ratio": round(latest_ratio, 2),
            "rs_momentum": round(latest_momo, 2),
            "quadrant": quad,
            "perf_20d_pct": round(perf_20d.get(target, 0.0), 2),
            "perf_60d_pct": round(perf_60d.get(target, 0.0), 2),
        })
        
    results_list.sort(key=lambda x: x["rs_ratio"], reverse=True)
    
    output = {
        "as_of": as_of,
        "benchmark": benchmark,
        "universe": args.universe,
        "targets": results_list,
        "quadrants": quadrants,
        "correlation": corr_matrix
    }
    
    if args.json:
        print(json.dumps(output, indent=2))
        return
        
    print(f"\n=== NSE Sector Rotation Factor Study ({args.universe.upper()} Universe, As of {as_of}) ===")
    print(f"  benchmark: {benchmark} | total aligned history: {len(df)} days\n")
    
    print("  --- Rotation Quadrant Mapping ---")
    for quad, list_secs in quadrants.items():
        print(f"    {quad:<10}: {', '.join(list_secs) if list_secs else 'None'}")
        
    print("\n  --- Details (ranked by RS-Ratio) ---")
    print(f"    {'symbol':<12}{'rs-ratio':>10}{'rs-momentum':>12}{'quadrant':<12}{'20d-perf%':>10}{'60d-perf%':>10}")
    for item in results_list:
        print(f"    {item['target']:<12}{item['rs_ratio']:>10.2f}{item['rs_momentum']:>12.2f}  {item['quadrant']:<12}{item['perf_20d_pct']:>10.1f}%{item['perf_60d_pct']:>10.1f}%")

if __name__ == "__main__":
    main()

