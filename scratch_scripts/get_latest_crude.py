import requests
import json

from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
def get_latest_price(instrument_key):
    # Query for July 24, 2026
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/2026-07-24/2026-07-24"
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            candles = data.get("data", {}).get("candles", [])
            if candles:
                latest = candles[0]  # First element is the latest minute candle
                timestamp = latest[0]
                # Price components divided by 10
                o, h, l, c = latest[1]/10.0, latest[2]/10.0, latest[3]/10.0, latest[4]/10.0
                return {
                    "status": "success",
                    "timestamp": timestamp,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "raw_close": latest[4]
                }
            return {
                "status": "success",
                "detail": "Market session not started or no candles returned for this day yet"
            }
        return {"status": "error", "detail": f"HTTP {res.status_code}: {res.text}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

if __name__ == "__main__":
    print("=== LATEST CRUDE VALUES (JULY 27, 2026) ===")
    
    brent = get_latest_price("GLOBAL_INDICATOR|BZUSD")
    print("\n[Brent Crude (BZUSD)]")
    print(json.dumps(brent, indent=2))
    
    wti = get_latest_price("GLOBAL_INDICATOR|CLUSD")
    print("\n[WTI Crude (CLUSD)]")
    print(json.dumps(wti, indent=2))
