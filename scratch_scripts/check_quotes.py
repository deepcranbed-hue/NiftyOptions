from breeze_connect import BreezeConnect
import json

session_token = "56199493"
api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "584F70+Z075364Cz35y6O9931Y16I387"

breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

print("Fetching quotes for RELIND:")
res = breeze.get_quotes(stock_code="RELIND", exchange_code="NSE", product_type="cash")
if res and res.get("Success"):
    first = res["Success"][0]
    print("Keys returned by get_quotes:")
    print(list(first.keys()))
    print("Values for some keys:")
    for k in ["symbol", "open", "last", "high", "low", "change", "close", "pe", "eps", "dividend"]:
        if k in first:
            print(f"{k}: {first[k]}")
else:
    print("Failed or empty response:", res)
