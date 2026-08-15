"""
strategy_framework/run_backtest_full.py
=======================================
FULL-DETAIL BACKTEST BLOTTER — every (entry day × structure × management) cell as a
complete, self-explanatory trade record:

  market      spot at entry, ATM strike, OI walls
  position    every leg with strike + entry premium, short strikes, wing width,
              credit (pts and ₹), max loss (capped structures)
  life        nights held, first short-strike breach (when/side),
              overlay trigger (when/side/spot) where management = overlay
  outcome     settlement spot + debit, total costs, WORST mark-to-market along the
              way, final P&L

1 lot, ₹85/leg per transaction, LTP marks with intrinsic fallback, intrinsic
settlement. Output: knowledge/backtest_full.csv.

    python -m strategy_framework.run_backtest_full
"""
from __future__ import annotations
import csv
import os
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series
from strategy_framework.strategy.tape_regime import efficiency_ratio

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_LOT = 65
_TXN = 20.0 + 1.0 * _LOT
_STRUCTS = ["condor", "fly", "strangle", "straddle"]
_WING = 100


def _px(rows, k, cp, s):
    r = rows.get(k)
    v = (r[0] if cp == "C" else r[1]) if r else None
    if v is not None and v > 0:
        return v
    return max(0.0, (s - k) if cp == "C" else (k - s))


def _build(rows, s, structure):
    K = min(rows, key=lambda k: abs(k - s))
    below = [(k, v[3]) for k, v in rows.items() if k < s]
    above = [(k, v[2]) for k, v in rows.items() if k > s]
    if not below or not above:
        return None
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]
    spec = {"straddle": [("C", K, 1), ("P", K, 1)],
            "strangle": [("C", cw, 1), ("P", pw, 1)],
            "condor": [("C", cw, 1), ("C", cw + _WING, -1), ("P", pw, 1), ("P", pw - _WING, -1)],
            "fly": [("C", K, 1), ("P", K, 1), ("C", K + _WING, -1), ("P", K - _WING, -1)]}[structure]
    legs = []
    for cp, k, sgn in spec:
        p = _px(rows, k, cp, s)
        if p <= 0 and sgn > 0:
            return None
        legs.append((cp, k, sgn, round(p, 2)))
    return {"legs": legs, "K": K, "pw": pw, "cw": cw}


def run_cell(series, tss, entry_date, structure, overlay):
    ent = next((t for t in tss if t[:10] == entry_date and t[11:16] >= "04:30"), None)
    if ent is None:
        return None
    d0 = series[ent]
    b = _build(d0["rows"], d0["spot"], structure)
    if b is None:
        return None
    legs = b["legs"]
    credit = sum(sgn * p for _, _, sgn, p in legs)
    shorts = [(cp, k) for cp, k, sgn, _ in legs if sgn > 0]
    capped = structure in ("condor", "fly")
    max_loss = (_WING - credit) * _LOT if capped else None

    day_open, spots = {}, []
    first_breach = trigger = None
    for ts in tss:
        st = series[ts]
        s = st["spot"]
        spots.append(s)
        d, hm = ts[:10], ts[11:16]
        if hm <= "04:31" and d not in day_open:
            K2 = min(st["rows"], key=lambda k: abs(k - s))
            r = st["rows"].get(K2)
            day_open[d] = ((r[0] or 0) + (r[1] or 0)) if r else None
        if ts <= ent:
            continue
        br = next((cp for cp, k in shorts
                   if (cp == "P" and s <= k) or (cp == "C" and s >= k)), None)
        if br and first_breach is None:
            first_breach = (ts, br)
        if overlay and br and trigger is None and ts != tss[-1] and \
                (hm[:5] in ("07:30", "09:50") or (d > entry_date and "04:30" <= hm <= "04:31")):
            K2 = min(st["rows"], key=lambda k: abs(k - s))
            r = st["rows"].get(K2)
            S_now = ((r[0] or 0) + (r[1] or 0)) if r else None
            S_10 = day_open.get(d)
            schg = (S_now / S_10 - 1) * 100 if S_now and S_10 else None
            er = efficiency_ratio(spots[-31:]) if len(spots) >= 5 else None
            if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
                trigger = (ts, br, s)

    n_txn = 2 * len(legs) + (2 if trigger else 0)
    worst = 0.0
    fut = (-1 if trigger[1] == "P" else 1) if trigger else 0
    for ts in tss:
        if ts <= ent:
            continue
        st = series[ts]
        s = st["spot"]
        debit = sum(sgn * _px(st["rows"], k, cp, s) for cp, k, sgn, _ in legs)
        m = (credit - debit) * _LOT - n_txn * _TXN
        if trigger and ts >= trigger[0]:
            m += fut * (s - trigger[2]) * _LOT
        worst = min(worst, m)
    s_end = series[tss[-1]]["spot"]
    settle = sum(sgn * max(0.0, (s_end - k) if cp == "C" else (k - s_end))
                 for cp, k, sgn, _ in legs)
    pnl = (credit - settle) * _LOT - n_txn * _TXN
    if trigger:
        pnl += fut * (s_end - trigger[2]) * _LOT
    nights = len({t[:10] for t in tss if t > ent}) - 1
    return {"spot_entry": round(d0["spot"], 1), "atm_strike": b["K"],
            "put_wall": b["pw"], "call_wall": b["cw"],
            "legs": " ".join(f"{'S' if sgn > 0 else 'L'}{cp}{k:.0f}@{p}"
                             for cp, k, sgn, p in legs),
            "short_put": next((k for cp, k in shorts if cp == "P"), None),
            "short_call": next((k for cp, k in shorts if cp == "C"), None),
            "wing_width": _WING if capped else None,
            "credit_pts": round(credit, 1), "credit_inr": round(credit * _LOT),
            "max_loss_inr": round(max_loss) if max_loss is not None else None,
            "nights_held": nights,
            "first_breach_ts": first_breach[0][5:16] if first_breach else "",
            "first_breach_side": first_breach[1] if first_breach else "",
            "overlay_trigger_ts": trigger[0][5:16] if trigger else "",
            "overlay_trigger_side": trigger[1] if trigger else "",
            "overlay_trigger_spot": round(trigger[2]) if trigger else None,
            "settle_spot": round(s_end, 1), "settle_debit_pts": round(settle, 1),
            "costs_inr": round(n_txn * _TXN), "worst_mark_inr": round(worst),
            "pnl_inr": round(pnl)}


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
                    c = run_cell(series, tss, d, s, mgmt == "overlay")
                    if c:
                        rows.append({"expiry": exp[:10], "entry_date": d,
                                     "rank": f"T-{len(entries) - rank}",
                                     "structure": s, "mgmt": mgmt, **c})
    con.close()
    out = os.path.join(_KNOW, "backtest_full.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows × {len(rows[0])} columns → {out}")
    r = next(x for x in rows if x["entry_date"] == "2026-07-23"
             and x["structure"] == "straddle" and x["mgmt"] == "hold")
    print("\nsample row (23-Jul straddle, hold):")
    for k, v in r.items():
        print(f"  {k:<22} {v}")


if __name__ == "__main__":
    main()
