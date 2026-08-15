#!/usr/bin/env python3
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_writable_db_path, resolve_pg_dsn
import os
import sys
import requests
import sqlite3
from datetime import datetime, timezone, timedelta

# Add parent paths so we can import upstox_auth
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))

from upstox_auth import get_upstox_token

IST = timezone(timedelta(hours=5, minutes=30))


def _flow_date(ts_ms):
    """Epoch millis -> the IST trading date, or None if it is not a trading day.

    WHY THE None BRANCH EXISTS
    --------------------------
    Upstox returns a record dated SUNDAY whose payload is byte-identical to the
    following Monday — 7 of 41 rows on 2026-08-09, six of them exact duplicates. Any
    weekly aggregate therefore double-counted Monday. FII/DII flows only exist on
    trading days, so a weekend row is wrong whatever produced it.

    The conversion is also done properly here: the previous version added a raw 5:30
    timedelta to a datetime still labelled UTC. It happened to yield the right date,
    but it is the kind of thing that is right by accident.
    """
    if not ts_ms:
        return None
    d = datetime.fromtimestamp(ts_ms / 1000, tz=IST).date()
    return None if d.weekday() >= 5 else d


def main():
    db_url = resolve_pg_dsn()
    token = get_upstox_token()
    if not token:
        print("Error: No Upstox access token found.")
        sys.exit(1)
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    fii_types = {
        "NSE_EQ|CASH": "fii_net",
        "NSE_FO|INDEX_FUTURES": "fii_idx_fut_net",
        "NSE_FO|STOCK_FUTURES": "fii_stk_fut_net",
        "NSE_FO|INDEX_OPTIONS": "fii_idx_opt_net",
        "NSE_FO|STOCK_OPTIONS": "fii_stk_opt_net",
    }
    
    # Map data by date
    flows_by_date = {}
    
    # Fetch FII data
    for data_type, net_key in fii_types.items():
        print(f"Fetching FII {data_type}...")
        res = requests.get(f"https://api.upstox.com/v2/market/fii?data_type={data_type}&interval=1D", headers=headers, timeout=15).json()
        if res.get("status") == "success":
            data = res.get("data", {}).get(data_type, [])
            for x in data:
                ts = x.get("time_stamp")
                if not ts: continue
                dt = _flow_date(ts)
                if dt is None:
                    continue
                if dt not in flows_by_date:
                    flows_by_date[dt] = {
                        "fii_buy": 0.0, "fii_sell": 0.0, "fii_net": 0.0,
                        "fii_idx_fut_net": 0.0, "fii_stk_fut_net": 0.0,
                        "fii_idx_opt_net": 0.0, "fii_stk_opt_net": 0.0,
                        "dii_buy": 0.0, "dii_sell": 0.0, "dii_net": 0.0
                    }
                buy = float(x.get("buy_amount", 0.0))
                sell = float(x.get("sell_amount", 0.0))
                net = buy - sell
                
                if data_type == "NSE_EQ|CASH":
                    flows_by_date[dt]["fii_buy"] = buy
                    flows_by_date[dt]["fii_sell"] = sell
                    flows_by_date[dt]["fii_net"] = net
                else:
                    flows_by_date[dt][net_key] = net

    # Fetch DII Cash
    print("Fetching DII daily cash flows...")
    dii_res = requests.get("https://api.upstox.com/v2/market/dii?data_type=NSE_EQ|CASH&interval=1D", headers=headers, timeout=15).json()
    if dii_res.get("status") == "success":
        dii_data = dii_res.get("data", {}).get("NSE_EQ|CASH", [])
        for x in dii_data:
            ts = x.get("time_stamp")
            if not ts: continue
            dt = _flow_date(ts)
            if dt is None:
                continue
            if dt not in flows_by_date:
                flows_by_date[dt] = {
                    "fii_buy": 0.0, "fii_sell": 0.0, "fii_net": 0.0,
                    "fii_idx_fut_net": 0.0, "fii_stk_fut_net": 0.0,
                    "fii_idx_opt_net": 0.0, "fii_stk_opt_net": 0.0,
                    "dii_buy": 0.0, "dii_sell": 0.0, "dii_net": 0.0
                }
            buy = float(x.get("buy_amount", 0.0))
            sell = float(x.get("sell_amount", 0.0))
            flows_by_date[dt]["dii_buy"] = buy
            flows_by_date[dt]["dii_sell"] = sell
            flows_by_date[dt]["dii_net"] = buy - sell

    # Connect and upsert to SQLite
    db_path = resolve_writable_db_path()
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        
        # Upsert
        count = 0
        now_str = datetime.now(timezone.utc).isoformat()
        for dt, val in flows_by_date.items():
            # Update columns, we must not overwrite existing data if we are missing it (though we fetch all together anyway).
            cur.execute("""
                INSERT OR REPLACE INTO fii_dii_flows (
                    flow_date, fii_buy, fii_sell, fii_net, 
                    dii_buy, dii_sell, dii_net, updated_at,
                    fii_idx_fut_net, fii_stk_fut_net, fii_idx_opt_net, fii_stk_opt_net
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dt.isoformat(), val["fii_buy"], val["fii_sell"], val["fii_net"],
                val["dii_buy"], val["dii_sell"], val["dii_net"], now_str,
                val["fii_idx_fut_net"], val["fii_stk_fut_net"], val["fii_idx_opt_net"], val["fii_stk_opt_net"]
            ))
            count += 1
        conn.commit()
        print(f"Upserted {count} FII/DII flow records into SQLite successfully.")

if __name__ == "__main__":
    main()
