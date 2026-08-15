import os
from breeze_connect import BreezeConnect

api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "s28*2~69700KUN944d63l#AN72Z66m38"
session_token = "56218283"
expiry_date = "2026-07-09T06:00:00.000Z"

print("Connecting...")
breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

print(f"Fetching Option Chain for NIFTY (Expiry: {expiry_date})...")
try:
    response = breeze.get_option_chain_quotes(
        stock_code="NIFTY",
        exchange_code="NFO",
        product_type="options",
        expiry_date=expiry_date,
        right="others"
    )
    print(response)
except Exception as e:
    print(f"Exception: {e}")
