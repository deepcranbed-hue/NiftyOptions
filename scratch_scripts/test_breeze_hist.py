import sys
from breeze_connect import BreezeConnect

api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
session_token = "56232089"

breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

res = breeze.get_historical_data_v2(
    interval="30minute",
    from_date="2026-07-07T09:15:00.000Z",
    to_date="2026-07-07T15:30:00.000Z",
    stock_code="NIFTY",
    exchange_code="NFO",
    product_type="options",
    expiry_date="2026-07-07T06:00:00.000Z",
    right="call",
    strike_price="24300"
)
print("RAW RES:", res)
