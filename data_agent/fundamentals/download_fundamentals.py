#!/usr/bin/env python3
"""
download_fundamentals.py — pull Upstox Company Fundamentals into PostgreSQL.

Targets the `fundamentals` schema from POSTGRES_MIGRATION_PLAN.md (psycopg 3,
%s placeholders, ON CONFLICT ... DO UPDATE). Idempotent — safe to re-run.

DEPS
    pip install "psycopg[binary,pool]" requests python-dotenv

DOWNLOADS EVERYTHING Upstox exposes per company, into the normalized
`fundamentals` schema:
    profile · income statement · balance sheet · cash flow  (yearly AND quarterly,
    summary + full line items) · key ratios · shareholding (quarterly) ·
    corporate actions · competitors.

AUTH
    Prefers UPSTOX_ANALYTICS_TOKEN (1-yr read-only, Developer Apps -> Analytics
    tab — the right token for fundamentals/history), else UPSTOX_ACCESS_TOKEN.

DB CONNECTION (any of):
    export DATABASE_URL="postgresql://user:pass@localhost:5432/niftyoptions"
    # or standard libpq vars: PGHOST, PGDATABASE, PGUSER, PGPASSWORD, PGPORT
    # or put DATABASE_URL in .env alongside UPSTOX_ACCESS_TOKEN

ISIN RESOLUTION
    {symbol -> ISIN} from SecurityMaster.zip / NSEScripMaster.txt
    (ExchangeCode -> ISINCode, Series == 'EQ').

USAGE
    python download_fundamentals.py --universe niftyit
    python download_fundamentals.py --symbols INFY,TCS,HCLTECH --standalone-too
    python download_fundamentals.py --isins INE009A01021,INE467B01029
    python download_fundamentals.py --init-only          # just create schema/tables

CADENCE (fundamentals barely move):
    statements + shareholding: weekly + after results season
    key-ratios + profile (mcap): daily if you want the drift
    corporate-actions: weekly
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

import requests

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError:
    sys.exit('psycopg 3 is required: pip install "psycopg[binary,pool]"')

# --- repo paths / token -----------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))              # data_agent/fundamentals/
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))             # -> repo root
sys.path.insert(0, os.path.join(REPO_ROOT, "scratch_scripts"))
try:
    from upstox_auth import get_upstox_token
except Exception:                                       # pragma: no cover
    def get_upstox_token():
        return os.getenv("UPSTOX_ACCESS_TOKEN")


def resolve_token():
    """Prefer the 1-year read-only ANALYTICS token (Developer Apps -> Analytics
    tab) — it's the right token for fundamentals/historical pulls and doesn't
    expire daily. Fall back to the daily access token."""
    return (os.getenv("UPSTOX_ANALYTICS_TOKEN")
            or os.getenv("UPSTOX_ACCESS_TOKEN")
            or get_upstox_token())

# load .env so DATABASE_URL / UPSTOX_ACCESS_TOKEN are visible when run standalone
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except Exception:
    pass

SCHEMA_PATH = os.path.join(HERE, "schema.sql")
SECURITY_MASTER = os.path.join(REPO_ROOT, "SecurityMaster.zip")
BASE = "https://api.upstox.com/v2/fundamentals"
SCHEMA = "fundamentals"

NIFTY_IT = ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
            "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"]

# Symbols missing/stale in older SecurityMaster dumps -> verified ISIN overrides.
#
# The second group is NOT a stale-dump problem: build_symbol_isin_map() keys the map on
# ExchangeCode, which carries the CURRENT NSE ticker, while the constituents registry
# still carries the pre-rename one. The scrip master holds the row — under a name the
# lookup never asks for — so re-downloading it changes nothing, because a fresh dump
# will not contain "ZOMATO" either. Both ISINs below were read straight out of the
# NSEScripMaster.txt already in this repo.
SCRIP_ISIN_OVERRIDES = {
    "LTIM": "INE214T01019",   # LTIMindtree (post LTI-Mindtree merger; absent from older scrip masters)
    # NSE renames / demergers — old symbol on the left, current entity in the comment.
    "ZOMATO": "INE758T01015",      # ETERNAL LIMITED (Zomato renamed, 2025)
    "TATAMOTORS": "INE155A01022",  # TATA MOTORS PAX VEHICLES LTD / TMPV (2025 demerger)
}


def now_ts():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# ISIN resolution from the NSE scrip master
# ---------------------------------------------------------------------------
def build_symbol_isin_map() -> dict:
    with zipfile.ZipFile(SECURITY_MASTER) as z:
        raw = z.read("NSEScripMaster.txt").decode("utf-8", "replace")
    out = {}
    for row in csv.DictReader(io.StringIO(raw), skipinitialspace=True):
        r = {(k or "").strip().strip('"'): (v or "").strip().strip('"')
             for k, v in row.items()}
        if r.get("Series") != "EQ":
            continue
        sym = r.get("ExchangeCode") or r.get("Symbol")
        isin = r.get("ISINCode")
        if sym and isin and isin.startswith("INE"):
            out[sym.upper()] = isin
    return out


def resolve_universe(args, sym2isin):
    if args.isins:
        return [(None, i.strip().upper()) for i in args.isins.split(",") if i.strip()]
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.universe == "niftyit":
        syms = NIFTY_IT
    else:
        try:
            from data_agent.constituents import symbols as reg_symbols
            syms = [s.upper() for s in reg_symbols()]
        except Exception as e:
            sys.exit(f"Registry import failed for --universe {args.universe}: {e}. "
                     f"Use --symbols or --isins.")
    pairs, missing = [], []
    for s in syms:
        isin = SCRIP_ISIN_OVERRIDES.get(s) or sym2isin.get(s)
        pairs.append((s, isin)) if isin else missing.append(s)
    if missing:
        print(f"[warn] no ISIN in scrip master for: {', '.join(missing)}", file=sys.stderr)
    return pairs


# ---------------------------------------------------------------------------
# period label -> date  ("Mar 2026" -> date(2026,3,31))
# ---------------------------------------------------------------------------
_QEND = {"jan": (1, 31), "feb": (2, 28), "mar": (3, 31), "apr": (4, 30),
         "may": (5, 31), "jun": (6, 30), "jul": (7, 31), "aug": (8, 31),
         "sep": (9, 30), "oct": (10, 31), "nov": (11, 30), "dec": (12, 31)}


def period_to_date(label):
    if not label:
        return None
    m = re.search(r"([A-Za-z]{3})[a-z]*\s+(\d{4})", str(label))
    if not m:
        return None
    md = _QEND.get(m.group(1).lower())
    if not md:
        return None
    from datetime import date
    y, (mo, d) = int(m.group(2)), md
    if mo == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        d = 29
    return date(y, mo, d)


def _num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"-?\d+(\.\d+)?", str(x).replace(",", ""))
    return float(m.group()) if m else None


# ---------------------------------------------------------------------------
# HTTP with light rate-limiting / backoff
# ---------------------------------------------------------------------------
class Api:
    def __init__(self, token, pause=0.3):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.pause = pause

    def get(self, key, resource, params=None):
        # `key` is the ISIN for most endpoints, but the instrument_key
        # (NSE_EQ|ISIN) for /competitors — url-encode so the '|' survives.
        url = f"{BASE}/{quote(key, safe='')}/{resource}"
        r = None
        for attempt in range(4):
            r = self.s.get(url, params=params, timeout=25)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            time.sleep(self.pause)
            return r
        return r


# ---------------------------------------------------------------------------
# DB helpers (psycopg 3)
# ---------------------------------------------------------------------------
def connect():
    dsn = os.getenv("DATABASE_URL")            # else psycopg reads PG* libpq vars
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    conn.autocommit = False
    return conn


def init_db(conn):
    with open(SCHEMA_PATH) as f:
        conn.execute(f.read())                 # psycopg3 runs multi-statement DDL (no params)
    conn.commit()


def upsert(conn, table, row, conflict_cols):
    cols = list(row)
    ph = ",".join(["%s"] * len(cols))
    sets = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict_cols)
    sql = (f"INSERT INTO {SCHEMA}.{table} ({','.join(cols)}) VALUES ({ph}) "
           f"ON CONFLICT ({','.join(conflict_cols)}) DO UPDATE SET {sets}")
    conn.execute(sql, [row[c] for c in cols])


def log(conn, isin, endpoint, status, ok, msg=""):
    conn.execute(
        f"INSERT INTO {SCHEMA}.fetch_log (isin,endpoint,http_status,ok,message,fetched_at) "
        f"VALUES (%s,%s,%s,%s,%s,%s)", (isin, endpoint, status, ok, msg, now_ts()))


# ---------------------------------------------------------------------------
# Per-endpoint parse + store
# ---------------------------------------------------------------------------
def store_profile(conn, isin, data, ts):
    inr = data.get("sector_market_cap_inr") or {}
    usd = data.get("sector_market_cap_usd") or {}
    upsert(conn, "company_profile", {
        "isin": isin, "description": data.get("company_profile"), "sector": data.get("sector"),
        "sector_mcap_inr_cr": _num(inr.get("value")), "sector_mcap_usd": _num(usd.get("value")),
        "sector_mcap_usd_unit": usd.get("unit"), "fetched_at": ts,
    }, ["isin"])


def _put_fin(conn, isin, statement, basis, tp, section, li, period, value, change, units, ts):
    if li is None or period is None:
        return
    upsert(conn, "financials", {
        "isin": isin, "statement": statement, "basis": basis, "time_period": tp,
        "section": section, "line_item": li, "period_label": period,
        "period_end": period_to_date(period), "value": _num(value),
        "change_pct": _num(change), "units": units, "fetched_at": ts,
    }, ["isin", "statement", "basis", "time_period", "section", "line_item", "period_label"])


def store_statement(conn, isin, statement, data, ts):
    basis = data.get("type", "consolidated")
    tp = data.get("time_period", "yearly")
    units = data.get("units_in", "crore")

    if statement == "balance":
        for h in (data.get("history") or []):
            for li, val in (("Total Assets", h.get("total_asset")),
                            ("Total Liabilities", h.get("total_liability"))):
                _put_fin(conn, isin, statement, basis, tp, "summary", li,
                         h.get("period"), val, None, units, ts)
    else:
        container = "income_statement" if statement == "income" else "cash_flow"
        name_key = "category" if statement == "cashflow" else "name"
        for item in (data.get(container) or []):
            name = item.get(name_key) or item.get("name") or item.get("category")
            for h in (item.get("history") or []):
                _put_fin(conn, isin, statement, basis, tp, "summary", name,
                         h.get("period"), h.get("value"), h.get("change"), units, ts)

    for item in (data.get("full_statement") or []):        # fs=true detailed lines
        li = item.get("particular") or item.get("name")
        for h in (item.get("history") or []):
            _put_fin(conn, isin, statement, basis, tp, "full", li,
                     h.get("period"), h.get("value"), None, units, ts)


def store_ratios(conn, isin, data, ts):
    as_of = ts.date()
    for item in (data or []):
        upsert(conn, "key_ratios", {
            "isin": isin, "ratio": item.get("name"),
            "company_value": _num(item.get("company_value")),
            "sector_value": _num(item.get("sector_value")),
            "as_of": as_of, "fetched_at": ts,
        }, ["isin", "ratio", "as_of"])


def store_shareholding(conn, isin, data, ts):
    for item in (data or []):
        cat = item.get("category")
        for h in (item.get("history") or []):
            upsert(conn, "shareholding", {
                "isin": isin, "category": cat, "period_label": h.get("period"),
                "period_end": period_to_date(h.get("period")),
                "pct": _num(h.get("value")), "fetched_at": ts,
            }, ["isin", "category", "period_label"])


def store_corporate_actions(conn, isin, data, ts):
    for a in (data or []):
        atype = a.get("name")
        ex_raw = a.get("expiry_date") or a.get("date") or a.get("ex_date")
        amt, ratio = _num(a.get("amount")), a.get("ratio")
        uid = f"{atype}|{ex_raw}|{'' if amt is None else amt}|{ratio or ''}"
        upsert(conn, "corporate_actions", {
            "isin": isin, "action_type": atype,
            "ex_date": period_to_date(ex_raw) or _try_date(ex_raw),
            "ex_date_raw": ex_raw, "amount": amt, "ratio": ratio,
            "details": Json(a.get("event_details") or []),
            "action_uid": uid, "fetched_at": ts,
        }, ["isin", "action_uid"])


def _try_date(s):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(s), fmt).date()
        except Exception:
            pass
    return None


def store_competitors(conn, isin, data, ts):
    for c in (data or []):
        ik = c.get("instrument_key", "") or ""
        inr = c.get("sector_market_cap_inr") or {}
        usd = c.get("sector_market_cap_usd") or {}
        upsert(conn, "competitors", {
            "isin": isin, "peer_instrument_key": ik,
            "peer_isin": ik.split("|")[-1] if "|" in ik else None,
            "peer_description": c.get("company_profile"), "peer_sector": c.get("sector"),
            "peer_mcap_inr_cr": _num(inr.get("value")), "peer_mcap_usd": _num(usd.get("value")),
            "peer_mcap_usd_unit": usd.get("unit"), "fetched_at": ts,
        }, ["isin", "peer_instrument_key"])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def fetch_company(api, conn, symbol, isin, bases, ts):
    upsert(conn, "companies", {
        "isin": isin, "symbol": symbol, "instrument_key": f"NSE_EQ|{isin}",
        "company_name": None, "updated_at": ts,
    }, ["isin"])

    def call(resource, params=None, key=None):
        r = api.get(key or isin, resource, params)
        ok, payload = (r.status_code == 200), None
        if ok:
            try:
                j = r.json()
                ok = j.get("status") == "success"
                payload = j.get("data")
            except Exception:
                ok = False
        log(conn, isin, resource, r.status_code, ok, "" if ok else (r.text[:180]))
        return payload if ok else None

    if (d := call("profile")) is not None:
        store_profile(conn, isin, d, ts)
    if (d := call("key-ratios")) is not None:
        store_ratios(conn, isin, d, ts)
    if (d := call("share-holdings")) is not None:
        store_shareholding(conn, isin, d, ts)
    if (d := call("corporate-actions")) is not None:
        store_corporate_actions(conn, isin, d, ts)
    if (d := call("competitors", key=f"NSE_EQ|{isin}")) is not None:  # competitors keys on instrument_key, not ISIN
        store_competitors(conn, isin, d, ts)

    for statement, res in (("income", "income-statement"),
                           ("balance", "balance-sheet"),
                           ("cashflow", "cash-flow")):
        for basis in bases:
            for tp in ("yearly", "quarterly"):
                if (d := call(res, {"type": basis, "time_period": tp, "fs": "true"})) is not None:
                    store_statement(conn, isin, statement, d, ts)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--universe", choices=["niftyit", "nifty50", "all"], default="niftyit")
    g.add_argument("--symbols")
    g.add_argument("--isins")
    ap.add_argument("--basis", choices=["consolidated", "standalone"], default="consolidated")
    ap.add_argument("--standalone-too", action="store_true")
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--init-only", action="store_true", help="create schema/tables and exit")
    args = ap.parse_args()

    conn = connect()
    init_db(conn)
    if args.init_only:
        print(f"Schema '{SCHEMA}' initialized.")
        conn.close()
        return

    token = resolve_token()
    if not token:
        sys.exit("No token found. Set UPSTOX_ANALYTICS_TOKEN (preferred) or "
                 "UPSTOX_ACCESS_TOKEN in .env.")

    sym2isin = {} if args.isins else build_symbol_isin_map()
    universe = resolve_universe(args, sym2isin)
    if not universe:
        sys.exit("Empty universe.")

    bases = [args.basis] + (["standalone"] if args.standalone_too and args.basis == "consolidated" else [])
    api = Api(token, pause=args.pause)
    ts = now_ts()
    for symbol, isin in universe:
        print(f"[{symbol or isin}] {isin} ...", flush=True)
        try:
            fetch_company(api, conn, symbol, isin, bases, ts)
        except Exception as e:
            conn.rollback()
            print(f"  ! {isin} failed: {e}", file=sys.stderr)
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
