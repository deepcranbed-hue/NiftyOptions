#!/usr/bin/env python3
"""
bank_valuation_probe.py — find the P/B ingredient BEFORE asking for a re-load.

The financials load dropped net-worth + share count (and loaded a generic, non-bank
balance sheet). But three tables weren't opened yet: key_ratios + two ratio views.
Screener usually stores Book Value / share (and sometimes P/B) there. If per-period
BVPS exists, P/B = adjusted_price ÷ BVPS — no shares, no re-load.

This probe answers ONE question: is there a per-period book-value or P/B anywhere
already in Postgres? If yes → build now. If no → the concrete re-load ask is exact.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    python bank_valuation_probe.py
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_pg_dsn
import os, sys
try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required')
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SCHEMA = "fundamentals"
SAMPLE_SYM = "HDFCBANK"
# what we're hunting for, by role → fuzzy name fragments (lowercased contains-match)
HUNT = {
    "book value / net worth": ["book value", "net worth", "reserves", "shareholder", "equity (net)"],
    "shares outstanding":     ["no. of shares", "no of shares", "number of shares", "equity shares",
                               "shares outstanding", "share capital"],
    "P/B (precomputed)":      ["price to book", "p/b", "pb ratio", "price / book"],
    "book value / share":     ["book value per share", "bvps", "book value/share"],
    "market cap":             ["market cap", "mcap", "m.cap"],
}


def cols(cur, t):
    cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (SCHEMA, t))
    return cur.fetchall()


def isin_for(cur, sym):
    try:
        cur.execute(f"SELECT isin FROM {SCHEMA}.companies WHERE symbol=%s", (sym,))
        r = cur.fetchone()
        return r[0] if r else None
    except Exception:
        return None


def scan_long(cur, table, label_col, sym_isin):
    """A long/tall table (metric,value): dump distinct labels + flag hunt hits."""
    cur.execute(f"SELECT DISTINCT {label_col} FROM {SCHEMA}.{table}")
    labels = sorted(str(r[0]) for r in cur.fetchall() if r[0] is not None)
    print(f"    distinct {label_col} ({len(labels)}): {labels[:60]}")
    low = [l.lower() for l in labels]
    print(f"    ── hunt in {table}.{label_col}:")
    for role, frags in HUNT.items():
        hit = sorted({labels[i] for i, l in enumerate(low) if any(f in l for f in frags)})
        print(f"       {'✅' if hit else '❌'} {role:<24} {hit if hit else ''}")


def main():
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    cur = conn.cursor()
    isin = isin_for(cur, SAMPLE_SYM)
    print("\n=== BANK VALUATION PROBE — is book-value / P/B already in Postgres? ===")
    print(f"  sample: {SAMPLE_SYM}  isin={isin}")

    for t in ("key_ratios", "v_company_ratios", "v_company_scorecard"):
        try:
            c = cols(cur, t)
        except Exception as e:
            print(f"\n  [{t}] not found ({e})"); continue
        cnames = [n for n, _ in c]
        print(f"\n  [{t}] columns: {cnames}")
        # long format? (a metric/label column + a value column)
        label_col = next((n for n in ("metric", "ratio", "line_item", "name", "field", "label") if n in cnames), None)
        if label_col:
            scan_long(cur, t, label_col, isin)
        else:
            # WIDE format → the column names themselves are the metrics
            low = [n.lower() for n in cnames]
            print(f"    (wide format — columns are the metrics)")
            for role, frags in HUNT.items():
                hit = sorted({cnames[i] for i, n in enumerate(low) if any(f in n for f in frags)})
                print(f"       {'✅' if hit else '❌'} {role:<24} {hit if hit else ''}")
        # sample one bank's most-recent row(s) so we SEE real values + period granularity
        keycol = "symbol" if "symbol" in cnames else ("isin" if "isin" in cnames else None)
        if keycol and isin is not None:
            kv = SAMPLE_SYM if keycol == "symbol" else isin
            datec = next((n for n in ("period_end", "period", "date") if n in cnames), None)
            order = f" ORDER BY {datec} DESC" if datec else ""
            try:
                cur.execute(f"SELECT * FROM {SCHEMA}.{t} WHERE {keycol}=%s{order} LIMIT 3", (kv,))
                rows = cur.fetchall()
                print(f"    sample {SAMPLE_SYM} rows ({len(rows)}): {[tuple(str(x)[:18] for x in r) for r in rows]}")
            except Exception as e:
                print(f"    (sample failed: {e})")

    # re-examine the financials balance sheet: is Reserves/shares hiding under section/statement?
    print("\n  [financials → real balance-sheet structure]")
    try:
        cur.execute(f"SELECT DISTINCT statement, section FROM {SCHEMA}.financials ORDER BY 1,2")
        print(f"    (statement, section) pairs: {cur.fetchall()}")
        if isin:
            cur.execute(f"SELECT DISTINCT line_item FROM {SCHEMA}.financials "
                        f"WHERE isin=%s AND statement ILIKE '%%balance%%'", (isin,))
            bs = sorted(str(r[0]) for r in cur.fetchall())
            print(f"    balance-sheet line_items for {SAMPLE_SYM} ({len(bs)}): {bs}")
    except Exception as e:
        print(f"    (probe failed: {e})")

    print("\n  ── DECISION ──")
    print("    If any ✅ above is a per-PERIOD book value or P/B → P/B is buildable NOW, no re-load.")
    print("    If all ❌ → re-load must add just two Data-Sheet rows: Reserves (57) + Adjusted Shares (92).")
    print("    (Net worth = Equity Capital + Reserves; P/B = adj_price × adj_shares ÷ net worth.)")
    conn.close()


if __name__ == "__main__":
    main()
