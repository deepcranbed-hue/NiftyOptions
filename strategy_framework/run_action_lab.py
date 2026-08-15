"""
strategy_framework/run_action_lab.py
====================================
THE ACTION LAB — the Management AI's training-data generator.

At every checkpoint of a held position, enumerate the LEGAL MOVES, replay each one
forward to expiry settlement on the real minute chain, and record what each would
have earned. Every checkpoint becomes a chess position with a hindsight evaluation:

  hold            do nothing
  exit            close everything now
  roll_tested     move the breached side to the current wall (2-leg sided roll)
  future_hedge    neutralise the threatened side's delta with 1 futures lot
                  (put breached → short future; call breached → long future;
                  spot used as the futures proxy)
  buy_hedge       add 1 long ATM option on the threatened side (positive gamma)
  harvest_winner  buy back the cheap, winning side; keep the tested side

All actions pay ₹85/leg per transaction (futures leg the same). Output: a per-
checkpoint table of action → final P&L, plus the hindsight-best move — the raw
material from which management rules get LEARNED instead of asserted. n is tiny;
this builds the table, it does not yet prove any rule.

    python -m strategy_framework.run_action_lab --expiry 2026-07-28 \
        --entry-date 2026-07-23 --structure straddle
"""
from __future__ import annotations
import argparse
import csv
import os
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_LOT = 65
_TXN = 20.0 + 1.0 * _LOT          # ₹/leg per transaction (brokerage + 1pt slippage)
_CKPTS = ["04:30", "07:30", "09:50"]


def _px(rows, k, cp, s):
    r = rows.get(k)
    v = (r[0] if cp == "C" else r[1]) if r else None
    if v is not None and v > 0:
        return v
    return max(0.0, (s - k) if cp == "C" else (k - s))


def _walls(rows, s):
    below = [(k, v[3]) for k, v in rows.items() if k < s]
    above = [(k, v[2]) for k, v in rows.items() if k > s]
    if not below or not above:
        return None, None
    return max(below, key=lambda x: x[1])[0], max(above, key=lambda x: x[1])[0]


def _open_legs(rows, s, structure, wing=100):
    K = min(rows, key=lambda k: abs(k - s))
    pw, cw = _walls(rows, s)
    L = {"straddle": [("C", K, 1), ("P", K, 1)],
         "strangle": [("C", cw, 1), ("P", pw, 1)],
         "fly": [("C", K, 1), ("P", K, 1), ("C", K + wing, -1), ("P", K - wing, -1)],
         "condor": [("C", cw, 1), ("C", cw + wing, -1), ("P", pw, 1), ("P", pw - wing, -1)]}[structure]
    return [[cp, k, sgn, _px(rows, k, cp, s)] for cp, k, sgn in L]


def _settle_value(legs, s_end):
    return sum(sgn * max(0.0, (s_end - k) if cp == "C" else (k - s_end))
               for cp, k, sgn, _ in legs)


def _final(legs, entry_credit_delta, s_end, n_txn, fut=None, s_now=None):
    """P&L to settlement: (credit collected − settlement debit)·lot − txn costs
    (+ futures leg settle if any)."""
    pnl = (entry_credit_delta - _settle_value(legs, s_end)) * _LOT
    if fut:
        pnl += fut * (s_end - s_now) * _LOT
        n_txn += 2                                       # open + settle the future
    return pnl - n_txn * _TXN


def evaluate(db, exp_full, entry_date, structure):
    series = _full_chain_series(sqlite3.connect(db), exp_full,
                                f"{entry_date}T04:29:00Z", exp_full[:10] + "T23:59:59Z")
    tss = sorted(series)
    ent_ts = next(t for t in tss if t[11:16] >= "04:30")
    d0 = series[ent_ts]
    legs0 = _open_legs(d0["rows"], d0["spot"], structure)
    credit0 = sum(sgn * px for _, _, sgn, px in legs0)
    s_end = series[tss[-1]]["spot"]
    base_txn = 2 * len(legs0)                            # open + settle every original leg

    out = []
    days = sorted({t[:10] for t in tss})
    for d in days:
        for hm in _CKPTS:
            if d == entry_date and hm == "04:30":
                continue
            ts = next((t for t in tss if t[:10] == d and t[11:16] >= hm), None)
            if ts is None or ts[:10] != d or ts == tss[-1]:
                continue
            st = series[ts]
            rows_, s = st["rows"], st["spot"]
            breach = None
            for cp, k, sgn, _ in legs0:
                if sgn > 0 and ((cp == "P" and s <= k) or (cp == "C" and s >= k)):
                    breach = cp
            acts = {}
            # HOLD
            acts["hold"] = _final(legs0, credit0, s_end, base_txn)
            # EXIT now
            debit_now = sum(sgn * _px(rows_, k, cp, s) for cp, k, sgn, _ in legs0)
            acts["exit"] = (credit0 - debit_now) * _LOT - base_txn * _TXN
            if breach:
                side = [l for l in legs0 if l[0] == breach]
                keep = [l for l in legs0 if l[0] != breach]
                # ROLL TESTED side to the current wall
                pw, cw = _walls(rows_, s)
                newk = pw if breach == "P" else cw
                closed = sum(sgn * _px(rows_, k, cp, s) for cp, k, sgn, _ in side)
                side_credit = sum(sgn * px for _, _, sgn, px in side)
                new_side = [[breach, newk, 1, _px(rows_, newk, breach, s)]]
                if len(side) > 1:                        # winged structure → roll wing too
                    wk = newk - 100 if breach == "P" else newk + 100
                    new_side.append([breach, wk, -1, _px(rows_, wk, breach, s)])
                new_credit = sum(sgn * px for _, _, sgn, px in new_side)
                legs_r = keep + new_side
                cr_r = credit0 - side_credit + (side_credit - closed) + new_credit
                acts["roll_tested"] = _final(legs_r, cr_r, s_end,
                                             base_txn + 2 * len(side))
                # FUTURE HEDGE: put breached → short fut; call breached → long fut
                acts["future_hedge"] = _final(legs0, credit0, s_end, base_txn,
                                              fut=(-1 if breach == "P" else 1), s_now=s)
                # BUY HEDGE option (long ATM on threatened side)
                K = min(rows_, key=lambda k: abs(k - s))
                hl = [breach, K, -1, _px(rows_, K, breach, s)]
                acts["buy_hedge"] = _final(legs0 + [hl], credit0 - hl[3], s_end,
                                           base_txn + 2)
                # HARVEST the winning side
                closed_w = sum(sgn * _px(rows_, k, cp, s) for cp, k, sgn, _ in keep)
                keep_credit = sum(sgn * px for _, _, sgn, px in keep)
                acts["harvest_winner"] = _final(side, credit0 - keep_credit
                                                + (keep_credit - closed_w), s_end,
                                                base_txn)
            best = max(acts, key=acts.get)
            ranked = sorted(acts.values(), reverse=True)
            # GAP = best − second-best: the recommendation's CONFIDENCE. A ₹300 gap
            # says "coin flip, stay flexible"; a ₹7,000 gap says "commit".
            gap = round(ranked[0] - ranked[1], 0) if len(ranked) > 1 else None
            out.append({"date": d, "ckpt_utc": hm, "spot": round(s, 0),
                        "breach": breach or "", **{k: round(v, 0) for k, v in acts.items()},
                        "best": best, "action_gap": gap,
                        "hold_regret": round(acts[best] - acts["hold"], 0)})
    return out, structure


def main():
    ap = argparse.ArgumentParser(description="Evaluate every legal move at every checkpoint.")
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--expiry", required=True)
    ap.add_argument("--entry-date", required=True)
    ap.add_argument("--structure", default="straddle")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    exp = con.execute("SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE ?",
                      (args.expiry + "%",)).fetchone()[0]
    con.close()
    rows, structure = evaluate(args.db, exp, args.entry_date, args.structure)
    out = os.path.join(_KNOW, f"action_lab_{args.expiry}_{structure}.csv")
    cols = ["date", "ckpt_utc", "spot", "breach", "hold", "exit", "roll_tested",
            "future_hedge", "buy_hedge", "harvest_winner", "best", "action_gap",
            "hold_regret"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{structure} entered {args.entry_date}, settled {exp[:10]} — "
          f"action value to expiry (₹, all costs):")
    print(f"{'date':<12}{'ckpt':<7}{'breach':<7}{'hold':>7}{'exit':>7}{'roll':>7}"
          f"{'fut':>7}{'hedge':>7}{'harv':>7}   best (vs hold)")
    print("-" * 84)
    for r in rows:
        def g(k):
            return f"{r[k]:.0f}" if k in r and r.get(k) is not None else "·"
        print(f"{r['date']:<12}{r['ckpt_utc']:<7}{r['breach']:<7}{g('hold'):>7}{g('exit'):>7}"
              f"{g('roll_tested'):>7}{g('future_hedge'):>7}{g('buy_hedge'):>7}"
              f"{g('harvest_winner'):>7}   {r['best']} ({r['hold_regret']:+.0f})")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
