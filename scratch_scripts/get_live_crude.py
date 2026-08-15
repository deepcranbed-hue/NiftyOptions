import requests
import json

from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
def get_live_crude_quotes():
    # Upstox V2 Live Quote API accepts comma-separated instrument keys
    instruments = "GLOBAL_INDICATOR|BZUSD,GLOBAL_INDICATOR|CLUSD"
    url = f"https://api.upstox.com/v2/market-quote/quotes?symbol={instruments}"
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'
    }
    
    print(f"[Upstox] Requesting live quotes: {url}")
    try:
        res = requests.get(url, headers=headers, timeout=10)
        print(f"[Upstox] Status Code: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            quotes = data.get("data", {})
            
            print("\n=== LIVE CRUDE QUOTES ===")
            for key, val in quotes.items():
                symbol = key.split("|")[-1]
                last_price = val.get("last_price", 0.0)
                converted_price = last_price / 10.0
                print(f"\n[{symbol} - {key}]")
                print(f"  Raw Last Price: {last_price}")
                print(f"  Standard Price: ${converted_price:.2f} / barrel")
                print(f"  Volume: {val.get('volume', 0)}")
            return quotes
        else:
            print(f"[Upstox] Error: {res.text}")
            return None
    except Exception as e:
        print(f"[Upstox] Exception: {e}")
        return None

if __name__ == "__main__":
    get_live_crude_quotes()
