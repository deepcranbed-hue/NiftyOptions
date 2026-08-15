from event_fetcher import refresh_dates
import httpx
from datetime import date
def get(url):
    print("Fetching", url)
    return httpx.get(url, timeout=10.0, headers={'User-Agent': 'Mozilla/5.0'}).text

res = refresh_dates(get, date.today())
for code, fd in res.items():
    print(f"{code:8} {fd.date} stale={fd.stale} ({fd.note})")
