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

def main():
    db_path = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    conn = sqlite3.connect(db_path)
    
    start_date = "2018-01-01"
    symbol = "CRUDEOIL"
    ticker = "CL=F" # WTI Crude Oil on NYMEX
    
    print(f"Fetching {ticker} from yfinance (auto_adjust=True)...")
    df = yf.download(ticker, start=start_date, auto_adjust=True, progress=False)
    
    if df.empty:
        print(f"Warning: No data for {ticker}")
        return
        
    c = conn.cursor()
    c.execute("DELETE FROM price_bars WHERE symbol=? AND timeframe='1d'", (symbol,))
    deleted = c.rowcount
    print(f"-> Deleted {deleted} old records for {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    records = []
    for index, row in df.iterrows():
        try:
            dt_naive = index.tz_localize(None).replace(hour=0, minute=0, second=0, microsecond=0)
        except TypeError:
            dt_naive = index.replace(hour=0, minute=0, second=0, microsecond=0)
            
        # Using YYYY-MM-DDTHH:MM:SS format without Z, consistent with equities, 
        # or wait! sync_commodities uses YYYY-MM-DDTHH:MM:SSZ for commodities?
        # Nifty uses '2026-07-31T00:00:00'
        # I'll use the same naive format as Nifty
        ts_str = dt_naive.strftime("%Y-%m-%dT%H:%M:%S")
        
        op = float(row['Open'])
        hi = float(row['High'])
        lo = float(row['Low'])
        cl = float(row['Close'])
        vol = int(row['Volume'])
        
        records.append((
            'NYMEX', # exchange
            symbol,
            '1d',
            ts_str,
            op, hi, lo, cl, vol
        ))
        
    c.executemany("""
        INSERT OR IGNORE INTO price_bars 
        (exchange, symbol, timeframe, ts, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    inserted = c.rowcount
    conn.commit()
    conn.close()
    
    print(f"Done. Total new daily bars inserted for {symbol}: {inserted}")

if __name__ == "__main__":
    main()
