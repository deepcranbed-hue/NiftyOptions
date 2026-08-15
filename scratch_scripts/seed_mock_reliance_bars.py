import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars
import random
from datetime import datetime, timedelta

db_path = "option_chains.db"
symbol = "RELIANCE"

# 1-minute bars for the last trading day (July 3rd, 2026)
random.seed(42)
px = 3120.0
rows1m = []
for i in range(375):
    h, m = divmod(9 * 60 + 15 + i, 60)
    o = px
    px += random.gauss(0.05, 1.5)
    c = px
    rows1m.append((f"2026-07-03T{h:02d}:{m:02d}:00Z",
                   o, max(o, c) + 0.5, min(o, c) - 0.5, c, random.randint(10, 100) * 100))

# Daily bars for the last 30 days
pd = 3050.0
rowsd = []
base_date = datetime(2026, 7, 3)
for i in range(30):
    o = pd
    pd += random.gauss(5, 15)
    c = pd
    dt = base_date - timedelta(days=(30 - i))
    rowsd.append((f"{dt.strftime('%Y-%m-%d')}T09:15:00Z",
                  o, max(o, c) + 12, min(o, c) - 12, c, random.randint(1000, 10000) * 100))

save_bars(rows1m, symbol=symbol, timeframe="1m", db=db_path)
save_bars(rowsd, symbol=symbol, timeframe="1d", db=db_path)
print(f"Mock historical price bars for {symbol} populated successfully in option_chains.db!")
