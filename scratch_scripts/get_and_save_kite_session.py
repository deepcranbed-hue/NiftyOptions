import os
import sys
import json
import argparse
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime

def make_request(url, headers=None, method="POST", data=None):
    if data:
        data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()

def run():
    parser = argparse.ArgumentParser(description="Generate and Save Zerodha Kite Session Key (Pure REST)")
    parser.add_argument("--request_token", required=True, help="One-time request token from Kite login redirect")
    
    args = parser.parse_args()
    
    # Default credentials from test_kite_connect.py
    api_key = "x2ob63qqr9dhyj6o"
    api_secret = "10swuoyms3l3id21cfcwri8f0cj7sapn"
    
    try:
        print("Generating session via Kite Connect REST API...")
        
        # Calculate checksum: sha256(api_key + request_token + api_secret)
        raw_checksum = api_key + args.request_token + api_secret
        checksum = hashlib.sha256(raw_checksum.encode("utf-8")).hexdigest()
        
        token_url = "https://api.kite.trade/session/token"
        headers = {
            "X-Kite-Version": "3",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        post_data = {
            "api_key": api_key,
            "request_token": args.request_token,
            "checksum": checksum
        }
        
        # Exchange token
        res_bytes = make_request(token_url, headers=headers, method="POST", data=post_data)
        res_json = json.loads(res_bytes.decode("utf-8"))
        
        data = res_json.get("data", {})
        access_token = data.get("access_token")
        
        if not access_token:
            print("Error: No access token returned in response:")
            print(json.dumps(res_json, indent=2))
            sys.exit(1)
            
        # Create folder zerodhasession in workspace root
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        folder_path = os.path.join(base_dir, "zerodhasession")
        os.makedirs(folder_path, exist_ok=True)
        
        # Save token to file named session_<YYYY-MM-DD>.json
        today_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(folder_path, f"session_{today_str}.json")
        
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
            
        print(f"\nSuccess! Saved session key for {today_str} to:")
        print(file_path)
        print(f"Access Token: {access_token}")
        
    except urllib.error.HTTPError as http_err:
        err_msg = http_err.read().decode("utf-8")
        print(f"\nAPI Error: HTTP {http_err.code} returned by Kite API.")
        try:
            print(json.dumps(json.loads(err_msg), indent=2))
        except Exception:
            print(err_msg)
        sys.exit(1)
    except Exception as e:
        print(f"\nSession generation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run()
