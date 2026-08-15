import requests
import pandas as pd

import gzip
import io
import json

def search_upstox_instruments():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
    print(f"Downloading MCX instruments file from: {url}")
    
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        print(f"Failed to download instrument file. Status: {response.status_code}")
        return
        
    print("Parsing Gzipped JSON data...")
    try:
        compressed_file = io.BytesIO(response.content)
        with gzip.GzipFile(fileobj=compressed_file) as f:
            instruments = json.loads(f.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to decompress and parse JSON: {e}")
        return
        
    df = pd.DataFrame(instruments)
    print(f"Total MCX instruments found: {len(df)}")
    
    # Filter for crude
    matches = df[df['trading_symbol'].str.contains('CRUDE', case=False, na=False)]
    
    print(f"Found {len(matches)} matching instruments:")
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', 100)
    pd.set_option('display.width', 1000)
    print(matches[['instrument_key', 'trading_symbol', 'name', 'instrument_type', 'expiry']])

if __name__ == "__main__":
    search_upstox_instruments()
