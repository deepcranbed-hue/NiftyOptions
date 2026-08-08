#!/usr/bin/env python3
import os
import sys
import argparse
import sqlite3
import yfinance as yf
import pandas as pd

def main():
    parser = argparse.ArgumentParser(description="Download daily Indian index data from yfinance into SQLite price_bars.")
    parser.add_argument("--since", type=str, default="2018-01-02", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    db_path = os.environ.get(
        "OPTION_CHAINS_DB",
        "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"
    )
    
    symbol_map = {
        "^NSEI": "NIFTY",
        "^CNXIT": "NIFTYIT",
        "^INDIAVIX": "INDIAVIX",
        "^NSEBANK": "BANKNIFTY",
        "USDINR=X": "USDINR"
    }
    
    print(f"Downloading Indian index daily prices since {args.since} from yfinance...")
    
    data_list = []
    for ticker, local_symbol in symbol_map.items():
        try:
            print(f"Fetching {ticker}...")
            t = yf.Ticker(ticker)
            df = t.history(start=args.since, interval="1d")
            if df.empty:
                print(f"No data returned for {ticker}")
                continue
                
            for dt, row in df.iterrows():
                # Format timestamp as YYYY-MM-DDT00:00:00Z
                # Canonical daily format — no trailing Z. ts is part of the
                # primary key, so a Z here is a SECOND row for the same session
                # beside whatever daily_bars wrote. This script targets NIFTY,
                # NIFTYIT and BANKNIFTY, which sync_sectors_yf and
                # sync_nifty50_bars_yf also own — so the Z was about to
                # re-duplicate ~2,117 sessions per symbol on the next
                # /api/sync-all-data run. See data_agent/fetching/daily_bars.py.
                ts_str = dt.strftime("%Y-%m-%dT00:00:00")
                data_list.append((
                    "NSE",
                    local_symbol,
                    "1d",
                    ts_str,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row.get("Volume", 0.0))
                ))
            print(f"Loaded {len(df)} rows for {ticker} -> {local_symbol}")
        except Exception as err:
            print(f"Error fetching {ticker}: {err}")
            
    if not data_list:
        print("No data fetched.")
        return
        
    print(f"Upserting {len(data_list)} rows into SQLite price_bars at {db_path}...")
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        # Batch upsert
        cur.executemany("""
            INSERT INTO price_bars (exchange, symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (exchange, symbol, timeframe, ts) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume;
        """, data_list)
        con.commit()
        print(f"Successfully upserted {len(data_list)} rows.")
    finally:
        con.close()

if __name__ == "__main__":
    main()
