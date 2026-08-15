import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from breeze_connect import BreezeConnect

breeze = BreezeConnect(api_key='999407AZb39Vu3D&9X405B977330807K')
breeze.generate_session(api_secret='584F70+Z075364Cz35y6O9931Y16I387', session_token='56539983')

print("Fetching NIFTY FUT...")
res = breeze.get_historical_data_v2(
    interval="1minute",
    from_date="2026-07-31T09:15:00.000Z",
    to_date="2026-07-31T15:30:00.000Z",
    stock_code="NIFTY",
    exchange_code="NFO",
    product_type="futures",
    expiry_date="2026-08-25"
)
print("Futures response status:", res.get("Status") if res else "None")
print("Success list length:", len(res.get("Success", [])) if res and res.get("Success") else 0)
if res and not res.get("Success"):
    print("Full response:", res)
