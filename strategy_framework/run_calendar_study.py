"""
strategy_framework/run_calendar_study.py
========================================
CALENDAR (time-spread) STUDY — the macro-shock defence built from TWO expiries:

    SELL the front-expiry ATM straddle   (harvest the fast theta)
    BUY  the next-expiry straddle, same strike   (the shock absorber: on a violent
         move or vol spike the far straddle gains — intrinsic AND vega — offsetting
         the front's blowout)

Net position = short near vol, long far vol. Exit: front settles at intrinsic on
expiry; the far legs are CLOSED AT MARKET at the same moment. Marks: far legs carry
time value, so missing snapshots carry the last known mark (flagged) — intrinsic
fallback would be wrong for the far expiry. 1 lot, ₹85/leg on all 8 transactions.

Compared against the naked front straddle on identical entries — including the
August shock week (entries 30/31-Jul, 03-Aug vs the 11-Aug far chain), which is
precisely the 'violent index move' scenario the structure exists for.

    python -m strategy_framework.run_calendar_study
"""
from __future__ import annotations
import sqlite3

from strategy_framework.run_condor_study import _full_chain_series

_LOT = 65
_TXN = 20.0 + 1.0 * _LOT


def _straddle_at(series, ts, K):
    st = series.get(ts)
    if not st:
        return None, None
    r = st["rows"].get(K)
    if r and r[0] and r[1]:
        return r[0] + r[1], st["spot"]
    return None, st["spot"]


def run_pair(con, front, nxt, entry_date):
    ent_ts = f"{entry_date}T04:30:00Z"
    end = front[:10] + "T23:59:59Z"
    sf = _full_chain_series(con, front, f"{entry_date}T04:29:00Z", end)
    sn = _full_chain_series(con, nxt, f"{entry_date}T04:29:00Z", end)
    tss = sorted(sf)
    ent = next((t for t in tss if t[11:16] >= "04:30"), None)
    if ent is None or ent not in sn:
        return None
    spot0 = sf[ent]["spot"]
    K = min(sf[ent]["rows"], key=lambda k: abs(k - spot0))
    Sf0, _ = _straddle_at(sf, ent, K)
    Sn0, _ = _straddle_at(sn, ent, K)
    if not Sf0 or not Sn0:
        return None

    worst_cal = worst_str = 0.0
    last_Sn = Sn0
    n_carry = 0
    for ts in tss:
        if ts <= ent:
            continue
        Sf, s = _straddle_at(sf, ts, K)
        if Sf is None:
            Sf = max(0.0, s - K) + max(0.0, K - s) if s else None
        Sn, _ = _straddle_at(sn, ts, K)
        if Sn is None:
            Sn = last_Sn
            n_carry += 1
        else:
            last_Sn = Sn
        if Sf is None:
            continue
        cal = ((Sf0 - Sf) + (Sn - Sn0)) * _LOT - 8 * _TXN
        std = (Sf0 - Sf) * _LOT - 4 * _TXN
        worst_cal = min(worst_cal, cal)
        worst_str = min(worst_str, std)
    s_end = sf[tss[-1]]["spot"]
    settle_f = max(0.0, s_end - K) + max(0.0, K - s_end)
    Sn_end, _ = _straddle_at(sn, tss[-1], K)
    if Sn_end is None:
        Sn_end = last_Sn
    cal_final = ((Sf0 - settle_f) + (Sn_end - Sn0)) * _LOT - 8 * _TXN
    str_final = (Sf0 - settle_f) * _LOT - 4 * _TXN
    return {"K": K, "spot": spot0, "front_straddle": round(Sf0, 1),
            "next_straddle": round(Sn0, 1), "net_debit_pts": round(Sn0 - Sf0, 1),
            "cal_pnl": round(cal_final), "cal_worst": round(worst_cal),
            "str_pnl": round(str_final), "str_worst": round(worst_str),
            "carry_marks": n_carry}


def main():
    con = sqlite3.connect("option_chains_full.db")
    exps = [r[0] for r in con.execute(
        "SELECT DISTINCT expiry FROM chain_rows ORDER BY expiry")]
    print(f"{'front':<7}{'entry':<7}{'K':>7}{'F-strad':>8}{'N-strad':>8}"
          f"{'calendar':>9}{'cal-worst':>10}{'straddle':>9}{'str-worst':>10}")
    print("-" * 76)
    tc = ts_ = 0.0
    wc = ws = 0.0
    n = 0
    for i, front in enumerate(exps[:-1]):
        nxt = exps[i + 1]
        fdays = {r[0] for r in con.execute(
            "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c JOIN chain_rows r "
            "ON r.capture_id=c.capture_id AND r.expiry=?", (front,))}
        ndays = {r[0] for r in con.execute(
            "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c JOIN chain_rows r "
            "ON r.capture_id=c.capture_id AND r.expiry=?", (nxt,))}
        overlap = sorted(d for d in fdays & ndays if d < front[:10])[-4:]
        for d in overlap:
            r = run_pair(con, front, nxt, d)
            if not r:
                continue
            print(f"{front[5:10]:<7}{d[5:]:<7}{r['K']:>7.0f}{r['front_straddle']:>8}"
                  f"{r['next_straddle']:>8}{r['cal_pnl']:>9}{r['cal_worst']:>10}"
                  f"{r['str_pnl']:>9}{r['str_worst']:>10}")
            tc += r["cal_pnl"]; ts_ += r["str_pnl"]
            wc = min(wc, r["cal_worst"]); ws = min(ws, r["str_worst"])
            n += 1
    print("-" * 76)
    print(f"TOTAL ({n}):          calendar ₹{tc:.0f} (worst ₹{wc:.0f})   "
          f"naked straddle ₹{ts_:.0f} (worst ₹{ws:.0f})")
    con.close()


if __name__ == "__main__":
    main()
