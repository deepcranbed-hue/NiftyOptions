import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
sessionid = os.getenv('SCREENER_SESSION_ID')

url = "https://www.screener.in/company/TCS/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, cookies={'sessionid': sessionid})
soup = BeautifulSoup(res.text, 'html.parser')

forms = soup.find_all('form')
for f in forms:
    if 'excel' in str(f).lower() or 'export' in str(f).lower():
        print(f"Form HTML: {str(f)}")
