import requests
import re

url = "https://www.screener.in/company/INFY/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
matches = re.findall(r'/excel/\d+/', res.text)
print("Excel Links:", list(set(matches)))
company_id = re.findall(r'data-company-id="(\d+)"', res.text)
print("Company IDs:", company_id)
