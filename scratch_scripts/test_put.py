import sys, json
from breeze_connect import BreezeConnect
api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "s28*2~69700KUN944d63l#AN72Z66m38"
session_token = "56191246"
expiry_date = "2026-07-07T06:00:00.000Z"
breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)
try:
    res = breeze.get_option_chain_quotes(
        stock_code="NIFTY", exchange_code="NFO", product_type="options", expiry_date=expiry_date, right="Put"
    )
    if res.get("Success"):
        print("Found", len(res["Success"]), "puts")
    else:
        print("No success for Put:", res)
except Exception as e:
    print("Error:", e)
