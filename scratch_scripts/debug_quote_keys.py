from breeze_connect import BreezeConnect
import json

def debug():
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    session_token = "56218283"
    
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    # Fetch Call option chain for Nifty near expiry
    res = breeze.get_option_chain_quotes(
        stock_code="NIFTY",
        exchange_code="NFO",
        product_type="options",
        expiry_date="2026-07-07T06:00:00.000Z",
        right="Call",
        strike_price="24400"
    )
    
    success_list = res.get("Success", [])
    if success_list:
        print("KEYS IN BREEZE OPTION QUOTE:")
        print(json.dumps(success_list[0], indent=2))
    else:
        print("NO DATA RETURNED:", res)

if __name__ == "__main__":
    debug()
