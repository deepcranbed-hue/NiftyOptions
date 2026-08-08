#!/usr/bin/env python3
"""
sync_india_macro.py — build/refresh india_macro_history (Layer-1 macro regime) alongside price_bars.

Fetches India CPI + 3-month interbank rate from FRED (OECD MEI) and upserts a clean monthly series.
Guardrails (per SECTOR_INTELLIGENCE_FRAMEWORK.md discipline):
  • FRESHNESS GATE — prints each series' last date; WARNS LOUDLY if stale (>100d). The two OECD-MEI
    codes have a discontinuation history (OECD stopped updating most MEI series on FRED ~2024); if
    the last date is old, this data is fine for backtests but NOT a live regime detector — switch
    the source (RBI repo step-table for rates; MoSPI/RBI for CPI).
  • POINT-IN-TIME — stores `available_from` (period + publication lag) so downstream joins to daily
    price_bars use merge_asof on available_from and never see a reading before it was released.
  • DERIVED SIGNAL — stores cpi_yoy (inflation %), not just the raw index level.

Table:  india_macro_history(date PK, cpi_index, cpi_yoy, interbank_rate, available_from, fetched_at)

USAGE
    pip install pandas_datareader --break-system-packages   # if missing
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python sync_india_macro.py
"""
from __future__ import annotations
import os, sqlite3, sys, datetime as dt
try:
    import pandas as pd
except ImportError:
    sys.exit("needs pandas")
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
CPI = "INDCPIALLMINMEI"        # India CPI All Items (OECD MEI, monthly index)
RATE = "INDIR3TIB01STM"        # India 3-month interbank rate (OECD MEI, monthly %)
START = "2010-01-01"
PUB_LAG_DAYS = 45              # conservative: CPI for month M is public ~mid-M+1
STALE_DAYS = 100


def fetch_fred():
    try:
        from pandas_datareader import data as web
    except ImportError:
        sys.exit("needs pandas_datareader → pip install pandas_datareader --break-system-packages")
    end = dt.date.today().isoformat()
    frames = {}
    for code in (CPI, RATE):
        try:
            s = web.DataReader(code, "fred", START, end)[code]
            frames[code] = s
            last = s.dropna().index.max()
            age = (pd.Timestamp.today().normalize() - last).days
            flag = "  ⚠️  STALE — discontinued? switch source" if age > STALE_DAYS else "ok"
            print(f"  {code:<18} {s.dropna().shape[0]:>4} obs  last {last.date()}  ({age}d old)  {flag}")
        except Exception as e:
            print(f"  {code:<18} ❌ fetch failed: {e}")
    return frames


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    print("\n=== SYNC INDIA MACRO (FRED → india_macro_history) ===")
    frames = fetch_fred()
    if CPI not in frames and RATE not in frames:
        sys.exit("  both series failed — nothing to write.")

    df = pd.DataFrame(frames)
    df.index = pd.to_datetime(df.index).normalize()
    df = df.sort_index()
    df = df.rename(columns={CPI: "cpi_index", RATE: "interbank_rate"})
    for c in ("cpi_index", "interbank_rate"):
        if c not in df:
            df[c] = float("nan")
    # derived: YoY inflation from the CPI index (the actual regime signal)
    df["cpi_yoy"] = (df["cpi_index"] / df["cpi_index"].shift(12) - 1.0) * 100.0
    # point-in-time publication date
    df["available_from"] = (df.index + pd.Timedelta(days=PUB_LAG_DAYS)).strftime("%Y-%m-%d")
    df = df.dropna(subset=["cpi_index", "interbank_rate"], how="all")

    con = sqlite3.connect(SQLITE_DB)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS india_macro_history (
        date TEXT PRIMARY KEY, cpi_index REAL, cpi_yoy REAL, interbank_rate REAL,
        available_from TEXT, fetched_at TEXT)""")
    now = dt.datetime.now().isoformat(timespec="seconds")
    n = 0
    for d, row in df.iterrows():
        cur.execute("""INSERT INTO india_macro_history(date, cpi_index, cpi_yoy, interbank_rate, available_from, fetched_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET cpi_index=excluded.cpi_index, cpi_yoy=excluded.cpi_yoy,
              interbank_rate=excluded.interbank_rate, available_from=excluded.available_from,
              fetched_at=excluded.fetched_at""",
            (d.strftime("%Y-%m-%d"),
             None if pd.isna(row["cpi_index"]) else float(row["cpi_index"]),
             None if pd.isna(row["cpi_yoy"]) else round(float(row["cpi_yoy"]), 2),
             None if pd.isna(row["interbank_rate"]) else float(row["interbank_rate"]),
             row["available_from"], now))
        n += 1
    con.commit()

    # verdict
    last = cur.execute("SELECT date, cpi_index, cpi_yoy, interbank_rate FROM india_macro_history "
                       "ORDER BY date DESC LIMIT 6").fetchall()
    tot = cur.execute("SELECT COUNT(*) FROM india_macro_history").fetchone()[0]
    con.close()
    print(f"\n  upserted {n} rows · table now has {tot} months")
    print(f"    {'month':<12}{'CPI':>9}{'infl%':>8}{'rate%':>8}")
    for d, ci, yy, rt in last:
        print(f"    {d:<12}{(ci if ci is not None else float('nan')):>9.1f}"
              f"{(yy if yy is not None else float('nan')):>8.1f}{(rt if rt is not None else float('nan')):>8.2f}")
    latest = last[0][0] if last else None
    if latest:
        age = (dt.date.today() - dt.date.fromisoformat(latest)).days
        if age > STALE_DAYS:
            print(f"\n  ⚠️  LATEST DATA IS {age} DAYS OLD — usable for BACKTEST, NOT a live regime detector.")
            print("     Pivot: RBI repo as a maintained step-table (rates) + MoSPI/RBI (CPI) for current.")
        else:
            print(f"\n  ✅ current ({age}d) — usable as a live macro-regime input.")
    print("\n  JOIN to price_bars point-in-time: merge_asof(daily_dates, macro, on available_from, direction='backward').")
    print("  Regime signals to derive: inflation TREND (cpi_yoy Δ), rate LEVEL + rate CHANGE (Δ interbank_rate).")


if __name__ == "__main__":
    main()
