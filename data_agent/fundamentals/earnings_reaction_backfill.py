"""earnings_reaction_backfill.py — the measured expectation record.

Answers a question four years of fundamentals structurally CANNOT answer: does a
stock systematically beat or miss the expectation embedded in its own price?
Fundamentals record what a company EARNED; they never record what the market
HOPED for. The market's verdict on actual-vs-expected is the price reaction on
the results day — and that is reconstructable from data already on disk.

Worked example that motivated this (all from option_chains.db):
    TRENT fell on SIX consecutive results/update days between Nov-2024 and
    Aug-2026, averaging about -10%, while profit grew >20% every quarter, and
    the stock went 7,597 -> 3,107. A fundamentals-only view said "buy" at every
    one of those events. The reaction record says "the bar is set far above
    delivery" — which is the actionable fact.

THREE STAGES, each independently useful
---------------------------------------
1. CORPORATE-ACTION SWEEP.  price_bars is NOT split/bonus adjusted — verified:
   TRENT closed 4,272.69 on 2025-12-31 and opened 2,848.46 on 2026-01-01, a
   ratio of exactly 2/3 (a 1:2 bonus). Every return spanning such a date is
   wrong by that ratio, which silently corrupts returns, volatility and any
   backtest crossing it. Detects clean gaps whose ratio matches a standard
   bonus/split fraction. NON-DESTRUCTIVE: reports what it finds and adjusts
   only its own in-memory copy — your database is never rewritten.

2. ANNOUNCEMENT-DATE DETECTION.  Results dates are stored nowhere (financials
   hold period_end — the quarter covered — while the market reacts on the
   announcement day, typically 5-6 weeks later; earnings_feed.py is RSS and
   live-only, so it cannot reconstruct history). Detected as the highest-volume
   session inside each quarter's reporting window.

   METHOD NOTE — selection is by VOLUME ONLY, deliberately. Selecting on the
   size of the move would guarantee finding big moves and bias the measured
   reaction upward by construction. Volume is correlated with results days but
   is not the variable being measured, so it avoids that circularity. Residual
   bias remains (volume spikes and large moves co-occur) and is declared, not
   hidden.

3. REACTION MEASUREMENT.  Per event: the 1-day and 3-day move, both absolute and
   RELATIVE to NIFTY. A results-day move contains market beta; the stock-specific
   surprise is the residual, so the relative number is the honest one.

STATUS: PRIOR-until-calibrated, per SECTOR_INTELLIGENCE_FRAMEWORK.md. This is a
DESCRIPTIVE record of how price has responded, not a predictor. It decays — a
reaction bias measured at 60x earnings does not survive the multiple resetting to
25x — and the sample is thin (~4-8 events per name), so it flags patterns rather
than proving them. Walk-forward test before it gates anything.

Run:  python3 data_agent/fundamentals/earnings_reaction_backfill.py
Out:  earnings_reactions.json (repo root)
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_writable_db_path

import json
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# TWO COPIES OF option_chains.db EXIST, ON PURPOSE.
#
#   Drive  — /Users/deepak/.../My Drive/option_chains.db   WRITTEN by the sync and
#            backfill jobs; read by backend/ and the rest of data_agent/fundamentals.
#            This is the source of truth.
#   Mirror — <repo>/option_chains.db                       A manual copy of Drive,
#            kept so agents/tooling without Drive access can read the data.
#
# Same size, different inode — a copy, not a link. So the mirror goes stale the
# moment anything writes to Drive without a re-copy, and a backfill run against
# Drive is invisible here until that copy happens.
#
# Order: OPTION_CHAINS_DB override -> Drive -> mirror. Never silent about which.
_DRIVE_DB = resolve_writable_db_path()
_MIRROR_DB = os.path.join(_REPO, "option_chains.db")


def _resolve_db():
    env = os.getenv("OPTION_CHAINS_DB")
    if env:
        return env
    if os.path.exists(_DRIVE_DB):
        # Both reachable: the mirror is only trustworthy if it is no older than Drive.
        if os.path.exists(_MIRROR_DB):
            try:
                if os.path.getmtime(_MIRROR_DB) < os.path.getmtime(_DRIVE_DB):
                    print("NOTE: repo-root mirror is OLDER than Drive. Re-copy it "
                          "before any agent reads the repo copy:\n"
                          f"      cp '{_DRIVE_DB}' '{_MIRROR_DB}'\n")
            except OSError:
                pass
        return _DRIVE_DB
    if os.path.exists(_MIRROR_DB):
        print(f"NOTE: Drive not reachable here; reading the repo-root mirror "
              f"({_MIRROR_DB}). It is only as fresh as the last manual copy.\n")
        return _MIRROR_DB
    raise SystemExit("ERROR: no option_chains.db found — set OPTION_CHAINS_DB.")


_DB = _resolve_db()
_CSV = os.path.join(_REPO, "nifty-50-stock-list.csv")
_OUT = os.path.join(_REPO, "earnings_reactions.json")

_BENCH = "NIFTY"                # index benchmark (2,140 daily bars back to 2018).
                                # NOT NIFTY_FUT_1 — that is front-month futures with
                                # only ~68 bars, which silently reduced the relative
                                # measure to a 1-event sample on the first run.

# Stock's own sector index — the sharper benchmark, since it strips the sector move
# and leaves the STOCK-SPECIFIC surprise. Only confident mappings; the rest fall back
# to NIFTY rather than being force-fitted.
_SECTOR_INDEX = {
    "Financial Services": "NIFTYFIN",
    "Information Technology": "NIFTYIT",
    "Automobile": "NIFTYAUTO",
    "Healthcare": "NIFTYPHARMA",
    "FMCG": "NIFTYFMCG",
    "Metals & Mining": "NIFTYMETAL",
    "Oil & Gas": "NIFTYENERGY",
    "Consumer Durables": "NIFTYCONSUM",
    "Consumer Services": "NIFTYCONSUM",
}
_RECENT_N = 8                   # trailing events for the "current regime" read
_GAP_FLAG = 0.15                # |gap| above this is investigated as a corp action
_RATIO_TOL = 0.02               # match tolerance to a standard bonus/split ratio
_VOL_SPIKE = 1.8                # min volume vs trailing median to call it a results day
_TRAIL = 20                     # trailing window for the volume baseline

# DOCUMENTED actions that produce a NON-standard ratio (demergers, schemes of
# arrangement). These cannot be inferred from the ratio alone, so they are declared
# here with a source. Keyed (symbol, date) -> (ratio, label).
#
# TATAMOTORS 2025-10-14: demerger record date. NSE ran a special price-discovery
# session 09:00-10:00 and the PV+JLR entity (TMPV) reopened at 400.00 against a
# 660.75 prior close — the market valuing PV+JLR at ~61% and the new TMLCV entity
# at ~39%. Holders received 1 TMLCV share per 1 held, so NO value was lost; the
# price series simply needs rebasing. (TMLCV listed separately on 2025-11-12.)
#
# NOTE the deliberate choice of ratio: 0.6054 is the MARKET split and is what a
# price series must use. The company's 68.85%/31.15% cost-of-acquisition
# apportionment is a TAX basis for capital-gains computation — a different number
# for a different purpose. Using the COA here would misstate every return.
_KNOWN_ACTIONS = {
    ("TATAMOTORS", "2025-10-14"): (0.6054, "demerger — PV+JLR (TMPV) retained, "
                                           "TMLCV spun off 1:1; market split ~61/39"),
}

# open/prev_close ratios produced by standard Indian corporate actions
_ACTIONS = {
    0.5000: "1:1 bonus (or 2:1 split)",
    0.6667: "1:2 bonus",
    0.7500: "1:3 bonus",
    0.8000: "1:4 bonus",
    0.3333: "2:1 bonus",
    0.2500: "3:1 bonus (or 4:1 split)",
    0.2000: "5:1 split",
    0.1000: "10:1 split",
    0.0500: "20:1 split",
}

# Indian quarterly reporting windows: (start month/day, end month/day, quarter label)
_WINDOWS = [
    ((1, 5), (2, 25), "Q3"),    # Oct-Dec quarter
    ((4, 5), (5, 31), "Q4"),    # Jan-Mar quarter + annual
    ((7, 5), (8, 25), "Q1"),    # Apr-Jun quarter
    ((10, 5), (11, 25), "Q2"),  # Jul-Sep quarter
]


def _load_bars(con, symbol):
    """Daily bars as [(date, open, close, volume)], ascending."""
    rows = con.execute(
        "select ts, open, close, volume from price_bars "
        "where symbol=? and timeframe='1d' order by ts", (symbol,)).fetchall()
    out = []
    for ts, o, c, v in rows:
        try:
            out.append((str(ts)[:10], float(o), float(c), float(v or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _detect_actions(bars, symbol):
    """Clean gaps matching a standard bonus/split ratio -> corporate actions."""
    found, unexplained = [], []
    for i in range(1, len(bars)):
        prev_close, open_ = bars[i - 1][2], bars[i][1]
        if prev_close <= 0 or open_ <= 0:
            continue
        ratio = open_ / prev_close
        if abs(ratio - 1.0) < _GAP_FLAG:
            continue
        match = next((lbl for r, lbl in _ACTIONS.items() if abs(ratio - r) <= _RATIO_TOL), None)
        rec = {"date": bars[i][0], "ratio": round(ratio, 4),
               "prev_close": round(prev_close, 2), "open": round(open_, 2)}
        known = _KNOWN_ACTIONS.get((symbol, bars[i][0]))
        if known:
            rec["ratio"], rec["likely_action"] = known[0], known[1]
            rec["source"] = "declared in _KNOWN_ACTIONS (documented corporate action)"
            found.append(rec)
        elif match:
            rec["likely_action"] = match
            found.append(rec)
        else:
            # A real crash/limit move, or a data gap — never auto-adjusted.
            rec["note"] = "large gap, no standard ratio match — review, NOT adjusted"
            unexplained.append(rec)
    return found, unexplained


def _adjust(bars, actions):
    """Back-adjust bars before each action (in memory only — the DB is untouched)."""
    adj = list(bars)
    for a in actions:
        r = a["ratio"]
        adj = [(d, o * r, c * r, v) if d < a["date"] else (d, o, c, v) for d, o, c, v in adj]
    return adj


def _detect_events(bars):
    """Highest-volume session in each reporting window = likely announcement day."""
    by_year = defaultdict(list)
    for i, (d, _o, _c, v) in enumerate(bars):
        by_year[int(d[:4])].append((i, d, v))

    events = []
    for year, entries in by_year.items():
        for (sm, sd), (em, ed), q in _WINDOWS:
            lo, hi = f"{year}-{sm:02d}-{sd:02d}", f"{year}-{em:02d}-{ed:02d}"
            window = [(i, d, v) for i, d, v in entries if lo <= d <= hi]
            if not window:
                continue
            i, d, v = max(window, key=lambda x: x[2])
            base = [bars[j][3] for j in range(max(0, i - _TRAIL), i) if bars[j][3] > 0]
            if len(base) < 5:
                continue
            med = statistics.median(base)
            if med <= 0 or v / med < _VOL_SPIKE:
                continue                      # no volume signature -> not confident
            events.append({"idx": i, "date": d, "quarter": f"{q} {year}",
                           "vol_ratio": round(v / med, 2)})
    return sorted(events, key=lambda e: e["date"])


def _series(con, symbol):
    """(daily-return map, close map) for a benchmark symbol."""
    bars = _load_bars(con, symbol)
    ret = {}
    for i in range(1, len(bars)):
        if bars[i - 1][2]:
            ret[bars[i][0]] = bars[i][2] / bars[i - 1][2] - 1.0
    return ret, {d: c for d, _o, c, _v in bars}


def _stats(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.mean(vals), 2) if vals else None


def main():
    con = sqlite3.connect(_DB)

    symbols = []
    with open(_CSV) as f:
        next(f)
        for line in f:
            s = line.split(",")[0].strip()
            if s:
                symbols.append(s)
    have = {r[0] for r in con.execute("select distinct symbol from price_bars")}
    symbols = [s for s in symbols if s in have]

    sectors = {}
    with open(_CSV) as f:
        next(f)
        for line in f:
            p = line.split(",")
            if len(p) >= 3:
                sectors[p[0].strip()] = p[2].strip()

    bench_ret, bench_close = _series(con, _BENCH)
    sect_series = {}
    for idx in set(_SECTOR_INDEX.values()):
        if idx in have:
            sect_series[idx] = _series(con, idx)

    all_actions, all_unexplained, all_events, summary = [], [], [], {}
    for sym in symbols:
        bars = _load_bars(con, sym)
        if len(bars) < 120:
            continue
        actions, unexplained = _detect_actions(bars, sym)
        for a in actions:
            all_actions.append(dict(a, symbol=sym))
        for u in unexplained:
            all_unexplained.append(dict(u, symbol=sym))

        bars = _adjust(bars, actions)
        rows = []
        for ev in _detect_events(bars):
            i = ev["idx"]
            if i < 1 or i + 2 >= len(bars):
                continue
            prev, day = bars[i - 1][2], bars[i][2]
            if prev <= 0:
                continue
            r1 = day / prev - 1.0
            r3 = bars[i + 2][2] / prev - 1.0
            def _rel(ret_map, close_map):
                """(1d, 3d) excess return over a benchmark, or (None, None)."""
                b1 = ret_map.get(bars[i][0])
                b3 = None
                d0, d2 = bars[i - 1][0], bars[i + 2][0]
                if d0 in close_map and d2 in close_map and close_map[d0]:
                    b3 = close_map[d2] / close_map[d0] - 1.0
                return (round((r1 - b1) * 100, 2) if b1 is not None else None,
                        round((r3 - b3) * 100, 2) if b3 is not None else None)

            rel1, rel3 = _rel(bench_ret, bench_close)
            sidx = _SECTOR_INDEX.get(sectors.get(sym, ""))
            srel1 = srel3 = None
            if sidx and sidx in sect_series:
                srel1, srel3 = _rel(*sect_series[sidx])
            rows.append({
                "symbol": sym, "date": ev["date"], "quarter": ev["quarter"],
                "vol_ratio": ev["vol_ratio"],
                "r1d_pct": round(r1 * 100, 2), "r3d_pct": round(r3 * 100, 2),
                "rel1d_pct": rel1, "rel3d_pct": rel3,
                "sector_index": sidx, "sect_rel1d_pct": srel1, "sect_rel3d_pct": srel3,
            })
        all_events.extend(rows)

        if len(rows) >= 3:
            recent = rows[-_RECENT_N:]
            mean_rel = _stats(rows, "rel1d_pct")
            recent_rel = _stats(recent, "rel1d_pct")
            pos = sum(1 for r in rows if r["r1d_pct"] > 0)
            rpos = sum(1 for r in recent if r["r1d_pct"] > 0)

            def _label(v):
                # Categorical, per the framework — never an invented score.
                if v is None:
                    return "unmeasured"
                return "negative" if v <= -1.5 else "positive" if v >= 1.5 else "neutral"

            summary[sym] = {
                "n_events": len(rows),
                "full_mean_r1d_pct": _stats(rows, "r1d_pct"),
                "full_mean_rel1d_pct": mean_rel,
                "full_positive_share": round(pos / len(rows), 2),
                "full_bias": _label(mean_rel),
                # The current-regime read. Reaction bias DECAYS as the multiple resets,
                # so the trailing window is the one to act on; the full sample is context.
                "recent_n": len(recent),
                "recent_mean_r1d_pct": _stats(recent, "r1d_pct"),
                "recent_mean_rel1d_pct": recent_rel,
                "recent_mean_sect_rel1d_pct": _stats(recent, "sect_rel1d_pct"),
                "recent_positive_share": round(rpos / len(recent), 2),
                "recent_bias": _label(recent_rel),
            }

    by_sector = defaultdict(list)
    for sym, st in summary.items():
        if st["recent_mean_rel1d_pct"] is not None:
            by_sector[sectors.get(sym, "—")].append(st["recent_mean_rel1d_pct"])
    sector_bias = {k: {"n_stocks": len(v),
                       "recent_mean_rel1d_pct": round(statistics.mean(v), 2)}
                   for k, v in by_sector.items() if len(v) >= 2}

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "status": "PRIOR — descriptive record of realised reactions, NOT a predictor. "
                  "Announcement dates are volume-detected heuristics, not official filings. "
                  "Thin sample per name. Walk-forward test before gating anything on it.",
        "method": {
            "announcement_detection": f"highest-volume session per reporting window, "
                                      f"requiring volume >= {_VOL_SPIKE}x the trailing "
                                      f"{_TRAIL}-day median. Selected on VOLUME ONLY so the "
                                      f"measured return is not chosen on its own magnitude.",
            "reaction": "close-to-close 1-day and 3-day from the prior close. Relative is "
                        f"measured twice: vs {_BENCH}, and vs the stock's own sector index "
                        "where one exists (the sharper benchmark — it strips the sector "
                        "move and leaves the stock-specific surprise).",
            "corporate_actions": "clean gaps matching a standard bonus/split ratio are "
                                 "back-adjusted IN MEMORY ONLY; the database is not modified",
        },
        "corporate_actions_found": sorted(all_actions, key=lambda a: a["date"]),
        "unexplained_gaps": sorted(all_unexplained, key=lambda a: a["date"]),
        "sector_bias": sector_bias,
        "summary": dict(sorted(summary.items())),
        "events": sorted(all_events, key=lambda e: (e["symbol"], e["date"])),
    }
    with open(_OUT, "w") as f:
        json.dump(out, f, indent=1)

    print(f"symbols processed      : {len(symbols)}")
    print(f"corporate actions found: {len(all_actions)}")
    print(f"unexplained gaps       : {len(all_unexplained)}")
    print(f"events detected        : {len(all_events)}")
    print(f"names with >=3 events  : {len(summary)}")
    print(f"written -> {_OUT}")


if __name__ == "__main__":
    main()
