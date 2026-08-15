"""
strategy_framework/run_profit_cycle.py
======================================
PROFIT-BOOKING + RE-ENTRY vs HOLD — the "one straddle at a time, bank it when the
price is good, then take it again" policy, tested with frozen rules:

  * always exactly ONE position (1 lot ATM straddle, same expiry);
  * BOOK when unrealized ≥ pt × entry credit (pt ∈ {25%, 50%}; frozen, not tuned);
  * RE-ENTER a fresh ATM straddle at the NEXT checkpoint (10:00 / 13:00 / 15:20)
    after booking; repeat until expiry;
  * every open/close pays 2 legs × ₹85; settlement at expiry intrinsic;
  * baseline: the same entry held to settlement untouched.

Reports per entry: hold vs cycle(25) vs cycle(50), number of cycles, and the worst
running mark — does banking profits protect, compound, or just pay the toll?

    python -m strategy_framework.run_profit_cycle
"""
from __future__ import annotations
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series

_LOT = 65
_LEG = 20.0 + 1.0 * _LOT
_CKPT = ("04:30", "07:30", "09:50")


def _atm_straddle_legs(rows, s):
    K = min(rows, key=lambda k: abs(k - s))
    r = rows.get(K)
    if not r or not r[0] or not r[1]:
        return None
    return K, r[0] + r[1]                     # strike, credit


def _debit(rows, K, s):
    r = rows.get(K)
    if r and r[0] and r[1]:
        return r[0] + r[1]
    return abs(s - K) * 2 if False else max(0.0, s - K) + max(0.0, K - s)  # intrinsic


def cycle(series, tss, entry_date, pt):
    ent = next(t for t in tss if t[:10] == entry_date and t[11:16] >= "04:30")
    i0 = tss.index(ent)
    realized = 0.0
    worst = 0.0
    n_cycles = 0
    pos = None
    reenter_ok = True
    for i in range(i0, len(tss)):
        ts = tss[i]
        st = series[ts]
        rows, s = st["rows"], st["spot"]
        last = i == len(tss) - 1
        if pos is None:
            if reenter_ok and ts[11:16] in _CKPT or i == i0:
                if ts[11:16] in _CKPT or i == i0:
                    made = _atm_straddle_legs(rows, s)
                    if made:
                        K, credit = made
                        pos = {"K": K, "credit": credit}
                        realized -= 2 * _LEG
                        n_cycles += 1
            continue
        deb = _debit(rows, pos["K"], s)
        unreal = (pos["credit"] - deb) * _LOT
        worst = min(worst, realized + unreal - 2 * _LEG)
        if last:                                # settle at intrinsic
            deb = max(0.0, s - pos["K"]) + max(0.0, pos["K"] - s)
            realized += (pos["credit"] - deb) * _LOT - 2 * _LEG
            pos = None
        elif pt is not None and unreal >= pt * pos["credit"] * _LOT:
            realized += (pos["credit"] - deb) * _LOT - 2 * _LEG      # BOOK it
            pos = None                                               # re-enter next ckpt
    return realized, n_cycles, worst


def main():
    con = sqlite3.connect("option_chains_full.db")
    exps = [r[0] for r in con.execute(
        "SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE '2026-%' ORDER BY expiry")]
    print(f"{'expiry':<7}{'entry':<7}{'hold':>8}{'cyc25':>8}{'n':>3}{'cyc50':>8}{'n':>3}"
          f"{'worst25':>9}")
    print("-" * 54)
    T = {"h": 0.0, "c25": 0.0, "c50": 0.0}
    W = {"h": 0.0, "c25": 0.0, "c50": 0.0}
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
        for d in entries:
            try:
                h, _, wh = cycle(series, tss, d, None)
                c25, n25, w25 = cycle(series, tss, d, 0.25)
                c50, n50, _ = cycle(series, tss, d, 0.50)
            except StopIteration:
                continue
            print(f"{exp[5:10]:<7}{d[5:]:<7}{h:>8.0f}{c25:>8.0f}{n25:>3}{c50:>8.0f}{n50:>3}"
                  f"{w25:>9.0f}")
            T["h"] += h; T["c25"] += c25; T["c50"] += c50
            W["h"] = min(W["h"], wh); W["c25"] = min(W["c25"], w25)
    print("-" * 54)
    print(f"TOTAL:        {T['h']:>8.0f}{T['c25']:>8.0f}   {T['c50']:>8.0f}")
    print(f"worst mark:   hold ₹{W['h']:.0f}   cycle25 ₹{W['c25']:.0f}")
    con.close()


if __name__ == "__main__":
    main()
