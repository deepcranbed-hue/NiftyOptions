import sys
import json
from breeze_connect import BreezeConnect

api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
session_token = "56347042"

breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

# Try get_option_chain_quotes without expiry_date to see if it lists them
try:
    res = breeze.get_option_chain_quotes(
        stock_code="NIFTY",
        exchange_code="NFO",
        product_type="options",
        right="Call"
    )
    print("API RESPONSE KEYS:", res.keys() if hasattr(res, "keys") else "None")
    if res and "Success" in res and isinstance(res["Success"], list) and len(res["Success"]) > 0:
        expiries = sorted(list(set(item.get("expiry_date") for item in res["Success"] if item.get("expiry_date"))))
        print("FOUND EXPIRIES:", expiries)
        print("FIRST ITEM:", res["Success"][0])
    else:
        print("No success or empty list:", res)
except Exception as e:
    print("Error:", str(e))
