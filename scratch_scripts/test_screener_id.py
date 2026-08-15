import requests
from bs4 import BeautifulSoup
import re

url = "https://www.screener.in/company/INFY/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
soup = BeautifulSoup(res.text, 'html.parser')

body = soup.find('body')
if body and body.has_attr('data-company-id'):
    print(f"data-company-id: {body['data-company-id']}")

form = soup.find('form', action=re.compile(r'/excel/'))
if form:
    print(f"form action: {form['action']}")
