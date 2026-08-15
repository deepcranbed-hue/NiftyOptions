"""
test_universe.py
================
Proves the expiry-selection rules (data_agent/fetching/universe.py), offline:

  * Futures: near + next.
  * Options: current only, until <= 2 days before expiry -> current + next.
  * The 2-day boundary is exact (expiry-3 = current only, expiry-2 = add next).
  * Expired series dropped; the roll advances the "current" expiry.
"""
from __future__ import annotations
import importlib.util, sys, os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

spec = importlib.util.spec_from_file_location("universe", HERE + "/data_agent/fetching/universe.py")
U = importlib.util.module_from_spec(spec); sys.modules["universe"] = U; spec.loader.exec_module(U)

def iso(ds): return [d.isoformat() for d in ds]

OPT = ["2026-07-09", "2026-07-16", "2026-07-23", "2026-07-30"]   # weekly expiries
FUT = ["2026-07-31", "2026-08-28", "2026-09-25"]                 # monthly expiries

# ── futures ─────────────────────────────────────────────────────────────────
hr("1. FUTURES = near + next")
f = U.active_future_expiries(FUT, date(2026, 7, 7))
check("picks exactly two (near, next)", iso(f) == ["2026-07-31", "2026-08-28"])

# ── options: far from expiry -> current only ────────────────────────────────
hr("2. OPTIONS far from expiry -> current only")
o = U.active_option_expiries(OPT, date(2026, 7, 6))   # 3 days before 07-09
check("expiry-3 -> current only", iso(o) == ["2026-07-09"])

# ── options: 2 days before -> add next (the rule) ───────────────────────────
hr("3. OPTIONS <= 2 days before -> current + next (start next series)")
o2 = U.active_option_expiries(OPT, date(2026, 7, 7))  # exactly 2 days before 07-09
check("expiry-2 -> current + next", iso(o2) == ["2026-07-09", "2026-07-16"])
o1 = U.active_option_expiries(OPT, date(2026, 7, 8))  # 1 day before
check("expiry-1 -> current + next", iso(o1) == ["2026-07-09", "2026-07-16"])
o0 = U.active_option_expiries(OPT, date(2026, 7, 9))  # expiry day
check("expiry day -> current + next", iso(o0) == ["2026-07-09", "2026-07-16"])

# ── boundary is exact: 3 days before is NOT yet rolling ─────────────────────
hr("4. BOUNDARY is exact (2, not 3, days)")
check("expiry-3 stays current-only", iso(U.active_option_expiries(OPT, date(2026, 7, 6))) == ["2026-07-09"])
check("expiry-2 begins next series", len(U.active_option_expiries(OPT, date(2026, 7, 7))) == 2)

# ── expired dropped; roll advances current ──────────────────────────────────
hr("5. EXPIRED dropped -> current rolls forward")
o_after = U.active_option_expiries(OPT, date(2026, 7, 10))  # 07-09 now expired
check("07-09 expired -> current is 07-16", iso(o_after)[0] == "2026-07-16")
check("is_expired(07-09) on 07-10", U.is_expired("2026-07-09", date(2026, 7, 10)) is True)
check("is_expired(07-16) on 07-10 == False", U.is_expired("2026-07-16", date(2026, 7, 10)) is False)

# ── full universe assembly ──────────────────────────────────────────────────
hr("6. BUILD UNIVERSE (typed targets)")
uni = U.build_universe(date(2026, 7, 7), stocks=["TCS", "RELIANCE"],
                       future_expiries=FUT, option_expiries=OPT)
kinds = [t["kind"] for t in uni]
check("has index + 2 stocks", kinds.count("index") == 1 and kinds.count("stock") == 2)
check("has 2 future expiries", kinds.count("future") == 2)
check("has 2 option expiries (2-day roll active)", kinds.count("option_expiry") == 2)
check("no expired series present", all(t.get("expiry", "9999") >= "2026-07-07" for t in uni if "expiry" in t))
opt_targets = [t["expiry"] for t in uni if t["kind"] == "option_expiry"]
print("  option expiries selected:", opt_targets)

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  rules: futures near+next | options current, +next when <=2d to expiry | expired excluded")
sys.exit(0 if n_pass == n else 1)
