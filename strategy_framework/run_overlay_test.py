"""
strategy_framework/run_overlay_test.py
======================================
MODEL 1 vs MODEL 3 — the loss-restriction test.

  Model 1: structure alone, held to settlement (unmanaged baseline).
  Model 3: same structure + a DYNAMIC FUTURES OVERLAY that activates only when a
           frozen rule fires at a checkpoint (13:00 / 15:20 / next 10:00s):

               short strike BREACHED  AND  trend-expansion CONFIRMED
               (straddle ≥ +3% vs the same day's 10:00, or 30-min ER ≥ 0.55)

           → add 1 futures lot against the threatened side, hold to settlement.
           The reserve-capital idea (Model 4) at 1 lot IS this overlay: capital
           held back, deployed only when the state justifies it.

Frozen thresholds (same as everywhere); spot as futures proxy; every leg ₹85.
The question, per the motivation: does the overlay RESTRICT LOSSES — cut the left
tail — and what does that insurance cost in the pin weeks?

    python -m strategy_framework.run_overlay_test --structure straddle
"""
from __future__ import annotations
import argparse
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series
from strategy_framework.strategy.tape_regime import efficiency_ratio

_LOT = 65
_TXN = 20.0 + 1.0 * _LOT
_CKPTS = ["07:30", "09:50", "04:30"]      # afternoon, pre-close, then mornings after


def _px(rows, k, cp, s):
    r = rows.get(k)
    v = (r[0] if cp == "C" else r[1]) if r else None
    if v is not None and v > 0:
        return v
    return max(0.0, (s - k) if cp == "C" else (k - s))


def _legs(rows, s, structure, wing=100):
    K = min(rows, key=lambda k: abs(k - s))
    below = [(k, v[3]) for k, v in rows.items() if k < s]
    above = [(k, v[2]) for k, v in rows.items() if k > s]
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]
    L = {"straddle": [("C", K, 1), ("P", K, 1)],
         "strangle": [("C", cw, 1), ("P", pw, 1)],
         "condor": [("C", cw, 1), ("C", cw + wing, -1), ("P", pw, 1), ("P", pw - wing, -1)],
         "fly": [("C", K, 1), ("P", K, 1), ("C", K + wing, -1), ("P", K - wing, -1)]}[structure]
    return [[cp, k, sgn, _px(rows, k, cp, s)] for cp, k, sgn in L]


def run_entry(db, exp, entry_date, structure):
    series = _full_chain_series(sqlite3.connect(db), exp,
                                f"{entry_date}T04:29:00Z", exp[:10] + "T23:59:59Z")
    tss = sorted(series)
    ent = next(t for t in tss if t[11:16] >= "04:30")
    d0 = series[ent]
    legs = _legs(d0["rows"], d0["spot"], structure)
    credit = sum(sgn * px for _, _, sgn, px in legs)
    s_end = series[tss[-1]]["spot"]
    settle = sum(sgn * max(0.0, (s_end - k) if cp == "C" else (k - s_end))
                 for cp, k, sgn, _ in legs)
    base_txn = 2 * len(legs)
    unmanaged = (credit - settle) * _LOT - base_txn * _TXN

    # walk checkpoints AFTER entry; fire the overlay once on the frozen rule
    day_open_straddle = {}
    spots_hist = []
    trigger = None
    for i, ts in enumerate(tss):
        st = series[ts]
        s = st["spot"]
        spots_hist.append(s)
        d, hm = ts[:10], ts[11:16]
        if hm <= "04:31" and d not in day_open_straddle:
            K = min(st["rows"], key=lambda k: abs(k - s))
            r = st["rows"].get(K)
            day_open_straddle[d] = (r[0] or 0) + (r[1] or 0) if r else None
        if ts <= ent or trigger or ts == tss[-1]:
            continue
        if hm[:5] not in ("07:30", "09:50") and not (hm[:5] >= "04:30" and hm[:5] <= "04:31" and d > entry_date):
            continue
        breach = None
        for cp, k, sgn, _ in legs:
            if sgn > 0 and ((cp == "P" and s <= k) or (cp == "C" and s >= k)):
                breach = cp
        if not breach:
            continue
        K = min(st["rows"], key=lambda k: abs(k - s))
        r = st["rows"].get(K)
        S_now = (r[0] or 0) + (r[1] or 0) if r else None
        S_10 = day_open_straddle.get(d)
        schg = (S_now / S_10 - 1) * 100 if S_now and S_10 else None
        er = efficiency_ratio(spots_hist[-31:]) if len(spots_hist) >= 5 else None
        if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
            trigger = {"ts": ts, "s": s, "side": breach}
    managed = unmanaged
    if trigger:
        fut = -1 if trigger["side"] == "P" else 1
        managed = unmanaged + fut * (s_end - trigger["s"]) * _LOT - 2 * _TXN
    return unmanaged, managed, trigger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--structure", default="straddle")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    exps = [r[0] for r in con.execute(
        "SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE '2026-%' ORDER BY expiry")]
    print(f"{'expiry':<7}{'entry':<7}{'unmanaged':>11}{'overlay':>10}{'saved':>8}  trigger")
    print("-" * 62)
    t1 = t3 = 0.0
    w1 = w3 = 0.0
    n = 0
    for exp in exps:
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c JOIN chain_rows r "
            "ON r.capture_id=c.capture_id AND r.expiry=? ORDER BY 1", (exp,))]
        for d in [x for x in days if x < exp[:10]][-4:]:
            try:
                u, m, trg = run_entry(args.db, exp, d, args.structure)
            except Exception:
                continue
            tag = f"{trg['ts'][5:16]} {trg['side']}-side" if trg else "—"
            print(f"{exp[5:10]:<7}{d[5:]:<7}{u:>11.0f}{m:>10.0f}{m - u:>8.0f}  {tag}")
            t1 += u; t3 += m
            w1 = min(w1, u); w3 = min(w3, m)
            n += 1
    print("-" * 62)
    print(f"TOTAL ({n}):   Model1 ₹{t1:.0f}   Model3 ₹{t3:.0f}   "
          f"worst: ₹{w1:.0f} vs ₹{w3:.0f}")
    con.close()


if __name__ == "__main__":
    main()
