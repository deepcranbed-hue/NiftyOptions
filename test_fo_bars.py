"""
test_fo_bars.py
===============
Proves the typed derivatives store (data_agent/fetching/fo_bars.py), offline:

  1. Table created; options + futures saved.
  2. Strike-range query returns exactly the band (typed numeric filter).
  3. Option chain slice at a timestamp returns all strikes/rights.
  4. IDEMPOTENT: re-saving the same minute does NOT duplicate (INSERT OR REPLACE
     works because futures use strike=0/right='' sentinels, not NULL).
  5. Futures & options are keyed independently (no collisions).
  6. Cash `price_bars` is untouched by writing to fo_price_bars.
"""
from __future__ import annotations
import importlib.util, os, sqlite3, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

spec = importlib.util.spec_from_file_location("fo_bars", HERE + "/data_agent/fetching/fo_bars.py")
fo = importlib.util.module_from_spec(spec); sys.modules["fo_bars"] = fo; spec.loader.exec_module(fo)

DB = os.path.join(tempfile.gettempdir(), "fo_test.db")
if os.path.exists(DB):
    os.remove(DB)

# ── 1. save options across a strike ladder + a future ───────────────────────
hr("1. SAVE options ladder + future")
TS = "2026-07-14T05:00:00Z"
for k, px in [(23900, 150), (23950, 120), (24000, 95), (24050, 72), (24100, 52)]:
    fo.save_fo_bars([(TS, px, px + 1, px - 1, px, 1000, 40000)],
                    db=DB, underlying="NIFTY", instrument_type=fo.OPT,
                    expiry="2026-07-14", strike=k, right="CE")
    fo.save_fo_bars([(TS, 200 - px, 201 - px, 199 - px, 200 - px, 900, 35000)],
                    db=DB, underlying="NIFTY", instrument_type=fo.OPT,
                    expiry="2026-07-14", strike=k, right="PE")
fo.save_fo_bars([(TS, 24050, 24060, 24040, 24055, 5000, 800000)],
                db=DB, underlying="NIFTY", instrument_type=fo.FUT, expiry="2026-07-31")
n_rows = sqlite3.connect(DB).execute("SELECT COUNT(*) FROM fo_price_bars").fetchone()[0]
check("rows stored (5 strikes x 2 rights + 1 future = 11)", n_rows == 11)

# ── 2. strike-range query ───────────────────────────────────────────────────
hr("2. STRIKE-RANGE query (ATM +/- one strike)")
band = fo.get_strike_range(DB, underlying="NIFTY", expiry="2026-07-14", right="CE", lo=23950, hi=24050)
strikes = [r["strike"] for r in band]
check("returns exactly 23950/24000/24050 CE", strikes == [23950.0, 24000.0, 24050.0])
check("does NOT include out-of-band or PE rows", all(r["right"] == "CE" for r in band))

# ── 3. chain slice ──────────────────────────────────────────────────────────
hr("3. OPTION CHAIN slice at a timestamp")
chain = fo.get_option_chain(DB, underlying="NIFTY", expiry="2026-07-14", at_ts=TS)
check("chain has all 10 option legs (5 strikes x 2 rights)", len(chain) == 10)

# ── 4. idempotency ──────────────────────────────────────────────────────────
hr("4. IDEMPOTENT re-save (no duplicates)")
fo.save_fo_bars([(TS, 999, 999, 999, 999, 1, 1)], db=DB, underlying="NIFTY",
                instrument_type=fo.OPT, expiry="2026-07-14", strike=24000, right="CE")
after = sqlite3.connect(DB).execute("SELECT COUNT(*) FROM fo_price_bars").fetchone()[0]
check("row count unchanged after re-save (INSERT OR REPLACE)", after == 11)
val = sqlite3.connect(DB).execute(
    "SELECT close FROM fo_price_bars WHERE underlying='NIFTY' AND expiry='2026-07-14' "
    "AND strike=24000 AND right='CE'").fetchone()[0]
check("re-save OVERWROTE the bar (close now 999)", val == 999)

# future re-save must also dedupe (the sentinel PK fix)
fo.save_fo_bars([(TS, 1, 1, 1, 1, 1, 1)], db=DB, underlying="NIFTY",
                instrument_type=fo.FUT, expiry="2026-07-31")
fut_rows = sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM fo_price_bars WHERE instrument_type='FUT'").fetchone()[0]
check("future NOT duplicated on re-save (sentinel PK, not NULL)", fut_rows == 1)

# ── 5. futures near/next derivation ─────────────────────────────────────────
hr("5. FUTURES near/next derived, not stored as labels")
fo.save_fo_bars([(TS, 24080, 24090, 24070, 24085, 4000, 500000)],
                db=DB, underlying="NIFTY", instrument_type=fo.FUT, expiry="2026-08-28")
nn = fo.near_next(DB, underlying="NIFTY")
check("near/next sorted (07-31 then 08-28)", nn == ["2026-07-31", "2026-08-28"])

# ── 6. cash table untouched ─────────────────────────────────────────────────
hr("6. CASH price_bars UNTOUCHED")
tabs = [r[0] for r in sqlite3.connect(DB).execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
check("only fo_price_bars was created (no price_bars side-effects)", tabs == ["fo_price_bars"])

# ── 7. review refinements: symbol, contract_size, INTEGER volume/OI ─────────
hr("7. CONVENIENCE symbol + contract_size + INTEGER volume/OI")
fo.save_fo_bars([(TS, 95, 96, 94, 95, 1234.0, 40000.0)], db=DB, underlying="NIFTY",
                instrument_type=fo.OPT, expiry="2026-07-14", strike=25500, right="CE",
                contract_size=75)
row = sqlite3.connect(DB).execute(
    "SELECT symbol, contract_size, volume, open_interest FROM fo_price_bars "
    "WHERE strike=25500 AND right='CE'").fetchone()
check("weekly-aware symbol incl. exact day (NIFTY26JUL14_25500CE)", row[0] == "NIFTY26JUL14_25500CE")
check("two July weeklies get distinct labels",
      fo.contract_symbol("NIFTY", fo.OPT, "2026-07-14", 24000, "CE")
      != fo.contract_symbol("NIFTY", fo.OPT, "2026-07-21", 24000, "CE"))
check("contract_size stored (75)", row[1] == 75)
check("volume stored as INTEGER (1234, not 1234.0)", isinstance(row[2], int) and row[2] == 1234)
check("open_interest INTEGER (40000)", isinstance(row[3], int) and row[3] == 40000)
check("future symbol form (NIFTY26JUL31_FUT)",
      fo.contract_symbol("NIFTY", fo.FUT, "2026-07-31") == "NIFTY26JUL31_FUT")

# ── 8. get_atm_chain (nearest strike +/- n) ─────────────────────────────────
hr("8. get_atm_chain (ATM +/- n, both rights)")
atm = fo.get_atm_chain(DB, underlying="NIFTY", expiry="2026-07-14", spot=24010, n=1)
atm_strikes = sorted({r["strike"] for r in atm})
check("spot 24010 -> nearest 24000, ±1 -> {23950,24000,24050}", atm_strikes == [23950.0, 24000.0, 24050.0])
check("returns both CE and PE", {r["right"] for r in atm} == {"CE", "PE"})

# ── 9. ExpiryResolver (near/next/weekly/monthly) ────────────────────────────
hr("9. EXPIRY RESOLVER (centralized)")
from datetime import date as _d
er = fo.ExpiryResolver(["2026-07-09", "2026-07-16", "2026-07-23", "2026-07-30", "2026-08-27"], _d(2026, 7, 7))
check("near = 2026-07-09", er.resolve("near") == "2026-07-09")
check("next = 2026-07-16", er.resolve("next") == "2026-07-16")
check("weekly (first non-month-end) = 2026-07-09", er.resolve("weekly") == "2026-07-09")
check("monthly (last of July) = 2026-07-30", er.resolve("monthly") == "2026-07-30")
check("all unexpired listed", len(er.resolve("all")) == 5)

# ── 10. new indexes present ─────────────────────────────────────────────────
hr("10. INDEXES (chain + all-contracts + single-contract time-series)")
idx = {r[0] for r in sqlite3.connect(DB).execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fo_price_bars'").fetchall()}
check("ix_fo_chain, ix_fo_all, ix_fo_ts all created", {"ix_fo_chain", "ix_fo_all", "ix_fo_ts"} <= idx)

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  typed fo_price_bars: strike-range fast, idempotent, futures sentinel-keyed, cash untouched")
sys.exit(0 if n_pass == n else 1)
