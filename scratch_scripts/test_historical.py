import sys, json
from breeze_connect import BreezeConnect
from datetime import datetime, timedelta

api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "s28*2~69700KUN944d63l#AN72Z66m38"
session_token = "56218283"

breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

try:
    # NIFTY index time series for today
    today_iso = datetime.now().strftime("%Y-%m-%dT06:00:00.000Z")
    yesterday_iso = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%dT06:00:00.000Z")

    response = breeze.get_historical_data_v2(
        interval="1minute",
        from_date= yesterday_iso,
        to_date= today_iso,
        stock_code="NIFTY",
        exchange_code="NSE",
        product_type="cash"
    )
    print("Keys in response:", response.keys())
    if "Success" in response and response["Success"]:
        print("Got", len(response["Success"]), "candles.")
        print("First candle:", response["Success"][0])
    else:
        print("Response:", response)
except Exception as e:
    print("Error:", e)
