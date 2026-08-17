#!/usr/bin/env python3
"""outlook_backtest.py — does the outlook methodology actually work?

THE TEST
    Stand at a date in the past. Use ONLY what was published by then. Run the same
    arithmetic the Outlook tab runs. Then look at what the index actually did over the
    next 6 months, 1 year and 2 years, and measure the error.

    Without this, `nifty_outlook.py` is a calculator that has never been asked whether
    its answers are any good.

THE ONE THING THAT MAKES THIS CLEAN — k CANCELS
    The reconstructed P/E series has a free scale constant: index EPS = k x aggregate
    panel profit, with k fixed so today matches the app's bottom-up multiple. Using
    today's k at a 2022 as-of date would be look-ahead.

    It isn't, because the projection only ever uses a RATIO of multiples:

        level / spot = (1 + g)^T  x  (exit_PE / PE_at_asof)

    Both multiples carry the same k, so it divides out exactly. Every method below is
    therefore expressed as a RETURN RATIO, and the backtest never needs to know the
    absolute level of the historical P/E — only its shape, which is measured. The
    valuation-anchor problem that makes the Outlook tab's absolute levels unverifiable
    simply does not arise here.

WHAT IS BEING COMPARED
    M1  RUN-RATE, NO RE-RATING   the tab's one conditional row: earnings grow at the
                                 last published rate, multiple unchanged.
    M2  REFERENCE MEDIAN         the tab's reference rows: growth at the median of
                                 published years, multiple reverts to its own median.
                                 This is the mean-reversion assumption, made testable.
    M3  NO CHANGE                the null. The index is where it is.
    M4  HISTORICAL DRIFT         the index rises by its own median realised T-return,
                                 measured only over windows that ended before the as-of.

    M3 is the one that matters. A valuation method that cannot beat "assume nothing
    happens" has not earned its complexity, and the literature on index-level forecasting
    says that is the usual outcome. Reporting M1 and M2 without M3 beside them would be
    marking our own homework.

POINT-IN-TIME DISCIPLINE
    · An annual result is usable only 92 days after its year-end — the same publication
      lag whose absence cost a backtest two thirds of its reported edge (correction C21).
    · The P/E at the as-of date is built from annuals known at the as-of date.
    · The historical-drift method uses only windows that had already CLOSED before the
      as-of date, not merely started.

WHAT STILL LEAKS, AND CANNOT BE FIXED HERE
    · RESTATEMENT (C22). The earnings panel is today's Screener export. A 2021 as-of date
      reads today's restated view of FY2020, not the figure printed in 2020.
    · CONSTANT CONSTITUENTS. The panel is today's Nifty 50 back-cast. Names entered the
      index by growing, so historical "index earnings" are flattered.
    Both push results in the optimistic direction. Treat every error figure as a floor.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB = os.path.join(_ROOT, "option_chains.db")
DELIVERY = os.path.join(_ROOT, "delivery_history.json")
OUT = os.path.join(_ROOT, "outlook_backtest.json")

PUBLICATION_LAG_DAYS = 92
HORIZONS = [("6M", 126, 0.5), ("1Y", 252, 1.0), ("2Y", 504, 2.0)]
# An as-of date needs at least this many published annual growth observations before
# "median published growth" means anything.
MIN_KNOWN_YEARS = 2
# As-of dates every quarter. Consecutive dates share almost all their data and their
# forward windows overlap heavily — the independent count is reported everywhere.
ASOF_STEP_DAYS = 91
MIN_INDEPENDENT = 5


def _panel():
    with open(DELIVERY) as f:
        hist = json.load(f)["history"]
    years = [f"{y}-03-31" for y in range(2018, 2027)]
    names = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]
    agg = {y: sum(next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)
                  for s in names) for y in years}
    # (date the figure became public, fiscal year, aggregate profit)
    pub = sorted((( datetime.date.fromisoformat(y)
                    + datetime.timedelta(days=PUBLICATION_LAG_DAYS)).isoformat(), y, agg[y])
                 for y in years)
    return names, pub


def _bars():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [(r[0][:10], float(r[1])) for r in con.execute(
        "SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
        "AND close IS NOT NULL ORDER BY ts")]
    con.close()
    return [d for d, _ in rows], [c for _, c in rows]


def _median(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def _known(pub, asof: str):
    """Annuals public at `asof`, and the YoY growths derivable from them."""
    k = [(y, a) for pd_, y, a in pub if pd_ <= asof]
    growth = [(k[i][1] / k[i - 1][1] - 1) for i in range(1, len(k))]
    return k, growth


def _pe_path(pub, dates, closes, upto_idx):
    """P/E in ARBITRARY UNITS (k omitted — it cancels) for every session up to upto_idx."""
    out = []
    for i in range(upto_idx + 1):
        d = dates[i]
        agg = None
        for pd_, _y, a in pub:
            if pd_ <= d:
                agg = a
        if agg:
            out.append(closes[i] / agg)      # = PE / k
    return out


def run() -> dict:
    names, pub = _panel()
    dates, closes = _bars()
    idx_of = {d: i for i, d in enumerate(dates)}

    first_asof = datetime.date.fromisoformat(pub[MIN_KNOWN_YEARS][0])
    last_date = datetime.date.fromisoformat(dates[-1])

    asofs = []
    d = first_asof
    while d <= last_date:
        # snap to the first trading session on or after d
        cand = next((x for x in dates if x >= d.isoformat()), None)
        if cand and cand not in asofs:
            asofs.append(cand)
        d += datetime.timedelta(days=ASOF_STEP_DAYS)

    results = {label: [] for label, _n, _y in HORIZONS}
    for asof in asofs:
        i = idx_of[asof]
        known, growth = _known(pub, asof)
        if len(growth) < MIN_KNOWN_YEARS:
            continue
        g_run = growth[-1]
        g_med = _median(growth)

        pe_hist = _pe_path(pub, dates, closes, i)
        if len(pe_hist) < 60:
            continue
        pe_now = pe_hist[-1]
        pe_med = _median(pe_hist)
        rerate = pe_med / pe_now          # k cancels here

        for label, n, yrs in HORIZONS:
            j = i + n
            if j >= len(closes):
                continue
            actual = closes[j] / closes[i]

            # M4 uses only windows that had CLOSED before the as-of date.
            prior = [closes[a + n] / closes[a] for a in range(0, i - n) if a + n < i]
            drift = _median(prior) if len(prior) >= 30 else None

            preds = {
                "M1_runrate_no_rerating": (1 + g_run) ** yrs,
                "M2_reference_median": ((1 + g_med) ** yrs) * rerate,
                "M3_no_change": 1.0,
                "M4_historical_drift": drift,
            }
            # Did the multiple do what M2 assumed? k cancels in this ratio too, so the
            # realised re-rating is directly comparable to the predicted one. This is the
            # closest thing to a test of O9 (does a low multiple revert?) that the data
            # supports — not a formal test, but evidence in a direction.
            pe_then = pe_hist[-1]
            pe_fwd = _pe_path(pub, dates, closes, j)
            realised_rerate = (pe_fwd[-1] / pe_then) if pe_fwd else None

            results[label].append({
                "asof": asof, "spot": round(closes[i], 1),
                "rerate_predicted": round(rerate, 3),
                "rerate_realised": (round(realised_rerate, 3) if realised_rerate else None),
                "actual_ratio": round(actual, 4),
                "actual_pct": round((actual - 1) * 100, 1),
                "known_years": len(growth),
                "g_runrate_pct": round(g_run * 100, 1),
                "g_median_pct": round(g_med * 100, 1),
                "rerate_factor": round(rerate, 3),
                "pred": {k: (round(v, 4) if v is not None else None)
                         for k, v in preds.items()},
                "err_pp": {k: (round((v - actual) * 100, 1) if v is not None else None)
                           for k, v in preds.items()},
            })

    summary = {}
    for label, n, _y in HORIZONS:
        rows = results[label]
        if not rows:
            continue
        methods = {}
        for m in ("M1_runrate_no_rerating", "M2_reference_median",
                  "M3_no_change", "M4_historical_drift"):
            errs = [r["err_pp"][m] for r in rows if r["err_pp"].get(m) is not None]
            if not errs:
                continue
            hits = [1 if ((r["pred"][m] > 1) == (r["actual_ratio"] > 1)) else 0
                    for r in rows if r["pred"].get(m) is not None]
            methods[m] = {
                "n": len(errs),
                "mae_pp": round(sum(abs(e) for e in errs) / len(errs), 1),
                "median_err_pp": round(_median(errs), 1),
                "bias_pp": round(sum(errs) / len(errs), 1),
                # M3 predicts a ratio of exactly 1.0, so "did it call the direction"
                # collapses to "was the window negative". Reported as None rather than
                # as a 0% that invites comparison with the others.
                "direction_hit_pct": (None if m == "M3_no_change"
                                      else round(sum(hits) / len(hits) * 100, 0) if hits else None),
            }
        base = methods.get("M3_no_change", {}).get("mae_pp")
        for m, v in methods.items():
            v["vs_null_pp"] = (round(base - v["mae_pp"], 1) if base is not None else None)
            v["beats_null"] = (v["vs_null_pp"] is not None and v["vs_null_pp"] > 0)
        rr = [(r["rerate_predicted"], r["rerate_realised"]) for r in rows
              if r.get("rerate_realised")]
        rerate_check = None
        if rr:
            # M2 predicts reversion whenever predicted > 1 (multiple below its median).
            pred_up = [(p, a) for p, a in rr if p > 1]
            rerate_check = {
                "n": len(rr),
                "n_predicted_rerating": len(pred_up),
                "reverted_as_predicted_pct": (round(sum(1 for p, a in pred_up if a > 1)
                                                    / len(pred_up) * 100, 0) if pred_up else None),
                "median_predicted": round(_median([p for p, _ in rr]), 3),
                "median_realised": round(_median([a for _, a in rr]), 3),
                "note": ("predicted is median-multiple ÷ multiple-at-as-of; realised is "
                         "multiple-at-horizon ÷ multiple-at-as-of. Both are k-free. If "
                         "the median realised sits well below the median predicted, the "
                         "multiple did NOT revert over these windows and the reference "
                         "rows on the Outlook tab are optimistic by that much."),
            }

        summary[label] = {
            "n_asof": len(rows), "n_independent": len(rows) // max(1, (n // ASOF_STEP_DAYS + 1)),
            "rerating_check": rerate_check,
            "sufficient": (len(rows) // max(1, (n // ASOF_STEP_DAYS + 1))) >= MIN_INDEPENDENT,
            "methods": methods,
            "best_by_mae": min(methods, key=lambda k: methods[k]["mae_pp"]) if methods else None,
        }

    return {
        "as_of": datetime.date.today().isoformat(),
        "panel_names": len(names),
        "asof_dates": len(asofs),
        "first_asof": results["6M"][0]["asof"] if results.get("6M") else None,
        "publication_lag_days": PUBLICATION_LAG_DAYS,
        "methods": {
            "M1_runrate_no_rerating": "earnings grow at the last PUBLISHED annual rate; multiple unchanged",
            "M2_reference_median": "growth at the median of published years; multiple reverts to its own median",
            "M3_no_change": "the null — index stays where it is",
            "M4_historical_drift": "index rises by its own median realised return over windows CLOSED before the as-of",
        },
        "summary": summary,
        "detail": results,
        "caveats": [
            "As-of dates are quarterly and their forward windows overlap heavily. "
            "n_independent is the honest count; below 5 the row is not evidence.",
            "RESTATEMENT (C22) — the earnings panel is today's Screener export, so a 2021 "
            "as-of reads today's restated view of FY2020. Not fixable without a "
            "point-in-time fundamentals archive.",
            "CONSTANT CONSTITUENTS — today's Nifty 50 back-cast. Names entered the index "
            "by growing, so historical index earnings are flattered and every method that "
            "extrapolates them is flattered with them.",
            "The k constant in the reconstructed P/E cancels out of every ratio here, so "
            "the valuation anchor is NOT a source of look-ahead in this test — unlike the "
            "absolute levels on the Outlook tab.",
            "Price return only; no dividends, no costs.",
            "The whole sample sits inside one regime: a de-rating from ~40x to ~20x with "
            "rising earnings. A method tuned to that is not thereby a method.",
        ],
    }


def main() -> int:
    doc = run()
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    print(f"panel {doc['panel_names']} names · {doc['asof_dates']} quarterly as-of dates "
          f"· first usable {doc['first_asof']} · publication lag {doc['publication_lag_days']}d\n")
    for label, s in doc["summary"].items():
        flag = "" if s["sufficient"] else "   << below the evidence floor"
        print(f"{label}   n={s['n_asof']} as-of dates, {s['n_independent']} independent{flag}")
        print(f"{'   method':34s}{'MAE pp':>9}{'bias':>8}{'median':>9}{'dir%':>7}{'vs null':>9}")
        for m, v in sorted(s["methods"].items(), key=lambda x: x[1]["mae_pp"]):
            mark = " *" if m == s["best_by_mae"] else "  "
            dh = v["direction_hit_pct"]
            # M3 projects a flat index, so a "direction hit rate" for it is not a
            # comparable quantity — printed as n/a rather than as a misleading 0%.
            dh_s = "n/a" if dh is None else f"{dh:.0f}"
            vs = v["vs_null_pp"] if v["vs_null_pp"] is not None else 0.0
            print(f"{mark} {m:32s}{v['mae_pp']:9.1f}{v['bias_pp']:8.1f}"
                  f"{v['median_err_pp']:9.1f}{dh_s:>7s}{vs:+9.1f}")
        rc = s.get("rerating_check")
        if rc:
            print(f"   re-rating check: M2 predicted a multiple change of "
                  f"{rc['median_predicted']}x (median); the index actually delivered "
                  f"{rc['median_realised']}x.")
            if rc["n_predicted_rerating"]:
                print(f"   of {rc['n_predicted_rerating']} as-of dates where the multiple "
                      f"was BELOW its median, it rose over the next {label} in "
                      f"{rc['reverted_as_predicted_pct']:.0f}% of them.")
        print()
    print("MAE is mean absolute error in percentage points of the realised return.")
    print("bias > 0 means the method projected MORE than the index delivered.")
    print("vs null > 0 means it beat 'assume no change'. That is the bar.")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
