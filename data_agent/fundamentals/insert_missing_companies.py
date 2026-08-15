
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_pg_dsn
import psycopg2
from datetime import datetime

NIFTY_50 = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK', 
    'BAJAJ-AUTO', 'BAJFINANCE', 'BAJAJFINSV', 'BEL', 'BHARTIARTL', 
    'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ZOMATO', 
    'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE', 'HINDALCO', 
    'HINDUNILVR', 'ICICIBANK', 'ITC', 'INFY', 'INDIGO', 
    'JSWSTEEL', 'JIOFIN', 'KOTAKBANK', 'LT', 'M&M', 
    'MARUTI', 'MAXHEALTH', 'NTPC', 'NESTLEIND', 'ONGC', 
    'POWERGRID', 'RELIANCE', 'SBILIFE', 'SHRIRAMFIN', 'SBIN', 
    'SUNPHARMA', 'TCS', 'TATACONSUM', 'TATAMOTORS', 'TATASTEEL', 
    'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO'
]

def main():
    conn = psycopg2.connect(resolve_pg_dsn())
    c = conn.cursor()
    
    c.execute("SELECT symbol FROM fundamentals.companies")
    existing_symbols = {row[0] for row in c.fetchall() if row[0]}
    
    missing = [sym for sym in NIFTY_50 if sym not in existing_symbols]
    print(f"Found {len(missing)} missing Nifty 50 companies.")
    
    if not missing:
        print("All companies already exist.")
        return
        
    records = []
    for sym in missing:
        dummy_isin = f"DUMMY_ISIN_{sym}"
        records.append((dummy_isin, sym, sym, datetime.now()))
        
    c.executemany("""
        INSERT INTO fundamentals.companies (isin, symbol, company_name, updated_at)
        VALUES (%s, %s, %s, %s)
    """, records)
    
    conn.commit()
    conn.close()
    
    print(f"Successfully inserted {len(missing)} companies with dummy ISINs.")

if __name__ == "__main__":
    main()
