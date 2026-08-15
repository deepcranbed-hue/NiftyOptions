#!/usr/bin/env python3
"""
calibrate.py
------------
Fit the market_scan.py SENSITIVITY coefficients from HISTORY instead of guessing.

It downloads ~3 years of daily data for the Indian indices and a comprehensive
set of drivers, aligns them for time-zone lag (US markets close AFTER India, so
India reacts to the *previous* US session), runs a multiple linear regression,
and prints a ready-to-paste SENSITIVITY dict plus fit quality (R²).

Drivers included (as requested — comprehensive):
  • Brent oil            (same day, trades ~24h)
  • India VIX            (same day)
  • USD/INR              (same day)
  • Kospi                (same day — Asia session leads/overlaps India)
  • US 10Y yield  ^TNX   (lagged 1d) — proxy for US rate hike/cut fear
  • US 2Y-ish     ^FVX   (lagged 1d) — shorter-rate / Fed-expectations proxy
  • Dollar Index  DXY    (lagged 1d)
  • Phila Semi    ^SOX   (lagged 1d) — AI / semiconductor cycle
  • Nasdaq        ^IXIC  (lagged 1d) — global tech risk

Two regressions are produced:
  1. CONTEMPORANEOUS  — today's index move vs today's driver moves.
     This is what the live "expected move" engine uses. Higher R² expected.
  2. PREDICTIVE       — tomorrow's index move vs today's driver moves.
     Honest reality check for "should I short tomorrow" — R² is usually tiny,
     because markets are close to efficient. Don't expect magic.

FII/DII flows are NOT here: yfinance has no historical FII series. Keep that
coefficient hand-set, or feed a CSV of daily FII net and extend REGRESS below.

Install:  pip install yfinance pandas numpy
Run:      python3 calibrate.py            # ~3y
          python3 calibrate.py --years 5  # more history
Output:   prints coefficients + writes suggested_sensitivity.py
"""

from __future__ import annotations
import sys
import argparse
import os
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    print("Need yfinance: pip install yfinance pandas numpy")
    sys.exit(1)


# ---- targets (Indian indices we want to explain) ----
TARGETS = {
    "Nifty 50":   "^NSEI",
    "Bank Nifty": "^NSEBANK",
    "Nifty IT":   "^CNXIT",
}

# ---- drivers: name -> (symbol, lag_days) ----
# lag=0 : same-day (Asia / 24h markets)   lag=1 : previous US close feeds India
DRIVERS = {
    "oil_pct":    ("BZ=F",      0),
    "vix_pct":    ("^INDIAVIX", 0),
    "usdinr_pct": ("INR=X",     0),
    "kospi_pct":  ("^KS11",     0),
    "us10y_pct":  ("^TNX",      1),   # US rate fear
    "us5y_pct":   ("^FVX",      1),   # shorter-rate / Fed expectations
    "dxy_pct":    ("DX-Y.NYB",  1),
    "sox_pct":    ("^SOX",      1),   # semiconductors / AI
    "nasdaq_pct": ("^IXIC",     1),   # global tech
}


def download(symbol: str, start: str) -> pd.Series:
    """Daily close as a Series, indexed by date. Empty on failure."""
    try:
        df = yf.Ticker(symbol).history(start=start, auto_adjust=False)
        if df is None or df.empty:
            print(f"  ! no data for {symbol}")
            return pd.Series(dtype=float)
        s = df["Close"].copy()
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s
    except Exception as e:
        print(f"  ! {symbol}: {str(e)[:70]}")
        return pd.Series(dtype=float)


def pct(s: pd.Series) -> pd.Series:
    return s.pct_change() * 100.0


def build_matrix(years: int):
    start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).isoformat()
    print(f"Downloading history from {start} ...")

    # targets
    tcols = {}
    for name, sym in TARGETS.items():
        print(f"  target {name} ({sym})")
        tcols[name] = pct(download(sym, start))

    # drivers (apply lag)
    dcols = {}
    for name, (sym, lag) in DRIVERS.items():
        print(f"  driver {name} ({sym}, lag={lag})")
        r = pct(download(sym, start))
        if lag:
            r = r.shift(lag)      # previous session feeds today's India move
        dcols[name] = r

    frame = pd.DataFrame({**{f"y::{k}": v for k, v in tcols.items()}, **dcols})
    frame = frame.dropna(how="all")
    return frame


def regress(y: pd.Series, X: pd.DataFrame):
    """OLS via numpy. Returns (coeffs dict, intercept, r2, n)."""
    data = pd.concat([y, X], axis=1).dropna()
    if len(data) < 50:
        return None
    yv = data.iloc[:, 0].values
    Xv = data.iloc[:, 1:].values
    Xd = np.column_stack([np.ones(len(Xv)), Xv])          # intercept
    beta, *_ = np.linalg.lstsq(Xd, yv, rcond=None)
    pred = Xd @ beta
    ss_res = float(np.sum((yv - pred) ** 2))
    ss_tot = float(np.sum((yv - yv.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    coeffs = {col: round(float(b), 4) for col, b in zip(X.columns, beta[1:])}
    return coeffs, round(float(beta[0]), 4), round(r2, 3), len(data)


def run(mode: str, frame: pd.DataFrame):
    """mode: 'contemp' or 'predict' (target shifted -1)."""
    print(f"\n{'='*66}\n{mode.upper()} regression"
          f"  ({'today vs today' if mode=='contemp' else 'TOMORROW vs today'})\n{'='*66}")
    Xcols = list(DRIVERS.keys())
    X = frame[Xcols]
    results = {}
    for name in TARGETS:
        y = frame[f"y::{name}"]
        if mode == "predict":
            y = y.shift(-1)      # explain next-day return with today's drivers
        out = regress(y, X)
        if not out:
            print(f"\n{name}: not enough data")
            continue
        coeffs, intercept, r2, n = out
        results[name] = coeffs
        print(f"\n{name}   (n={n}, R²={r2})")
        for k, v in coeffs.items():
            bar = "🟩" if v > 0 else "🟥"
            print(f"    {k:12} {v:+.4f} {bar}")
    return results


def write_suggestion(contemp: dict):
    """Emit a SENSITIVITY-style dict for the two engine indices."""
    keep = ["oil_pct", "vix_pct", "us10y_pct", "dxy_pct", "kospi_pct", "sox_pct"]
    lines = ["# Auto-fitted by calibrate.py — paste into market_scan.py SENSITIVITY.",
             "# (fii_kcr & geopolitics_hits stay hand-set: no historical price series.)",
             "SENSITIVITY = {"]
    label = {"Nifty 50": "Nifty 50", "Bank Nifty": "Bank Nifty"}
    # Bank Nifty falls back to Nifty IT? No — use its own fit if present.
    for idx in ("Nifty 50", "Bank Nifty"):
        src = contemp.get(idx, {})
        lines.append(f'    "{idx}": {{')
        for k in keep:
            if k in src:
                lines.append(f'        "{k}": {src[k]:+.4f},')
        lines.append('        "fii_kcr":          0.12,   # hand-set (no history)')
        lines.append('        "geopolitics_hits": -0.05,  # hand-set (no history)')
        lines.append("    },")
    lines.append("}")
    # ── CANONICAL OUTPUT ─────────────────────────────────────────────────
    # Writing beside this script produced a SECOND copy of the fitted coefficients
    # (newsindex/ and NewsAgent/engine/ each had one). They happened to match, but
    # nothing kept them in step: re-running with --years 6 would have updated one and
    # left the other silently stale, and there is no import to reveal which is real —
    # these are pasted into SENSITIVITY by hand, so a stale copy is invisible.
    # Same canonical home as events.db. Override with NEWSINDEX_SENSITIVITY_OUT.
    _root = Path(__file__).resolve().parent
    out = Path(os.environ.get("NEWSINDEX_SENSITIVITY_OUT",
                              _root / "NewsAgent" / "engine" / "suggested_sensitivity.py"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}")
    print("  (canonical location — paste into market_scan.py / market_engine.py SENSITIVITY)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3.0)
    args = ap.parse_args()

    frame = build_matrix(args.years)
    if frame.empty:
        print("No data downloaded — check network / symbols.")
        return

    # correlation snapshot (contemporaneous) for sanity
    print("\nDriver → Nifty correlation (contemporaneous):")
    ny = frame["y::Nifty 50"]
    for k in DRIVERS:
        c = frame[[k]].join(ny).dropna().corr().iloc[0, 1]
        print(f"    {k:12} r = {c:+.2f}")

    contemp = run("contemp", frame)
    run("predict", frame)   # honest predictability check

    if contemp:
        write_suggestion(contemp)
    print("\nDone. Contemporaneous fit calibrates the live 'expected move' engine.")
    print("Predictive R² is usually tiny — that's an honest finding, not a bug.")


if __name__ == "__main__":
    main()
