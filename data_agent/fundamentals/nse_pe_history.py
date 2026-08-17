#!/usr/bin/env python3
"""
nse_pe_history.py — fetch NSE Indices' OFFICIAL daily P/E, P/B and dividend yield.

WHY REPLACE OUR RECONSTRUCTION
------------------------------
We rebuilt the index P/E bottom-up because we were building everything bottom-up. That
reconstruction has a known, measured defect (correction C27): our EPS steps ONCE a year,
92 days after 31-March, while NSE's denominator rolls every quarter. Between steps our
denominator is stale, so the printed P/E is wrong — and the sign of the error FLIPS with
the growth rate. In a growth year stale-low EPS prints the P/E too high; in FY20, when
earnings fell 12.1%, stale-high EPS printed it too LOW.

That is why the scalar 1.082 deflator we currently apply is not merely imprecise, it is
wrong-signed in exactly the years doing most of the work in the distribution. No single
constant can fix a bias whose sign varies.

NSE publishes the thing we were approximating. Its documented method is

    P/E = Index Market Capitalisation / Gross Earnings

on the constituents' trailing FOUR QUARTERS, free-float adjusted, consolidated financials
where available and standalone otherwise, computed daily by NSE Indices. A rolling
four-quarter denominator is precisely the measure a valuation series wants. Using an
internally reconstructed number when an official one exists is unnecessary model risk,
and P/E is one of the two inputs to `level = EPS x exit_PE`.

WHAT THIS CHANGES, AND WHAT IT DOES NOT
---------------------------------------
It can move the current percentile, the historical median, the starting multiple, the
2026 de-rating magnitude and therefore the forecast grid. It does NOT overturn the
finding that the multiple has not mean-reverted (H53) — that is a qualitative result
about direction, not level. Expect the numbers to move and the conclusions to hold.

TWO THINGS TO WATCH
-------------------
1. NSE switched from STANDALONE to CONSOLIDATED earnings around April 2021. Sources
   disagree on this — indexpe.in states the switch happened, PrimeInvestor says the
   published P/E "continues to be based on standalone earnings". The series is therefore
   flagged, not silently spliced: `--from 2021-04-01` gives a clean single-regime window,
   which is also the five-year window our 23.88-vs-22.06 median disagreement lives in.
2. This endpoint is undocumented and NSE changes it without notice. It is isolated in one
   function so a break is one fix, and the raw response is saved so a parse bug never
   costs a refetch.

    python3 nse_pe_history.py                      # 2018-01-01 -> today
    python3 nse_pe_history.py --from 2021-04-01    # consolidated era only
    python3 nse_pe_history.py --index "NIFTY 500"

OUTPUT
  .state/nse_pe_history_raw.json   the untouched response
  nse_pe_history.csv               date,pe,pb,div_yield
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("need: pip install playwright")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent if HERE.name == "fundamentals" else HERE
RAW = ROOT / ".state" / "nse_pe_history_raw.json"
OUT = ROOT / "nse_pe_history.csv"

BASE = "https://niftyindices.com"
ENDPOINT = f"{BASE}/Backpage.aspx/getpepbHistoricaldataDBtoString"


def fetch(index: str, start: str, end: str) -> list[dict]:
    """One function, deliberately. When NSE changes the endpoint, this is the only edit."""
    d = lambda x: datetime.date.fromisoformat(x).strftime("%d-%b-%Y")
    
    payload = {"name": index, "startDate": d(start), "endDate": d(end)}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        # Navigate to warm cookies/tokens and bypass Akamai
        page.goto(f"{BASE}/reports/historical-data", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)  # Let Akamai script settle
        
        js_code = """
        async (payload) => {
            const response = await fetch('/Backpage.aspx/getpepbHistoricaldataDBtoString', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json; charset=UTF-8',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                throw new Error('HTTP error ' + response.status);
            }
            return await response.json();
        }
        """
        try:
            body = page.evaluate(js_code, payload)
        except Exception as exc:
            browser.close()
            sys.exit(f"Failed to execute API call inside browser context: {exc}")
            
        browser.close()

    RAW.parent.mkdir(exist_ok=True)
    RAW.write_text(json.dumps(body))

    try:
        rows = json.loads(body["d"]) if isinstance(body.get("d"), str) else body["d"]
    except Exception as exc:
        sys.exit(f"could not parse NSE's response ({exc}).\n"
                 f"  Raw body saved to {RAW} — inspect it and fix the parse, no refetch needed.")
    if not rows:
        sys.exit("NSE returned an empty series — check the index name and date range.")
    return rows


def norm(rows: list[dict]) -> list[dict]:
    def pick(r, *names):
        for n in names:
            for k in r:
                if k.strip().lower().replace(" ", "") == n:
                    v = str(r[k]).strip()
                    if v and v not in ("-", "NA"):
                        try:
                            return float(v)
                        except ValueError:
                            return v
        return None

    out = []
    for r in rows:
        raw = pick(r, "date", "historicaldate", "dateindex")
        if not raw:
            continue
        dt = None
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.datetime.strptime(str(raw).strip(), fmt).date()
                break
            except ValueError:
                continue
        if not dt:
            continue
        out.append({"date": dt.isoformat(), "pe": pick(r, "pe"), "pb": pick(r, "pb"),
                    "div_yield": pick(r, "divyield", "dy")})
    out.sort(key=lambda x: x["date"])
    return [r for r in out if r["pe"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY 50")
    ap.add_argument("--from", dest="start", default="2018-01-01")
    ap.add_argument("--to", dest="end", default=datetime.date.today().isoformat())
    a = ap.parse_args()

    print(f"fetching {a.index} P/E from NSE Indices, {a.start} -> {a.end} ...")
    rows = norm(fetch(a.index, a.start, a.end))
    if not rows:
        sys.exit(f"parsed 0 usable rows — raw response is at {RAW}")

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "pe", "pb", "div_yield"])
        w.writeheader()
        w.writerows(rows)

    pes = sorted(r["pe"] for r in rows)
    med = pes[len(pes) // 2]
    today = rows[-1]["pe"]
    pct = 100 * sum(1 for p in pes if p < today) / len(pes)
    five = sorted(r["pe"] for r in rows if r["date"] >= "2021-08-14")

    print(f"\nwrote {OUT.name}   {len(rows):,} rows   {rows[0]['date']} .. {rows[-1]['date']}")
    print(f"  latest   {today:.2f}   ({rows[-1]['date']})")
    print(f"  median   {med:.2f}   range {pes[0]:.2f} - {pes[-1]:.2f}")
    print(f"  latest sits at the {pct:.1f}th percentile of this window")
    if five:
        fmed = sorted(five)[len(five) // 2]
        fpct = 100 * sum(1 for p in five if p < today) / len(five)
        print(f"  trailing 5y: median {fmed:.2f}, latest at {fpct:.1f}th percentile")

    print("\n  COMPARE AGAINST OUR RECONSTRUCTION (correction C27):")
    print("    ours     today 20.39   5y median 23.88   percentile  3.0 (10.3 adjusted)")
    print("    3rd-party NSE-derived, 14-Aug: 20.56 / 20.56 / 20.6, 5y median 22.06")
    print("  If NSE's own 5y median lands near 22.06, the 1.082 bias measured in C27 is")
    print("  confirmed from the source and the reconstruction can be retired for the")
    print("  distribution. If it lands near 23.88 instead, the bias story is wrong and")
    print("  C27 needs reopening rather than closing.")
    print("\n  NOTE: NSE moved from standalone to consolidated earnings around Apr-2021 and")
    print("  sources disagree on whether that switch happened. Do not treat pre-2021 and")
    print("  post-2021 as one regime until that is settled.")


if __name__ == "__main__":
    main()
