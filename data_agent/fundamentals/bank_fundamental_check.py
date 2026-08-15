#!/usr/bin/env python3
"""
bank_fundamental_check.py — what actually landed in fundamentals.* after the screener load?
No model. Reports schema so bank_factor_model.py is built against reality:
  - financials: key (symbol vs isin), granularity, and whether the P/B + growth ingredients survived
  - asset_quality: metrics + bank coverage
  - companies: symbol↔isin bridge for the 12 banks

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    python bank_fundamental_check.py
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
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
         "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK"]
# ingredients we need, by role → fuzzy name candidates
NEED = {
    "net_worth (book)":   ["equity share capital", "reserves", "net worth", "book value", "total equity", "shareholder"],
    "shares":             ["no. of equity shares", "number of shares", "adjusted equity shares", "shares outstanding"],
    "price":              ["price", "current price", "adjusted price", "market cap"],
    "net_profit":         ["net profit", "pat", "profit after tax"],
    "sales/income":       ["sales", "revenue", "total income", "interest earned"],
    "interest (for NII)": ["interest", "interest expended", "finance cost"],
}


def cols(cur, t):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
                "ORDER BY ordinal_position", (SCHEMA, t))
    return [r[0] for r in cur.fetchall()]


def tables(cur):
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (SCHEMA,))
    return sorted(r[0] for r in cur.fetchall())


def main():
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    cur = conn.cursor()
    print("\n=== BANK FUNDAMENTAL DATA CHECK (loaded Postgres) — no model ===")
    tbls = tables(cur)
    print(f"  {SCHEMA}.* tables: {tbls}")

    # ---- companies bridge ----
    b2i = {}
    if "companies" in tbls:
        ccols = cols(cur, "companies")
        if "symbol" in ccols and "isin" in ccols:
            cur.execute(f"SELECT symbol, isin FROM {SCHEMA}.companies")
            b2i = {s: i for s, i in cur.fetchall()}
            hit = [b for b in BANKS if b in b2i]
            print(f"\n  [companies] symbol↔isin present. {len(hit)}/12 banks mapped.")
        else:
            print(f"\n  [companies] columns={ccols} (need symbol+isin to bridge)")

    if "financials" not in tbls:
        print("\n  ❌ no fundamentals.financials — where did the screener load go? tables above.")
        conn.close(); return

    fcols = cols(cur, "financials")
    print(f"\n  [financials] columns: {fcols}")
    key = "symbol" if "symbol" in fcols else ("isin" if "isin" in fcols else None)
    print(f"    join key: {key or 'UNKNOWN (neither symbol nor isin)'}")

    # granularity
    if "time_period" in fcols:
        cur.execute(f"SELECT DISTINCT time_period FROM {SCHEMA}.financials")
        print(f"    time_period values: {[r[0] for r in cur.fetchall()]}")
    # distinct line items (the actual row labels that loaded)
    li_col = next((c for c in ("line_item", "metric", "item", "field") if c in fcols), None)
    items = []
    if li_col:
        cur.execute(f"SELECT DISTINCT {li_col} FROM {SCHEMA}.financials")
        items = sorted(str(r[0]) for r in cur.fetchall() if r[0] is not None)
        print(f"    distinct {li_col} ({len(items)}): {items[:40]}")
    else:
        print(f"    (no line_item-style column — may be WIDE format; the columns above are the fields)")

    # ---- ingredient availability ----
    print("\n  [INGREDIENTS for P/B + growth]")
    pool = [i.lower() for i in items] if items else [c.lower() for c in fcols]
    for role, cands in NEED.items():
        found = sorted({p for p in pool if any(c in p for c in cands)})
        mark = "✅" if found else "❌"
        print(f"    {mark} {role:<20} → {found if found else 'NOT FOUND'}")

    # ---- bank coverage + depth in financials ----
    print("\n  [financials coverage per bank]")
    def bank_filter(sym):
        if key == "symbol":
            return ("symbol = %s", (sym,))
        if key == "isin" and sym in b2i:
            return ("isin = %s", (b2i[sym],))
        return (None, None)
    date_col = next((c for c in ("period_end", "period", "date", "report_date") if c in fcols), None)
    got = 0
    for b in BANKS:
        w, args = bank_filter(b)
        if not w or not date_col:
            continue
        cur.execute(f"SELECT COUNT(DISTINCT {date_col}), MIN({date_col}), MAX({date_col}) "
                    f"FROM {SCHEMA}.financials WHERE {w}", args)
        n, lo, hi = cur.fetchone()
        if n:
            got += 1
            print(f"    {b:<12} {n:>3} periods  {lo} → {hi}")
    print(f"    → {got}/12 banks have financials rows")

    # ---- asset_quality ----
    if "asset_quality" in tbls:
        aqc = cols(cur, "asset_quality")
        aqkey = "symbol" if "symbol" in aqc else ("isin" if "isin" in aqc else None)
        print(f"\n  [asset_quality] columns: {aqc}   key={aqkey}")
        metric_cols = [c for c in aqc if c not in (aqkey, "period_end", "period", "date", "id")]
        print(f"    metric columns: {metric_cols}")
        if aqkey == "symbol":
            cur.execute(f"SELECT COUNT(DISTINCT symbol) FROM {SCHEMA}.asset_quality")
            print(f"    banks covered: {cur.fetchone()[0]}")
    else:
        print("\n  [asset_quality] ❌ not found")

    print("\n  ── VERDICT ── (what bank_factor_model.py will do)")
    print("    P/B  = clean adjusted price (price_bars) × current shares ÷ net worth (financials)")
    print("    quality = GNPA/NNPA/NIM/PCR (asset_quality) ; growth = sales/PAT (financials)")
    print("    → if any ingredient is ❌ above, tell me its real name from the distinct-list and I map it.")
    conn.close()


if __name__ == "__main__":
    main()
