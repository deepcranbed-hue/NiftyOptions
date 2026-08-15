import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
sessionid = os.getenv('SCREENER_SESSION_ID')

url = "https://www.screener.in/company/TCS/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, cookies={'sessionid': sessionid})
soup = BeautifulSoup(res.text, 'html.parser')

buttons = soup.find_all(lambda tag: 'excel' in str(tag.get('href', '')).lower() or 'export' in str(tag.get('href', '')).lower() or 'export' in tag.text.lower())
for b in buttons:
    print("Found button:", b.get('href'), b.text.strip())

