#!/usr/bin/env python3
"""
signal_validation.py — statistical validation of the IT rotation signal.

Calculates advanced performance metrics (Sharpe, Sortino, Max Drawdown) and
runs a t-test to check statistical significance of next-day returns across
the Bullish and Bearish signal regimes.
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
import math

try:
    import numpy as np
    import pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas: pip install numpy pandas")

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    resolve_db_path(),
)

GLOBAL_SERVICES = ["ACN", "CTSH", "EPAM", "WIT_ADR", "INFY_ADR"]

def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()

def load_data(pgconn):
    series = {}
    
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite db not found: {SQLITE_DB} (set OPTION_CHAINS_DB)")
    con = sqlite3.connect(SQLITE_DB)
    try:
        rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' ORDER BY ts").fetchall()
        series["NIFTY"] = pd.Series({_dkey(ts): float(c) for ts, c in rows if c is not None})
        rows_it = con.execute("SELECT ts, close FROM price_bars WHERE symbol='NIFTYIT' AND timeframe='1d' ORDER BY ts").fetchall()
        series["NIFTYIT"] = pd.Series({_dkey(ts): float(c) for ts, c in rows_it if c is not None})
    finally:
        con.close()
        
    with pgconn.cursor() as cur:
        cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor='NASDAQ' ORDER BY obs_date")
        series["NASDAQ"] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
        
        for stock in GLOBAL_SERVICES:
            cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date", (stock,))
            series[stock] = pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None})
            
    df = pd.DataFrame(series).sort_index().dropna(how="all")
    df = df.ffill().dropna()
    return df

def calculate_max_drawdown(cum_returns_series: pd.Series) -> float:
    # cum_returns_series is actual equity line (1.0 + cumprod)
    wealth_index = cum_returns_series + 1.0
    previous_peaks = wealth_index.cummax()
    drawdowns = (wealth_index - previous_peaks) / previous_peaks
    return float(drawdowns.min() * 100.0)

def run_t_test(x1: pd.Series, x2: pd.Series) -> tuple[float, float]:
    """Runs a Welch's t-test return (t-stat, p-value approximation)."""
    n1 = len(x1)
    n2 = len(x2)
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    mean1, mean2 = x1.mean(), x2.mean()
    var1, var2 = x1.var(), x2.var()
    
    # Welch's t-statistic
    t_stat = (mean1 - mean2) / math.sqrt((var1 / n1) + (var2 / n2))
    
    # Degrees of freedom approximation
    df = ((var1/n1 + var2/n2)**2) / ( ((var1/n1)**2)/(n1-1) + ((var2/n2)**2)/(n2-1) )
    
    # Simple two-tailed p-value approximation using normal distribution as proxy for large df
    # p = 2 * (1 - CND(|t|))
    def cnd(x):
        # standard normal cumulative distribution function approximation
        a1 =  0.254829592
        a2 = -0.284496736
        a3 =  1.421413741
        a4 = -1.453152027
        a5 =  1.061405429
        p  =  0.3275911
        sign = 1
        if x < 0:
            sign = -1
        x = abs(x) / math.sqrt(2.0)
        t = 1.0 / (1.0 + p*x)
        y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1)*t*math.exp(-x*x)
        return 0.5 * (1.0 + sign * y)
        
    p_val = 2.0 * (1.0 - cnd(abs(t_stat)))
    return float(t_stat), float(p_val)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import psycopg
    dsn = os.getenv("DATABASE_URL")
    pgconn = psycopg.connect(dsn) if dsn else psycopg.connect()
    df = load_data(pgconn)
    pgconn.close()
    
    if len(df) < args.window + 10:
        sys.exit("Insufficient aligned history.")
        
    # 1. Compute Signal
    norm_services = df[GLOBAL_SERVICES] / df[GLOBAL_SERVICES].iloc[0] * 100.0
    df["GLOBAL_IT_INDEX"] = norm_services.mean(axis=1)
    
    rp = df["GLOBAL_IT_INDEX"] / df["NASDAQ"]
    mean_rp = rp.rolling(args.window).mean()
    std_rp = rp.rolling(args.window).std().replace(0, float("nan"))
    rs_ratio = 100.0 + ((rp - mean_rp) / std_rp) * 1.0
    
    rs_ratio_roc = rs_ratio.pct_change(3) * 100.0
    mean_roc = rs_ratio_roc.rolling(args.window).mean()
    std_roc = rs_ratio_roc.rolling(args.window).std().replace(0, float("nan"))
    rs_momentum = 100.0 + ((rs_ratio_roc - mean_roc) / std_roc) * 1.0
    
    df["lsi"] = (rs_ratio + rs_momentum) - 200.0
    df["signal"] = df["lsi"].shift(1)
    df["niftyit_ret"] = df["NIFTYIT"].pct_change() * 100.0
    
    test_df = df.dropna(subset=["signal", "niftyit_ret"]).copy()
    test_df["position"] = np.where(test_df["signal"] > 0, 1, 0)
    test_df["strat_ret"] = test_df["position"] * test_df["niftyit_ret"]
    
    # 2. Cumulative Equity Curves
    cum_bh = (1.0 + test_df["niftyit_ret"] / 100.0).cumprod() - 1.0
    cum_strat = (1.0 + test_df["strat_ret"] / 100.0).cumprod() - 1.0
    
    # 3. Validation Metrics
    mdd_bh = calculate_max_drawdown(cum_bh)
    mdd_strat = calculate_max_drawdown(cum_strat)
    
    # Annualized Sharpe (assuming 252 days)
    mean_bh, std_bh = test_df["niftyit_ret"].mean(), test_df["niftyit_ret"].std()
    sharpe_bh = (mean_bh / std_bh * math.sqrt(252)) if std_bh > 0 else 0.0
    
    mean_strat, std_strat = test_df["strat_ret"].mean(), test_df["strat_ret"].std()
    sharpe_strat = (mean_strat / std_strat * math.sqrt(252)) if std_strat > 0 else 0.0
    
    # Sortino (Downside deviation only)
    downside_bh = test_df[test_df["niftyit_ret"] < 0]["niftyit_ret"].std()
    sortino_bh = (mean_bh / downside_bh * math.sqrt(252)) if downside_bh > 0 else 0.0
    
    downside_strat = test_df[test_df["strat_ret"] < 0]["strat_ret"].std()
    sortino_strat = (mean_strat / downside_strat * math.sqrt(252)) if downside_strat > 0 else 0.0
    
    # 4. Statistical Regimes & Hypothesis Testing (Welch's t-test)
    bull_returns = test_df[test_df["position"] == 1]["niftyit_ret"]
    bear_returns = test_df[test_df["position"] == 0]["niftyit_ret"]
    
    t_stat, p_val = run_t_test(bull_returns, bear_returns)
    
    # 5. Predictive Information Coefficients (ICs)
    ic_lag1 = test_df["lsi"].corr(test_df["niftyit_ret"])
    ic_lag2 = test_df["lsi"].shift(1).corr(test_df["niftyit_ret"])
    
    result = {
        "as_of": test_df.index[-1].strftime("%Y-%m-%d"),
        "n_observations": len(test_df),
        "regime_means_pct": {
            "bullish_days": round(float(bull_returns.mean()), 4),
            "bearish_days": round(float(bear_returns.mean()), 4)
        },
        "regime_sample_sizes": {
            "bullish_days": len(bull_returns),
            "bearish_days": len(bear_returns)
        },
        "hypothesis_testing": {
            "t_statistic": round(t_stat, 4),
            "p_value": round(p_val, 6),
            "statistically_significant_95": bool(p_val < 0.05)
        },
        "risk_adjusted_performance": {
            "buy_and_hold": {
                "sharpe_ratio": round(sharpe_bh, 3),
                "sortino_ratio": round(sortino_bh, 3),
                "max_drawdown_pct": round(mdd_bh, 2)
            },
            "rotation_strategy": {
                "sharpe_ratio": round(sharpe_strat, 3),
                "sortino_ratio": round(sortino_strat, 3),
                "max_drawdown_pct": round(mdd_strat, 2)
            }
        },
        "information_coefficients": {
            "ic_lag1_overnight": round(float(ic_lag1), 3),
            "ic_lag2_two_days": round(float(ic_lag2), 3)
        }
    }
    
    if args.json:
        print(json.dumps(result, indent=2))
        return
        
    print(f"\n=== NIFTYIT Rotation Signal Validation (As of {result['as_of']}) ===")
    print(f"  Total Aligned Days: {result['n_observations']} | Signal Window: {args.window}\n")
    
    print("  --- Risk-Adjusted Performance ---")
    print(f"    {'Metric':<25}{'Buy & Hold':<20}{'Rotation Strategy'}")
    print(f"    {'Sharpe Ratio':<25}{result['risk_adjusted_performance']['buy_and_hold']['sharpe_ratio']:>8.3f}{result['risk_adjusted_performance']['rotation_strategy']['sharpe_ratio']:>18.3f}")
    print(f"    {'Sortino Ratio':<25}{result['risk_adjusted_performance']['buy_and_hold']['sortino_ratio']:>8.3f}{result['risk_adjusted_performance']['rotation_strategy']['sortino_ratio']:>18.3f}")
    print(f"    {'Max Drawdown':<25}{result['risk_adjusted_performance']['buy_and_hold']['max_drawdown_pct']:>7.2f}%{result['risk_adjusted_performance']['rotation_strategy']['max_drawdown_pct']:>17.2f}%")
    
    print("\n  --- Statistical Significance (Welch's t-test) ---")
    print(f"    Bullish Regime Mean Return : {result['regime_means_pct']['bullish_days']:.4f}% (n={result['regime_sample_sizes']['bullish_days']})")
    print(f"    Bearish Regime Mean Return : {result['regime_means_pct']['bearish_days']:.4f}% (n={result['regime_sample_sizes']['bearish_days']})")
    print(f"    Welch's t-Statistic        : {result['hypothesis_testing']['t_statistic']:.4f}")
    print(f"    p-Value                    : {result['hypothesis_testing']['p_value']:.6f}")
    sig_str = "YES (p < 0.05)" if result['hypothesis_testing']['statistically_significant_95'] else "NO (p >= 0.05)"
    print(f"    Statistically Significant  : {sig_str}")
    
    print("\n  --- Predictive Information Coefficients (IC) ---")
    print(f"    IC Lag 1 (Overnight lead)  : {result['information_coefficients']['ic_lag1_overnight']:.3f}")
    print(f"    IC Lag 2 (Two-day lead)    : {result['information_coefficients']['ic_lag2_two_days']:.3f}")

if __name__ == "__main__":
    main()
