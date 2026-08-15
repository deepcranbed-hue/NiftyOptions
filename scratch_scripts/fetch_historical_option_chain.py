import sys
import json
import sqlite3
import math
import time
from datetime import datetime, timedelta, timezone
import urllib.request
import os

_original_urlopen = urllib.request.urlopen
def _patched_urlopen(url, *args, **kwargs):
    if isinstance(url, str) and "SecurityMaster.zip" in url:
        local_path = "/Users/deepak/antigravity/NiftyOptions/SecurityMaster.zip"
        if not os.path.exists(local_path):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with _original_urlopen(req, timeout=30) as resp:
                with open(local_path, "wb") as f:
                    f.write(resp.read())
        return open(local_path, "rb")
    return _original_urlopen(url, *args, **kwargs)
urllib.request.urlopen = _patched_urlopen

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "breeze_env", "lib", "python3.9", "site-packages"))
from breeze_connect import BreezeConnect

# Import our save utility
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chain_store import save_from_json_rows, DB_PATH
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'data_agent'))
from credentials import breeze_creds as _breeze_creds


def backfill():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Missing arguments. Usage: python script.py <session_token> <expiry_date> <symbol> [interval]"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    expiry_date = sys.argv[2]
    symbol = sys.argv[3] if len(sys.argv) > 3 else "NIFTY"
    interval = sys.argv[4] if len(sys.argv) > 4 else "1minute"
    start_arg = sys.argv[5] if len(sys.argv) > 5 else ""
    end_arg = sys.argv[6] if len(sys.argv) > 6 else ""
    
    api_key, api_secret = _breeze_creds()
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        # 1. Determine current spot price to center our strikes
        # Note: Index like NIFTY uses exchange_code="NSE" but can return different quote key structures
        quote_res = breeze.get_quotes(stock_code=symbol, exchange_code="NSE", product_type="cash")
        if not quote_res or not quote_res.get("Success") or not len(quote_res["Success"]) > 0:
            print(json.dumps({"error": f"Failed to retrieve quotes response for {symbol}. Response was: {quote_res}"}))
            return
            
        first_quote = quote_res["Success"][0]
        # Look for ltp, last, close, or open in order of preference
        spot_val = None
        for key in ["last", "ltp", "last_price", "close", "open"]:
            if key in first_quote and first_quote[key] is not None:
                try:
                    spot_val = float(first_quote[key])
                    break
                except ValueError:
                    pass
                    
        if spot_val is None:
            print(json.dumps({"error": f"Could not find valid price key in quote response: {first_quote}"}))
            return
            
        spot = spot_val
        strike_step = 50 if "NIFTY" in symbol.upper() else 100
        
        # 2. Determine time range (Automatic watermark detection or custom dates)
        # Note: Breeze API ignores "Z" and treats input as local IST (9:15 AM - 3:30 PM)
        db_watermark = None
        try:
            conn = sqlite3.connect(DB_PATH)
            q = """
                SELECT MAX(c.captured_at) 
                FROM captures c 
                JOIN chain_rows r ON c.capture_id = r.capture_id 
                WHERE r.expiry = ? OR r.expiry LIKE ?
            """
            row = conn.execute(q, (expiry_date, f"{expiry_date[:10]}%")).fetchone()
            if row and row[0]:
                db_watermark = row[0]
            conn.close()
        except Exception as e:
            sys.stderr.write(f"Failed to query watermark: {e}\n")

        if start_arg and end_arg:
            start_date_obj = datetime.strptime(start_arg[:10], "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_arg[:10], "%Y-%m-%d").date()
        else:
            now_utc = datetime.now(timezone.utc)
            now_ist = now_utc + timedelta(hours=5, minutes=30)
            end_date_obj = now_ist.date()
            
            if db_watermark:
                dt_utc = datetime.fromisoformat(db_watermark.replace('Z', '+00:00'))
                dt_ist = dt_utc + timedelta(hours=5, minutes=30)
                if dt_ist.hour > 15 or (dt_ist.hour == 15 and dt_ist.minute >= 30):
                    start_date_obj = (dt_ist + timedelta(days=1)).date()
                else:
                    start_date_obj = dt_ist.date()
                sys.stderr.write(f"DB watermark found for expiry {expiry_date}: {db_watermark}. Syncing starting from: {start_date_obj}\n")
            else:
                start_date_obj = end_date_obj - timedelta(days=5)
        
        snapshots = {}
        prev_oi_map = {}
        
        # Loop through each day individually to comply with Breeze chunking limits
        curr_date = start_date_obj
        while curr_date <= end_date_obj:
            if curr_date.weekday() >= 5: # Skip weekends
                curr_date += timedelta(days=1)
                continue
                
            # Check if option chain data for curr_date and this expiry already exists in DB
            has_data = False
            try:
                conn = sqlite3.connect(DB_PATH)
                q = """
                    SELECT COUNT(*) 
                    FROM captures c 
                    JOIN chain_rows r ON c.capture_id = r.capture_id 
                    WHERE c.captured_at LIKE ? AND (r.expiry = ? OR r.expiry LIKE ?)
                """
                count = conn.execute(q, (f"{curr_date}%", expiry_date, f"{expiry_date[:10]}%")).fetchone()[0]
                if count > 0:
                    has_data = True
                conn.close()
            except Exception as e:
                sys.stderr.write(f"Failed to check existing data count for {curr_date}: {e}\n")

            if has_data:
                sys.stderr.write(f"Date {curr_date} already has option chain data for expiry {expiry_date}. Skipping...\n")
                curr_date += timedelta(days=1)
                continue
                
            # Center ATM strikes dynamically for each day based on that day's spot close
            day_spot = None
            try:
                conn = sqlite3.connect(DB_PATH)
                db_bar = conn.execute("SELECT close FROM price_bars WHERE symbol=? AND ts LIKE ? LIMIT 1", (symbol, f"{curr_date}%")).fetchone()
                if db_bar:
                    day_spot = float(db_bar[0])
                conn.close()
            except:
                pass
                
            if not day_spot:
                try:
                    spot_res = breeze.get_historical_data_v2(
                        interval="1day",
                        from_date=f"{curr_date}T09:15:00.000Z",
                        to_date=f"{curr_date}T15:30:00.000Z",
                        stock_code=symbol,
                        exchange_code="NSE",
                        product_type="cash"
                    )
                    if spot_res and spot_res.get("Success") and len(spot_res["Success"]) > 0:
                        day_spot = float(spot_res["Success"][0].get("close") or spot_res["Success"][0].get("open"))
                except Exception as spot_err:
                    sys.stderr.write(f"Failed to fetch daily spot for {curr_date}: {spot_err}\n")
                    
            if not day_spot:
                day_spot = spot
                
            day_atm = round(day_spot / strike_step) * strike_step
            day_strikes = [day_atm + (i * strike_step) for i in range(-10, 11)]
            sys.stderr.write(f"Date {curr_date} spot: {day_spot}, ATM strike: {day_atm}, strikes range: {day_strikes[0]} - {day_strikes[-1]}\n")
            
            from_str = f"{curr_date}T09:15:00.000Z"
            to_str = f"{curr_date}T15:30:00.000Z"
            sys.stderr.write(f"Fetching option chain for {curr_date}...\n")
            
            for strike in day_strikes:
                for right in ["Call", "Put"]:
                    try:
                        res = breeze.get_historical_data_v2(
                            interval=interval,
                            from_date=from_str,
                            to_date=to_str,
                            stock_code=symbol,
                            exchange_code="NFO",
                            product_type="options",
                            expiry_date=expiry_date,
                            right=right.lower(),
                            strike_price=str(strike)
                        )
                        
                        rows = res.get("Success") if res else None
                        if not rows:
                            continue
                            
                        for r in rows:
                            ts = r.get("datetime")
                            if not ts:
                                continue
                                
                            strike_key = (float(strike), right.upper())
                            curr_oi = float(r.get("open_interest", 0.0) or 0.0)
                            prev_oi = prev_oi_map.get(strike_key)
                            oi_chg_pct = 0.0
                            if prev_oi is not None:
                                oi_chg = curr_oi - prev_oi
                                oi_chg_pct = round((oi_chg / prev_oi) * 100, 1) if prev_oi > 0 else 0.0
                            prev_oi_map[strike_key] = curr_oi
                            
                            chain_row = {
                                "strike_price": float(strike),
                                "option_type": right.upper(),
                                "call_ltp" if right == "Call" else "put_ltp": float(r["close"]),
                                "call_oi" if right == "Call" else "put_oi": curr_oi,
                                "call_oichg" if right == "Call" else "put_oichg": oi_chg_pct,
                                "call_volume" if right == "Call" else "put_volume": float(r.get("volume", 0.0) or 0.0)
                            }
                            snapshots.setdefault(ts, []).append(chain_row)
                        time.sleep(0.05) # Rate limit padding
                    except Exception as e:
                        sys.stderr.write(f"Error fetching {strike} {right} for {curr_date}: {str(e)}\n")
            
            curr_date += timedelta(days=1)
                    
        # 3. Save each compiled hourly snapshot as a separate Capture in DB
        saved_count = 0
        for ts_str, rows in snapshots.items():
            if len(rows) < 4:
                continue
                
            try:
                dt_ist = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                # Convert local IST time in Breeze response to UTC for database storage
                dt_utc = dt_ist - timedelta(hours=5, minutes=30)
                captured_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                # Merge strikes (Call & Put rows matching same strike) into single unified schema
                merged_strikes = {}
                for r in rows:
                    stk = r["strike_price"]
                    merged_strikes.setdefault(stk, {
                        "strike": stk,
                        "strike_price": stk,
                        "call_ltp": 0.0, "call_oi": 0, "call_volume": 0, "call_oichg": 0.0,
                        "put_ltp": 0.0, "put_oi": 0, "put_volume": 0, "put_oichg": 0.0
                    })
                    if r["option_type"] == "CALL":
                        merged_strikes[stk].update({
                            "call_ltp": r["call_ltp"],
                            "call_oi": int(r["call_oi"]),
                            "call_oichg": r["call_oichg"],
                            "call_volume": int(r["call_volume"])
                        })
                    else:
                        merged_strikes[stk].update({
                            "put_ltp": r["put_ltp"],
                            "put_oi": int(r["put_oi"]),
                            "put_oichg": r["put_oichg"],
                            "put_volume": int(r["put_volume"])
                        })
                        
                final_rows = list(merged_strikes.values())
                
                # Query historical spot close price from price_bars at this timestamp
                snapshot_spot = None
                
                # For closing captures of the day (3:00 PM IST / 09:30 UTC or later), 
                # prefer the official exchange settled close price from get_quotes over raw minute bar close.
                is_closing_capture = False
                try:
                    cap_hour_ist = dt_ist.hour
                    cap_min_ist = dt_ist.minute
                    if cap_hour_ist == 15 and cap_min_ist >= 0:
                        is_closing_capture = True
                except:
                    pass
                    
                if is_closing_capture and spot is not None:
                    snapshot_spot = spot
                else:
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        hist_sym = "NIFTY" if symbol.upper() == "NIFTY" else symbol
                        # Search for closest price bar leading up to this capture timestamp
                        bar_row = conn.execute("""
                            SELECT ts, close FROM price_bars 
                            WHERE symbol=? AND ts <= ? 
                            ORDER BY ts DESC LIMIT 1
                        """, (hist_sym, captured_at)).fetchone()
                        
                        if bar_row:
                            bar_ts, bar_close = bar_row[0], float(bar_row[1])
                            # Verify the index price bar belongs to the same trading day and within 2 minutes (120s)
                            cap_dt = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
                            bar_dt = datetime.fromisoformat(bar_ts.replace('Z', '+00:00'))
                            diff_sec = abs((cap_dt - bar_dt).total_seconds())
                            if bar_ts[:10] == captured_at[:10] and diff_sec <= 90.0:
                                snapshot_spot = bar_close
                                
                        conn.close()
                    except Exception as db_err:
                        raise ValueError(f"Database error reading spot price: {str(db_err)}")
                
                # If missing or stale, trigger on-demand Breeze NIFTY index download
                if snapshot_spot is None:
                    try:
                        sys.stderr.write(f"Index bar for {captured_at} not found or stale. Triggering on-demand Breeze NIFTY index download...\n")
                        day_str = captured_at[:10]
                        from_str = f"{day_str}T00:00:00.000Z"
                        to_str = f"{day_str}T23:59:59.000Z"
                        res_bars = breeze.get_historical_data_v2(
                            interval="1minute",
                            from_date=from_str,
                            to_date=to_str,
                            stock_code="NIFTY",
                            exchange_code="NSE",
                            product_type="cash"
                        )
                        if res_bars and res_bars.get("Success"):
                            formatted_rows = []
                            for item in res_bars["Success"]:
                                dt_str = item.get("datetime")
                                if dt_str:
                                    try:
                                        dt_item = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                                        dt_item_utc = dt_item - timedelta(hours=5, minutes=30)
                                        ts_iso = dt_item_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                                    except Exception:
                                        ts_iso = dt_str
                                else:
                                    continue
                                formatted_rows.append((
                                    ts_iso,
                                    float(item.get("open", 0.0)),
                                    float(item.get("high", 0.0)),
                                    float(item.get("low", 0.0)),
                                    float(item.get("close", 0.0)),
                                    float(item.get("volume", 0.0))
                                ))
                            if formatted_rows:
                                from bar_store import save_bars
                                save_bars(formatted_rows, symbol="NIFTY")
                            
                            # Query database again after sync
                            conn = sqlite3.connect(DB_PATH)
                            bar_row = conn.execute("""
                                SELECT ts, close FROM price_bars 
                                WHERE symbol=? AND ts <= ? 
                                ORDER BY ts DESC LIMIT 1
                            """, (hist_sym, captured_at)).fetchone()
                            if bar_row:
                                bar_ts, bar_close = bar_row[0], float(bar_row[1])
                                cap_dt = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
                                bar_dt = datetime.fromisoformat(bar_ts.replace('Z', '+00:00'))
                                diff_sec = abs((cap_dt - bar_dt).total_seconds())
                                if bar_ts[:10] == captured_at[:10] and diff_sec <= 90.0:
                                    snapshot_spot = bar_close
                            conn.close()
                    except Exception as sync_err:
                        sys.stderr.write(f"Failed to perform on-demand index sync: {sync_err}\n")
                
                if snapshot_spot is None:
                    # Throw error to force strict alignment
                    raise ValueError(f"No Nifty index price bar found in database within 2 minutes of {captured_at}. Skipping same spot price duplication.")
                
                save_from_json_rows(
                    final_rows,
                    expiry=expiry_date,
                    spot=snapshot_spot,
                    vix=12.0,
                    note=f"Historical hourly backfill",
                    exchange_code="NFO",
                    underlying=symbol,
                    captured_at=captured_at,
                    status="complete",
                    trigger="manual"
                )
                saved_count += 1
            except ValueError as val_err:
                # Bubble up index sync errors immediately
                print(json.dumps({"error": str(val_err)}))
                sys.exit(1)
            except Exception as e:
                sys.stderr.write(f"Unexpected error in snapshot loop: {str(e)}\n")
                
        print(json.dumps({"success": True, "snapshots_saved": saved_count}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    backfill()
