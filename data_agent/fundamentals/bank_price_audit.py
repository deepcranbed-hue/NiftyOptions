#!/usr/bin/env python3
"""
bank_price_audit.py — readiness of the 12 individual bank PRICE series for the
cross-sectional bank factor model. No model — presence / OHLC / depth / data-quality
/ cross-sectional overlap / join-to-fundamentals. PASS / FLAG per check.

Cross-sectional model needs, per bank: daily close (P/B = mktcap/book, forward relative
return) + a JOIN to fundamentals (financials) and asset_quality (GNPA/NIM/PCR).

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_price_audit.py
"""
from __future__ import annotations
import os, sqlite3, sys

try:
    import pandas as pd
except ImportError:
    sys.exit("needs pandas")
try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required')
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
         "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK"]
DEEP = "2018-01-01"        # want history at/earlier than this; 2015 ideal
SPLIT_THRESH = 25.0        # |daily %| beyond this ⇒ likely unadjusted split / bad bar


def sqlite_symbols(con):
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT symbol FROM price_bars").fetchall()}
    except Exception as e:
        return set()


def bank_stats(con, sym):
    rows = con.execute("SELECT ts, open, close FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY ts",
                       (sym,)).fetchall()
    if not rows:
        return None
    s = pd.DataFrame(rows, columns=["ts", "open", "close"])
    s["d"] = pd.to_datetime(s["ts"], utc=True).dt.tz_localize(None).dt.normalize()
    s = s.dropna(subset=["close"])
    r = s["close"].pct_change() * 100
    bad = [(str(d.date()), round(float(v), 1)) for d, v in
           zip(s["d"][r.abs() > SPLIT_THRESH], r[r.abs() > SPLIT_THRESH])][:4]
    return {"n": len(s), "lo": str(s["d"].min().date()), "hi": str(s["d"].max().date()),
            "has_open": s["open"].notna().any(), "bad": bad}


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    syms = sqlite_symbols(con)
    print("\n=== BANK PRICE AUDIT — 12 Nifty Bank names, cross-sectional readiness ===")

    present, missing, stats = [], [], {}
    for b in BANKS:
        st = bank_stats(con, b)
        if st:
            present.append(b); stats[b] = st
        else:
            missing.append(b)
    con.close()

    print(f"\n  [PRESENCE] {len(present)}/12 in price_bars")
    if missing:
        # try to surface near-matches so naming mismatches are obvious
        near = {m: [s for s in syms if m[:5] in s] for m in missing}
        print(f"    ❌ missing: {missing}")
        for m, n in near.items():
            if n:
                print(f"       (near-name in DB for {m}: {n})")

    print("\n  [DEPTH / OHLC / DATA-QUALITY] per bank")
    late = []
    for b in present:
        st = stats[b]
        deep = st["lo"] <= DEEP
        if not deep:
            late.append((b, st["lo"]))
        flag = ""
        if not st["has_open"]:
            flag += " NO-OHLC"
        if st["bad"]:
            flag += f" ⚠SPLIT?{st['bad']}"
        print(f"    {b:<12} n={st['n']:<5} {st['lo']} → {st['hi']}  "
              f"{'deep' if deep else 'LATE-LIST'}{flag}")

    # cross-sectional overlap: balanced panel starts at the latest first-date
    if present:
        common_start = max(stats[b]["lo"] for b in present)
        common_end = min(stats[b]["hi"] for b in present)
        print(f"\n  [CROSS-SECTIONAL OVERLAP] all-{len(present)}-present window: {common_start} → {common_end}")
        if late:
            print(f"    late listings limit the *balanced* panel: {late}")
            print("    → use an UNBALANCED panel (rank among banks PRESENT each quarter), not a")
            print("      balanced one — else you throw away pre-listing history for the old banks.")

    # join to fundamentals
    print("\n  [JOIN → fundamentals]")
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    with conn.cursor() as cur:
        def distinct(table):
            try:
                cur.execute(f"SELECT DISTINCT symbol FROM fundamentals.{table}")
                return {r[0] for r in cur.fetchall()}
            except Exception:
                # financials may key on isin; try companies map
                return None
        fin = distinct("financials")
        aq = distinct("asset_quality")
        comp = None
        try:
            cur.execute("SELECT symbol FROM fundamentals.companies")
            comp = {r[0] for r in cur.fetchall()}
        except Exception:
            pass
    conn.close()
    def cover(name, s):
        if s is None:
            print(f"    {name:<14} (symbol column not found — may key on isin; check join key)")
            return
        hit = [b for b in present if b in s]
        miss = [b for b in present if b not in s]
        print(f"    {name:<14} {len(hit)}/{len(present)} banks joinable" + (f"   MISSING: {miss}" if miss else ""))
    cover("financials", fin if fin is not None else comp)
    cover("asset_quality", aq)

    # verdict
    ok = (len(present) >= 10 and not any(not stats[b]["has_open"] for b in present)
          and all(not stats[b]["bad"] for b in present))
    print("\n  ── VERDICT ──")
    if missing:
        print(f"    ⚠️  {len(missing)} price series missing — backfill (check symbol naming vs DB).")
    if any(stats[b]["bad"] for b in present):
        print("    ⚠️  SPLIT/bad-bar flags — verify those series are SPLIT-ADJUSTED before computing")
        print("        returns or P/B (unadjusted split = spurious −50% bar; poisons rank + P/B).")
    if late:
        print("    ℹ️  late-listed banks (AU/Bandhan/IDFC-First ~2017-18) → unbalanced panel, fine.")
    print("    ✅ READY for the cross-sectional factor model" if ok and not missing
          else "    → resolve the flags above, then build the factor model.")
    print("\n  NOTE for the model: P/B must use market cap = price × *point-in-time* shares (or")
    print("  unadjusted price × shares) — split-adjusted price × current shares double-counts splits.")


if __name__ == "__main__":
    main()
