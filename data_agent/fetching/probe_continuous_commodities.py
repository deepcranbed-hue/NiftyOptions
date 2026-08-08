#!/usr/bin/env python3
"""probe_continuous_commodities.py — check Yahoo continuous futures before trusting them.

Run this BEFORE wiring GC=F / SI=F / HG=F into the pipeline. It answers three
questions that a bar count alone cannot:

  1. Does the ticker resolve at all?  (LTIM.NS returned an empty frame for weeks
     because NSE renamed the symbol — a dead ticker does not raise.)
  2. Is the series actually continuous?  A front-month contract that Yahoo rolls
     and back-adjusts has no discontinuities; a single contract does. Crude is the
     reference: 2,163 bars over 8.5 years with 2 gaps, both real 2020 oil shocks.
  3. Do the UNITS reconcile with what we already store?

Point 3 is the one worth the effort. MCX gold is quoted in INR per 10 grams; GC=F
is USD per troy ounce. Those are the same metal in different clothes, and if the
conversion does not land within a few percent then the two symbols are not tracking
the same thing and pairing them would be the CRUDEOIL currency mix all over again:

    USD/oz  x  USDINR  /  3.11035  =  INR per 10 g          (1 troy oz = 31.1035 g)

Silver on MCX is INR per KILOGRAM; copper is INR per kilogram too. Their factors
differ, so each is checked with its own conversion rather than one blanket rule.

Nothing is written. This only reads Yahoo and the existing database.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.append(_ROOT)

GRAMS_PER_TROY_OZ = 31.1035

# db symbol -> (candidate Yahoo tickers, how to convert USD quote -> the MCX unit)
#   gold   : USD/troy oz  -> INR per 10 g
#   silver : USD/troy oz  -> INR per kg
#   copper : USD/lb       -> INR per kg      (1 lb = 0.453592 kg)
PROBES = {
    "GOLD":   (["GC=F"], lambda usd, fx: usd * fx / GRAMS_PER_TROY_OZ * 10.0),
    "SILVER": (["SI=F"], lambda usd, fx: usd * fx / GRAMS_PER_TROY_OZ * 1000.0),
    "COPPER": (["HG=F"], lambda usd, fx: usd * fx / 0.453592),
    # control: already wired and known good, so a failure here means the probe is
    # wrong rather than the ticker.
    "CRUDEOIL": (["CL=F"], None),
}


def _db():
    from bar_store import DB_PATH
    return os.environ.get("OPTION_CHAINS_DB", DB_PATH)


def main():
    import yfinance as yf
    db = _db()
    con = sqlite3.connect(db)
    fx = con.execute(
        "select close from price_bars where symbol='USDINR' and timeframe='1d' "
        "order by ts desc limit 1").fetchone()
    fx = fx[0] if fx else None
    print(f"database: {db}")
    print(f"USDINR (latest stored): {fx}\n")

    for sym, (tickers, to_mcx) in PROBES.items():
        print(f"=== {sym} ===")
        for tk in tickers:
            try:
                df = yf.download(tk, start="2018-01-01", progress=False,
                                 auto_adjust=True, threads=False)
            except Exception as e:
                print(f"   {tk:8} ERROR {str(e)[:60]}")
                continue
            if df is None or df.empty:
                print(f"   {tk:8} 0 bars — ticker dead or renamed")
                continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df = df.droplevel(1, axis=1)
            closes = df["Close"].dropna()

            # continuity: a rolled+adjusted series should have almost none of these
            gaps = 0
            prev = None
            for v in closes:
                if prev and prev > 0 and not (0.85 < v / prev < 1.18):
                    gaps += 1
                prev = v
            last = float(closes.iloc[-1])
            print(f"   {tk:8} {len(closes):>5} bars  "
                  f"{str(closes.index[0])[:10]}..{str(closes.index[-1])[:10]}  "
                  f"last {last:,.2f} USD   gaps>15%: {gaps}")

            # unit reconciliation against what we already store for the MCX contract
            if to_mcx and fx:
                # Compare against the most recent LIQUID bar, not simply the last one.
                # MCX dailies carry many zero-volume carried-forward prints — silver
                # is 77/222 zero-volume — and gold's latest bar traded 93 lots. A
                # reconciliation against an untraded print measures nothing.
                stored = con.execute(
                    "select ts, close, volume from price_bars where symbol=? and "
                    "timeframe='1d' and volume >= 1000 order by ts desc limit 1",
                    (sym,)).fetchone()
                if not stored:
                    stored = con.execute(
                        "select ts, close, volume from price_bars where symbol=? and "
                        "timeframe='1d' order by ts desc limit 1", (sym,)).fetchone()
                if stored:
                    implied = to_mcx(last, fx)
                    diff = (stored[1] / implied - 1.0) * 100.0
                    # A UNIT error is an order-of-magnitude or clean-factor miss
                    # (10x, 31.1x, 2.2x). A BASIS is a market spread: Indian import
                    # duty plus GST is ~9% on its own, and the physical premium moves
                    # — India's silver premium over US spot is a published, tracked
                    # number. Do not call a real spread a data defect.
                    if abs(diff) > 60:
                        verdict = "UNIT ERROR — check the contract's quote convention"
                    elif abs(diff) > 25:
                        verdict = "LARGE BASIS — plausible but verify before relying on it"
                    else:
                        verdict = "OK — consistent with Indian duty + local premium"
                    print(f"            implied {implied:,.0f} INR vs stored "
                          f"{stored[1]:,.0f} ({stored[0][:10]}, vol {stored[2]:,.0f})")
                    print(f"            India basis {diff:+.1f}%  [{verdict}]")
        print()

    con.close()
    print("Nothing was written. If gold/silver/copper each show a continuous series")
    print("and no UNIT ERROR, the crude pattern can be applied to them:")
    print("  <SYM>      1d  Yahoo, USD, continuous   -> analysis")
    print("  <SYM>_MCX  1m  Upstox MCX, INR          -> algo, rolls with the contract")
    print()
    print("The two are NOT interchangeable and must never be converted into each other.")
    print("The India basis is real, time-varying, and a tracked quantity in its own")
    print("right — duty plus GST is ~9% before any physical premium. Separate symbols.")


if __name__ == "__main__":
    main()
