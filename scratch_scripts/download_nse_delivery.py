"""
download_nse_delivery.py
------------------------
Download NSE's daily security-wise delivery report (sec_bhavdata_full) and derive
the ONE number that matters for the money-vs-sentiment read:

    NIFTY-50 delivery % — how much of the day's trading was REAL delivery
    (investors actually taking/giving stock) vs intraday churn.

Interpretation (pair it with the day's index move):
  * HIGH delivery % on a DOWN day  -> conviction distribution (real selling / repositioning).
  * LOW  delivery % on a DOWN day  -> intraday churn / froth (move likely to mean-revert).
  * HIGH delivery % on an UP day    -> conviction accumulation.
Delivery is EOD daily data (one value per session), so it's a ~1-day-lagged CONTEXT
read, not an intraday signal — same cadence class as the FII/DII flows.

Usage:
    python scratch_scripts/download_nse_delivery.py YYYY-MM-DD [SYM1,SYM2,...]
"""
import urllib.request
import csv
import io
import os
import sys
from datetime import datetime

# reuse the framework's NIFTY-50 weights (source of truth)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategy_framework"))
try:
    from config import constituents as K
    _NIFTY = {s: K.weight_of(s) for s in K.symbols()}
except Exception:
    _NIFTY = {}


def download_delivery_report(date_str: str):
    """Daily security-wise delivery position CSV from NSE. Date: YYYY-MM-DD."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print("Error: Date must be in YYYY-MM-DD format.")
        return None
    formatted_date = dt.strftime("%d%m%Y")
    url = f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{formatted_date}.csv"
    print(f"Target URL: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.nseindia.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}. NSE may not have data for this date "
              "(weekend/holiday/not yet published).")
        return None
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None


def _to_num(x):
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_bhavdata(csv_content: str) -> dict:
    """PURE parser -> {symbol: {traded, deliv, deliv_pct}} for EQ series only."""
    reader = csv.reader(io.StringIO(csv_content.strip()))
    header = [h.strip() for h in next(reader)]
    ix = {name: header.index(name) for name in header}
    need = ("SYMBOL", "SERIES", "TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER")
    for n in need:
        if n not in ix:
            raise ValueError(f"missing column {n}; have {header}")
    out = {}
    for row in reader:
        if len(row) <= ix["DELIV_PER"]:
            continue
        if row[ix["SERIES"]].strip() != "EQ":
            continue
        sym = row[ix["SYMBOL"]].strip()
        traded = _to_num(row[ix["TTL_TRD_QNTY"]])
        deliv = _to_num(row[ix["DELIV_QTY"]])
        pct = _to_num(row[ix["DELIV_PER"]])
        if traded is None:
            continue
        out[sym] = {"traded": traded, "deliv": deliv or 0.0, "deliv_pct": pct}
    return out


def market_delivery_summary(rows: dict) -> dict:
    """Whole-market EQ delivery ratio + NIFTY-50 traded-weighted and index-weighted %."""
    # whole market (all EQ)
    tot_tr = sum(r["traded"] for r in rows.values())
    tot_dl = sum(r["deliv"] for r in rows.values())
    mkt = 100.0 * tot_dl / tot_tr if tot_tr else None

    # NIFTY-50 subset
    present = {s: rows[s] for s in _NIFTY if s in rows} if _NIFTY else {}
    n_tr = sum(r["traded"] for r in present.values())
    n_dl = sum(r["deliv"] for r in present.values())
    nifty_agg = 100.0 * n_dl / n_tr if n_tr else None      # traded-qty weighted (deliv ratio)

    # index-weighted average of per-stock delivery %
    wsum = pctsum = 0.0
    for s, r in present.items():
        if r["deliv_pct"] is None:
            continue
        w = _NIFTY[s]
        wsum += w
        pctsum += w * r["deliv_pct"]
    nifty_idx = (pctsum / wsum) if wsum else None

    return {"market_eq_pct": mkt, "nifty50_traded_weighted_pct": nifty_agg,
            "nifty50_index_weighted_pct": nifty_idx, "nifty50_names_found": len(present)}


_STATE_DIR = os.environ.get("NIFTY_STATE_DIR",
                            os.path.join(os.path.dirname(__file__), "..", ".state"))
_DELIV_CACHE = os.path.join(_STATE_DIR, "delivery_cache.json")


def _persist_delivery(date_str: str, summary: dict) -> None:
    """Append this day's NIFTY-50 delivery % to .state/delivery_cache.json (keyed by
    date), so the desk backend (/api/money-sentiment) can read latest + baseline."""
    import json
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        cache = {}
        if os.path.exists(_DELIV_CACHE):
            with open(_DELIV_CACHE) as f:
                cache = json.load(f)
        cache[date_str] = {
            "nifty50_index_weighted_pct": summary.get("nifty50_index_weighted_pct"),
            "nifty50_traded_weighted_pct": summary.get("nifty50_traded_weighted_pct"),
            "market_eq_pct": summary.get("market_eq_pct"),
            "names_found": summary.get("nifty50_names_found"),
        }
        # keep last 120 dates
        for k in sorted(cache)[:-120]:
            cache.pop(k, None)
        with open(_DELIV_CACHE, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        print(f"  (saved to {_DELIV_CACHE})")
    except Exception as e:
        print(f"  (could not persist delivery cache: {e})")


def main():
    if len(sys.argv) < 2:
        print("Usage: python download_nse_delivery.py <YYYY-MM-DD> [filter_symbols_comma_separated]")
        sys.exit(1)
    target_date = sys.argv[1]
    filter_syms = None
    if len(sys.argv) > 2:
        filter_syms = set(s.strip().upper() for s in sys.argv[2].split(","))

    print(f"Downloading NSE Delivery Position Report for {target_date}...")
    csv_content = download_delivery_report(target_date)
    if not csv_content:
        sys.exit(1)
    rows = parse_bhavdata(csv_content)
    s = market_delivery_summary(rows)
    _persist_delivery(target_date, s)   # write to .state so the desk backend can read it

    print("\n=== MARKET-LEVEL DELIVERY (the money-vs-sentiment read) ===")
    def fmt(x): return f"{x:.2f}%" if x is not None else "n/a"
    print(f"  Whole market (all EQ)        : {fmt(s['market_eq_pct'])}")
    print(f"  NIFTY-50 (traded-qty weighted): {fmt(s['nifty50_traded_weighted_pct'])}   "
          f"[{s['nifty50_names_found']}/50 names found]")
    print(f"  NIFTY-50 (index weighted)     : {fmt(s['nifty50_index_weighted_pct'])}")
    print("  Read: pair with the day's index move — HIGH delivery on a down day = real")
    print("        distribution; LOW delivery = intraday churn / froth (likely to revert).")

    # per-symbol table (filtered if asked, else default to all NIFTY 50 symbols present)
    if filter_syms:
        picks = [(k, rows[k]) for k in filter_syms if k in rows]
    else:
        # Sort by symbol name or delivery %? Let's sort Nifty-50 by delivery percentage descending to see the highest/lowest conviction.
        nifty_picks = [(k, rows[k]) for k in _NIFTY if k in rows]
        picks = sorted(nifty_picks, key=lambda kv: (kv[1]["deliv_pct"] or -1), reverse=True)
        
    print(f"\n| Symbol | Total Traded Qty | Deliverable Qty | Delivery % |")
    print("| :--- | ---: | ---: | ---: |")
    for sym, r in picks:
        pct = f"{r['deliv_pct']}%" if r["deliv_pct"] is not None else "n/a"
        print(f"| **{sym}** | {int(r['traded']):,} | {int(r['deliv']):,} | {pct} |")


if __name__ == "__main__":
    main()
