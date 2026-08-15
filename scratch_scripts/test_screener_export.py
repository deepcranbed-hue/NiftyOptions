import requests
from bs4 import BeautifulSoup

url = "https://www.screener.in/company/INFY/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
soup = BeautifulSoup(res.text, 'html.parser')
buttons = soup.find_all(lambda tag: tag.name in ['a', 'form'] and 'excel' in str(tag.get('href', '')) + str(tag.get('action', '')))
for b in buttons:
    print(b)
