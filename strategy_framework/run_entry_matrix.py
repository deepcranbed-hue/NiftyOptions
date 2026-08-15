"""
strategy_framework/run_entry_matrix.py
======================================
THE ENTRY × STRUCTURE × MANAGEMENT MATRIX — three optimisation problems, decomposed:

    1. WHEN to enter        (T-4 … T-1)
    2. WHAT structure       (condor / fly / strangle / straddle)
    3. HOW to manage        (hold  /  state-triggered futures overlay)

Every cell = one full simulation to settlement (1 lot, ₹85/leg, intrinsic settle).
Overlay = the frozen rule (short-strike breach + trend-expansion confirmation →
1 futures lot against the threatened side, held to settle).

Output: knowledge/entry_structure_mgmt_matrix.csv (one row per cell) + pivots.
Research aggregates across expiries — per-cell rows are single experiments.

    python -m strategy_framework.run_entry_matrix
"""
from __future__ import annotations
import csv
import os
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series
from strategy_framework.run_overlay_test import _legs, _px
from strategy_framework.strategy.tape_regime import efficiency_ratio

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_LOT = 65
_TXN = 20.0 + 1.0 * _LOT
_STRUCTS = ["condor", "fly", "strangle", "straddle"]


def _run_cell(series, tss, entry_date, structure, overlay):
    ent = next((t for t in tss if t[:10] == entry_date and t[11:16] >= "04:30"), None)
    if ent is None:
        return None
    d0 = series[ent]
    try:
        legs = _legs(d0["rows"], d0["spot"], structure)
    except Exception:
        return None
    credit = sum(sgn * px for _, _, sgn, px in legs)
    s_end = series[tss[-1]]["spot"]
    settle = sum(sgn * max(0.0, (s_end - k) if cp == "C" else (k - s_end))
                 for cp, k, sgn, _ in legs)
    pnl = (credit - settle) * _LOT - 2 * len(legs) * _TXN
    if not overlay:
        return pnl
    day_open = {}
    spots = []
    trigger = None
    for ts in tss:
        st = series[ts]
        s = st["spot"]
        spots.append(s)
        d, hm = ts[:10], ts[11:16]
        if hm <= "04:31" and d not in day_open:
            K = min(st["rows"], key=lambda k: abs(k - s))
            r = st["rows"].get(K)
            day_open[d] = ((r[0] or 0) + (r[1] or 0)) if r else None
        if ts <= ent or trigger or ts == tss[-1]:
            continue
        if hm[:5] not in ("07:30", "09:50") and not (d > entry_date and "04:30" <= hm <= "04:31"):
            continue
        breach = None
        for cp, k, sgn, _ in legs:
            if sgn > 0 and ((cp == "P" and s <= k) or (cp == "C" and s >= k)):
                breach = cp
        if not breach:
            continue
        K = min(st["rows"], key=lambda k: abs(k - s))
        r = st["rows"].get(K)
        S_now = ((r[0] or 0) + (r[1] or 0)) if r else None
        S_10 = day_open.get(d)
        schg = (S_now / S_10 - 1) * 100 if S_now and S_10 else None
        er = efficiency_ratio(spots[-31:]) if len(spots) >= 5 else None
        if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
            trigger = {"s": s, "side": breach}
    if trigger:
        fut = -1 if trigger["side"] == "P" else 1
        pnl += fut * (s_end - trigger["s"]) * _LOT - 2 * _TXN
    return pnl


def main():
    con = sqlite3.connect("option_chains_full.db")
    exps = [r[0] for r in con.execute(
        "SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE '2026-%' ORDER BY expiry")]
    rows = []
    for exp in exps:
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c JOIN chain_rows r "
            "ON r.capture_id=c.capture_id AND r.expiry=? ORDER BY 1", (exp,))]
        entries = [d for d in days if d < exp[:10]][-4:]
        if not entries:
            continue
        series = _full_chain_series(con, exp, f"{entries[0]}T04:29:00Z",
                                    exp[:10] + "T23:59:59Z")
        tss = sorted(series)
        for rank, d in enumerate(entries):
            for s in _STRUCTS:
                for mgmt in ("hold", "overlay"):
                    p = _run_cell(series, tss, d, s, mgmt == "overlay")
                    if p is None:
                        continue
                    rows.append({"expiry": exp[:10], "entry": d,
                                 "rank": f"T-{len(entries) - rank}",
                                 "structure": s, "mgmt": mgmt, "pnl": round(p)})
    con.close()
    out = os.path.join(_KNOW, "entry_structure_mgmt_matrix.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["expiry", "entry", "rank", "structure",
                                          "mgmt", "pnl"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} cells → {out}\n")

    ranks = ["T-4", "T-3", "T-2", "T-1"]

    def agg(rank, s, mgmt, fn):
        v = [r["pnl"] for r in rows if r["rank"] == rank and r["structure"] == s
             and r["mgmt"] == mgmt]
        return fn(v) if v else None

    for title, mgmt in (("HOLD — total across expiries", "hold"),
                        ("OVERLAY minus HOLD (insurance value)", None)):
        print(title)
        print(f"{'rank':<6}" + "".join(f"{s:>10}" for s in _STRUCTS))
        for rk in ranks:
            line = f"{rk:<6}"
            for s in _STRUCTS:
                if mgmt == "hold":
                    v = agg(rk, s, "hold", sum)
                else:
                    h, o = agg(rk, s, "hold", sum), agg(rk, s, "overlay", sum)
                    v = (o - h) if h is not None and o is not None else None
                line += f"{v:>10.0f}" if v is not None else f"{'·':>10}"
            print(line)
        print()
    print("WORST single cell per rank (hold):")
    for rk in ranks:
        v = [(r["pnl"], r["structure"], r["expiry"]) for r in rows
             if r["rank"] == rk and r["mgmt"] == "hold"]
        if v:
            worst = min(v)
            print(f"  {rk}: ₹{worst[0]:.0f}  ({worst[1]}, {worst[2]})")


if __name__ == "__main__":
    main()
