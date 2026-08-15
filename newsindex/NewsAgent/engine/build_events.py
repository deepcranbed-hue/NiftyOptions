#!/usr/bin/env python3
"""
build_events.py
---------------
Event memory / historical-analogue engine. Backfills from price history and
computes, for each kind of market event, what the INDEX did the NEXT day:
count, average, median, hit-rate (how often it fell), and dispersion.

So instead of only "oil up -> banks down (rule)", the report can say:
  "Historically, the last 34 times Brent rose >3%, next-day Bank Nifty averaged
   -0.6% and fell 62% of the time (since 2021)."

Conditions are conditioned on day t's drivers and measure the index return on
day t+1 (predictive, timezone-valid: US drivers close before India's next open).

HONEST LIMITS:
  * No historical FII or geopolitics tags in free data, so conditions are
    PRICE-BASED only (oil/VIX/SOX/DXY/rupee). "Oil rose because of Iran" can't
    be separated from "oil rose for other reasons."
  * Small samples for tight conditions -> the report shows N; treat low-N with care.
  * Past behaviour != future. This is evidence, not a forecast.

Build/refresh:  python3 build_events.py            # ~4y
                python3 build_events.py --years 6
Then market_scan.py reads events.db automatically.

Requires: yfinance pandas numpy
"""

from __future__ import annotations
import argparse
import os
import sqlite3
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

# ── CANONICAL EVENTS DB ──────────────────────────────────────────────────────
# There are two copies of this module (newsindex/ and NewsAgent/engine/) and each
# used to write events.db BESIDE ITSELF. Callers do a bare `import build_events`, so
# which database you got depended on sys.path order — market_scan.py read one,
# market_engine.py read the other, and they drifted (one refreshed Jul 15, the other
# Jul 16). Calibration numbers silently differed by which entry point ran.
#
# CANONICAL HOME: NewsAgent/engine/events.db. Both forks now resolve to it, whichever
# directory they live in. Override with NEWSINDEX_EVENTS_DB for a one-off.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1] if (_HERE.name == "engine" and _HERE.parent.name == "NewsAgent") else _HERE
DB_PATH = Path(os.environ.get("NEWSINDEX_EVENTS_DB",
                              _ROOT / "NewsAgent" / "engine" / "events.db"))

TARGETS = {"Nifty 50": "^NSEI", "Bank Nifty": "^NSEBANK", "Nifty IT": "^CNXIT"}
DRIVERS = {"oil": "BZ=F", "vix": "^INDIAVIX", "sox": "^SOX",
           "dxy": "DX-Y.NYB", "usdinr": "INR=X", "kospi": "^KS11", "us10y": "^TNX"}

# Event conditions — predicate over a dict r of driver % moves (day t).
# (name, description, predicate). Keep the same names in the report matcher.
EVENT_CONDITIONS = [
    ("oil_up_2",       "Brent up >2%",                 lambda r: r["oil"] > 2),
    ("oil_up_3",       "Brent up >3%",                 lambda r: r["oil"] > 3),
    ("oil_down_2",     "Brent down >2%",               lambda r: r["oil"] < -2),
    ("vix_spike_5",    "India VIX up >5%",             lambda r: r["vix"] > 5),
    ("sox_drop_3",     "US SOX down >3%",              lambda r: r["sox"] < -3),
    ("riskoff_combo",  "Oil >1.5% AND VIX up >3%",     lambda r: r["oil"] > 1.5 and r["vix"] > 3),
    ("dxy_up_strong",  "Dollar Index up >0.5%",        lambda r: r["dxy"] > 0.5),
    ("rupee_weak",     "Rupee weak (USDINR >0.3%)",    lambda r: r["usdinr"] > 0.3),
    ("kospi_drop_2",   "Kospi down >2%",               lambda r: r["kospi"] < -2),
]
COND_BY_NAME = {c[0]: c for c in EVENT_CONDITIONS}

# Linkage reliability: how often did the proxy stocks move as the rule predicts?
# (name matches market_scan.RELATIONSHIPS). name, driver_sym, thr, lag_days,
# [(proxy_sym, sign_on_up)]. lag=1 for US drivers (close before India's next open).
CONFIDENCE_LINKAGES = [
    ("Oil → producers up / users down", "BZ=F", 1.0, 0,
        [("ONGC.NS", +1), ("BPCL.NS", -1), ("ASIANPAINT.NS", -1), ("INDIGO.NS", -1)]),
    ("Weak rupee → IT exporters up", "INR=X", 0.2, 0,
        [("TCS.NS", +1), ("INFY.NS", +1), ("WIPRO.NS", +1), ("HCLTECH.NS", +1)]),
    ("Gold → financiers & jewellers up", "GC=F", 0.5, 0,
        [("MUTHOOTFIN.NS", +1), ("TITAN.NS", +1)]),
    ("Copper → base-metal producers up", "HG=F", 0.7, 0,
        [("TATASTEEL.NS", +1), ("HINDALCO.NS", +1)]),
    ("US semis (SOX) → Indian IT services", "^SOX", 1.5, 1,
        [("TCS.NS", +1), ("INFY.NS", +1), ("WIPRO.NS", +1), ("HCLTECH.NS", +1)]),
    # EMS validated separately — different economic bucket (AI-infra beneficiary, no regime flip)
    ("AI infrastructure (SOX) → EMS", "^SOX", 1.5, 1,
        [("DIXON.NS", +1), ("KAYNES.NS", +1)]),
    ("Kospi (AI proxy) → Indian IT", "^KS11", 1.5, 0,
        [("TCS.NS", +1), ("INFY.NS", +1)]),
    ("Rising US yields → banks pressured", "^TNX", 0.5, 1,
        [("HDFCBANK.NS", -1), ("ICICIBANK.NS", -1)]),
    ("Oil ↑ → ICE autos pressured (fuel/demand)", "BZ=F", 0.7, 0,
        [("MARUTI.NS", -1), ("HEROMOTOCO.NS", -1), ("BAJAJ-AUTO.NS", -1)]),
    ("Weak rupee → pharma exporters up", "INR=X", 0.2, 0,
        [("SUNPHARMA.NS", +1), ("DRREDDY.NS", +1), ("CIPLA.NS", +1)]),
    ("Oil ↑ → chemicals input-cost pressure", "BZ=F", 1.0, 0,
        [("SRF.NS", -1), ("ASIANPAINT.NS", -1)]),
]


def compute_linkage_confidence(years: float) -> list[dict]:
    """For each linkage, the historical hit-rate: fraction of (proxy, day)
    observations that moved as the rule predicts, on days the driver was active."""
    start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).isoformat()
    cache = {}

    def ret(sym):
        if sym not in cache:
            cache[sym] = _dl(sym, start).pct_change() * 100.0
        return cache[sym]

    out = []
    for name, dsym, thr, lag, proxies in CONFIDENCE_LINKAGES:
        drv = ret(dsym)
        hits = tot = 0
        for psym, sign in proxies:
            pr = ret(psym)
            if pr.empty or drv.empty:
                continue
            df = pd.DataFrame({"d": drv, "p": pr.shift(-lag)}).dropna()
            active = df[df["d"].abs() >= thr]
            for _, row in active.iterrows():
                exp = sign * (1 if row["d"] > 0 else -1)
                act = 1 if row["p"] > 0 else -1 if row["p"] < 0 else 0
                if act == 0:
                    continue
                tot += 1
                hits += (act == exp)
        if tot >= 30:
            out.append({"name": name, "hit_rate": round(hits / tot * 100, 0), "n": tot})
    return out


# ---------------------------------------------------------------- data
def _dl(symbol: str, start: str) -> pd.Series:
    try:
        df = yf.Ticker(symbol).history(start=start, auto_adjust=False)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        s = df["Close"]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s
    except Exception:
        return pd.Series(dtype=float)


def build_frame(years: float) -> pd.DataFrame:
    start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).isoformat()
    cols = {}
    for name, sym in DRIVERS.items():
        cols[name] = _dl(sym, start).pct_change() * 100.0       # driver return, day t
    for name, sym in TARGETS.items():
        r = _dl(sym, start).pct_change() * 100.0
        cols["y_" + name] = r.shift(-1)                          # index return, day t+1
    return pd.DataFrame(cols).dropna(how="all")


# ---------------------------------------------------------------- stats
def _predicate_ok(pred, row) -> bool:
    try:
        r = {k: (None if pd.isna(row.get(k)) else float(row.get(k))) for k in DRIVERS}
        if any(r[k] is None for k in DRIVERS if k in pred.__code__.co_names):
            return False
        return bool(pred(r))
    except Exception:
        return False


def compute(frame: pd.DataFrame) -> list[dict]:
    out = []
    for name, desc, pred in EVENT_CONDITIONS:
        mask = frame.apply(lambda row: _predicate_ok(pred, row), axis=1)
        sub = frame[mask]
        n = int(len(sub))
        for tgt in TARGETS:
            col = "y_" + tgt
            vals = sub[col].dropna()
            if len(vals) == 0:
                continue
            out.append({
                "condition": name, "description": desc, "target": tgt,
                "n": int(len(vals)),
                "mean": round(float(vals.mean()), 2),
                "median": round(float(vals.median()), 2),
                "hit_down": round(float((vals < 0).mean() * 100), 0),
                "std": round(float(vals.std()), 2),
            })
    return out


# ---------------------------------------------------------------- db
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS event_stats(
        condition TEXT, description TEXT, target TEXT, n INTEGER,
        mean REAL, median REAL, hit_down REAL, std REAL, updated TEXT,
        PRIMARY KEY(condition, target))""")
    con.execute("""CREATE TABLE IF NOT EXISTS linkage_conf(
        name TEXT PRIMARY KEY, hit_rate REAL, n INTEGER, updated TEXT)""")
    return con


def save_linkage_conf(rows: list[dict]):
    con = _db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    for r in rows:
        con.execute("INSERT OR REPLACE INTO linkage_conf VALUES (?,?,?,?)",
                    (r["name"], r["hit_rate"], r["n"], now))
    con.commit(); con.close()


def load_linkage_conf() -> dict:
    """{linkage_name: {'hit_rate': %, 'n': N}} — used by market_scan's scorecard."""
    try:
        con = _db()
        rows = con.execute("SELECT name, hit_rate, n FROM linkage_conf").fetchall()
        con.close()
    except Exception:
        return {}
    return {r[0]: {"hit_rate": r[1], "n": r[2]} for r in rows}


def save(stats: list[dict]):
    con = _db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    for s in stats:
        con.execute("INSERT OR REPLACE INTO event_stats VALUES (?,?,?,?,?,?,?,?,?)",
                    (s["condition"], s["description"], s["target"], s["n"],
                     s["mean"], s["median"], s["hit_down"], s["std"], now))
    con.commit(); con.close()


def load_event_stats() -> dict:
    """{condition: {"description":..., "targets": {target: {n,mean,median,hit_down,std}}}}"""
    try:
        con = _db()
        rows = con.execute("SELECT condition,description,target,n,mean,median,hit_down,std "
                           "FROM event_stats").fetchall()
        con.close()
    except Exception:
        return {}
    out = {}
    for c, d, t, n, me, md, hd, sd in rows:
        out.setdefault(c, {"description": d, "targets": {}})
        out[c]["targets"][t] = {"n": n, "mean": me, "median": md, "hit_down": hd, "std": sd}
    return out


def match_conditions(today_r: dict) -> list[str]:
    """Which event conditions fire for today's driver moves (dict of % moves)."""
    fired = []
    for name, desc, pred in EVENT_CONDITIONS:
        try:
            if all(today_r.get(k) is not None for k in DRIVERS if k in pred.__code__.co_names) \
               and pred({k: today_r.get(k, 0.0) for k in DRIVERS}):
                fired.append(name)
        except Exception:
            continue
    return fired


# ---------------------------------------------------------------- main
def main():
    if yf is None:
        print("need yfinance: pip install yfinance pandas numpy"); return
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=4.0)
    args = ap.parse_args()

    print(f"Building event memory from ~{args.years}y of history...")
    frame = build_frame(args.years)
    if frame.empty:
        print("no data downloaded"); return
    stats = compute(frame)
    save(stats)

    print("Computing linkage confidence (historical hit-rates)...")
    conf = compute_linkage_confidence(args.years)
    save_linkage_conf(conf)
    for r in sorted(conf, key=lambda x: -x["hit_rate"]):
        band = "High" if r["hit_rate"] >= 65 else "Moderate" if r["hit_rate"] >= 55 else "Low"
        print(f"   {r['name'][:36]:36} {r['hit_rate']:.0f}% (n={r['n']}, {band})")

    print(f"\nSaved {len(stats)} stat rows to {DB_PATH}\n")
    print(f"{'condition':16}{'target':12}{'N':>5}{'mean%':>8}{'hit_down%':>11}")
    for s in sorted(stats, key=lambda x: (x["condition"], x["target"])):
        print(f"{s['condition']:16}{s['target']:12}{s['n']:>5}{s['mean']:>8.2f}{s['hit_down']:>11.0f}")
    print("\nDone. market_scan.py will now cite these under 'Historical analogues'.")
    print("Note: price-based conditions only (no FII/geopolitics in free history).")


if __name__ == "__main__":
    main()
