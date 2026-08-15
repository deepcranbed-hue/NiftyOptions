"""
test_broker.py
==============
Proves the dual-broker adapter (data_agent/fetching/broker.py), offline — no live
Breeze/Kite calls (SDKs are guarded; connections are lazy):

  1. Factory picks the right adapter by kind; validates required creds.
  2. Both adapters expose the SAME interface (fetch_cash/future/option).
  3. Breeze + Kite normalizers emit the identical Bar shape (IST->UTC, OI).
  4. Kite instrument-token resolver matches by structured fields.
  5. Constructing a broker does NOT connect (lazy) — safe without the SDK.
"""
from __future__ import annotations
import importlib.util, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

spec = importlib.util.spec_from_file_location("broker", HERE + "/data_agent/fetching/broker.py")
B = importlib.util.module_from_spec(spec); sys.modules["broker"] = B; spec.loader.exec_module(B)
IST = timezone(timedelta(hours=5, minutes=30))

# ── 1. factory ──────────────────────────────────────────────────────────────
hr("1. FACTORY — one broker per run, by kind")
bz = B.get_broker("breeze", session_token="tok123")
kt = B.get_broker("kite", access_token="acc123", api_key="k")
check("'breeze' -> BreezeBroker", bz.kind == "breeze")
check("'kite' -> KiteBroker", kt.kind == "kite")
check("'zerodha' alias -> kite", B.get_broker("zerodha", access_token="x").kind == "kite")
try:
    B.get_broker("kraken", token="x"); ok = False
except ValueError:
    ok = True
check("unknown broker rejected", ok)
try:
    B.get_broker("breeze"); ok = False       # missing session_token
except ValueError:
    ok = True
check("breeze without session_token rejected", ok)

# ── 2. interface conformance ────────────────────────────────────────────────
hr("2. SAME INTERFACE on both adapters")
for name, b in (("breeze", bz), ("kite", kt)):
    ok = all(callable(getattr(b, m, None)) for m in ("fetch_cash", "fetch_future", "fetch_option"))
    check(f"{name} exposes fetch_cash/future/option", ok)

# ── 3. normalizers -> identical Bar shape ───────────────────────────────────
hr("3. NORMALIZERS (IST->UTC, OI) — identical shape")
bzb = B.normalize_breeze([{"datetime": "2026-07-14 09:16:00", "open": 100, "high": 101,
                           "low": 99, "close": 100.5, "volume": 1200, "open_interest": 45000}])[0]
ktb = B.normalize_kite([{"date": datetime(2026, 7, 14, 9, 16, tzinfo=IST), "open": 100,
                         "high": 101, "low": 99, "close": 100.5, "volume": 1200, "oi": 45000}])[0]
check("breeze IST 09:16 -> UTC 03:46Z", bzb[0] == "2026-07-14T03:46:00Z")
check("kite   IST 09:16 -> UTC 03:46Z", ktb[0] == "2026-07-14T03:46:00Z")
check("both produce the same 7-field Bar", bzb == ktb and len(bzb) == 7)
check("OI carried through as int", bzb[6] == 45000)
cash = B.normalize_breeze([{"datetime": "2026-07-14 09:16:00", "open": 1, "high": 1,
                            "low": 1, "close": 1, "volume": 5}])[0]
check("cash row -> OI None (no open_interest)", cash[6] is None)

# ── 4. kite token resolver (structured match) ───────────────────────────────
hr("4. KITE instrument-token resolver")
insts = [
    {"name": "NIFTY", "expiry": "2026-07-14", "strike": 24000, "instrument_type": "CE",
     "instrument_token": 111, "tradingsymbol": "NIFTY2571424000CE"},
    {"name": "NIFTY", "expiry": "2026-07-14", "strike": 24000, "instrument_type": "PE",
     "instrument_token": 222, "tradingsymbol": "NIFTY2571424000PE"},
    {"name": "NIFTY", "expiry": "2026-07-31", "strike": 0, "instrument_type": "FUT",
     "instrument_token": 333, "tradingsymbol": "NIFTY26JULFUT"},
]
check("CE 24000 -> token 111", B.KiteBroker.resolve_token(insts, underlying="NIFTY", expiry="2026-07-14", strike=24000, right="CE") == 111)
check("PE 24000 -> token 222", B.KiteBroker.resolve_token(insts, underlying="NIFTY", expiry="2026-07-14", strike=24000, right="PE") == 222)
check("FUT -> token 333", B.KiteBroker.resolve_token(insts, underlying="NIFTY", expiry="2026-07-31", instrument_type="FUT") == 333)
check("no match -> None", B.KiteBroker.resolve_token(insts, underlying="NIFTY", expiry="2026-07-14", strike=99999, right="CE") is None)

# ── 5. lazy connect (safe without SDK) ──────────────────────────────────────
hr("5. LAZY CONNECT (construct != connect)")
check("BreezeBroker constructed without SDK/network", bz._b is None)
check("KiteBroker constructed without SDK/network", kt._k is None)
check("breeze symbol code map (RELIANCE->RELIND)", B.BreezeBroker.code("RELIANCE") == "RELIND")

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  Breeze + Kite behind one interface; identical normalized Bar; live fetch needs the SDK + token")
sys.exit(0 if n_pass == n else 1)
