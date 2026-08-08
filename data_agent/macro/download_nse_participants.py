#!/usr/bin/env python3
import os
import sys
import time
import requests
import sqlite3
import pandas as pd
import io
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)

def main():
    db_path = os.environ.get("SQLITE_DB_PATH", "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }

    session = requests.Session()
    session.headers.update(headers)
    
    print("Fetching NSE homepage to acquire cookies...")
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except Exception as e:
        print(f"Warning: Failed to hit homepage for cookies: {e}")
        
    time.sleep(2)
    
    # We will try the last 365 days (1 year)
    today = datetime.now()
    dates_to_check = [(today - timedelta(days=i)) for i in range(365)]
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    new_inserts = 0
    requests_made = 0
    
    for dt in dates_to_check:
        # Check if we already have data for this date
        date_str_iso = dt.strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM participant_flows WHERE flow_date=?", (date_str_iso,))
        if cur.fetchone()[0] > 0:
            print(f"[{date_str_iso}] Data already exists. Skipping.")
            continue
            
        # Format for NSE URL (DDMMYYYY)
        nse_date_str = dt.strftime("%d%m%Y")
        url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{nse_date_str}.csv"
        
        print(f"[{date_str_iso}] Fetching from NSE: {url}")
        requests_made += 1
        
        try:
            res = session.get(url, timeout=10)
        except Exception as e:
            print(f"  -> Error fetching: {e}")
            time.sleep(15)
            continue
            
        if res.status_code == 200:
            try:
                # The CSV has a descriptive first line, headers on the second line.
                df = pd.read_csv(io.StringIO(res.text), skiprows=1)
                
                # Column names often have trailing spaces
                df.columns = df.columns.str.strip()
                
                records = []
                now_str = datetime.now().isoformat()
                for _, row in df.iterrows():
                    participant = str(row.get('Client Type', '')).strip()
                    if not participant:
                        continue
                        
                    records.append((
                        date_str_iso,
                        participant,
                        int(row.get('Future Index Long', 0)),
                        int(row.get('Future Index Short', 0)),
                        int(row.get('Future Stock Long', 0)),
                        int(row.get('Future Stock Short', 0)),
                        int(row.get('Option Index Call Long', 0)),
                        int(row.get('Option Index Call Short', 0)),
                        int(row.get('Option Index Put Long', 0)),
                        int(row.get('Option Index Put Short', 0)),
                        int(row.get('Option Stock Call Long', 0)),
                        int(row.get('Option Stock Call Short', 0)),
                        int(row.get('Option Stock Put Long', 0)),
                        int(row.get('Option Stock Put Short', 0)),
                        int(row.get('Total Long Contracts', 0)),
                        int(row.get('Total Short Contracts', 0)),
                        now_str
                    ))
                
                cur.executemany("""
                    INSERT OR REPLACE INTO participant_flows (
                        flow_date, participant_type,
                        idx_fut_long, idx_fut_short, stk_fut_long, stk_fut_short,
                        idx_opt_call_long, idx_opt_call_short, idx_opt_put_long, idx_opt_put_short,
                        stk_opt_call_long, stk_opt_call_short, stk_opt_put_long, stk_opt_put_short,
                        total_long, total_short, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
                conn.commit()
                print(f"  -> Success! Inserted {len(records)} records for {date_str_iso}.")
                new_inserts += 1
            except Exception as e:
                print(f"  -> Error parsing CSV: {e}")
        elif res.status_code == 404:
            print(f"  -> 404 Not Found (likely weekend/holiday)")
        elif res.status_code == 403:
            print(f"  -> 403 Forbidden! NSE blocked the request. Halting scraper.")
            break
        else:
            print(f"  -> Unexpected status code: {res.status_code}")
            
        # Check if we need a long pause to avoid blocks
        if requests_made >= 30:
            print(f"  -> Reached {requests_made} requests. Taking a long 3-minute pause to avoid IP bans...")
            time.sleep(180)
            requests_made = 0
            # Completely wipe the old session and create a new one to get fresh cookies
            print("  -> Creating a completely new browser session to clear old cookies...")
            session = requests.Session()
            session.headers.update(headers)
            try:
                session.get("https://www.nseindia.com", timeout=10)
            except Exception as e:
                print(f"  -> Warning: Failed to hit homepage for new cookies: {e}")
        else:
            print("  -> Sleeping for 15 seconds to respect NSE servers...")
            time.sleep(15)

    conn.close()
    print(f"\nFinished! Inserted new data for {new_inserts} days.")

if __name__ == "__main__":
    main()
