#!/usr/bin/env python3
import os
import sys
import sqlite3
import requests
import pandas as pd
from datetime import datetime
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))

from bar_store import DB_PATH
from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()

# The 10 missing FINNIFTY stocks
MISSING_FINNIFTY = [
    'CHOLAFIN', 'MUTHOOTFIN', 'PFC', 'RECLTD', 
    'ICICIGI', 'ICICIPRULI', 'LICHSGFIN', 'HDFCAMC', 
    'ABCAPITAL', 'SYNGENE'
]

def fetch_upstox_daily(instrument_key, from_date, to_date):
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('data', {}).get('candles', [])
    else:
        print(f"Error fetching {instrument_key}: {response.text}")
        return []

def main():
    if not UPSTOX_ACCESS_TOKEN:
        print("Error: No UPSTOX_ACCESS_TOKEN found.")
        return

    # Download complete instrument list
    print("Downloading Upstox instrument list...")
    df = pd.read_csv("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz")
    
    # Filter for NSE_EQ
    df = df[df['instrument_key'].str.startswith('NSE_EQ|', na=False)]
    
    keys_map = {}
    for symbol in MISSING_FINNIFTY:
        match = df[df['tradingsymbol'] == symbol]
        if not match.empty:
            keys_map[symbol] = match.iloc[0]['instrument_key']
        else:
            print(f"Could not find key for {symbol}")

    today_str = datetime.now().strftime("%Y-%m-%d")
    from_date = "2018-01-01" 
    
    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0

    for symbol in MISSING_FINNIFTY:
        instrument_key = keys_map.get(symbol)
        if not instrument_key:
            continue
            
        print(f"Fetching daily bars for {symbol} ({instrument_key})...")
        candles = fetch_upstox_daily(instrument_key, from_date, today_str)
        if not candles:
            continue
            
        inserted_for_symbol = 0
        c = conn.cursor()
        for candle in candles:
            ts_str = candle[0]
            open_p, high_p, low_p, close_p, vol, oi = candle[1:]
            
            c.execute("""
                INSERT OR REPLACE INTO price_bars (exchange, symbol, timeframe, ts, open, high, low, close, volume, open_interest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("NSE", symbol, "1d", ts_str, open_p, high_p, low_p, close_p, vol, oi))
            inserted_for_symbol += 1
            total_inserted += 1
            
        conn.commit()
        print(f" -> Upserted {inserted_for_symbol} daily bars for {symbol}")

    conn.close()
    print(f"Done! Upserted {total_inserted} total 1d bars into SQLite price_bars.")

if __name__ == "__main__":
    main()
