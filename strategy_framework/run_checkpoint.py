"""
strategy_framework/run_checkpoint.py
====================================
The 13:00 and 15:20 CHECKPOINTS — the two intraday decision points after the
morning report. Each answers a different question:

  afternoon (~13:00 / 07:30Z)  "Is my kill-list firing?"   — position management
  pre-close (~15:20 / 09:50Z)  "Do I hold the night?"      — overnight decision

Everything is measured AGAINST THE 10:00 BASELINE of the same day: straddle change,
wall migration, coverage now. Kill-list triggers are the frozen ones from the
morning report. The overnight section applies the PRE-CLOSE FLAG (straddle
expanding ≥3% into the close OR coverage < 0.10 at 15:20) — July evidence: flagged
nights averaged −₹397 vs +₹76 unflagged, but mechanically flattening cost more than
it saved (₹680 toll vs ₹397 signal), so the flag is an ADVISORY for judgment/sizing,
not an automatic order.

    python -m strategy_framework.run_checkpoint --date 2026-07-10 --time 09:50
"""
from __future__ import annotations
import argparse
import sqlite3

from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import option_oi


def _state(da, d, exp, hm):
    ch = da.chain_as_of(f"{d}T{hm}:00Z", exp)
    if not ch or ch.ts[:10] != d:
        return None
    S, _ = option_oi.atm_straddle(ch)
    below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
    above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
    if not below or not above or not S:
        return None
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]
    cov = min(ch.spot - pw, cw - ch.spot) / (0.8 * S)
    return {"spot": ch.spot, "S": S, "pw": pw, "cw": cw, "cov": cov, "ts": ch.ts}


def _mark_positions(da, d, hm, lot=65):
    """Mark every open position from knowledge/positions.csv at this checkpoint:
    per-leg current px (LTP, intrinsic fallback), unrealized P&L net of entry+exit
    costs (₹85/leg all-in), and the position's short strikes for kill-list checks."""
    import csv
    import os
    p = os.path.join(os.path.dirname(__file__), "knowledge", "positions.csv")
    if not os.path.exists(p):
        return []
    out = []
    for r in csv.DictReader(open(p)):
        if r.get("status") != "open" or r["entry_date"] > d or r["expiry"] < d:
            continue
        ch = da.chain_as_of(f"{d}T{hm}:00Z", r["expiry_full"])
        if not ch or ch.ts[:10] != d:
            continue
        lots = int(r.get("lots", 1))
        legs, shorts, debit, credit = [], [], 0.0, float(r["credit_pts"])
        for leg in r["legs"].split(";"):
            cp, k, sgn, epx = leg.split(":")
            k, sgn, epx = float(k), int(sgn), float(epx)
            cur = (ch.call_ltp if cp == "C" else ch.put_ltp).get(k)
            if not cur or cur <= 0:
                cur = max(0.0, (ch.spot - k) if cp == "C" else (k - ch.spot))
            debit += sgn * cur
            legs.append((cp, k, sgn, epx, cur))
            if sgn > 0:
                shorts.append((cp, k))
        n_legs = len(legs)
        costs = 2 * n_legs * (20.0 + 1.0 * lot) * lots        # entry + exit, ₹85/leg
        pnl = (credit - debit) * lot * lots - costs
        out.append({"r": r, "legs": legs, "shorts": shorts, "pnl": pnl,
                    "credit": credit, "debit": debit, "spot": ch.spot,
                    "ret_on_credit": 100 * (credit - debit) / credit if credit else 0.0})
    return out


def main():
    ap = argparse.ArgumentParser(description="Afternoon / pre-close checkpoint.")
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="09:50", help="UTC HH:MM (07:30=13:00 IST, 09:50=15:20 IST)")
    args = ap.parse_args()

    da = DataAccess(args.db)
    con = sqlite3.connect(args.db)
    exps = sorted({r[0] for r in con.execute("SELECT DISTINCT expiry FROM chain_rows")},
                  key=lambda e: e[:10])
    con.close()
    exp = next((e for e in exps if e[:10] >= args.date), None)
    if not exp:
        print("no front expiry for", args.date)
        return

    base = _state(da, args.date, exp, "04:30")
    now = _state(da, args.date, exp, args.time)
    if not base or not now:
        print("missing chain snapshot (baseline 04:30 or requested time)")
        return

    ist = "13:00" if args.time < "08:30" else "15:20"
    schg = (now["S"] / base["S"] - 1) * 100
    pw_mig = now["pw"] - base["pw"]
    cw_mig = now["cw"] - base["cw"]
    spot_mv = now["spot"] - base["spot"]

    print("=" * 68)
    print(f"CHECKPOINT {args.date} ~{ist} IST   (front expiry {exp[:10]})")
    print("=" * 68)
    print(f"since 10:00:  spot {spot_mv:+.0f}pts  straddle {schg:+.1f}%  "
          f"put-wall mig {pw_mig:+.0f}  call-wall mig {cw_mig:+.0f}")
    print(f"now:          straddle {now['S']:.0f}  coverage {now['cov']:.2f}  "
          f"walls {now['pw']:.0f}/{now['cw']:.0f}  spot {now['spot']:.0f}")

    # ---- the BOOK: open positions marked at this checkpoint -----------------
    marks = _mark_positions(da, args.date, args.time)
    if marks:
        print("\nbook:")
        for m in marks:
            r = m["r"]
            legstr = "  ".join(f"{cp}{k:.0f}{'s' if sgn > 0 else 'l'} {epx}→{cur:.1f}"
                               for cp, k, sgn, epx, cur in m["legs"])
            print(f"   {r['structure']} (entered {r['entry_date']}, exp {r['expiry']}): "
                  f"credit {m['credit']} → cost-to-close {m['debit']:.1f}")
            print(f"     {legstr}")
            print(f"     unrealized P&L ₹{m['pnl']:+.0f} (net of ₹85/leg both ways)  "
                  f"[{m['ret_on_credit']:+.0f}% of credit]")
            for cp, k in m["shorts"]:
                dist = (m["spot"] - k) if cp == "P" else (k - m["spot"])
                if dist <= 0:
                    print(f"     ✗ short {cp}{k:.0f} BREACHED by {-dist:.0f}pts — defend only "
                          f"if trend-expansion confirms (sided, 2 legs), else hold/exit")
                elif dist < 40:
                    print(f"     ! short {cp}{k:.0f} within {dist:.0f}pts of spot")
    else:
        print("\nbook: no open positions")

    print("\nkill-list:")
    fired = False
    if schg >= 5.0:
        print(f"   ✗ straddle expansion {schg:+.1f}% ≥ +5% → EXIT (repricing has begun)")
        fired = True
    if max(abs(pw_mig), abs(cw_mig)) > 30 and (pw_mig * spot_mv < 0 or cw_mig * spot_mv < 0
                                               or abs(spot_mv) > 30):
        print(f"   ✗ wall migration {pw_mig:+.0f}/{cw_mig:+.0f} — structure has moved; "
              f"re-check coverage vs your shorts")
        fired = True
    if now["cov"] < 0.05:
        print(f"   ✗ coverage {now['cov']:.2f} < 0.05 — compressed gamma NOW; "
              f"defending is late, exiting is honest")
        fired = True
    elif now["cov"] < 0.10:
        print(f"   ! coverage {now['cov']:.2f} < 0.10 — escape capacity thin")
        fired = True
    if not fired:
        print("   ✓ nothing firing — hold per plan (defend only regime-confirmed touches)")

    if ist == "15:20":
        flag = schg >= 3.0 or now["cov"] < 0.10
        print("\novernight decision:")
        if flag:
            print(f"   PRE-CLOSE FLAG ON (straddle {schg:+.1f}% / coverage {now['cov']:.2f})")
            print("   July evidence: flagged nights averaged −₹397 vs +₹76 unflagged.")
            print("   ADVISORY: reduce/widen if cheaply possible; mechanical flattening did")
            print("   NOT pay at 1 lot (₹680 toll vs ₹397 signal) — judgment call, not order.")
        else:
            print("   flag off — hold; unflagged nights averaged +₹76 in July.")
        print("   (This flag is the last-set hedge/overnight state until tomorrow 10:00.)")


if __name__ == "__main__":
    main()
