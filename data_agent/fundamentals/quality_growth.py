#!/usr/bin/env python3
"""quality_growth.py — walk the index by weight and keep the names that delivered.

THE QUESTION THIS ANSWERS
    Start at the heaviest stock in the Nifty 50. Does it meet three tests? If yes, keep
    it; if no, move to the next heaviest. Continue to the bottom of the index. "Heavy"
    is only the ORDER OF INSPECTION — it is not itself a test, which is why the walk
    runs all 50 names rather than stopping at the top 20 by weight.

THE THREE TESTS
    1. CONSISTENCY  — share of measured years in which net profit GREW.
    2. DELIVERED    — 5-year net-profit CAGR.
    3. FORWARD      — implied EPS growth = trailing P/E / forward P/E - 1.

    Tests 1 and 2 are measured on NET PROFIT, not EPS. EPS moves when the share count
    moves, so a bank that issued paper to absorb a merger reads as a collapse and a
    company buying its own stock back reads as growth. See delivery_history.py.

    Test 3 is not a forecast and not guidance. It is what the two multiples the market
    is already paying imply about earnings — the hurdle a print has to clear to leave
    the price unchanged. A name passes this test by being PRICED for growth, which is
    a different (and weaker) thing than being expected to deliver it by someone
    accountable. Where a real guidance statement exists it belongs in the curated
    `guidance` field of nifty50_drivers.json, sourced and dated; this file does not
    invent one. The screen reports the distinction rather than blurring it.

    Every rejection is recorded WITH THE LEG THAT FAILED and the numbers, so the walk is
    auditable end to end instead of arriving as a list of 15 names from nowhere.

THE BACKTEST, AND WHY THERE ARE TWO OF THEM
    Screening on today's fundamentals and then measuring the return since 2021 is not a
    backtest — it is a measurement of how well hindsight performs, and it performs
    superbly. This file computes both and prints the gap between them:

      A · TODAY'S SCREEN, held from the start date. INVALID as evidence. Reported only
          because it is the number a naive version of this script would have produced,
          and the gap to B is the size of the illusion.

      B · POINT-IN-TIME. Re-runs tests 1 and 2 using ONLY annual periods on or before
          the start date, then holds that list untouched. Test 3 cannot be run
          point-in-time — nothing in this repo stores what the forward multiple was in
          2021, and reconstructing it from today's data would put the look-ahead back in
          through the side door. So B is a two-leg screen and says so.

    Neither is a strategy. Both inherit SURVIVORSHIP BIAS: the universe is today's
    Nifty 50, so every name in it survived to be in it. Names that fell out of the index
    between the start date and now are absent from the walk entirely, and their absence
    flatters both portfolios by an amount this file cannot measure.

    Equal-weighted, no rebalancing, no costs, price return only — dividends are not in
    price_bars, which understates every holding and the index alike.

OUTPUT
    quality_growth.json at the repo root. A derived artifact, same convention as
    pe_history.json / fii_holdings.json / delivery_history.json — no new table.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DELIVERY = os.path.join(_ROOT, "delivery_history.json")
SNAPSHOT = os.path.join(_ROOT, "expectation_snapshots.json")
DRIVERS = os.path.join(_ROOT, "nifty50_drivers.json")
UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")
DB = os.path.join(_ROOT, "option_chains.db")
OUT = os.path.join(_ROOT, "quality_growth.json")

# Defaults. All three are PRIORS, not calibrated values — they are surfaced as controls
# in the UI precisely so the list can be watched move as they change.
CONSISTENCY_MIN = 70.0   # percent of measured years with net profit growth
DELIVERED_MIN = 10.0     # 5-year net profit CAGR, percent
FORWARD_MIN = 15.0       # implied EPS growth from trailing/forward P/E, percent
TARGET_N = 20
BACKTEST_START = "2021-04-01"


# ---------------------------------------------------------------------------- inputs

def _load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _universe() -> list[dict]:
    with open(UNIVERSE) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["Weight"] = float(r["Weight"])
    # Descending weight IS the walk order.
    return sorted(rows, key=lambda r: -r["Weight"])


def _implied_growth() -> tuple[dict, str | None]:
    """trailing P/E / forward P/E - 1, per symbol, from the latest snapshot.

    Returns None for a name rather than 0 when either multiple is missing — a
    loss-maker and a company nobody covers are not names priced for zero growth, and
    scoring them as such would silently reject them on the wrong leg.
    """
    doc = _load(SNAPSHOT, {}) or {}
    snaps = doc.get("snapshots") or []
    if not snaps:
        return {}, None
    latest = snaps[-1]
    out = {}
    for r in latest.get("rows", []):
        t, fwd = r.get("trailingPE"), r.get("forwardPE")
        out[r["symbol"].upper()] = {
            "trailing_pe": round(t, 1) if t else None,
            "forward_pe": round(fwd, 1) if fwd else None,
            "implied_growth_pct": (round((t / fwd - 1.0) * 100.0, 1)
                                   if (t and fwd and fwd > 0) else None),
            "target_mean": r.get("targetMeanPrice"),
            "analysts": int(r["numberOfAnalystOpinions"]) if r.get("numberOfAnalystOpinions") else None,
        }
    return out, latest.get("captured_at")


# ------------------------------------------------------------------- point-in-time

def _as_of(hist: dict, cutoff: str) -> dict | None:
    """Re-derive consistency and 5y CAGR using only periods on or before `cutoff`.

    This is the whole of the point-in-time discipline: same code path, truncated input.
    Nothing here may read `hist` fields computed on the full series.
    """
    ser = [s for s in hist.get("series", []) if s["period"] <= cutoff]
    if len(ser) < 4:
        return None
    yoy = []
    for i in range(1, len(ser)):
        a, b = ser[i - 1]["net_profit"], ser[i]["net_profit"]
        yoy.append(round((b / a - 1.0) * 100.0, 1) if (a and b and a >= 1.0) else None)
    real = [x for x in yoy if x is not None]
    if not real:
        return None
    end = ser[-1]
    cagr = None
    for want in (5, 4, 3):
        cut = (datetime.date.fromisoformat(end["period"])
               - datetime.timedelta(days=int(want * 365.25)))
        older = [s for s in ser if datetime.date.fromisoformat(s["period"]) <= cut]
        if older:
            st, yrs = older[-1], want
            if st["net_profit"] > 0 and end["net_profit"] > 0:
                cagr = round(((end["net_profit"] / st["net_profit"]) ** (1 / yrs) - 1) * 100, 1)
            break
    return {"consistency_pct": round(sum(1 for x in real if x > 0) / len(real) * 100, 1),
            "cagr_pct": cagr, "n_years": len(ser), "last_period": end["period"]}


# ------------------------------------------------------------------------ the walk

def walk(consistency_min: float, delivered_min: float, forward_min: float,
         target_n: int) -> dict:
    uni = _universe()
    delivery = (_load(DELIVERY, {}) or {}).get("history", {})
    growth, snap_at = _implied_growth()
    drivers = (_load(DRIVERS, {}) or {}).get("companies", {})

    selected, rejected, no_data = [], [], []
    for r in uni:
        sym = r["Symbol"]
        h = delivery.get(sym)
        if not h:
            no_data.append({"symbol": sym, "weight_pct": r["Weight"], "sector": r["Sector"],
                            "reason": "no financial history in delivery_history.json"})
            continue
        g = growth.get(sym, {})
        cons, cagr = h.get("consistency_pct"), h.get("profit_cagr_5y_pct")
        fwd = g.get("implied_growth_pct")

        fails = []
        if cons is None or cons < consistency_min:
            fails.append("consistency")
        if cagr is None or cagr < delivered_min:
            fails.append("delivered")
        if fwd is None or fwd < forward_min:
            fails.append("forward" if fwd is not None else "forward (not computable)")

        rec = {
            "symbol": sym, "company": r["Company Name"], "sector": r["Sector"],
            "weight_pct": r["Weight"],
            "consistency_pct": cons, "years_grown": h.get("years_grown"),
            "years_measured": h.get("years_measured"),
            "profit_cagr_5y_pct": cagr, "profit_cagr_3y_pct": h.get("profit_cagr_3y_pct"),
            "worst_year_pct": h.get("worst_year_pct"),
            "corporate_action_years": h.get("corporate_action_years"),
            "trailing_pe": g.get("trailing_pe"), "forward_pe": g.get("forward_pe"),
            "implied_growth_pct": fwd,
            "analysts": g.get("analysts"),
        }
        # Curated qualitative layer — present or absent, never fabricated.
        d = drivers.get(sym) or {}
        # `guidance` does not exist in nifty50_drivers.json today, and this file will not
        # manufacture it. Add a dated, sourced "guidance" string to a company entry there
        # and it appears here automatically; until then the field is empty and the UI
        # says why rather than substituting the implied multiple for a commitment.
        rec["guidance"] = d.get("guidance")
        rec["position"] = d.get("position")
        # The nearest thing that DOES exist: the curated read on the last print, dated.
        rec["latest_quarter"] = d.get("latest_quarter")
        rec["tailwinds"] = (d.get("tailwinds") or [])[:3]
        rec["headwinds"] = (d.get("headwinds") or [])[:3]
        # Charts travel with EVERY name, passed or failed. The three thresholds are
        # controls in the UI, so a name rejected at 70/10/15 becomes a selected name at
        # 60/8/12 — and it has to arrive with its history already attached, or moving a
        # slider produces a row that cannot draw itself.
        rec["series"] = h.get("series", [])
        rec["yoy_net_profit_pct"] = h.get("yoy_net_profit_pct", [])
        rec["share_move_flags"] = h.get("share_move_flags", [])
        rec["quarters"] = h.get("quarters", [])
        if fails:
            rec["failed"] = fails
            rejected.append(rec)
            continue
        selected.append(rec)
    # The walk does NOT break at target_n. Stopping early would leave the tail of the
    # index uninspected, and the thresholds are UI controls — loosening one has to be
    # able to pull in a name from the bottom of the list, which it cannot do if that
    # name was never looked at. `overflow` holds passers beyond the target.
    overflow, selected = selected[target_n:], selected[:target_n]

    # Where does each selected name sit against its own Nifty-50 sector peers? A 20%
    # CAGR means one thing in FMCG and another in metals.
    by_sector: dict[str, list] = {}
    for rec in selected + overflow + rejected:
        if rec.get("profit_cagr_5y_pct") is not None:
            by_sector.setdefault(rec["sector"], []).append(rec)
    for rec in selected:
        peers = sorted(by_sector.get(rec["sector"], []),
                       key=lambda x: -x["profit_cagr_5y_pct"])
        rec["sector_peers"] = len(peers)
        rec["sector_rank_cagr"] = next(
            (i + 1 for i, p in enumerate(peers) if p["symbol"] == rec["symbol"]), None)
        vals = sorted(p["profit_cagr_5y_pct"] for p in peers)
        rec["sector_median_cagr_pct"] = vals[len(vals) // 2] if vals else None

    return {
        "thresholds": {"consistency_min_pct": consistency_min,
                       "delivered_cagr_min_pct": delivered_min,
                       "forward_growth_min_pct": forward_min,
                       "target_n": target_n},
        "walk_length": len(selected) + len(overflow) + len(rejected) + len(no_data),
        "more_passed_than_target": len(overflow),
        "selected": selected, "overflow": overflow,
        "rejected": rejected, "no_data": no_data,
        "selected_weight_pct": round(sum(s["weight_pct"] for s in selected), 1),
        "expectation_captured_at": snap_at,
    }


# ------------------------------------------------------------------------ backtest

def _closes(symbols: list[str], start: str) -> dict[str, list[tuple[str, float]]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out: dict[str, list] = {}
    q = ("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
         "AND ts>=? AND close IS NOT NULL ORDER BY ts")
    for s in symbols:
        rows = con.execute(q, (s, start)).fetchall()
        if rows:
            out[s] = [(r[0][:10], float(r[1])) for r in rows]
    con.close()
    return out


def backtest(symbols: list[str], start: str, label: str) -> dict:
    """Equal-weighted, bought once at the first close on or after `start`, never
    rebalanced, held to the last bar. No costs, no dividends, price return only.

    Not rebalancing is a deliberate choice, not laziness: rebalancing an equal-weight
    book quarterly is itself a strategy (it sells winners), and mixing it in would make
    the result a test of two things at once.
    """
    px = _closes(symbols + ["NIFTY"], start)
    idx = px.get("NIFTY")
    if not idx:
        return {"label": label, "error": "no NIFTY bars"}
    i0, i1 = idx[0], idx[-1]
    index_ret = (i1[1] / i0[1] - 1) * 100
    years = (datetime.date.fromisoformat(i1[0]) - datetime.date.fromisoformat(i0[0])).days / 365.25

    holdings, missing = [], []
    for s in symbols:
        ser = px.get(s)
        if not ser or len(ser) < 100:
            missing.append(s)
            continue
        a, b = ser[0], ser[-1]
        holdings.append({"symbol": s, "buy_date": a[0], "buy": round(a[1], 2),
                         "last": round(b[1], 2),
                         "ret_pct": round((b[1] / a[1] - 1) * 100, 1),
                         "cagr_pct": round(((b[1] / a[1]) ** (1 / years) - 1) * 100, 1)
                                     if years > 0 else None})
    if not holdings:
        return {"label": label, "error": "no holdings with price history"}

    rets = sorted(h["ret_pct"] for h in holdings)
    port = sum(rets) / len(rets)
    holdings.sort(key=lambda h: -h["ret_pct"])
    return {
        "label": label, "start": i0[0], "end": i1[0], "years": round(years, 2),
        "n_holdings": len(holdings), "missing_price_history": missing,
        "portfolio_ret_pct": round(port, 1),
        "portfolio_cagr_pct": round(((1 + port / 100) ** (1 / years) - 1) * 100, 1),
        "index_ret_pct": round(index_ret, 1),
        "index_cagr_pct": round(((i1[1] / i0[1]) ** (1 / years) - 1) * 100, 1),
        "excess_pp": round(port - index_ret, 1),
        "beat_index": sum(1 for h in holdings if h["ret_pct"] > index_ret),
        "median_ret_pct": rets[len(rets) // 2],
        "best": holdings[0], "worst": holdings[-1],
        "holdings": holdings,
    }


def point_in_time(cutoff: str, consistency_min: float, delivered_min: float,
                  target_n: int) -> dict:
    """The same weight-ordered walk, run on truncated financials. TWO legs only —
    the forward multiple in 2021 is not recoverable from anything in this repo."""
    uni = _universe()
    delivery = (_load(DELIVERY, {}) or {}).get("history", {})
    picked, considered = [], 0
    for r in uni:
        h = delivery.get(r["Symbol"])
        if not h:
            continue
        considered += 1
        a = _as_of(h, cutoff)
        if not a or a["cagr_pct"] is None:
            continue
        if a["consistency_pct"] >= consistency_min and a["cagr_pct"] >= delivered_min:
            picked.append({"symbol": r["Symbol"], "weight_pct": r["Weight"],
                           "sector": r["Sector"], **a})
            if len(picked) >= target_n:
                break
    return {"cutoff": cutoff, "considered": considered, "picked": picked,
            "legs": ["consistency", "delivered"],
            "note": ("Forward-growth leg omitted: no historical forward-P/E snapshot "
                     "exists before 2026-08-08, and reconstructing one from today's "
                     "multiples would reintroduce the look-ahead this test removes.")}


# ---------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--consistency", type=float, default=CONSISTENCY_MIN)
    ap.add_argument("--delivered", type=float, default=DELIVERED_MIN)
    ap.add_argument("--forward", type=float, default=FORWARD_MIN)
    ap.add_argument("--n", type=int, default=TARGET_N)
    ap.add_argument("--start", default=BACKTEST_START)
    a = ap.parse_args()

    screen = walk(a.consistency, a.delivered, a.forward, a.n)
    syms = [s["symbol"] for s in screen["selected"]]

    pit = point_in_time(a.start, a.consistency, a.delivered, a.n)
    pit_syms = [p["symbol"] for p in pit["picked"]]

    bt_today = backtest(syms, a.start, "A · today's screen, held from start (INVALID — look-ahead)")
    bt_pit = backtest(pit_syms, a.start, "B · point-in-time screen (walk-forward)")
    inflation = (round(bt_today["portfolio_ret_pct"] - bt_pit["portfolio_ret_pct"], 1)
                 if "error" not in bt_today and "error" not in bt_pit else None)

    doc = {
        "as_of": datetime.date.today().isoformat(),
        "source": ("delivery_history.json (net profit + derived EPS from Screener raw "
                   "sheets) · expectation_snapshots.json (trailing/forward P/E) · "
                   "nifty50_drivers.json (curated guidance) · price_bars 1d for returns"),
        "method": ("Walk the Nifty 50 in DESCENDING INDEX WEIGHT. Weight sets the order "
                   "of inspection only — it is not a test. Each name must pass three "
                   "gates: consistency of net-profit growth, 5-year net-profit CAGR, and "
                   "implied forward EPS growth (trailing P/E / forward P/E - 1). Walk "
                   "continues to the last constituent or until the target count fills."),
        "screen": screen,
        "point_in_time": pit,
        "backtest": {
            "today_screen": bt_today,
            "point_in_time": bt_pit,
            "lookahead_inflation_pp": inflation,
            "overlap": sorted(set(syms) & set(pit_syms)),
            "only_in_today_screen": sorted(set(syms) - set(pit_syms)),
            "caveats": [
                "SURVIVORSHIP — the universe is TODAY'S Nifty 50. Every name in it "
                "survived to be in it; names that dropped out of the index over the "
                "period are absent from the walk and their absence flatters both "
                "portfolios by an amount not measured here.",
                "PRICE RETURN ONLY — price_bars carries no dividends, so every holding "
                "and the index are both understated. The comparison is like-for-like; "
                "the absolute levels are not total return.",
                "NO COSTS, NO REBALANCING, EQUAL WEIGHT, SINGLE ENTRY DATE — one start "
                "date is one draw. A different start month gives a different answer and "
                "this file does not sweep them.",
                "PORTFOLIO A IS NOT EVIDENCE. It screens on 2026 fundamentals and buys "
                "in 2021. It is reported only so the gap to B is visible.",
            ],
        },
        "note": ("A screen, not advice, and not a portfolio anyone runs. The forward leg "
                 "measures what the MARKET is already paying for, which is not the same "
                 "as management guidance — where a real, dated guidance statement exists "
                 "it is carried through from nifty50_drivers.json and attributed; where "
                 "none exists the field is empty rather than filled in."),
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    s = screen
    print(f"thresholds: consistency>={a.consistency}%  5y CAGR>={a.delivered}%  "
          f"implied forward>={a.forward}%   target {a.n}")
    print(f"walked {s['walk_length']} names -> {len(s['selected'])} selected "
          f"({s['selected_weight_pct']}% of index weight), "
          f"{len(s['rejected'])} rejected, {len(s['no_data'])} no history\n")
    print(f"{'sym':13s}{'wt%':>6}{'cons%':>7}{'5yCAGR':>8}{'fwd%':>7}  sector")
    for r in s["selected"]:
        print(f"{r['symbol']:13s}{r['weight_pct']:6.1f}{r['consistency_pct']:7.1f}"
              f"{r['profit_cagr_5y_pct']:8.1f}{r['implied_growth_pct']:7.1f}  {r['sector']}")
    for name, bt in (("A", bt_today), ("B", bt_pit)):
        if "error" in bt:
            print(f"\n{name}: {bt['error']}")
            continue
        print(f"\n{bt['label']}\n  {bt['start']} -> {bt['end']} ({bt['years']}y), "
              f"{bt['n_holdings']} holdings")
        print(f"  portfolio {bt['portfolio_ret_pct']:+.1f}%  ({bt['portfolio_cagr_pct']:.1f}% CAGR)   "
              f"index {bt['index_ret_pct']:+.1f}%  ({bt['index_cagr_pct']:.1f}% CAGR)   "
              f"excess {bt['excess_pp']:+.1f}pp")
        print(f"  {bt['beat_index']}/{bt['n_holdings']} beat the index, median {bt['median_ret_pct']:+.1f}%")
    if inflation is not None:
        print(f"\nlook-ahead inflation: {inflation:+.1f}pp  (A minus B)")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
