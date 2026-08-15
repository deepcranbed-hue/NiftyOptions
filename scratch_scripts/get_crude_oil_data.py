import os
import requests
import pandas as pd
import json

from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
def fetch_crude_oil_candles(instrument_key="MCX_FO|560977", interval="1minute", to_date="2026-07-28", from_date="2026-07-27"):
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
    print(f"Requesting Crude Oil candles from Upstox: {url}")
    
    headers = {
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
        'Accept': 'application/json'
    }
    
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        print(f"Failed to fetch data. Status Code: {response.status_code}")
        print(response.text)
        return None
        
    data = response.json()
    candles = data.get("data", {}).get("candles", [])
    print(f"Successfully fetched {len(candles)} candles.")
    
    # Columns in Upstox candles: [timestamp, open, high, low, close, volume, open_interest]
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    return df

def main():
    # Active Crude Oil Future Contract Key (expiry: 19 Aug 2026)
    instrument_key = "MCX_FO|560977" 
    df = fetch_crude_oil_candles(instrument_key=instrument_key)
    
    if df is not None:
        output_file = "/Users/deepak/antigravity/NiftyOptions/scratch_scripts/crude_oil_candles.csv"
        df.to_csv(output_file, index=False)
        print(f"Saved candle data to: {output_file}")
        print("\n--- Recent 5 Candles ---")
        print(df.head())

if __name__ == "__main__":
    main()
