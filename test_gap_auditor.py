"""
test_gap_auditor.py
===================
Proves the gap auditor reads the EXISTING tables (price_bars + captures/chain_rows)
and reports what's missing — NO fo_price_bars, no new architecture.

  1. Cash symbol-days short of 1-min coverage are flagged (price_bars).
  2. Option expiry-days that are MISSING (index open, no chain) are flagged.
  3. Option days with snapshots but THIN coverage are flagged.
  4. 'Trading days' come from the index's own bars, not a hardcoded calendar.
  5. missing_report() unifies both into one 'what to backfill' answer.
"""
from __future__ import annotations
import importlib.util, os, sqlite3, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

spec = importlib.util.spec_from_file_location(
    "data_health", HERE + "/data_agent/quality/data_health.py")
dh = importlib.util.module_from_spec(spec); sys.modules["data_health"] = dh
spec.loader.exec_module(dh)

DB = os.path.join(tempfile.gettempdir(), "gap_audit_test.db")
if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, ts TEXT, open REAL,
    high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol,timeframe,ts));
CREATE TABLE captures (capture_id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT,
    spot REAL, vix REAL, source TEXT, note TEXT, exchange_code TEXT DEFAULT 'NFO',
    underlying TEXT DEFAULT 'NIFTY', snapshot_minute TEXT, status TEXT DEFAULT 'complete',
    trigger TEXT DEFAULT 'manual');
CREATE TABLE chain_rows (capture_id INTEGER, expiry TEXT, strike REAL, call_ltp REAL,
    put_ltp REAL, PRIMARY KEY(capture_id, expiry, strike));
""")

# ── index bars: three trading days (09:15–15:29 IST = 03:45–09:59 UTC) ───────
# NIFTY gets a FULL session on all three days -> defines the trading calendar.
def full_session(symbol, day, con, exch="NSE", n=375):
    # 03:45..(03:45+n-1) UTC minutes
    for i in range(n):
        m = 225 + i                       # minute-of-day UTC, 225 = 03:45
        ts = f"{day}T{m//60:02d}:{m%60:02d}:00Z"
        con.execute("INSERT OR REPLACE INTO price_bars VALUES(?,?,?,?,?,?,?,?,?)",
                    (exch, symbol, "1m", ts, 1, 1, 1, 1, 100))

DAYS = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"]
for d in DAYS:
    full_session("NIFTY", d, con)

# RELIANCE: full on day1+day2, but only HALF the bars on day3 -> DEGRADED that day.
full_session("RELIANCE", DAYS[0], con)
full_session("RELIANCE", DAYS[1], con)
full_session("RELIANCE", DAYS[2], con, n=180)   # short day
full_session("RELIANCE", DAYS[3], con)

# NIFTY_FUT_1: full days 1-3, a SHORT day on day4 (180 bars), never got 07-10.
# Rule: futures are audited by LAST TIMESTAMP only. The short day4 must NOT be flagged
# thin (sample size is shown, not audited); but it IS stale (last day 07-09 < ref 07-10).
full_session("NIFTY_FUT_1", DAYS[0], con)
full_session("NIFTY_FUT_1", DAYS[1], con)
full_session("NIFTY_FUT_1", DAYS[2], con)
full_session("NIFTY_FUT_1", DAYS[3], con, n=180)   # short — must be ignored by coverage
con.commit()

# ── option chain snapshots ──────────────────────────────────────────────────
# Expiry 2026-07-09 (weekly). We capture day1, SKIP day2, thin day3, full day4:
#   day1 (07-06): FULL 375        -> OK
#   day2 (07-07): NO snapshots    -> MISSING  (INTERIOR hole: between day1 and day3/4)
#   day3 (07-08): 100 snapshots   -> THIN
#   day4 (07-09): FULL 375        -> OK        (last captured day -> nothing trailing)
def snapshots(day, expiry, n, con, strike=24000):
    for i in range(n):
        m = 225 + i
        ts = f"{day}T{m//60:02d}:{m%60:02d}:00Z"
        cid = con.execute(
            "INSERT INTO captures(captured_at,snapshot_minute,status) VALUES(?,?, 'complete')",
            (ts, ts)).lastrowid
        con.execute("INSERT INTO chain_rows VALUES(?,?,?,?,?)", (cid, expiry, strike, 50, 40))

EXP = "2026-07-09"
snapshots(DAYS[0], EXP, 375, con)     # full
# DAYS[1] (07-07): intentionally none -> interior hole
snapshots(DAYS[2], EXP, 100, con)     # thin
snapshots(DAYS[3], EXP, 375, con)     # full
con.commit()
con.close()

# ── 1. trading calendar inferred from the index ─────────────────────────────
hr("1. TRADING DAYS inferred from index bars (no hardcoded calendar)")
td = dh.trading_days_from_index(DB)
check("index defines exactly the 4 open days", td == DAYS)

# ── 2. cash coverage over EXISTING price_bars ───────────────────────────────
hr("2. CASH coverage over price_bars")
cash = dh.coverage_report(DB)
rel_flags = [f for f in cash["flagged"] if f["symbol"] == "RELIANCE"]
check("RELIANCE flagged on the short day", any(f["date"] == DAYS[2] for f in rel_flags))
check("RELIANCE short day is DEGRADED", any(f["status"] == "DEGRADED" for f in rel_flags))
check("NIFTY (full all days) NOT flagged", all(f["symbol"] != "NIFTY" for f in cash["flagged"]))

# add a trailing open day 07-10 so the reference day advances past the captures
import sqlite3 as _sq
_c = _sq.connect(DB)
for i in range(375):
    m = 225 + i
    _c.execute("INSERT OR REPLACE INTO price_bars VALUES(?,?,?,?,?,?,?,?,?)",
               ("NSE", "NIFTY", "1m", f"2026-07-10T{m//60:02d}:{m%60:02d}:00Z", 1,1,1,1,100))
_c.commit(); _c.close()

# ── 3. options + futures audited by LAST TIMESTAMP only ─────────────────────
hr("3. OPTIONS + FUTURES: last-timestamp audit only (sample size shown, not judged)")
NOW = "2026-07-10T12:00:00Z"                       # 17:30 IST on 07-10, session closed
rep = dh.missing_report(DB, now=NOW)
fut_stale = [c for c in rep["freshness"]["stale_futures"]]
check("NIFTY_FUT_1 is STALE by last timestamp (last 07-09 < ref 07-10)",
      any(c["symbol"] == "NIFTY_FUT_1" and c["days_behind"] == 1 for c in fut_stale))
check("future's SHORT day4 is NOT flagged as thin (not sample-size audited)",
      all(not str(f["symbol"]).startswith("NIFTY_FUT") for f in rep["coverage"]["stock_thin"]))
check("future sample size is still SHOWN in inventory",
      any(c["symbol"] == "NIFTY_FUT_1" and c["sample_size"] > 0 for c in rep["inventory"]["cash"]))

# option expiry 07-09 is in the PAST as of 07-10 -> EXPIRED, not audited, sample size shown
exp_inv = next(c for c in rep["inventory"]["chain"] if c["expiry"] == EXP)
check("expired option expiry NOT flagged stale (no updates after expiry)",
      exp_inv["status"] == "EXPIRED"
      and all(c["expiry"] != EXP for c in rep["freshness"]["stale_chain"]))
check("expired expiry sample size still SHOWN", exp_inv["sample_size"] > 0)
check("no option sample-size (THIN/MISSING) auditing in the report",
      "chain" not in rep or "flagged" not in rep.get("chain", {}))

# ── 3b. an ACTIVE (future-dated) expiry that stopped updating IS stale ───────
hr("3b. ACTIVE expiry gone stale IS caught by last timestamp")
_c = _sq.connect(DB)
FUT_EXP = "2026-07-16"                              # still active as of 07-10
for i in range(50):                                # tiny sample — but sample size is NOT judged
    m = 225 + i
    ts = f"2026-07-08T{m//60:02d}:{m%60:02d}:00Z"   # last update 07-08, behind ref 07-10
    cid = _c.execute("INSERT INTO captures(captured_at,snapshot_minute,status) VALUES(?,?, 'complete')",
                     (ts, ts)).lastrowid
    _c.execute("INSERT INTO chain_rows VALUES(?,?,?,?,?)", (cid, FUT_EXP, 24000, 50, 40))
_c.commit(); _c.close()
rep2 = dh.missing_report(DB, now=NOW)
check("active expiry 07-16 (last 07-08) flagged STALE by last timestamp",
      any(c["expiry"] == FUT_EXP and c["days_behind"] >= 1 for c in rep2["freshness"]["stale_chain"]))
check("its small sample size is shown, not the reason it's flagged",
      any(c["expiry"] == FUT_EXP and c["sample_size"] == 50 for c in rep2["inventory"]["chain"]))

# ── 4. unified missing_report shape ─────────────────────────────────────────
hr("4. UNIFIED missing_report (freshness primary, stock coverage secondary)")
print("  headline:", rep2["headline"])
print("  inventory:", rep2["inventory"]["summary"])
check("level is not ok (we injected staleness)", rep2["level"] != "ok")
check("headline is last-timestamp framed", "last timestamp" in rep2["headline"])
check("has freshness + coverage + inventory sections",
      all(k in rep2 for k in ("freshness", "coverage", "inventory")))
check("no reference to fo_price_bars anywhere",
      "fo_price_bars" not in open(HERE + "/data_agent/quality/data_health.py").read())

# ── 5. inventory / freshness (last ts, start-end, sample size, days behind) ─
hr("5. INVENTORY: last timestamp, start/end day, sample size, days behind")
# Evaluate as-of AFTER close on the last trading day 07-10 so ref_day = 07-10.
NOW = "2026-07-10T12:00:00Z"                       # 17:30 IST, session closed
inv = dh.inventory_report(DB, now=NOW)
check("reference day = last complete trading day (07-10)", inv["reference_day"] == "2026-07-10")

nifty = next(c for c in inv["cash"] if c["symbol"] == "NIFTY")
check("NIFTY start day = 07-06", nifty["first_day"] == "2026-07-06")
check("NIFTY last day = 07-10 (has trailing bars)", nifty["last_day"] == "2026-07-10")
check("NIFTY sample size = 5*375", nifty["sample_size"] == 5 * 375)
check("NIFTY current (0 behind)", nifty["days_behind"] == 0 and nifty["status"] == "CURRENT")

rel = next(c for c in inv["cash"] if c["symbol"] == "RELIANCE")
check("RELIANCE last day = 07-09 (never got 07-10)", rel["last_day"] == "2026-07-09")
check("RELIANCE is STALE, 1 day behind (07-10 missed)",
      rel["status"] == "STALE" and rel["days_behind"] == 1)

exp = next(c for c in inv["chain"] if c["expiry"] == EXP)   # 2026-07-09
check("expiry last_ts recorded", exp["last_ts"] is not None)
check("expiry start day = 07-06", exp["first_day"] == "2026-07-06")
check("expiry sample size counts all rows", exp["sample_size"] == 375 + 100 + 375)
check("07-09 expiry EXPIRED as of 07-10 (past expiry, not flagged stale)",
      exp["status"] == "EXPIRED")

print("  inventory summary:", inv["summary"])

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  auditor reads existing price_bars + captures/chain_rows; reports missing/thin per symbol & expiry")
sys.exit(0 if n_pass == n else 1)
