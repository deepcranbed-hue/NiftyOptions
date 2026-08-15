"""
test_orchestrator.py
====================
Proves the data-agent orchestrator (data_agent/fetching/orchestrator.py) offline
with a MOCK broker — no live Breeze/Kite, no token:

  1. build_plan applies the universe expiry rules (cash + FUT near/next + OPT
     current(+next) x strikes x CE/PE).
  2. run() collects each target and writes cash->price_bars, F&O->fo_price_bars.
  3. WATERMARK-INCREMENTAL: a second run only asks for bars AFTER the last stored.
  4. Per-target ERROR ISOLATION: one failing contract doesn't kill the batch.
  5. Empty result gets a retry.
"""
from __future__ import annotations
import importlib.util, os, sqlite3, sys, tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # so orchestrator's `from bar_store import save_bars` works
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m

O = load(HERE + "/data_agent/fetching/orchestrator.py", "data_agent.fetching.orchestrator")

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 7, 7, 15, 0, tzinfo=IST)          # mid-afternoon, session open earlier
DB = os.path.join(tempfile.gettempdir(), "orch_test.db")
if os.path.exists(DB):
    os.remove(DB)


class MockBroker:
    """Records the (frm) it was asked for, returns a couple of bars per call.
    `fail_on` forces an exception for a specific option strike (error-isolation test)."""
    kind = "breeze"
    def __init__(self, fail_on=None, empty_first=False):
        self.calls = []
        self.fail_on = fail_on
        self.empty_first = empty_first
        self._served = set()
    def _bars(self, base):
        # two 1-min bars stamped inside the session (UTC 'Z')
        return [("2026-07-07T05:00:00Z", base, base+1, base-1, base, 100, 1000),
                ("2026-07-07T05:01:00Z", base, base+1, base-1, base+0.5, 90, 1010)]
    def fetch_cash(self, symbol, frm, to):
        self.calls.append(("cash", symbol, frm)); return self._bars(100)
    def fetch_future(self, underlying, expiry, frm, to):
        self.calls.append(("fut", expiry, frm)); return self._bars(24050)
    def fetch_option(self, underlying, expiry, strike, right, frm, to):
        key = (expiry, strike, right)
        self.calls.append(("opt", key, frm))
        if self.fail_on and strike == self.fail_on:
            raise RuntimeError("broker 500 for this strike")
        if self.empty_first and key not in self._served:
            self._served.add(key); return []          # first attempt empty -> should retry
        return self._bars(120)


# ── 1. build_plan ───────────────────────────────────────────────────────────
hr("1. BUILD PLAN (universe expiry rules)")
FUT_EXP = ["2026-07-31", "2026-08-28"]
OPT_EXP = ["2026-07-09", "2026-07-16"]                  # today 07-07 is 2 days before 07-09 -> both
STRIKES = [24000, 24050, 24100]
plan = O.build_plan(stocks=["TCS", "RELIANCE"], future_expiries=FUT_EXP,
                    option_expiries=OPT_EXP, option_strikes=STRIKES, today=NOW.date())
kinds = [t["kind"] for t in plan]
check("cash targets = 2 stocks + NIFTY index", kinds.count("cash") == 3)
check("future targets = near + next (2)", kinds.count("FUT") == 2)
check("option targets = 2 expiries x 3 strikes x 2 rights = 12", kinds.count("OPT") == 12)
opt_exps = sorted({t["expiry"] for t in plan if t["kind"] == "OPT"})
check("both option expiries present (2-day roll active)", opt_exps == ["2026-07-09", "2026-07-16"])

# ── 2. run() writes cash->price_bars, F&O->fo_price_bars ────────────────────
hr("2. RUN collects to the right tables")
b = MockBroker()
rep = O.run(b, plan, db=DB, now_ist=NOW)
check("saved_total > 0", rep["saved_total"] > 0)
check("no errors", rep["errors"] == 0)
con = sqlite3.connect(DB)
n_cash = con.execute("SELECT COUNT(DISTINCT symbol) FROM price_bars").fetchone()[0]
n_fo_opt = con.execute("SELECT COUNT(*) FROM fo_price_bars WHERE instrument_type='OPT'").fetchone()[0]
n_fo_fut = con.execute("SELECT COUNT(*) FROM fo_price_bars WHERE instrument_type='FUT'").fetchone()[0]
check("cash written to price_bars (3 symbols)", n_cash == 3)
check("options written to fo_price_bars (12 contracts x 2 bars = 24)", n_fo_opt == 24)
check("futures written to fo_price_bars (2 x 2 = 4)", n_fo_fut == 4)
con.close()

# ── 3. watermark-incremental on a second run ────────────────────────────────
hr("3. WATERMARK-INCREMENTAL (2nd run asks only for NEW bars)")
b2 = MockBroker()
O.run(b2, plan, db=DB, now_ist=NOW)
# last stored bar was 2026-07-07T05:01:00Z -> next 'from' should be 05:02 IST (10:32)
cash_frm = [c[2] for c in b2.calls if c[0] == "cash"][0]
check("2nd-run 'from' advanced past the last bar (not 09:15 bootstrap)",
      "10:32" in cash_frm or "05:02" in cash_frm or cash_frm > "2026-07-07T04")
print("   2nd-run cash 'from' =", cash_frm)

# ── 4. error isolation ──────────────────────────────────────────────────────
hr("4. ERROR ISOLATION (one bad contract doesn't kill the batch)")
os.remove(DB)
bfail = MockBroker(fail_on=24050.0)
repf = O.run(bfail, plan, db=DB, now_ist=NOW, retries=0)
check("some targets errored", repf["errors"] > 0)
check("but most still collected", repf["ok"] >= 10)
check("failing strike isolated to its own record",
      all(r.get("error") for r in repf["results"] if r.get("strike") == 24050.0 and r["right"] in ("CE", "PE")))

# ── 5. empty -> retry ───────────────────────────────────────────────────────
hr("5. EMPTY RESULT -> RETRY")
os.remove(DB)
bempty = MockBroker(empty_first=True)
repe = O.run(bempty, plan, db=DB, now_ist=NOW, retries=1)
# each option was asked twice (empty then real); count option calls per key
from collections import Counter
opt_calls = Counter(c[1] for c in bempty.calls if c[0] == "opt")
check("empty option targets were retried (2 calls each)", all(v == 2 for v in opt_calls.values()))
check("after retry, options saved", repe["saved_total"] > 0)

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  universe -> broker(mock) -> price_bars(cash) + fo_price_bars(F&O); watermark-incremental, isolated, retried")
sys.exit(0 if n_pass == n else 1)
