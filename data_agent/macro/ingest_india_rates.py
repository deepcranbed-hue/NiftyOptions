#!/usr/bin/env python3
"""
ingest_india_rates.py — fetch Indian macro factor series (Repo Rate, 10Y) into macro.factor_series.

USAGE
    export DATABASE_URL="postgresql://postgres@localhost:5432/niftyoptions"
    python ingest_india_rates.py                        # fetches Repo Rate from FRED
    python ingest_india_rates.py --file in10y.csv       # ingests India 10Y from local CSV
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone
import pandas as pd

try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS macro;
CREATE TABLE IF NOT EXISTS macro.factor_series (
    factor      TEXT NOT NULL,
    obs_date    DATE NOT NULL,
    value       NUMERIC,
    source      TEXT,
    updated_at  TIMESTAMPTZ,
    PRIMARY KEY (factor, obs_date)
);
CREATE INDEX IF NOT EXISTS ix_factor_series ON macro.factor_series(factor, obs_date);
"""

def now_ts():
    return datetime.now(timezone.utc)

def fetch_upstox_gs10(token, from_date, to_date):
    import requests
    instrument_key = "NSE_INDEX|Nifty GS 10Yr Cln"
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json().get('data', {}).get('candles', [])
    out = []
    for candle in reversed(data): # Upstox returns newest first, reverse it
        d_str = candle[0][:10] # "2026-07-31T00:00:00+05:30" -> "2026-07-31"
        close_price = candle[4]
        out.append((d_str, float(close_price)))
    return out

def connect():
    dsn = os.getenv("DATABASE_URL")
    return psycopg.connect(dsn) if dsn else psycopg.connect()

def main():
    conn = connect()
    ts = now_ts()
    
    with conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
    conn.commit()
    
    # 2. Fetch IN10Y Index from Upstox
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'fetching'))
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
    try:
        from upstox_auth import get_upstox_token
        token = get_upstox_token()
    except ImportError:
        token = os.getenv("UPSTOX_ACCESS_TOKEN")
        
    if token:
        print("Fetching IN10Y Index (Nifty GS 10Yr Cln) from Upstox...")
        today_str = datetime.now().strftime("%Y-%m-%d")
        in10y_rows = fetch_upstox_gs10(token, "2018-01-01", today_str)
        if in10y_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO macro.factor_series (factor, obs_date, value, source, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (factor, obs_date) DO UPDATE SET "
                    "value = EXCLUDED.value, source = EXCLUDED.source, updated_at = EXCLUDED.updated_at",
                    [("IN10Y_INDEX", d, v, "Upstox:Nifty GS 10Yr Cln", ts) for d, v in in10y_rows])
            conn.commit()
            print(f"IN10Y_INDEX: upserted {len(in10y_rows)} obs, latest = {in10y_rows[-1][1]} on {in10y_rows[-1][0]}")
    else:
        print("No UPSTOX_ACCESS_TOKEN found. Skipping IN10Y_INDEX.")

    conn.close()

if __name__ == "__main__":
    main()
