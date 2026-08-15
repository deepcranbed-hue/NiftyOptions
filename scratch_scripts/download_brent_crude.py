import sys
import requests
import json

from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
def fetch_brent_crude_candles(to_date="2026-07-24", from_date="2026-07-24", access_token=None):
    token = access_token or UPSTOX_ACCESS_TOKEN
    instrument_key = "GLOBAL_INDICATOR|CLUSD"  # WTI Crude USD on Upstox
    interval = "1minute"
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    print(f"[Upstox] Fetching Brent Crude ({instrument_key}) via URL: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"[Upstox] Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            candles = data.get("data", {}).get("candles", [])
            print(f"[Upstox] Successfully fetched {len(candles)} Brent Crude candles.")
            if candles:
                print("[Upstox] Sample Candle (Timestamp, Open, High, Low, Close, Volume, OI):")
                print(json.dumps(candles[0], indent=2))
            return data
        else:
            print(f"[Upstox] Error response: {response.text}")
            return None
    except Exception as e:
        print(f"[Upstox] Exception during request: {e}")
        return None

if __name__ == "__main__":
    token_arg = sys.argv[1] if len(sys.argv) > 1 else None
    fetch_brent_crude_candles(access_token=token_arg)
