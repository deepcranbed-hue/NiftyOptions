#!/usr/bin/env python3
import os
import sys
import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)

from bar_store import DB_PATH

FINNIFTY = [
    'CHOLAFIN', 'MUTHOOTFIN', 'PFC', 'RECLTD', 
    'ICICIGI', 'ICICIPRULI', 'LICHSGFIN', 'HDFCAMC', 
    'ABCAPITAL', 'SYNGENE'
]

def main():
    db_path = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    conn = sqlite3.connect(db_path)
    
    start_date = "2018-01-01"
    
    total_inserted = 0
    c = conn.cursor()

    for bank in FINNIFTY:
        ticker = f"{bank}.NS"
        print(f"Fetching {ticker} from yfinance (auto_adjust=True)...")
        
        # We explicitly request auto_adjust=True to fix splits/bonuses
        # and avoid unadjusted phantom crashes.
        df = yf.download(ticker, start=start_date, auto_adjust=True, progress=False)
        
        if df.empty:
            print(f"  Warning: No data for {ticker}")
            continue
            
        # Clear out any old buggy Upstox data for this symbol to ensure a clean slate
        c.execute("DELETE FROM price_bars WHERE symbol=? AND timeframe='1d'", (bank,))
        deleted = c.rowcount
        print(f"  -> Deleted {deleted} old records for {bank}")

        # Insert new clean data
        inserted = 0
        # Check if columns are multi-level (yfinance >= 0.2.40)
        if isinstance(df.columns, pd.MultiIndex):
            # Flatten multi-index columns: 'Close', 'HDFCBANK.NS' -> 'Close'
            df.columns = df.columns.get_level_values(0)
            
        for index, row in df.iterrows():
            # Format datetime index to string matching our DB (e.g., "2024-07-26T00:00:00")
            # yfinance returns timezone-aware (Asia/Kolkata) or naive depending on version. 
            # We'll normalize to a naive string at midnight to match other daily data.
            try:
                dt_naive = index.tz_localize(None).replace(hour=0, minute=0, second=0, microsecond=0)
            except TypeError:
                dt_naive = index.replace(hour=0, minute=0, second=0, microsecond=0)
                
            ts_str = dt_naive.strftime("%Y-%m-%dT00:00:00")
            
            # Extract OHLCV
            try:
                open_p = float(row['Open'])
                high_p = float(row['High'])
                low_p = float(row['Low'])
                close_p = float(row['Close'])
                vol = int(row['Volume'])
            except Exception as e:
                # Skip rows with NaNs
                continue
                
            if pd.isna(close_p):
                continue

            c.execute("""
                INSERT INTO price_bars (exchange, symbol, timeframe, ts, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("NSE", bank, "1d", ts_str, open_p, high_p, low_p, close_p, vol))
            inserted += 1
            total_inserted += 1
            
        conn.commit()
        print(f"  -> Upserted {inserted} clean split-adjusted bars for {bank}")

    conn.close()
    print(f"Done! Inserted {total_inserted} total clean 1d bars from yfinance.")

if __name__ == "__main__":
    main()
