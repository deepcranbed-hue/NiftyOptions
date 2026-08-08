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

IT_STOCKS = [
    'LTIM'
]

def get_upstox_keys():
    dsn = os.getenv("DATABASE_URL", "postgresql://localhost/niftyoptions")
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol, instrument_key FROM fundamentals.companies WHERE symbol = ANY(%s)",
        (IT_STOCKS,)
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
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"Error fetching {instrument_key}: {resp.status_code} {resp.text}")
        return []
    data = resp.json().get('data', {}).get('candles', [])
    return data

def main():
    db_path = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    conn = sqlite3.connect(db_path)
    
    keys = get_upstox_keys()
    
    start_date = "2018-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    total_inserted = 0
    c = conn.cursor()

    for symbol in IT_STOCKS:
        if symbol not in keys or not keys[symbol]:
            print(f"No instrument key found for {symbol} in postgres!")
            continue
            
        ikey = keys[symbol]
        print(f"Fetching {symbol} ({ikey}) from upstox...")
        
        candles = fetch_upstox_daily(ikey, start_date, end_date)
        if not candles:
            print(f"  Warning: No data for {symbol}")
            continue
            
        c.execute("DELETE FROM price_bars WHERE symbol=? AND timeframe='1d'", (symbol,))
        deleted = c.rowcount
        print(f"  -> Deleted {deleted} old records for {symbol}")

        records = []
        for candle in candles:
            # candle format: [timestamp, open, high, low, close, vol, oi]
            ts_iso = candle[0] 
            # e.g., "2024-07-26T00:00:00+05:30"
            try:
                # Upstox returns tz-aware, we'll strip tz and just keep YYYY-MM-DDTHH:MM:SS
                dt = datetime.fromisoformat(ts_iso).replace(tzinfo=None)
                ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                ts_str = ts_iso[:19]
                
            op = float(candle[1])
            hi = float(candle[2])
            lo = float(candle[3])
            cl = float(candle[4])
            vol = int(candle[5])
            
            records.append((
                symbol,
                '1d',
                ts_str,
                op, hi, lo, cl, vol
            ))
            
        c.executemany("""
            INSERT OR IGNORE INTO price_bars 
            (symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        
        inserted = c.rowcount
        total_inserted += inserted
        print(f"  -> Inserted {inserted} bars for {symbol}")

    conn.commit()
    conn.close()
    print(f"\nDone. Total new bars inserted: {total_inserted}")

if __name__ == "__main__":
    main()
