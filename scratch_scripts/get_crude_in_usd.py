import os
import requests
import pandas as pd
import json

from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
def fetch_candles(instrument_key, name, to_date="2026-07-28", from_date="2026-07-27"):
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
    headers = {
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
        'Accept': 'application/json'
    }
    print(f"Fetching {name} candles...")
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        print(f"Failed to fetch {name}. Status Code: {response.status_code}")
        return None
    data = response.json()
    candles = data.get("data", {}).get("candles", [])
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "open_interest"])
    # Convert timestamp to datetime for alignment
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def main():
    # Active Crude Oil contract
    crude_key = "MCX_FO|560977"
    # Active USDINR contract expiring July 29, 2026
    usdinr_key = "NCD_FO|1265"
    
    df_crude = fetch_candles(crude_key, "Crude Oil")
    df_usdinr = fetch_candles(usdinr_key, "USDINR")
    
    if df_crude is None or df_usdinr is None:
        print("Error: Could not retrieve both dataframes.")
        return
        
    print(f"Crude Oil candles: {len(df_crude)}, USDINR candles: {len(df_usdinr)}")
    
    # Select only timestamp and close prices for the conversion
    df_crude = df_crude[['timestamp', 'close']].rename(columns={'close': 'crude_inr'})
    df_usdinr = df_usdinr[['timestamp', 'close']].rename(columns={'close': 'usdinr'})
    
    # Merge datasets on timestamp
    merged = pd.merge(df_crude, df_usdinr, on='timestamp', how='left')
    
    # Forward-fill and backward-fill any missing exchange rate data (currency derivatives trade 9:00 AM - 5:00 PM, while commodities trade 9:00 AM - 11:30 PM)
    merged['usdinr'] = merged['usdinr'].ffill().bfill()
    
    # Calculate Crude Oil price in USD
    merged['crude_usd'] = (merged['crude_inr'] / merged['usdinr']).round(2)
    
    output_file = "/Users/deepak/antigravity/NiftyOptions/scratch_scripts/crude_oil_usd.csv"
    merged.to_csv(output_file, index=False)
    print(f"\n[SUCCESS] Conversion complete. Data saved to {output_file}")
    
    print("\n--- Recent 10 Combined Candles ---")
    print(merged.head(10))

if __name__ == "__main__":
    main()
