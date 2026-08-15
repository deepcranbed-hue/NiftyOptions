import sys
import json
from breeze_connect import BreezeConnect

def fetch_history():
    if len(sys.argv) < 5:
        print(json.dumps({"error": "Missing arguments"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    from_date = sys.argv[2]
    to_date = sys.argv[3]
    interval = sys.argv[4] # "1day" or "1minute"
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        response = breeze.get_historical_data_v2(
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            stock_code="NIFTY",
            exchange_code="NSE",
            product_type="cash"
        )
        
        if response and response.get("Success"):
            # Clean and map the data
            cleaned = []
            for item in response["Success"]:
                # Map datetime format from "2026-06-30 11:32:00" or similar to standard format if needed
                # We will just pass it out as-is and let backend or UI handle mapping.
                cleaned.append({
                    "date": item.get("datetime"),
                    "open": float(item.get("open", 0.0)),
                    "high": float(item.get("high", 0.0)),
                    "low": float(item.get("low", 0.0)),
                    "close": float(item.get("close", 0.0)),
                    "volume": int(item.get("volume", 0))
                })
            print(json.dumps({"success": True, "data": cleaned}))
        else:
            print(json.dumps({"error": f"API returned no success: {response}"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    fetch_history()
