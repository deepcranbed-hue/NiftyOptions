import requests
from bs4 import BeautifulSoup

url = "https://www.screener.in/company/INFY/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
print(res.status_code)
if res.status_code == 200:
    soup = BeautifulSoup(res.text, 'html.parser')
    tables = soup.find_all('table', class_='data-table')
    for t in tables:
        print(t.find_previous('h2').text.strip())
