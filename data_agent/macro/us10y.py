#!/usr/bin/env python3
"""
us10y.py — fetch US macro factor series from Yahoo Finance into macro.factor_series.

The one factor missing from the app for the sector-attribution model is the
US 10-Year Treasury yield. Yahoo Finance serves it (ticker ^TNX) as a daily
series — full history in one request, then a daily append. Same shape works for
the dollar index and fed funds, so the fetcher is generic (--series).

Lands in a single long factor table the regression panel reads:
    macro.factor_series(factor, obs_date, value, source, updated_at)

USAGE
    export DATABASE_URL="postgresql://postgres@localhost:5432/niftyoptions"
    python us10y.py                       # backfill+update US10Y from yfinance
    python us10y.py --series DXY           # dollar index (DX-Y.NYB)
    python us10y.py --since 2020-01-01     # limit to recent

Runs on your machine (which has network). No API key required.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# factor name -> Yahoo Finance ticker. Extend as you add macro factors.
YF_TICKERS = {
    "US10Y": "^TNX",         # 10Y Treasury constant maturity
    "US02Y": "^IRX",         # 13-week T-bill (closest proxy easily available on YF, or ^FVX for 5Y)
    "NASDAQ": "^IXIC",       # Nasdaq Composite
    "CRUDE": "CL=F",         # WTI crude continuous
    "BRENT": "BZ=F",         # Brent crude continuous
    "DXY": "DX-Y.NYB",       # US Dollar Index
    "FEDFUNDS": "FF",        # fed funds? YF doesn't have a direct continuous rate, left as placeholder
}

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


def fetch_yfinance(ticker, since_date=None):
    import yfinance as yf
    import pandas as pd
    t = yf.Ticker(ticker)
    
    # We fetch max history if no since_date, otherwise just since that date
    if since_date:
        df = t.history(start=since_date)
    else:
        df = t.history(period="max")
        
    out = []
    if df.empty:
        return out
        
    for date, row in df.iterrows():
        d_str = date.strftime("%Y-%m-%d")
        v = row['Close']
        if pd.isna(v):
            continue
        out.append((d_str, float(v)))
    return out


def connect():
    dsn = os.getenv("DATABASE_URL")
    return psycopg.connect(dsn) if dsn else psycopg.connect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="US10Y", choices=list(YF_TICKERS),
                    help="factor to fetch (default US10Y)")
    ap.add_argument("--since", help="only keep observations on/after YYYY-MM-DD")
    args = ap.parse_args()

    factor = args.series
    ticker = YF_TICKERS[factor]
    source = f"YF:{ticker}"

    rows = fetch_yfinance(ticker, args.since)
    if not rows:
        sys.exit("No observations parsed.")

    conn = connect()
    conn.execute(SCHEMA_DDL)
    conn.commit()
    ts = now_ts()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO macro.factor_series (factor, obs_date, value, source, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (factor, obs_date) DO UPDATE SET "
            "value = EXCLUDED.value, source = EXCLUDED.source, updated_at = EXCLUDED.updated_at",
            [(factor, d, v, source, ts) for d, v in rows])
    conn.commit()

    latest_d, latest_v = rows[-1]
    print(f"{factor} ({source}): upserted {len(rows)} obs, "
          f"{rows[0][0]} -> {latest_d}, latest = {latest_v}")
    conn.close()


if __name__ == "__main__":
    main()
