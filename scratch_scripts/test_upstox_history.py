import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('UPSTOX_ACCESS_TOKEN')

url = "https://api.upstox.com/v2/fundamentals/INE009A01021/income-statement?type=consolidated&time_period=quarterly"
res = requests.get(url, headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
if res.status_code == 200:
    data = res.json()
    revenue_history = next((item['history'] for item in data['data']['income_statement'] if item['category'] == 'revenue'), [])
    print(f"Quarterly Revenue Periods ({len(revenue_history)} total):")
    for h in revenue_history:
        print(f" - {h['period']}")
else:
    print(f"Error: {res.status_code}, {res.text}")

url_yearly = "https://api.upstox.com/v2/fundamentals/INE009A01021/income-statement?type=consolidated&time_period=yearly"
res_yearly = requests.get(url_yearly, headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
if res_yearly.status_code == 200:
    data_y = res_yearly.json()
    revenue_history_y = next((item['history'] for item in data_y['data']['income_statement'] if item['category'] == 'revenue'), [])
    print(f"\nYearly Revenue Periods ({len(revenue_history_y)} total):")
    for h in revenue_history_y:
        print(f" - {h['period']}")
