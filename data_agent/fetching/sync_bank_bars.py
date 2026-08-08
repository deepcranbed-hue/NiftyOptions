#!/usr/bin/env python3
import os
import sys
import sqlite3
import requests
from datetime import datetime
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))

from bar_store import DB_PATH
from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()

BANKS = [
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 
    'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB', 
    'FEDERALBNK', 'BANDHANBNK'
]

def get_upstox_keys():
    dsn = os.getenv("DATABASE_URL", "postgresql://localhost/niftyoptions")
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol, instrument_key FROM fundamentals.companies WHERE symbol = ANY(%s)",
        (BANKS,)
    )
    keys = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return keys

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

    keys_map = get_upstox_keys()
    if not keys_map:
        print("Error: Could not retrieve instrument keys from PostgreSQL.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    from_date = "2018-01-01" # Fetch ~6 years of data
    
    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0

    for symbol in BANKS:
        instrument_key = keys_map.get(symbol)
        if not instrument_key:
            print(f"Warning: No instrument_key found for {symbol}. Skipping.")
            continue
            
        print(f"Fetching daily bars for {symbol} ({instrument_key})...")
        candles = fetch_upstox_daily(instrument_key, from_date, today_str)
        if not candles:
            continue
            
        inserted_for_symbol = 0
        c = conn.cursor()
        for candle in candles:
            # Upstox returns: [timestamp, open, high, low, close, volume, oi]
            # timestamp is like "2024-07-26T00:00:00+05:30"
            ts_str = candle[0]
            # parse to a standardized format if needed, or keep as is.
            # bar_store expects ISO 8601 strings usually.
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
