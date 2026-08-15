import sys
import json
import time
from breeze_connect import BreezeConnect
from datetime import datetime

def fetch():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Missing arguments"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    expiry_date = sys.argv[2]
    symbol = sys.argv[3] if len(sys.argv) > 3 else "NIFTY"
    
    # Configure target strike window (ATM ± 10 strikes)
    WIND_STRIKES = 10
    STRIKE_STEP = 50.0 if symbol.upper() == "NIFTY" else 100.0
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        # 1. Fetch bulk option chain (ATM ± 5 strikes)
        response_call = breeze.get_option_chain_quotes(
            stock_code=symbol,
            exchange_code="NFO",
            product_type="options",
            expiry_date=expiry_date,
            right="Call"
        )
        response_put = breeze.get_option_chain_quotes(
            stock_code=symbol,
            exchange_code="NFO",
            product_type="options",
            expiry_date=expiry_date,
            right="Put"
        )
        
        merged_calls = response_call.get("Success", []) if response_call and isinstance(response_call.get("Success"), list) else []
        merged_puts = response_put.get("Success", []) if response_put and isinstance(response_put.get("Success"), list) else []
        
        # Find spot price to calculate ATM and backfill wider strikes
        spot_price = 0.0
        for item in (merged_calls + merged_puts):
            if item.get("spot_price"):
                try:
                    spot_price = float(item.get("spot_price"))
                    break
                except ValueError:
                    pass
                    
        # 2. Backfill outer strikes to reach ATM ± WIND_STRIKES
        if spot_price > 0:
            atm_strike = round(spot_price / STRIKE_STEP) * STRIKE_STEP
            target_strikes = [atm_strike + i * STRIKE_STEP for i in range(-WIND_STRIKES, WIND_STRIKES + 1)]
            
            existing_call_strikes = {float(item["strike_price"]) for item in merged_calls if "strike_price" in item}
            existing_put_strikes = {float(item["strike_price"]) for item in merged_puts if "strike_price" in item}
            
            for strike in target_strikes:
                # Fetch missing calls
                if strike not in existing_call_strikes:
                    try:
                        res = breeze.get_option_chain_quotes(
                            stock_code=symbol,
                            exchange_code="NFO",
                            product_type="options",
                            expiry_date=expiry_date,
                            right="Call",
                            strike_price=str(strike)
                        )
                        if res and isinstance(res.get("Success"), list) and len(res["Success"]) > 0:
                            merged_calls.extend(res["Success"])
                        time.sleep(0.05) # Rate limit padding
                    except Exception:
                        pass
                
                # Fetch missing puts
                if strike not in existing_put_strikes:
                    try:
                        res = breeze.get_option_chain_quotes(
                            stock_code=symbol,
                            exchange_code="NFO",
                            product_type="options",
                            expiry_date=expiry_date,
                            right="Put",
                            strike_price=str(strike)
                        )
                        if res and isinstance(res.get("Success"), list) and len(res["Success"]) > 0:
                            merged_puts.extend(res["Success"])
                        time.sleep(0.05) # Rate limit padding
                    except Exception:
                        pass
                        
        merged_data = merged_calls + merged_puts
        status = "complete"
        
        call_ok = len(merged_calls) > 0
        put_ok = len(merged_puts) > 0
        
        if not call_ok and not put_ok:
            status = "failed"
        elif not call_ok:
            status = "puts_only"
        elif not put_ok:
            status = "calls_only"
            
        print(json.dumps({"Success": merged_data, "Status": 200, "status": status, "Error": None}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    fetch()
