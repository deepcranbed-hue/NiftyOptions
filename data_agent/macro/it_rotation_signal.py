#!/usr/bin/env python3
"""
it_rotation_signal.py — cross-border IT rotation trading signal.

Builds a leading indicator for Indian IT (NIFTYIT) based on the relative rotation
of Global IT Services giants (ACN, CTSH, EPAM, WIT_ADR, INFY_ADR) vs the NASDAQ.
Since US markets close overnight, this signal is lagged by 1 session to provide
a tradeable trigger for the next morning's India open.
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

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db",
)

GLOBAL_SERVICES = ["ACN", "CTSH", "EPAM", "WIT_ADR", "INFY_ADR"]

def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()

def load_data(pgconn):
    series = {}
    
    # 1. Load domestic IT and benchmark from SQLite
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite db not found: {SQLITE_DB} (set OPTION_CHAINS_DB)")
    con = sqlite3.connect(SQLITE_DB)
    try:
        # Benchmark
        rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' ORDER BY ts").fetchall()
        series["NIFTY"] = pd.Series({_dkey(ts): float(c) for ts, c in rows if c is not None})
        # Nifty IT
        rows_it = con.execute("SELECT ts, close FROM price_bars WHERE symbol='NIFTYIT' AND timeframe='1d' ORDER BY ts").fetchall()
        series["NIFTYIT"] = pd.Series({_dkey(ts): float(c) for ts, c in rows_it if c is not None})
    finally:
        con.close()
        
    # 2. Load US benchmark (NASDAQ) and US stock factors from PG
    with pgconn.cursor() as cur:
        cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor='NASDAQ' ORDER BY obs_date")
        series["NASDAQ"] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
        
        for stock in GLOBAL_SERVICES:
            cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date", (stock,))
            series[stock] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
            
    df = pd.DataFrame(series).sort_index().dropna(how="all")
    df = df.ffill().dropna()
    return df

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=14, help="RRG rolling calculation window")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import psycopg
    dsn = os.getenv("DATABASE_URL")
    pgconn = psycopg.connect(dsn) if dsn else psycopg.connect()
    df = load_data(pgconn)
    pgconn.close()
    
    if len(df) < args.window + 10:
        sys.exit("Insufficient aligned history to compute signal.")
        
    # 1. Construct the Global IT Services Basket Close (average of the normalized prices)
    # We normalize each to 100 at the start of the series to compute an equally weighted index
    norm_services = df[GLOBAL_SERVICES] / df[GLOBAL_SERVICES].iloc[0] * 100.0
    df["GLOBAL_IT_INDEX"] = norm_services.mean(axis=1)
    
    # 2. Compute RRG metrics for Global IT Index vs NASDAQ benchmark
    rp = df["GLOBAL_IT_INDEX"] / df["NASDAQ"]
    mean_rp = rp.rolling(args.window).mean()
    std_rp = rp.rolling(args.window).std().replace(0, float("nan"))
    rs_ratio = 100.0 + ((rp - mean_rp) / std_rp) * 1.0
    
    rs_ratio_roc = rs_ratio.pct_change(3) * 100.0
    mean_roc = rs_ratio_roc.rolling(args.window).mean()
    std_roc = rs_ratio_roc.rolling(args.window).std().replace(0, float("nan"))
    rs_momentum = 100.0 + ((rs_ratio_roc - mean_roc) / std_roc) * 1.0
    
    df["rs_ratio"] = rs_ratio
    df["rs_momentum"] = rs_momentum
    
    # 3. Leading Signal Indicator (LSI) value: sum of relative strength and relative momentum deviations
    # We subtract 200 so the baseline sits around 0.
    df["lsi"] = (df["rs_ratio"] + df["rs_momentum"]) - 200.0
    
    # Lag the LSI by 1 day to make it causal (overnight US signal -> today's India trading)
    df["signal"] = df["lsi"].shift(1)
    
    # 4. Strategy Backtest
    df["niftyit_ret"] = df["NIFTYIT"].pct_change() * 100.0
    
    # Clean up RRG warm-up periods
    test_df = df.dropna(subset=["signal", "niftyit_ret"]).copy()
    
    # Signal logic
    test_df["position"] = np.where(test_df["signal"] > 0, 1, 0) # 1 = Long NiftyIT, 0 = Flat/Cash
    test_df["strat_ret"] = test_df["position"] * test_df["niftyit_ret"]
    
    # Cumulative performance
    test_df["cum_bh"] = (1.0 + test_df["niftyit_ret"] / 100.0).cumprod() - 1.0
    test_df["cum_strat"] = (1.0 + test_df["strat_ret"] / 100.0).cumprod() - 1.0
    
    # Statistics
    long_days = test_df[test_df["position"] == 1]
    flat_days = test_df[test_df["position"] == 0]
    
    avg_bh_ret = float(test_df["niftyit_ret"].mean())
    avg_strat_ret = float(test_df["strat_ret"].mean())
    
    win_rate_bh = float((test_df["niftyit_ret"] > 0).sum() / len(test_df) * 100)
    win_rate_strat = float((test_df["strat_ret"] > 0).sum() / test_df["position"].sum() * 100) if test_df["position"].sum() > 0 else 0.0
    
    as_of = test_df.index[-1].strftime("%Y-%m-%d")
    current_lsi = float(df["lsi"].iloc[-1])
    current_signal = "BULLISH (LONG)" if current_lsi > 0 else "BEARISH (FLAT/CASH)"
    
    result = {
        "as_of": as_of,
        "n_obs": len(test_df),
        "current_lsi_value": round(current_lsi, 3),
        "current_signal_state": current_signal,
        "cum_returns": {
            "buy_and_hold_pct": round(float(test_df["cum_bh"].iloc[-1] * 100), 2),
            "rotation_strategy_pct": round(float(test_df["cum_strat"].iloc[-1] * 100), 2)
        },
        "average_daily_returns": {
            "buy_and_hold_pct": round(avg_bh_ret, 4),
            "rotation_strategy_pct": round(avg_strat_ret, 4)
        },
        "daily_win_rate": {
            "buy_and_hold_pct": round(win_rate_bh, 2),
            "rotation_strategy_pct": round(win_rate_strat, 2)
        },
        "position_days_pct": round(float(test_df["position"].sum() / len(test_df) * 100), 1)
    }
    
    if args.json:
        print(json.dumps(result, indent=2))
        return
        
    print(f"\n=== NIFTYIT Rotation Signal Strategy (As of {as_of}) ===")
    print(f"  Total Backtest Days: {result['n_obs']} | Window: {args.window}")
    print(f"  Current LSI Value: {result['current_lsi_value']:.3f} -> {current_signal}\n")
    
    print("  --- Backtest Performance ---")
    print(f"    {'Metric':<25}{'Buy & Hold':<20}{'Rotation Strategy'}")
    print(f"    {'Cumulative Return':<25}{result['cum_returns']['buy_and_hold_pct']:>8.2f}%{result['cum_returns']['rotation_strategy_pct']:>18.2f}%")
    print(f"    {'Avg Daily Return':<25}{result['average_daily_returns']['buy_and_hold_pct']:>8.4f}%{result['average_daily_returns']['rotation_strategy_pct']:>18.4f}%")
    print(f"    {'Daily Win Rate':<25}{result['daily_win_rate']['buy_and_hold_pct']:>8.2f}%{result['daily_win_rate']['rotation_strategy_pct']:>18.2f}%")
    print(f"    {'Days Invested':<25}{'100.0%':>9}{result['position_days_pct']:>18.1f}%")
    
    print("\n  --- Strategy Mechanics ---")
    print("    Signal is generated from US IT Services Basket (ACN, CTSH, EPAM, WIT, INFY) vs NASDAQ.")
    print("    LSI = (RS_Ratio + RS_Momentum) - 200. Long Nifty IT when LSI > 0, else Flat.")

if __name__ == "__main__":
    main()
