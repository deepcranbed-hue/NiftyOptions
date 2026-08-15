import requests
from bs4 import BeautifulSoup
import pandas as pd

url = "https://www.screener.in/company/INFY/consolidated/"
res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
soup = BeautifulSoup(res.text, 'html.parser')
section = soup.find('section', id='quarters')
if section:
    table = section.find('table', class_='data-table')
    df = pd.read_html(str(table))[0]
    print(df.columns.tolist())
    print(f"Total Quarters Found: {len(df.columns) - 1}")
else:
    print("No quarters section found")
