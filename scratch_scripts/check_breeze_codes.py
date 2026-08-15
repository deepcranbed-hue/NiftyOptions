import sys, os
from breeze_connect import BreezeConnect
import json

session_token = "56199493"
api_key = "999407AZb39Vu3D&9X405B977330807K"
api_secret = "584F70+Z075364Cz35y6O9931Y16I387"

breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=api_secret, session_token=session_token)

print("Checking names for RELIANCE:")
print(breeze.get_names(exchange_code="NSE", stock_code="RELIANCE"))
print("Checking names for RELIAN:")
print(breeze.get_names(exchange_code="NSE", stock_code="RELIAN"))
