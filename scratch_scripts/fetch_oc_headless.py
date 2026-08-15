import requests
import json
import pandas as pd
import time
from datetime import datetime

def fetch_nse_option_chain(symbol="NIFTY"):
    print(f"Fetching Option Chain for {symbol}...")
    
    # Endpoints used by NSE
    url_oc = "https://www.nseindia.com/option-chain"
    url_api = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    
    # Headers to mimic a real browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': url_oc
    }
    
    session = requests.Session()
    
    try:
        # Step 1: Hit the main option chain page to acquire valid session cookies
        print("1. Establishing session and fetching cookies...")
        session.get(url_oc, headers=headers, timeout=10)
        
        # Step 2: Hit the API endpoint
        print("2. Fetching API data...")
        response = session.get(url_api, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"Error: NSE API returned status code {response.status_code}.")
            print("Note: If you get 401/404, NSE is blocking automated requests from this IP.")
            return
            
        data = response.json()
        records = data.get('records', {}).get('data', [])
        
        if not records:
            print("No records found in the API response.")
            return
            
        print(f"Success! Fetched {len(records)} strike records.")
        
        # Step 3: Flatten into a CSV format
        rows = []
        for rec in records:
            ce = rec.get('CE', {})
            pe = rec.get('PE', {})
            rows.append({
                'Strike_Price': rec.get('strikePrice'),
                'Expiry_Date': rec.get('expiryDate'),
                'CE_LTP': ce.get('lastPrice', 0),
                'CE_OI': ce.get('openInterest', 0),
                'CE_Change_in_OI': ce.get('changeinOpenInterest', 0),
                'CE_Implied_Vol': ce.get('impliedVolatility', 0),
                'PE_LTP': pe.get('lastPrice', 0),
                'PE_OI': pe.get('openInterest', 0),
                'PE_Change_in_OI': pe.get('changeinOpenInterest', 0),
                'PE_Implied_Vol': pe.get('impliedVolatility', 0)
            })
            
        df = pd.DataFrame(rows)
        
        # Save to CSV
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{symbol}_Option_Chain_{timestamp}.csv"
        df.to_csv(filename, index=False)
        print(f"Done! Option chain saved to {filename}")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    fetch_nse_option_chain("NIFTY")
