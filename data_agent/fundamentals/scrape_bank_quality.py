#!/usr/bin/env python3
import os
import io
import sys
import argparse
import requests
import psycopg
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

BANKS = [
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 
    'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB', 
    'FEDERALBNK', 'BANDHANBNK'
]

def connect():
    dsn = os.getenv("DATABASE_URL")
    return psycopg.connect(dsn) if dsn else psycopg.connect()

def parse_period_label(label):
    """Convert 'Jun 2023' to '2023-06-30'"""
    try:
        dt = datetime.strptime(label, "%b %Y")
        # Go to last day of month
        if dt.month in [1,3,5,7,8,10,12]:
            day = 31
        elif dt.month in [4,6,9,11]:
            day = 30
        else:
            day = 29 if dt.year % 4 == 0 else 28
        return dt.replace(day=day).strftime("%Y-%m-%d")
    except ValueError:
        return None

def scrape_bank(symbol, sessionid):
    url = f"https://www.screener.in/company/{symbol}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    cookies = {"sessionid": sessionid} if sessionid else {}
    
    print(f"Fetching {symbol}...")
    res = requests.get(url, headers=headers, cookies=cookies)
    if res.status_code != 200:
        print(f"  HTTP {res.status_code} for {symbol}")
        return []
        
    soup = BeautifulSoup(res.content, "html.parser")
    quarters_section = soup.find("section", id="quarters")
    if not quarters_section:
        print(f"  No quarters section found for {symbol}")
        return []
        
    table = quarters_section.find("table")
    if not table:
        print(f"  No table in quarters section for {symbol}")
        return []
        
    df = pd.read_html(io.StringIO(str(table)))[0]
    # The first column is unnamed, let's call it "Metric"
    df.rename(columns={df.columns[0]: "Metric"}, inplace=True)
    
    # We want Gross NPA %, Net NPA %, Financing Margin %
    # They might have non-breaking spaces or trailing symbols
    gnpa_row = None
    nnpa_row = None
    nim_row = None
    
    for _, row in df.iterrows():
        metric = str(row["Metric"]).strip().replace('\xa0', ' ')
        if "Gross NPA" in metric:
            gnpa_row = row
        elif "Net NPA" in metric:
            nnpa_row = row
        elif "Financing Margin" in metric:
            nim_row = row
            
    if gnpa_row is None and nnpa_row is None and nim_row is None:
        print(f"  Could not find NPA/NIM rows for {symbol}")
        return []
        
    # Build list of dicts per period
    periods = [c for c in df.columns if c != "Metric" and "TTM" not in c]
    
    records = []
    for p in periods:
        period_end = parse_period_label(p)
        if not period_end:
            continue
            
        gnpa = gnpa_row[p] if gnpa_row is not None else None
        nnpa = nnpa_row[p] if nnpa_row is not None else None
        nim = nim_row[p] if nim_row is not None else None
        
        # Clean % signs and convert to float
        def clean_val(v):
            if pd.isna(v) or str(v).strip() == "" or str(v).strip() == "-":
                return None
            try:
                return float(str(v).replace("%", "").replace(",", "").strip())
            except ValueError:
                return None
                
        gnpa_pct = clean_val(gnpa)
        nnpa_pct = clean_val(nnpa)
        nim_pct = clean_val(nim)
        
        # Compute PCR
        pcr_pct = None
        if gnpa_pct is not None and nnpa_pct is not None and gnpa_pct > 0:
            pcr_pct = ((gnpa_pct - nnpa_pct) / gnpa_pct) * 100.0
            
        # Only keep if we have at least one valid metric
        if gnpa_pct is not None or nnpa_pct is not None or nim_pct is not None:
            records.append({
                "symbol": symbol,
                "period_end": period_end,
                "gnpa_pct": gnpa_pct,
                "nnpa_pct": nnpa_pct,
                "pcr_pct": round(pcr_pct, 2) if pcr_pct is not None else None,
                "nim_pct": nim_pct
            })
            
    return records

def main():
    load_dotenv()
    sessionid = os.getenv('SCREENER_SESSION_ID')
    
    conn = connect()
    ts = datetime.now()
    
    total_upserted = 0
    for bank in BANKS:
        records = scrape_bank(bank, sessionid)
        if not records:
            continue
            
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""
                    INSERT INTO fundamentals.asset_quality (
                        symbol, period_end, gnpa_pct, nnpa_pct, pcr_pct, nim_pct, fetched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, period_end) DO UPDATE SET
                        gnpa_pct = EXCLUDED.gnpa_pct,
                        nnpa_pct = EXCLUDED.nnpa_pct,
                        pcr_pct = EXCLUDED.pcr_pct,
                        nim_pct = EXCLUDED.nim_pct,
                        fetched_at = EXCLUDED.fetched_at
                """, (
                    r["symbol"], r["period_end"], 
                    r["gnpa_pct"], r["nnpa_pct"], r["pcr_pct"], r["nim_pct"], ts
                ))
        conn.commit()
        print(f"  -> Upserted {len(records)} periods for {bank}")
        total_upserted += len(records)
        
    print(f"Done. Total periods upserted: {total_upserted}")
    conn.close()

if __name__ == "__main__":
    main()
