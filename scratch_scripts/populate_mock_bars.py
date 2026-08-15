import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars
import random

db_path = "option_chains.db"
random.seed(7); px = 24175.0; rows1m = []
for i in range(375):
    h, m = divmod(9 * 60 + 15 + i, 60)
    o = px; px += random.gauss(0.1, 6); c = px
    rows1m.append((f"2026-07-03T{h:02d}:{m:02d}:00+05:30",
                   o, max(o, c) + 3, min(o, c) - 3, c, random.randint(50, 500) * 1000))
save_bars(rows1m, timeframe="1m", db=db_path)

pd = 23500.0; rowsd = []
for i in range(30):
    o = pd; pd += random.gauss(20, 120); c = pd
    rowsd.append((f"2026-06-{(i % 30) + 1:02d}T09:15:00+05:30",
                  o, max(o, c) + 60, min(o, c) - 60, c, random.randint(100, 1000) * 1000))
save_bars(rowsd, timeframe="1d", db=db_path)
print("Mock bars populated in option_chains.db")
