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
# The fiscal year whose annuals the point-in-time screen is allowed to read.
PIT_CUTOFF = "2021-04-01"
# ...and the gap before it may TRADE on them. Indian March-year-end annual results are
# published in May-June. An earlier version of this file read the FY2021 annual number
# and bought on 1-Apr-2021 — two to three months before that number existed. The excess
# return it reported (+14.2pp) fell to +4.6pp once the buy was moved to 1 July. Ninety-two
# days is the first date by which every Nifty 50 constituent has certainly reported.
PUBLICATION_LAG_DAYS = 92
# Cutoffs for the start-date sweep. One start date is one draw, and the corrected result
# above moved 10pp on a single quarter's shift — so the spread across starts is reported
# rather than one number being presented as the answer.
SWEEP_CUTOFFS = ["2019-04-01", "2020-04-01", "2021-04-01", "2022-04-01", "2023-04-01"]


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

FII_HOLDINGS = os.path.join(_ROOT, "fii_holdings.json")

# FII holding is a FLAG, deliberately not a gate.
#
# The tempting reading is that a high FII stake means institutions did the diligence, so
# it proxies for quality. The data in this panel says otherwise, and the reason is
# mechanical: FII% is a share of FREE FLOAT. A company whose promoter owns most of it
# cannot show a high FII stake however good it is. The two LOWEST-FII names in the
# selection — Bajaj Finserv 6.9% and Hindustan Unilever 9.5% — are two of only four with
# 100% consistency, profit up in every measured year. Unilever owns ~62% of HUL. A 10%
# minimum would drop both, screening on promoter holding while believing it screened on
# institutional conviction.
#
# The reverse holds at the top: the HIGHEST FII holding among the selected is Shriram
# Finance at 54.8%, which also has the widest CAGR-to-median gap (+23.1pp) — the weakest
# underlying compounder in the fifteen.
#
# So the threshold below marks only the floor case — a name with essentially NO foreign
# institutional participation, which is worth seeing — and never removes anything. The
# informative FII number is the CHANGE (fii_change_pp / fii_change_4q_pp), which is a
# decision made this quarter and is not capped by free float.
FII_MIN_PCT = 5.0




_QUARTER_ENDS = {"Mar", "Jun", "Sep", "Dec"}
FII_IMPLAUSIBLE_PP = 5.0


def _fii_quality(f: dict) -> dict:
    """Is this company's FII series trustworthy enough to read a CHANGE off?

    FLAGS, NEVER IMPUTES — and the ICICI case is why.

    The series reads 45.6 -> 43.9 -> 34.5 -> 49.8. The first reading was that the 34.5
    must be corrupt, because Trendlyne shows ICICI's FII stake sitting in a 44.8-46.2%
    band through 2024. That reading was WRONG, and checking a second source is what
    showed it: Upstox reports 45.56 / 43.87 / 34.49 / 49.82 — our series exactly — so
    the Mar-2026 value of 34.49 is confirmed, not corrupt.

    The genuine dispute is the LATEST quarter. For Jun-2026, Upstox says 49.82% while
    MarketsMojo says 33.79%, and MarketsMojo's own breakdown gives the reason: its
    "Other DII" line rose 16.51pp in the same quarter. Roughly sixteen points of the
    same holdings are being classified as FII by one vendor and as domestic-institutional
    by the other. There is no arithmetic error to repair — there is a classification
    disagreement, and picking a side without a filing-level source would be inventing an
    answer to a question neither vendor has settled.

    So a suspect series is MARKED, never replaced or interpolated. An imputed value would
    sit in the series indistinguishable from an observation, and here it would be a
    figure two published sources contradict. A visible flag can be interrogated; a smooth
    fabrication cannot.

    Two independent symptoms, which do not always coincide:
      - a quarter-on-quarter move beyond 5pp, which for a large-cap stake usually means
        a reclassification rather than buying or selling
      - a period label that is not a filing quarter. Indian shareholding is filed at
        Mar/Jun/Sep/Dec only, yet ADANIENT carries "Jul 2026" and JIOFIN and SHRIRAMFIN
        carry "Apr 2026". Shriram's implausible +11.0pp move sits exactly on its odd
        label; ICICI's disputed value carries a perfectly valid one.

    The LEVEL survives both; only the CHANGE fields are unsafe to read.
    """
    t = f.get("trend") or []
    jumps, odd = [], []
    for a, b in zip(t, t[1:]):
        pa, pb = a.get("pct"), b.get("pct")
        if pa is not None and pb is not None and abs(pb - pa) >= FII_IMPLAUSIBLE_PP:
            jumps.append({"period": b.get("period"), "move_pp": round(pb - pa, 2)})
    for r in t + [{"period": f.get("period")}]:
        lab = (r.get("period") or "").split()
        if lab and lab[0] not in _QUARTER_ENDS:
            odd.append(r.get("period"))
    suspect = bool(jumps or odd)
    return {
        "fii_suspect": suspect,
        "fii_suspect_reason": (
            None if not suspect else
            "; ".join(filter(None, [
                (f"{len(jumps)} implausible quarter move(s) >= {FII_IMPLAUSIBLE_PP}pp: "
                 + ", ".join(f"{j['period']} {j['move_pp']:+.1f}pp" for j in jumps))
                if jumps else None,
                (f"period label(s) not a filing quarter: {', '.join(sorted(set(odd)))}")
                if odd else None]))),
        "fii_change_trustworthy": not suspect,
        "fii_repair_policy": ("flagged, never imputed — see the docstring. For ICICI a second source (Upstox) confirms our Mar-2026 value and a third (MarketsMojo) disputes only Jun-2026, at 33.79% against 49.82%, because roughly 16pp is classified as FII by one vendor and Other-DII by the other. No arithmetic error exists to repair."),
    }


def _worst_year_context(delivery: dict, sectors: dict) -> dict:
    """WHY was each company's worst year bad — a market fall, its sector, or itself?

    "Worst year -69.9%" is a number without a verdict. A quality screen cares whether
    that was the whole market having a bad year, the company's sector repricing, or the
    company alone breaking — those are three different risks and only the last one is
    evidence about the business.

    Classified from the panel itself, NOT from recollection about each company:
      market-wide       the median of all 47 names also fell that year
      sector-wide       the sector median fell, the panel median did not
      company-specific  neither fell; this name fell alone
      corporate action  the share count moved >5% that year, so a merger, demerger or
                        buyback is inside the comparison and the fall may be arithmetic

    Rank within the sector is carried too, because "worst in a bad year" and "worst in a
    good year" are different facts about the same percentage.
    """
    fy_of, yoy_of = {}, {}
    for sym, v in delivery.items():
        ys = [x["period"] for x in v.get("series", [])][1:]     # yoy has one fewer entry
        g = v.get("yoy_net_profit_pct") or []
        if not ys or not g:
            continue
        fy_of[sym] = ys[:len(g)]
        yoy_of[sym] = g

    years = sorted({y for ys in fy_of.values() for y in ys})
    panel_med, sector_med = {}, {}
    for y in years:
        vals = [yoy_of[s][fy_of[s].index(y)] for s in fy_of
                if y in fy_of[s] and yoy_of[s][fy_of[s].index(y)] is not None]
        if vals:
            panel_med[y] = round(sorted(vals)[len(vals) // 2], 1)
        by = {}
        for s in fy_of:
            if y not in fy_of[s]:
                continue
            v = yoy_of[s][fy_of[s].index(y)]
            if v is not None:
                by.setdefault(sectors.get(s, "?"), []).append(v)
        for sec, vs in by.items():
            sector_med[(sec, y)] = round(sorted(vs)[len(vs) // 2], 1)

    out = {}
    for sym in fy_of:
        g = [x for x in yoy_of[sym] if x is not None]
        if not g:
            continue
        worst = min(g)
        i = yoy_of[sym].index(worst)
        y = fy_of[sym][i]
        sec = sectors.get(sym, "?")
        pm, sm = panel_med.get(y), sector_med.get((sec, y))
        flags = (delivery[sym].get("share_move_flags") or [])
        corp = bool(i < len(flags) and flags[i])

        # A "worst year" that is still POSITIVE is not a fall at all — it is the floor of
        # an unbroken run, and the strongest single fact a consistency screen can report.
        # The first version classified these as "company-specific" and told the reader
        # HDFC Bank "fell alone" in a year it grew 10.7%. Four of the fifteen are in this
        # category, so the error was not a corner case.
        if worst >= 0:
            kind = "no down year"
        elif corp:
            kind = "corporate action"
        elif pm is not None and pm < 0:
            kind = "market-wide"
        elif sm is not None and sm < 0:
            kind = "sector-wide"
        else:
            kind = "company-specific"

        peers = [(s2, yoy_of[s2][fy_of[s2].index(y)]) for s2 in fy_of
                 if sectors.get(s2) == sec and y in fy_of[s2]
                 and yoy_of[s2][fy_of[s2].index(y)] is not None]
        peers.sort(key=lambda x: x[1])
        rank = [x[0] for x in peers].index(sym) + 1 if any(x[0] == sym for x in peers) else None

        out[sym] = {
            "worst_year_fy": f"FY{y[:4]}",
            "worst_year_kind": kind,
            "worst_year_panel_median_pct": pm,
            "worst_year_sector_median_pct": sm,
            "worst_year_sector_rank": (f"{rank} of {len(peers)} worst in {sec}"
                                       if rank else None),
            "worst_year_reason": {
                "no down year": (f"never had a down year in the measured period — the "
                                 f"weakest was still +{worst}% growth. The floor of an "
                                 f"unbroken run, not a drawdown."),
                "market-wide": (f"the whole panel fell that year (median {pm}%), so this is "
                                "a market-wide earnings year, not evidence about the business"),
                "sector-wide": (f"{sec} fell as a sector (median {sm}%) while the panel did "
                                f"not (median {pm}%) — a sector repricing"),
                "company-specific": (f"neither the panel ({pm}%) nor {sec} ({sm}%) fell that "
                                     "year. This name fell alone — the only one of the three "
                                     "that is evidence about the company"),
                "corporate action": ("the share count moved more than 5% that year, so a "
                                     "merger, demerger or buyback sits inside the comparison "
                                     "and the fall may be arithmetic rather than operational"),
            }[kind],
        }
    return out


def _median_growth(h: dict) -> float | None:
    """Median annual net-profit growth — the endpoint-independent companion to CAGR.

    WHY THIS SITS BESIDE THE CAGR RATHER THAN REPLACING IT
    A CAGR is two endpoints and nothing in between, so it inherits whatever cycle its
    endpoints happen to sit in. The 5-year gate runs FY21->FY26: it starts AFTER the
    -12.1% COVID collapse and captures the +54.3% rebound, so it reads a recovery as
    compound growth. Titan prints 39.1% that way against 20.1% from a pre-COVID base;
    Eicher 32.6% against 14.0%.

    Moving the window does NOT fix it — it swaps one cycle for another. From an FY19
    base ICICI reads 47.1% instead of 25.4%, because FY19 is the bottom of the bank NPA
    cycle. There is no clean base year in this sample.

    The median of the annual growths cannot be moved by a single depressed or inflated
    endpoint, so the GAP between CAGR and median is itself the base-effect measure. A
    name where they agree grew; a name where the CAGR is far higher rebounded.
    """
    ys = [x for x in (h.get("yoy_net_profit_pct") or []) if x is not None]
    if len(ys) < 3:
        return None
    ys = sorted(ys)
    n = len(ys)
    return round(ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2, 1)


def _eps_always_positive(h: dict) -> tuple[bool | None, int]:
    """Did this company earn a positive EPS in EVERY measured year?

    A loss year is not a small negative growth rate — it breaks the arithmetic a quality
    screen depends on. CAGR through a negative endpoint is undefined, growth off a
    negative base has the wrong sign, and a 'consistency' fraction counts a swing from
    -160 to -8 as a year of growth. Seven of the 47 names have at least one loss year
    (IndiGo 5, Max Healthcare 3, Bharti 2 from the AGR provision, Zomato 2, Tata Steel 2,
    SBI 1, Trent 1). None are currently selected, so this gate changes nothing today —
    which is exactly when it is safe to add a rule, and it makes an assumption that was
    incidental into one that is explicit.
    """
    eps = [x.get("eps") for x in h.get("series", []) if x.get("eps") is not None]
    if not eps:
        return None, 0
    neg = sum(1 for e in eps if e <= 0)
    return neg == 0, neg


def walk(consistency_min: float, delivered_min: float, forward_min: float,
         target_n: int) -> dict:
    uni = _universe()
    delivery = (_load(DELIVERY, {}) or {}).get("history", {})
    growth, snap_at = _implied_growth()
    drivers = (_load(DRIVERS, {}) or {}).get("companies", {})
    fii = (_load(FII_HOLDINGS, {}) or {}).get("holdings", {})
    worst_ctx = _worst_year_context(delivery, {x["Symbol"]: x["Sector"] for x in uni})

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

        med = _median_growth(h)
        eps_ok, eps_neg_years = _eps_always_positive(h)
        f = fii.get(sym) or {}

        fails = []
        # A loss year breaks the arithmetic the other gates rely on — see
        # _eps_always_positive. Applied before the growth gates for that reason.
        if eps_ok is False:
            fails.append(f"eps_negative ({eps_neg_years}y)")
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
            **(worst_ctx.get(sym) or {}),
            "median_annual_growth_pct": med,
            "cagr_over_median_pp": (round(cagr - med, 1)
                                    if cagr is not None and med is not None else None),
            "eps_always_positive": eps_ok, "eps_negative_years": eps_neg_years,
            "fii_pct": f.get("latest_pct"),
            "fii_above_min": (None if f.get("latest_pct") is None
                              else f["latest_pct"] >= FII_MIN_PCT),
            "fii_min_pct": FII_MIN_PCT,
            "fii_flag_note": ("FLAG ONLY — never gates. FII% is a share of free float, so "
                              "a high-promoter-holding company cannot score well on it "
                              "regardless of quality. Read fii_change_pp instead."),
            **_fii_quality(f),
            "fii_change_pp": f.get("change_pp"),
            "fii_change_4q_pp": f.get("change_4q_pp"), "fii_direction": f.get("direction"),
            "fii_period": f.get("period"),
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
    # Ranked for EVERY record, not just the selected ones. The thresholds are controls in
    # the UI: a name promoted from the rejected pool by moving a slider has to arrive
    # with its peer rank already attached, or the column renders blank for exactly the
    # rows the reader just went looking for.
    for rec in selected + overflow + rejected:
        if rec.get("profit_cagr_5y_pct") is None:
            continue
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
        # Each holding's CAGR uses ITS OWN elapsed years, not the index's. Identical
        # today because every name has bars from the start date, but a later-listed
        # constituent would otherwise have its compound rate silently understated.
        hy = (datetime.date.fromisoformat(b[0]) - datetime.date.fromisoformat(a[0])).days / 365.25
        holdings.append({"symbol": s, "buy_date": a[0], "buy": round(a[1], 2),
                         "last": round(b[1], 2), "years_held": round(hy, 2),
                         "ret_pct": round((b[1] / a[1] - 1) * 100, 1),
                         "cagr_pct": round(((b[1] / a[1]) ** (1 / hy) - 1) * 100, 1)
                                     if hy > 0 else None})
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


def buy_date(cutoff: str) -> str:
    """First date the screen may TRADE on data it was allowed to READ at `cutoff`."""
    return (datetime.date.fromisoformat(cutoff)
            + datetime.timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()


def sweep(consistency_min: float, delivered_min: float, target_n: int) -> dict:
    """Re-run the whole point-in-time exercise from several fiscal cutoffs.

    One start date is one draw, and this screen is demonstrably sensitive to which draw
    you take: correcting the publication lag alone moved the 2021 excess from +14.2pp to
    +4.6pp. Reporting a single number from a single start after seeing that would be
    presenting the noise as the result.

    Excess is reported ANNUALISED (portfolio CAGR minus index CAGR). Total excess is not
    comparable across starts — a 2019 start holds for seven years and a 2023 start for
    three, so the longer window wins on arithmetic before any skill is involved.
    """
    runs = []
    for cut in SWEEP_CUTOFFS:
        bd = buy_date(cut)
        pit = point_in_time(cut, consistency_min, delivered_min, target_n)
        syms = [p["symbol"] for p in pit["picked"]]
        if not syms:
            # Two very different reasons produce an empty list and they must not read
            # the same. `considered` counts names that HAD a usable series at this
            # cutoff; if it is zero the history simply does not reach back that far
            # (delivery_history starts around FY2017, and _as_of needs four periods),
            # which is a data limit, not a verdict on the screen.
            runs.append({
                "cutoff": cut, "buy_date": bd, "n_picked": 0,
                "considered": pit["considered"],
                "error": ("history does not reach this cutoff — no name has four annual "
                          "periods on or before it" if not pit["picked"] and
                          all(_as_of(h, cut) is None
                              for h in (_load(DELIVERY, {}) or {}).get("history", {}).values())
                          else "no name passed the two legs at this cutoff")})
            continue
        bt = backtest(syms, bd, f"cutoff {cut}")
        if "error" in bt:
            runs.append({"cutoff": cut, "buy_date": bd, "n_picked": len(syms),
                         "error": bt["error"]})
            continue
        runs.append({
            "cutoff": cut, "buy_date": bt["start"], "end": bt["end"],
            "years": bt["years"], "n_picked": len(syms), "n_holdings": bt["n_holdings"],
            "portfolio_cagr_pct": bt["portfolio_cagr_pct"],
            "index_cagr_pct": bt["index_cagr_pct"],
            "excess_cagr_pp": round(bt["portfolio_cagr_pct"] - bt["index_cagr_pct"], 1),
            "excess_total_pp": bt["excess_pp"],
            "beat_index": bt["beat_index"],
            "picks": syms,
        })
    good = [r for r in runs if "excess_cagr_pp" in r]
    ex = sorted(r["excess_cagr_pp"] for r in good)
    return {
        "runs": runs, "n_starts": len(good),
        "excess_cagr_pp_min": ex[0] if ex else None,
        "excess_cagr_pp_median": ex[len(ex) // 2] if ex else None,
        "excess_cagr_pp_max": ex[-1] if ex else None,
        "starts_beating_index": sum(1 for x in ex if x > 0),
        "note": ("Overlapping holding periods — these are NOT independent trials. They "
                 "share most of their price history and most of their holdings, so the "
                 "spread describes sensitivity to the start date, not a sampling "
                 "distribution. A consistent sign across starts is weak evidence; a sign "
                 "that flips is strong evidence of nothing."),
    }


# ---------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--consistency", type=float, default=CONSISTENCY_MIN)
    ap.add_argument("--delivered", type=float, default=DELIVERED_MIN)
    ap.add_argument("--forward", type=float, default=FORWARD_MIN)
    ap.add_argument("--n", type=int, default=TARGET_N)
    ap.add_argument("--cutoff", default=PIT_CUTOFF)
    a = ap.parse_args()

    screen = walk(a.consistency, a.delivered, a.forward, a.n)
    syms = [s["symbol"] for s in screen["selected"]]

    pit = point_in_time(a.cutoff, a.consistency, a.delivered, a.n)
    pit_syms = [p["symbol"] for p in pit["picked"]]

    # BOTH portfolios buy on the lagged date. A is invalid regardless, but it has to
    # start where B starts or the gap between them measures the calendar as well as the
    # look-ahead.
    start = buy_date(a.cutoff)
    bt_today = backtest(syms, start, "A · today's screen, held from start (INVALID — look-ahead)")
    bt_pit = backtest(pit_syms, start, "B · point-in-time screen (walk-forward)")
    sw = sweep(a.consistency, a.delivered, a.n)
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
            "cutoff": a.cutoff, "buy_date": start,
            "publication_lag_days": PUBLICATION_LAG_DAYS,
            "start_date_sweep": sw,
            "caveats": [
                "PUBLICATION LAG — the screen reads annual results dated on or before "
                f"{a.cutoff} and does not trade them until {start}, "
                f"{PUBLICATION_LAG_DAYS} days later, because Indian March-year-end "
                "annuals are published in May-June. An earlier version of this file "
                "bought the day after the cutoff and reported +14.2pp of excess; the "
                "same portfolio bought after publication returned +4.6pp. Two thirds of "
                "that result was reading results before they existed.",
                "RESTATEMENT — delivery_history.json is built from TODAY'S Screener "
                "exports, and Screener carries restated history (Ind-AS transitions, "
                "reclassified consolidations, merger restatements). So even a correctly "
                "lagged cutoff reads today's VIEW of 2021 rather than what was published "
                "in 2021. There is no point-in-time fundamentals archive in this repo, "
                "so this cannot be fixed here — only disclosed. It makes every "
                "walk-forward figure an upper bound.",
                "ONE START DATE IS ONE DRAW — see start_date_sweep. Shifting the buy by a "
                "single quarter moved the 2021 excess by roughly 10pp, which is larger "
                "than the excess itself.",
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

    print(f"\nSTART-DATE SWEEP  (annualised excess — total excess is not comparable "
          f"across different holding lengths)")
    print(f"{'cutoff':12s}{'buy':12s}{'yrs':>5}{'n':>4}{'port CAGR':>11}"
          f"{'index CAGR':>12}{'excess':>9}")
    for r in sw["runs"]:
        if "error" in r:
            print(f"{r['cutoff']:12s}{r['buy_date']:12s}  {r['error']}")
            continue
        print(f"{r['cutoff']:12s}{r['buy_date']:12s}{r['years']:5.1f}{r['n_holdings']:4d}"
              f"{r['portfolio_cagr_pct']:11.1f}{r['index_cagr_pct']:12.1f}"
              f"{r['excess_cagr_pp']:+9.1f}")
    print(f"  across {sw['n_starts']} starts: excess CAGR min {sw['excess_cagr_pp_min']:+.1f}pp, "
          f"median {sw['excess_cagr_pp_median']:+.1f}pp, max {sw['excess_cagr_pp_max']:+.1f}pp; "
          f"{sw['starts_beating_index']}/{sw['n_starts']} beat the index")
    print("  NOT independent trials — overlapping periods and overlapping holdings.")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
