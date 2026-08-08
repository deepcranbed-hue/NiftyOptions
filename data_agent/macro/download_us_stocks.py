#!/usr/bin/env python3
import os
import sys
import argparse
from datetime import datetime, date, timezone
import pandas as pd
import yfinance as yf
import psycopg

def main():
    parser = argparse.ArgumentParser(description="Download daily close prices of US tech stocks/ADRs from yfinance into macro.factor_series.")
    parser.add_argument("--since", type=str, default="2025-01-02", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    db_url = os.environ.get("DATABASE_URL", "postgresql://localhost/niftyoptions")
    
    # Symbols to download
    # INFY is the NYSE ticker for Infosys ADR
    symbol_map = {
        # Bank macro indicators
        "SPY": "SPY",
        "^VIX": "VIX",
        "XLF": "XLF",
        "^IXIC": "NASDAQ",
        # IT macro indicators
        "ACN": "ACN",
        "CTSH": "CTSH",
        "CRM": "CRM",
        "INFY": "INFY_ADR",  # store as INFY_ADR to differentiate from local INFY stock
        "NVDA": "NVDA",
        "MU": "MU",
        "SMH": "SMH",
        "EPAM": "EPAM",
        "WIT": "WIT_ADR",    # store as WIT_ADR to differentiate from local WIT/WIPRO stock
        "IBM": "IBM",
        "ADBE": "ADBE",
        "MSFT": "MSFT",
        "XLK": "XLK",
        "SPY": "SPY",
        "^IXIC": "NASDAQ",
        "XLF": "XLF",
        "^VIX": "VIX"
    }

    print(f"Downloading daily prices since {args.since} from yfinance...")
    
    # We query from yfinance
    data_list = []
    for ticker, factor_name in symbol_map.items():
        try:
            print(f"Fetching {ticker}...")
            t = yf.Ticker(ticker)
            df = t.history(start=args.since, interval="1d")
            if df.empty:
                print(f"No data returned for {ticker}")
                continue
                
            for dt, row in df.iterrows():
                obs_date = dt.date()
                close_val = float(row["Close"])
                data_list.append((factor_name, obs_date, close_val, "yfinance"))
            print(f"Loaded {len(df)} rows for {ticker}")
        except Exception as err:
            print(f"Error fetching {ticker}: {err}")
            
    if not data_list:
        print("No data fetched.")
        return
        
    print(f"Upserting {len(data_list)} rows into PostgreSQL macro.factor_series...")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Create schema/table if not exists
            cur.execute("""
                CREATE SCHEMA IF NOT EXISTS macro;
                CREATE TABLE IF NOT EXISTS macro.factor_series (
                    factor       VARCHAR NOT NULL,
                    obs_date     DATE NOT NULL,
                    value        NUMERIC,
                    source       VARCHAR,
                    updated_at   TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (factor, obs_date)
                );
            """)
            
            # Batch insert/upsert
            count = 0
            for factor_name, obs_date, val, src in data_list:
                cur.execute("""
                    INSERT INTO macro.factor_series (factor, obs_date, value, source, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (factor, obs_date) DO UPDATE SET
                        value = EXCLUDED.value,
                        source = EXCLUDED.source,
                        updated_at = NOW();
                """, (factor_name, obs_date, val, src))
                count += 1
            print(f"Successfully upserted {count} rows.")

if __name__ == "__main__":
    main()
