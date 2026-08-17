"""Synthetic roll test — the real DB has no crossover yet, so the flag is unverified."""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'data_agent', 'fetching'))
import stock_futures as sf

db = os.path.join(tempfile.mkdtemp(), 't.db')
c = sqlite3.connect(db)
c.execute("""CREATE TABLE fo_price_bars(exchange TEXT, underlying TEXT,
  instrument_type TEXT, expiry TEXT, strike REAL, right TEXT, timeframe TEXT,
  ts TEXT, open REAL, high REAL, low REAL, close REAL, volume INT,
  open_interest INT, contract_size INT, symbol TEXT)""")

# AUG loses OI to SEP on 25-Aug. SEP trades ~40-45 pts higher (a month of carry).
plan = [('2026-08-21', 24400, 90000, 24440, 20000),
        ('2026-08-24', 24450, 70000, 24492, 45000),
        ('2026-08-25', 24500, 30000, 24545, 80000),   # crossover
        ('2026-08-26', None,  None,  24560, 95000)]
rows = []
for d, ac, aoi, sc, soi in plan:
    if ac:
        rows.append(('NFO','TESTCO','FUT','2026-08-25',-1.0,'NA','1d',
                     d+'T00:00:00',ac,ac,ac,ac,1,aoi,1,'x'))
    rows.append(('NFO','TESTCO','FUT','2026-09-29',-1.0,'NA','1d',
                 d+'T00:00:00',sc,sc,sc,sc,1,soi,1,'y'))
c.executemany("INSERT INTO fo_price_bars VALUES (" + ",".join("?"*16) + ")", rows)
c.commit(); c.close()

fs = sf.front_series(db, 'TESTCO', method='oi', timeframe='1d')
print("n_days %d   n_roll_days %d\n" % (fs['n_days'], fs['n_roll_days']))
print("%12s%12s%9s%10s%9s%10s" % ('date','contract','close','oi','is_roll','roll_gap'))
for r in fs['series']:
    gap = '-' if r['roll_gap'] is None else format(r['roll_gap'], '.1f')
    print("%12s%12s%9.0f%10s%9s%10s" % (r['d'], r['expiry'], r['close'],
                                        format(r['oi'], ','), r['is_roll'], gap))
print("\nrolls: %s" % fs['rolls'])
exp = 24545 - 24500
ok = (fs['n_roll_days'] == 1
      and any(r['is_roll'] and r['d'] == '2026-08-25' for r in fs['series'])
      and fs['rolls'] and abs(fs['rolls'][0]['roll_gap'] - exp) < 0.01)
print("\nexpected: roll on 2026-08-25, gap +%d (SEP 24545 - AUG 24500)" % exp)
print("PASS" if ok else "FAIL")
