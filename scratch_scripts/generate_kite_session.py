import sys
import argparse
import json
from kiteconnect import KiteConnect

def generate_session():
    parser = argparse.ArgumentParser(description="Generate Zerodha Kite Access Token")
    parser.add_argument("--api_key", required=True, help="Your Kite API Key")
    parser.add_argument("--api_secret", required=True, help="Your Kite API Secret")
    parser.add_argument("--request_token", required=True, help="Request Token received after login redirect")
    
    args = parser.parse_args()
    
    try:
        print(f"Initializing KiteConnect with API key: {args.api_key}...")
        kite = KiteConnect(api_key=args.api_key)
        
        print("Exchanging request token for access token...")
        data = kite.generate_session(args.request_token, api_secret=args.api_secret)
        
        access_token = data.get("access_token")
        if not access_token:
            print(json.dumps({"success": False, "error": "No access_token returned in session payload."}))
            sys.exit(1)
            
        print("\nSuccess! Generated Access Token:")
        print(access_token)
        print("\nFull Session Data:")
        print(json.dumps(data, indent=2, default=str))
        
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Failed to generate session: {str(e)}"}))
        sys.exit(1)

if __name__ == "__main__":
    generate_session()
