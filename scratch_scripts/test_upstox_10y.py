#!/usr/bin/env python3
import requests
import sys
from datetime import datetime
import os
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'data_agent', 'fetching'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from upstox_auth import get_upstox_token
except ImportError:
    load_dotenv()
    def get_upstox_token():
        return os.getenv("UPSTOX_ACCESS_TOKEN")

token = get_upstox_token()
if not token:
    print("Error: No Upstox token found.")
    sys.exit(1)

# Nifty GS 10Yr Cln
instrument_key = "NSE_INDEX|Nifty GS 10Yr Cln"
# Nifty GS 10Yr
instrument_key_2 = "NSE_INDEX|Nifty GS 10Yr"
to_date = datetime.now().strftime("%Y-%m-%d")
from_date = "2020-01-01"

headers = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {token}',
}

print(f"Fetching historical data for {instrument_key}...")
url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
res = requests.get(url, headers=headers)
print("Status:", res.status_code)
if res.status_code == 200:
    data = res.json().get('data', {}).get('candles', [])
    if data:
        print(f"Found {len(data)} candles for Cln. Latest:")
        print(data[:3])
    else:
        print("No candles returned.")
else:
    print(res.text)

print(f"\nFetching historical data for {instrument_key_2}...")
url2 = f"https://api.upstox.com/v2/historical-candle/{instrument_key_2}/day/{to_date}/{from_date}"
res2 = requests.get(url2, headers=headers)
print("Status:", res2.status_code)
if res2.status_code == 200:
    data2 = res2.json().get('data', {}).get('candles', [])
    if data2:
        print(f"Found {len(data2)} candles for GS 10Yr. Latest:")
        print(data2[:3])
    else:
        print("No candles returned.")
else:
    print(res2.text)
