#!/usr/bin/env python3
"""
bank_data_check.py — readiness check for a Nifty BANK signal validation (no model).

Bank is DOMESTICALLY driven (RBI, India rates, liquidity, FII flows) — unlike IT (US
software) / Energy (crude) it has no clean overnight foreign sector-peer. Candidate
overnight predictors are INDIRECT, via the risk/flow/rate channels:
    global risk  S&P500 / VIX      (risk-off → FIIs sell financials → Bank gaps down)
    US financials XLF               (global banking sentiment; likely weak — banks are domestic)
    rates        US10Y             (US rates → India rates → NIM)
    FX           USD-INR           (rupee weakness → FII outflow → banks down)

This checks the DATA only — target OHLC + each predictor's presence/depth — and prints
what is ready vs what to backfill. No prediction, no model.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_data_check.py
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_db_path, resolve_pg_dsn
import os
import sqlite3
import sys

try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    resolve_db_path(),
)
# target (index) candidates — stored name may be either
TARGET_SYMS = ["BANKNIFTY", "NIFTYBANK"]
SQLITE_PREDICTORS = ["USDINR"]                          # FX channel, in price_bars
# predictor candidates in Postgres macro.factor_series, with Bank relevance + need
PG_PREDICTORS = {
    "SPY":    "global risk (S&P500) — strongest candidate lead via FII risk-off",
    "VIX":    "global risk / fear — FII flow channel  (likely MISSING → backfill ^VIX)",
    "XLF":    "US financials — global banking sentiment (likely MISSING → backfill XLF)",
    "US10Y":  "rates → India rates → NIM",
    "NASDAQ": "broad risk (already present, cross-check only)",
}
DEEP_DAYS = 1500       # ~6yr ≈ back to ~2019; ideally back to 2018


def sqlite_stats(sym, timeframe="1d"):
    if not os.path.exists(SQLITE_DB):
        return None
    con = sqlite3.connect(SQLITE_DB)
    try:
        row = con.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(open), COUNT(close) "
            "FROM price_bars WHERE symbol=? AND timeframe=?", (sym, timeframe)).fetchone()
    except Exception as e:
        con.close(); return {"error": str(e)}
    con.close()
    n, lo, hi, nopen, nclose = row
    return {"n": n or 0, "lo": lo, "hi": hi, "has_ohlc": (nopen or 0) > 0, "nclose": nclose or 0}


def main():
    print("\n=== NIFTY BANK — signal-input data readiness (no model) ===")

    # ---- target ---------------------------------------------------------------------
    print("\n  [TARGET] Nifty Bank index (need OHLC, deep history)")
    target_ok, found_sym = False, None
    for s in TARGET_SYMS:
        st = sqlite_stats(s)
        if st and not st.get("error") and st["n"] > 0:
            found_sym = s
            deep = st["n"] >= DEEP_DAYS
            ohlc = st["has_ohlc"]
            print(f"      {s:<11} n={st['n']:<6} {st['lo']} → {st['hi']}   OHLC={'yes' if ohlc else 'NO (close only)'}")
            target_ok = deep and ohlc
            break
    if not found_sym:
        print(f"      ❌ none of {TARGET_SYMS} in price_bars — backfill ^NSEBANK OHLC to 2018.")
    elif not target_ok:
        st = sqlite_stats(found_sym)
        why = []
        if st["n"] < DEEP_DAYS: why.append(f"shallow ({st['n']}<{DEEP_DAYS}) — backfill to 2018")
        if not st["has_ohlc"]: why.append("no OHLC — need open for the gap/intraday test")
        print(f"      ⚠️  {found_sym}: {'; '.join(why)}")
    else:
        print(f"      ✅ {found_sym} ready (deep + OHLC)")

    # ---- SQLite predictors (FX) -----------------------------------------------------
    print("\n  [PREDICTOR · SQLite] USD-INR (FX channel)")
    for s in SQLITE_PREDICTORS:
        st = sqlite_stats(s)
        if st and not st.get("error") and st["n"] > 0:
            deep = st["n"] >= DEEP_DAYS
            print(f"      {s:<11} n={st['n']:<6} {st['lo']} → {st['hi']}   {'✅ deep' if deep else '⚠️ shallow — backfill to 2018'}")
        else:
            print(f"      {s:<11} ❌ absent — backfill USDINR to 2018")

    # ---- Postgres predictors --------------------------------------------------------
    print("\n  [PREDICTOR · Postgres macro.factor_series]")
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT factor FROM macro.factor_series")
        present = {r[0] for r in cur.fetchall()}
        for f, why in PG_PREDICTORS.items():
            if f in present:
                cur.execute("SELECT COUNT(*), MIN(obs_date), MAX(obs_date) FROM macro.factor_series WHERE factor=%s", (f,))
                n, lo, hi = cur.fetchone()
                deep = (n or 0) >= DEEP_DAYS
                flag = "✅ deep" if deep else "⚠️ shallow — extend --since 2018-01-01"
                print(f"      {f:<8} n={n:<6} {lo} → {hi}   {flag}")
                print(f"               ↳ {why}")
            else:
                print(f"      {f:<8} ❌ MISSING — {why}")
    conn.close()

    print("\n  ── NEXT ──")
    print("   To backfill (all price/macro, NO fundamentals):")
    print("     • ^NSEBANK  → price_bars as BANKNIFTY (OHLC, --since 2018)   [if target not ready]")
    print("     • XLF, ^VIX → macro.factor_series via download_us_stocks.py (--since 2018)")
    print("     • confirm USDINR / US10Y deep to 2018")
    print("   Then build bank_validation.py: target=Bank, predictors lagged 1 session,")
    print("   pre-registered expectation = MODERATE (domestic sector, no clean overnight peer).")


if __name__ == "__main__":
    main()
