#!/usr/bin/env python3
"""
ingest_screener.py
Parses the downloaded Screener.in Excel files and upserts them into Postgres `fundamentals.financials`
using the exact long-format schema designed for Upstox compatibility.
"""
import os
import glob
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
from datetime import datetime

DB_PARAMS = {
    'dbname': 'niftyoptions',
    'user': 'deepak',
    'host': 'localhost',
    'port': '5432'
}

def to_float(val):
    try:
        if pd.isna(val) or val == '':
            return None
        return float(val)
    except:
        return None

def find_row_index(df, col, text):
    for idx, val in df[col].items():
        if pd.notna(val) and str(val).strip().upper().startswith(text.upper()):
            return idx
    return None

def process_section(df, start_idx, end_idx, date_idx):
    dates = []
    for col in df.columns[1:]:
        d = df.at[date_idx, col]
        if pd.notna(d):
            if isinstance(d, datetime):
                dates.append((col, d.date()))
            elif isinstance(d, str):
                try:
                    dates.append((col, datetime.strptime(d.strip()[:10], '%Y-%m-%d').date()))
                except:
                    pass

    row_map = {}
    for idx in range(start_idx, end_idx):
        key = str(df.at[idx, df.columns[0]]).strip().upper()
        if key and key != 'NAN':
            row_map[key] = idx
            
    return dates, row_map

def main():
    data_dir = Path(__file__).parent / 'screener_data'
    excel_files = glob.glob(str(data_dir / '*.xlsx'))
    
    if not excel_files:
        print("No Excel files found in screener_data/")
        return
        
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    
    # Pre-fetch ISIN mapping
    cur.execute("SELECT symbol, isin FROM fundamentals.companies")
    symbol_to_isin = {row[0]: row[1] for row in cur.fetchall()}
    
    records = []
    
    for file_path in excel_files:
        symbol = Path(file_path).stem
        isin = symbol_to_isin.get(symbol)
        if not isin:
            print(f"[-] Could not find ISIN for {symbol}. Skipping.")
            continue
            
        print(f"Parsing {symbol}...")
        try:
            df = pd.read_excel(file_path, sheet_name="Data Sheet")
        except Exception as e:
            print(f"  [-] Failed to read: {e}")
            continue
            
        pl_idx = find_row_index(df, df.columns[0], 'PROFIT & LOSS')
        bs_idx = find_row_index(df, df.columns[0], 'BALANCE SHEET')
        cf_idx = find_row_index(df, df.columns[0], 'CASH FLOW')
        q_idx = find_row_index(df, df.columns[0], 'QUARTERS')
        
        if pl_idx is None or bs_idx is None:
            print("  [-] Could not find essential sections")
            continue
            
        cf_idx = find_row_index(df, df.columns[0], 'CASH FLOW')
        derived_idx = find_row_index(df, df.columns[0], 'DERIVED')
            
        # 1. PROCESS ANNUAL
        ann_dates, pl_map = process_section(df, pl_idx + 2, q_idx if q_idx else bs_idx, pl_idx + 1)
        
        bs_end = cf_idx if cf_idx else (derived_idx if derived_idx else len(df))
        bs_dates, bs_map = process_section(df, bs_idx + 2, bs_end, bs_idx + 1)
        
        derived_dates, derived_map = [], {}
        if derived_idx is not None:
            derived_dates, derived_map = process_section(df, derived_idx + 1, len(df), pl_idx + 1)
        
        is_bank = symbol in ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB', 'FEDERALBNK', 'BANDHANBNK']
        basis = 'standalone' if is_bank else 'consolidated'
        
        for col, rdate in ann_dates:
            sales = to_float(df.at[pl_map.get('SALES', -1), col]) if 'SALES' in pl_map else None
            net_income = to_float(df.at[pl_map.get('NET PROFIT', -1), col]) if 'NET PROFIT' in pl_map else None
            shares = to_float(df.at[derived_map.get('ADJUSTED EQUITY SHARES IN CR', -1), col]) if 'ADJUSTED EQUITY SHARES IN CR' in derived_map else None
            
            period_label = rdate.strftime('%b %Y') # e.g. Mar 2026
            
            if sales is not None:
                records.append((isin, 'income', basis, 'yearly', 'full', 'revenue', period_label, rdate, sales, None, 'crore', datetime.now()))
            if net_income is not None:
                records.append((isin, 'income', basis, 'yearly', 'full', 'net_profit', period_label, rdate, net_income, None, 'crore', datetime.now()))
            if shares is not None:
                records.append((isin, 'balance', basis, 'yearly', 'full', 'shares', period_label, rdate, shares, None, 'crore', datetime.now()))
            if net_income is not None and shares is not None and shares > 0:
                eps = net_income / shares
                records.append((isin, 'income', basis, 'yearly', 'full', 'EPS - Diluted', period_label, rdate, eps, None, 'rs', datetime.now()))
                
        for col, rdate in bs_dates:
            reserves = to_float(df.at[bs_map.get('RESERVES', -1), col]) if 'RESERVES' in bs_map else None
            eq_cap = to_float(df.at[bs_map.get('EQUITY SHARE CAPITAL', -1), col]) if 'EQUITY SHARE CAPITAL' in bs_map else None
            
            period_label = rdate.strftime('%b %Y')
            
            if reserves is not None:
                records.append((isin, 'balance', basis, 'yearly', 'full', 'reserves', period_label, rdate, reserves, None, 'crore', datetime.now()))
            if eq_cap is not None:
                records.append((isin, 'balance', basis, 'yearly', 'full', 'equity_capital', period_label, rdate, eq_cap, None, 'crore', datetime.now()))
                
        # 2. PROCESS QUARTERLY
        if q_idx is not None:
            q_dates, q_map = process_section(df, q_idx + 2, bs_idx, q_idx + 1)
            for col, rdate in q_dates:
                sales = to_float(df.at[q_map.get('SALES', -1), col]) if 'SALES' in q_map else None
                net_income = to_float(df.at[q_map.get('NET PROFIT', -1), col]) if 'NET PROFIT' in q_map else None
                
                period_label = rdate.strftime('%b %Y')
                
                if sales is not None:
                    records.append((isin, 'income', basis, 'quarterly', 'summary', 'revenue', period_label, rdate, sales, None, 'crore', datetime.now()))
                if net_income is not None:
                    records.append((isin, 'income', basis, 'quarterly', 'summary', 'net_profit', period_label, rdate, net_income, None, 'crore', datetime.now()))

    print(f"\nExtracted {len(records)} records. Upserting to PostgreSQL...")
    
    insert_query = """
        INSERT INTO fundamentals.financials (
            isin, statement, basis, time_period, section, line_item, 
            period_label, period_end, value, change_pct, units, fetched_at
        ) VALUES %s
        ON CONFLICT (isin, statement, basis, time_period, section, line_item, period_label) 
        DO UPDATE SET
            period_end = EXCLUDED.period_end,
            value = EXCLUDED.value,
            fetched_at = EXCLUDED.fetched_at
    """
    
    execute_values(cur, insert_query, records)
    conn.commit()
    cur.close()
    conn.close()
    
    print("✅ Ingestion complete.")

if __name__ == '__main__':
    main()
