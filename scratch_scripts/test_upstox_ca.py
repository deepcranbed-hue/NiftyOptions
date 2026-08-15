import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('UPSTOX_ACCESS_TOKEN')

url = "https://api.upstox.com/v2/fundamentals/INE062A01020/corporate-actions"
res = requests.get(url, headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
if res.status_code == 200:
    data = res.json()
    print(json.dumps(data, indent=2)[:2000])
else:
    print(f"Error: {res.status_code}, {res.text}")
