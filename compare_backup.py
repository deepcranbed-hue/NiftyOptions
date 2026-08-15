"""compare_backup.py — do the current bars match the backup, value by value?

Bar COUNTS told us almost nothing: GOLD went 249 -> 252, which looks like three new
sessions and hides the fact that every overlapping date may have been rewritten from
a different contract. This compares closes on the dates both databases hold.
"""
import os, sqlite3, sys
sys.path.insert(0, ".")
from bar_store import DB_PATH

backup = sys.argv[1] if len(sys.argv) > 1 else "option_chains.db"
cur = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
a, b = sqlite3.connect(cur), sqlite3.connect(backup)
print(f"current: {cur}\nbackup : {backup}\n")

for sym in ("GOLD", "SILVER", "COPPER", "CRUDEOIL_MCX"):
    A = {t[:10]: c for t, c in a.execute(
        "select ts, close from price_bars where symbol=? and timeframe='1d'", (sym,))}
    B = {t[:10]: c for t, c in b.execute(
        "select ts, close from price_bars where symbol=? and timeframe='1d'", (sym,))}
    both = sorted(set(A) & set(B))
    if not both:
        print(f"{sym}: no overlapping dates"); continue
    diffs = [(d, B[d], A[d], (A[d]/B[d] - 1) if B[d] else 0) for d in both
             if B[d] and abs(A[d]/B[d] - 1) > 0.001]
    print(f"{sym}: {len(both)} shared dates, {len(diffs)} differ by >0.1%"
          f"   (only in current: {len(set(A)-set(B))}, only in backup: {len(set(B)-set(A))})")
    for d, ob, ca, r in diffs[:5]:
        print(f"     {d}  backup {ob:>11,.1f}  ->  current {ca:>11,.1f}   {r:+.2%}")
    if len(diffs) > 5:
        worst = max(diffs, key=lambda x: abs(x[3]))
        print(f"     ... {len(diffs)-5} more; worst {worst[0]} {worst[3]:+.2%}")
    print()
