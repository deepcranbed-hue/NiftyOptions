#!/usr/bin/env python3
"""
screener_tables.py — scrape Screener.in company-page tables for ANY set of symbols.

Supersedes `download_screener_tables.py` (which superseded the Excel-export route in
`download_screener.py`). Kept general on purpose: nothing here knows about the Nifty 50.
Point it at any CSV with a `Symbol` column, or pass symbols directly.

WHY NOT THE EXCEL EXPORT
------------------------
Screener's "Export to Excel" workbook is capped at 10 annual + 10 quarterly periods and
flattens every company into one generic template. The rendered page gives 13 quarters,
12 annual years, published per-quarter EPS, and — decisively — a SEPARATE TEMPLATE for
lenders carrying Financing Profit, Financing Margin %, Gross/Net NPA % and Deposits.
Those rows are why the aggregate operating margin had to exclude financials (correction
C26): the export's `sales` for a bank is interest earned and its `operating profit` does
not net interest expense, which gave SBI a 70.8% "margin". Financing Profit does net it.

THREE THINGS THIS SCRIPT LEARNED THE HARD WAY
---------------------------------------------
1. AN EMPTY PAGE LOOKS LIKE A FULL ONE. Screener renders the section, every row label and
   every "+" schedule button even when it holds no figures for that basis. SBILIFE's
   consolidated page is exactly that shell. The original acceptance test was
   `'id="quarters"' in html`, which passed, so the standalone fallback never fired and
   SBILIFE entered the panel with 0 quarters while the summary counted it as one of 50
   fetched successfully. An empty result presenting as a success is the failure that
   matters, so the gate is now the DATA: a real period column and a parseable number.

2. THE CANONICAL TAG IS NOT THE PAGE YOU GOT. Screener emits `<link rel=canonical>`
   pointing at the consolidated URL even while serving standalone. Inferring basis from
   the markup labelled TCS standalone (no canonical at all) and HDFC Bank consolidated
   (a page fetched as standalone) — exactly inverted. Basis is now taken from the
   post-redirect `response.url` at fetch time and written to a log. Never inferred.

3. PAGE AND EXPORT DISAGREE ON NET PROFIT. Validated on 40 overlapping quarterly values:
   Sales and Operating Profit match to the rupee; Net Profit reads 0.39-0.59% ABOVE the
   export on every TCS quarter, never below (minority interest). A one-sided offset that
   stable is a definition gap, not noise. Rebuild any series END TO END from one source —
   a spliced series gets a ~0.5% step that will read as a growth inflection.

BASIS
-----
Consolidated and standalone are DIFFERENT COMPANIES for aggregation purposes: a bank's
standalone profit excludes its NBFC, broking, AMC and insurance subsidiaries. Mixing them
inside one panel is the C24 failure mode — coverage changing underneath a series. So basis
is part of the cache key, part of the CSV, and reported.

  --basis auto          one page per symbol: consolidated preferred, standalone fallback,
                        except symbols in the standalone-preferred config. Fast. Default.
  --basis consolidated  force consolidated only
  --basis standalone    force standalone only
  --basis both          BOTH where they exist. Twice the fetches, and the only mode that
                        lets you MEASURE the subsidiary gap rather than assume it.

USAGE
-----
  python3 screener_tables.py fetch                       # network, resumable
  python3 screener_tables.py parse                       # offline, no network
  python3 screener_tables.py                             # fetch then parse
  python3 screener_tables.py fetch --basis both --only HDFCBANK,ICICIBANK,SBIN
  python3 screener_tables.py --universe my_list.csv      # any CSV with a Symbol column
  python3 screener_tables.py --symbols TCS,INFY,WIPRO    # ad hoc, no file needed
  python3 screener_tables.py migrate                     # adopt an old flat HTML cache

Needs SCREENER_SESSION_ID in .env. Plain requests — these pages are server-rendered and
Playwright was only ever needed for the export button, which we no longer click.

OUTPUT
  screener_html/<SYMBOL>.<basis>.html     raw pages, the cache
  screener_html/_fetch_log.json           {"SYM.basis": {final_url, basis, fetched_at}}
  screener_page_tables.csv                symbol,basis,template,section,period,metric,value
  screener_page_coverage.csv              one row per (symbol, basis)
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import json
import os
import random
import re
import sys
import time
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("need: pip install requests beautifulsoup4 lxml python-dotenv")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
HTML_DIR = HERE / "screener_html"
LOG = HTML_DIR / "_fetch_log.json"
DEFAULT_UNIVERSE = ROOT / "nifty-50-stock-list.csv"
CONFIG = HERE / "screener_tables_config.json"
OUT_TABLES = HERE / "screener_page_tables.csv"
OUT_COVERAGE = HERE / "screener_page_coverage.csv"

# Screener's ticker differs from the exchange's for some names. Extend via the config
# file rather than editing this — the config wins and is where universe-specific
# knowledge belongs.
ALIAS_DEFAULT = {"LTIM": "LTM", "TATAMOTORS": "TMCV", "ZOMATO": "ETERNAL"}

# Symbols to try STANDALONE first under --basis auto. This is a preference, not a
# correctness claim: for a lender the standalone statements are what make NIM and NPA
# interpretable, and it matches the basis delivery_history.json already holds. It is
# deliberately NOT used to decide whether a company is a lender — that is detected from
# the parsed rows. `--basis both` sidesteps this list entirely.
STANDALONE_PREFERRED_DEFAULT = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK",
]

SECTIONS = {"quarters": "quarters", "profit-loss": "pnl", "balance-sheet": "balance_sheet",
            "cash-flow": "cash_flow", "ratios": "ratios"}

# Rows that prove Screener served its lender template. Detected, never assumed.
BANK_MARKERS = {"Financing Profit", "Financing Margin %"}

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
LAST_DAY = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
            7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


# --------------------------------------------------------------------------- config
def load_config() -> dict:
    cfg = {"alias": dict(ALIAS_DEFAULT),
           "standalone_preferred": list(STANDALONE_PREFERRED_DEFAULT)}
    if CONFIG.exists():
        try:
            user = json.loads(CONFIG.read_text())
        except Exception as exc:
            sys.exit(f"{CONFIG.name} is not valid JSON: {exc}")
        cfg["alias"].update(user.get("alias") or {})
        if "standalone_preferred" in user:
            cfg["standalone_preferred"] = list(user["standalone_preferred"])
    return cfg


def universe(path: Path | None, symbols: str | None) -> list[str]:
    if symbols:
        return [s.strip().upper() for s in symbols.split(",") if s.strip()]
    path = path or DEFAULT_UNIVERSE
    if not path.exists():
        sys.exit(f"no universe file at {path} — pass --universe or --symbols")
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        col = next((c for c in (rdr.fieldnames or []) if c.strip().lower() == "symbol"), None)
        if not col:
            sys.exit(f"{path.name} has no 'Symbol' column (found: {rdr.fieldnames})")
        return [r[col].strip().upper() for r in rdr if r.get(col, "").strip()]


# --------------------------------------------------------------------------- helpers
def to_period(label: str) -> str | None:
    """'Jun 2023' -> '2023-06-30'. None for TTM and anything unparseable."""
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{4})$", label.strip())
    if not m:
        return None
    mon = MONTHS.get(m.group(1))
    if not mon:
        return None
    year = int(m.group(2))
    day = 29 if (mon == 2 and year % 4 == 0 and (year % 100 or year % 400 == 0)) else LAST_DAY[mon]
    return f"{year:04d}-{mon:02d}-{day:02d}"


def to_number(cell: str) -> float | None:
    t = cell.replace(",", "").replace("%", "").replace("₹", "").strip()
    if t in ("", "-", "--"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def quarters_table(soup: BeautifulSoup):
    sec = soup.find("section", id="quarters")
    return sec.find("table", class_="data-table") if sec else None


def has_quarterly_data(html: str) -> bool:
    """Real numbers, or just a shell? See note 1 in the module docstring."""
    try:
        table = quarters_table(BeautifulSoup(html, "lxml"))
    except Exception:
        return False
    if not table:
        return False
    heads = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    if not any(to_period(h) for h in heads[1:]):
        return False
    body = table.find("tbody")
    if not body:
        return False
    return any(to_number(td.get_text(" ", strip=True)) is not None
               for tr in body.find_all("tr") for td in tr.find_all("td")[1:])


def cache_path(sym: str, basis: str) -> Path:
    return HTML_DIR / f"{sym}.{basis}.html"


def read_log() -> dict:
    if not LOG.exists():
        return {}
    try:
        return json.loads(LOG.read_text())
    except Exception:
        return {}


# --------------------------------------------------------------------------- migrate
def migrate() -> None:
    """Adopt a pre-basis flat cache (<SYMBOL>.html) into <SYMBOL>.<basis>.html."""
    if not HTML_DIR.exists():
        print("no cache to migrate")
        return
    log = read_log()
    moved = orphan = 0
    for f in sorted(HTML_DIR.glob("*.html")):
        parts = f.stem.split(".")
        if len(parts) != 1:
            continue                                    # already basis-tagged
        sym = parts[0]
        rec = log.get(sym)
        basis = rec.get("basis") if rec else None
        if not basis:
            # No log entry: refuse to guess. A wrongly-labelled basis silently corrupts
            # any panel built on it, which is worse than a refetch.
            print(f"  {sym:12s} no logged basis — leaving in place, refetch to resolve")
            orphan += 1
            continue
        f.rename(cache_path(sym, basis))
        log[f"{sym}.{basis}"] = {**rec, "migrated_from": f.name}
        log.pop(sym, None)
        moved += 1
    LOG.write_text(json.dumps(log, indent=1, sort_keys=True))
    print(f"migrate: {moved} renamed, {orphan} left for refetch")


# --------------------------------------------------------------------------- fetch
def bases_for(sym: str, mode: str, cfg: dict) -> list[str]:
    if mode == "both":
        return ["consolidated", "standalone"]
    if mode in ("consolidated", "standalone"):
        return [mode]
    return (["standalone", "consolidated"] if sym in cfg["standalone_preferred"]
            else ["consolidated", "standalone"])


def url_for(scr: str, basis: str) -> str:
    return (f"https://www.screener.in/company/{scr}/consolidated/" if basis == "consolidated"
            else f"https://www.screener.in/company/{scr}/")


def fetch(symbols: list[str], mode: str, force: bool, cfg: dict) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    sid = os.getenv("SCREENER_SESSION_ID")
    if not sid:
        sys.exit("SCREENER_SESSION_ID not in .env — the page needs a logged-in session.")

    HTML_DIR.mkdir(exist_ok=True)
    log = read_log()
    sess = requests.Session()
    sess.cookies.set("sessionid", sid, domain=".screener.in")
    sess.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9"})

    saved = skipped = empty = failed = 0
    for i, sym in enumerate(symbols, 1):
        scr = cfg["alias"].get(sym, sym)

        # A "target" is one thing we want on disk. In `both` mode each basis is its own
        # target, so both get fetched. Otherwise there is a single target and the list
        # inside it is a PREFERENCE ORDER — the first basis carrying real data wins and
        # the rest are not requested.
        if mode == "both":
            targets = [["consolidated"], ["standalone"]]
        else:
            targets = [bases_for(sym, mode, cfg)]

        for order in targets:
            if not force and any(cache_path(sym, b).exists() for b in order):
                skipped += 1
                continue

            print(f"[{i:3d}/{len(symbols)}] {sym:12s}", end=" ", flush=True)
            got = False
            for basis in order:
                nap = random.uniform(5, 12)
                print(f"({basis[:4]} {nap:.0f}s)", end=" ", flush=True)
                time.sleep(nap)
                try:
                    r = sess.get(url_for(scr, basis), timeout=30)
                except Exception as exc:
                    print(f"ERROR {exc}")
                    break
                if r.status_code == 429:
                    print("429 rate-limited — stopping so the session is not burned.")
                    print("     Wait a few minutes and re-run; cached pages are skipped.")
                    LOG.write_text(json.dumps(log, indent=1, sort_keys=True))
                    return
                if r.status_code != 200:
                    continue
                if 'href="/login/"' in r.text and 'id="quarters"' not in r.text:
                    print("session cookie rejected — refresh SCREENER_SESSION_ID in .env")
                    LOG.write_text(json.dumps(log, indent=1, sort_keys=True))
                    return
                if not has_quarterly_data(r.text):
                    empty += 1
                    continue          # shell page; try the next basis. This rescues SBILIFE.
                real = "consolidated" if "/consolidated/" in r.url else "standalone"
                cache_path(sym, real).write_text(r.text, encoding="utf-8")
                log[f"{sym}.{real}"] = {
                    "final_url": r.url, "basis": real,
                    "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")}
                LOG.write_text(json.dumps(log, indent=1, sort_keys=True))
                print(f"-> {real} {len(r.text)//1024} KB")
                saved += 1
                got = True
                break
            if not got:
                print("no quarterly DATA on any basis tried")
                failed += 1

    print(f"\nfetch: {saved} saved, {skipped} cached, {empty} empty shells skipped, "
          f"{failed} failed")


# --------------------------------------------------------------------------- parse
def parse_one(sym: str, basis: str, html: str) -> tuple[list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    cov = {"symbol": sym, "basis": basis, "template": "generic"}

    for sec_id, sec_name in SECTIONS.items():
        sec = soup.find("section", id=sec_id)
        table = sec.find("table", class_="data-table") if sec else None
        if not table:
            continue
        heads = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        periods = [to_period(h) for h in heads[1:]]
        body = table.find("tbody")
        if not body:
            continue

        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            metric = tds[0].get_text(" ", strip=True).rstrip("+").strip()
            if not metric or metric.lower().startswith("raw pdf"):
                continue
            if metric in BANK_MARKERS:
                cov["template"] = "bank"
            for period, td in zip(periods, tds[1:]):
                # period is None for TTM and any other non-dated column. Dropped, not
                # coerced: a TTM figure sitting inside a period series is a silent lie.
                if period is None:
                    continue
                val = to_number(td.get_text(" ", strip=True))
                if val is None:
                    continue
                rows.append({"symbol": sym, "basis": basis, "template": None,
                             "section": sec_name, "period": period,
                             "metric": metric, "value": val})

        live = [p for p in periods if p]
        cov[f"n_{sec_name}"] = len(live)
        if live and sec_name == "quarters":
            cov["q_first"], cov["q_last"] = live[0], live[-1]
        if live and sec_name == "pnl":
            cov["a_first"], cov["a_last"] = live[0], live[-1]

    metrics = {r["metric"] for r in rows}
    cov["has_bank_rows"] = int(BANK_MARKERS <= metrics)
    cov["has_npa"] = int("Gross NPA %" in metrics)
    cov["has_deposits"] = int("Deposits" in metrics)
    cov["has_eps"] = int(any(m.startswith("EPS") for m in metrics))
    cov["n_values"] = len(rows)
    for r in rows:
        r["template"] = cov["template"]
    return rows, cov


def parse(symbols: list[str]) -> None:
    if not HTML_DIR.exists():
        sys.exit(f"no {HTML_DIR} — run `fetch` first")

    want = set(symbols)
    all_rows: list[dict] = []
    covs: list[dict] = []
    stale: list[str] = []

    for f in sorted(HTML_DIR.glob("*.*.html")):
        sym, basis = f.stem.rsplit(".", 1)
        if sym not in want:
            continue
        try:
            rows, cov = parse_one(sym, basis, f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  {sym}.{basis}: PARSE FAILED: {exc}")
            continue
        if not rows:
            stale.append(f"{sym}.{basis}")
            continue
        all_rows.extend(rows)
        covs.append(cov)

    flat = [f.stem for f in HTML_DIR.glob("*.html") if "." not in f.stem]
    if flat:
        print(f"  {len(flat)} un-migrated flat cache files ignored "
              f"({', '.join(flat[:5])}{'...' if len(flat) > 5 else ''})")
        print("  run `migrate` to adopt them, or refetch.")

    if not all_rows:
        sys.exit("parsed nothing — check the cache holds real company pages")

    with open(OUT_TABLES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "basis", "template", "section",
                                           "period", "metric", "value"])
        w.writeheader()
        w.writerows(all_rows)

    keys = ["symbol", "basis", "template", "n_quarters", "q_first", "q_last", "n_pnl",
            "a_first", "a_last", "n_balance_sheet", "n_cash_flow", "n_ratios",
            "has_bank_rows", "has_npa", "has_deposits", "has_eps", "n_values"]
    with open(OUT_COVERAGE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for c in sorted(covs, key=lambda r: (r["symbol"], r["basis"])):
            w.writerow({k: c.get(k, "") for k in keys})

    # ------------------------------------------------------------------ the report
    syms = {c["symbol"] for c in covs}
    print(f"\nwrote {OUT_TABLES.name}   {len(all_rows):,} values, "
          f"{len(syms)} companies, {len(covs)} (symbol, basis) pairs")
    print(f"wrote {OUT_COVERAGE.name}\n")

    nq = [c.get("n_quarters", 0) for c in covs]
    na = [c.get("n_pnl", 0) for c in covs]
    print(f"  quarters   min {min(nq)}  median {sorted(nq)[len(nq)//2]}  max {max(nq)}")
    print(f"  annual     min {min(na)}  median {sorted(na)[len(na)//2]}  max {max(na)}")
    print(f"  basis      {dict(collections.Counter(c['basis'] for c in covs))}")

    both = [s for s in syms if sum(1 for c in covs if c["symbol"] == s) > 1]
    if both:
        print(f"  both bases {len(both)}: {', '.join(sorted(both))}")
        print("    -> subsidiary gap is MEASURABLE for these; see bank_basis_gap.py")
    mixed = {c["basis"] for c in covs}
    if len(mixed) > 1 and not both:
        print("    NOTE: panel mixes bases across companies. Standalone excludes")
        print("    subsidiaries; consolidated includes them. Decide before aggregating.")

    banks = sorted({c["symbol"] for c in covs if c["template"] == "bank"})
    print(f"\n  lender template: {len(banks)}" + (f" — {', '.join(banks)}" if banks else ""))
    if banks:
        b = [c for c in covs if c["template"] == "bank"]
        print(f"    Financing Profit + Margin {sum(c['has_bank_rows'] for c in b)}/{len(b)}"
              f"   Gross NPA % {sum(c['has_npa'] for c in b)}/{len(b)}"
              f"   Deposits {sum(c['has_deposits'] for c in b)}/{len(b)}")

    thin = [c for c in covs if c.get("n_quarters", 0) < max(nq)]
    if thin:
        print("\n  thin quarterly history (recent listings / year-end changes are expected):")
        for c in sorted(thin, key=lambda r: r.get("n_quarters", 0)):
            print(f"    {c['symbol']:12s} {c['basis']:12s} {c.get('n_quarters',0):2d}q "
                  f"{c.get('n_pnl',0):2d}a  {c.get('q_first','')} .. {c.get('q_last','')}")
    if stale:
        print(f"\n  EMPTY cached pages ({len(stale)}): {', '.join(stale)}")
        print("  refetch these — a shell page is not a company with no data.")

    missing = sorted(want - syms)
    if missing:
        print(f"\n  NOT IN CACHE ({len(missing)}): {', '.join(missing)}")

    best = max(nq)
    print(f"\n  deepest quarterly history: {best} quarters ({best/4:.2f} years)")
    if best < 20:
        print("  Correction C27 needs ~20 (five years) to put our reconstructed P/E on")
        print("  NSE's published footing. Screener's page cannot go deeper; that needs")
        print("  BSE's per-company XBRL results archive.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["fetch", "parse", "all", "migrate"])
    ap.add_argument("--basis", default="auto",
                    choices=["auto", "consolidated", "standalone", "both"])
    ap.add_argument("--force", action="store_true", help="refetch pages already cached")
    ap.add_argument("--only", help="comma-separated symbols to restrict to")
    ap.add_argument("--universe", type=Path, help="CSV with a Symbol column")
    ap.add_argument("--symbols", help="comma-separated symbols instead of a file")
    a = ap.parse_args()

    if a.mode == "migrate":
        migrate()
        return

    cfg = load_config()
    syms = universe(a.universe, a.symbols)
    if a.only:
        want = {s.strip().upper() for s in a.only.split(",")}
        syms = [s for s in syms if s in want] or sorted(want)

    if a.mode in ("fetch", "all"):
        fetch(syms, a.basis, a.force, cfg)
    if a.mode in ("parse", "all"):
        parse(syms)


if __name__ == "__main__":
    main()
