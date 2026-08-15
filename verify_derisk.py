#!/usr/bin/env python3
"""
verify_derisk.py — validate the liquidity-derisk overlay on real sessions.
=========================================================================
Runs the exact derisk_liquidity signal + tail-hedge construction the live panel
uses, for one or more dates, so you can confirm it ARMS on a de-risk day
(2026-07-08) and stays CLEAR on quiet days.

    python verify_derisk.py 2026-07-08 2026-07-07 2026-07-03
    NIFTY_DB=/path/to/option_chains.db python verify_derisk.py 2026-07-08

Uses resolve_db_path() — the same DB the framework/panel read.
"""
from __future__ import annotations
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_framework.config.settings import FrameworkConfig, resolve_db_path
from strategy_framework.signals.data_access import DataAccess, days_to_expiry
from strategy_framework.signals import derisk_liquidity as D
from strategy_framework.signals import derisk_preopen as P
from strategy_framework.strategy import constructor as C

CFG = FrameworkConfig()


def _last_bar_ts(db, date):
    c = sqlite3.connect(db)
    r = c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
                  "AND SUBSTR(ts,1,10)=?", (date,)).fetchone()
    c.close()
    return r[0] if r and r[0] else None


def _latest_expiry(da):
    try:
        caps = da.list_captures()
        return caps[-1]["expiry"] if caps else None
    except Exception:
        return None


def run(date, db):
    now = _last_bar_ts(db, date)
    if not now:
        print(f"{date}: no NIFTY 1m bars"); return
    da = DataAccess(db)
    expiry = _latest_expiry(da) or f"{date}T10:00:00Z"

    # LEAD: pre-open overnight fingerprint (as-of 09:14 IST = 03:44 UTC) — no cash peek
    pre = P.compute(da, f"{date}T03:44:00Z", {})
    if pre.status != "NO_DATA":
        pc = pre.detail["components"]; pr = pre.detail["reads"]
        pstate = "ARMED" if pre.detail["hedge_recommended"] else "clear"
        print(f"\n=== {date} ===")
        print(f"  LEAD  (pre-open 09:14)  -> {pstate:5}  intensity={pre.detail['intensity']:.2f}   "
              f"[crude {pr.get('crude_overnight_pct')}%  GIFT {pr.get('giftnifty_overnight_pct')}%  "
              f"gold {pr.get('gold_overnight_pct')}%]")
        print(f"        components: crude={pc['crude_shock']:.2f} gift={pc['gift_gap']:.2f} "
              f"haven={pc['haven_selloff']:.2f} ndf={pc['ndf_usd']:.2f}")
    else:
        print(f"\n=== {date} ===\n  LEAD  (pre-open): NO overnight cross-asset data")

    sig = D.compute(da, now, {})
    d = sig.detail
    inten = d.get("intensity", 0.0)
    comp = d.get("components", {})
    reads = d.get("reads", {})
    state = "NO DATA" if sig.status == "NO_DATA" else ("ARMED" if d.get("hedge_recommended") else "clear")
    print(f"  CONFIRM (session {now[11:16]}) -> {state:5}  intensity={inten:.2f}")
    if sig.status == "NO_DATA":
        print("  (NIFTY bars only — nothing to score)"); return
    print("  components: " + "  ".join(f"{k}={comp.get(k,0):.2f}" for k in
          ["haven_failure", "breadth_collapse", "cross_asset_comove", "persistence", "usdinr_up"]))
    print(f"  reads: NIFTY {reads.get('nifty_session_pct')}%  gold {reads.get('gold_pct')}%  "
          f"silver {reads.get('silver_pct')}%  USDINR {reads.get('usdinr_pct')}%")
    if reads.get("breadth"):
        b = reads["breadth"]
        print(f"  breadth: {b['frac_down']*100:.0f}% down, {b['frac_big']*100:.0f}% moving >1% (n={b['n']})")
    # tail hedge
    chain = da.chain_as_of(now, expiry)
    if chain is not None:
        straddle = None
        try:
            atm = chain.atm_strike()
            cp = (chain.call_ltp.get(atm, 0) or 0) + (chain.put_ltp.get(atm, 0) or 0)
            straddle = cp if cp > 0 else None
        except Exception:
            pass
        em = 0.8 * straddle if straddle else None
        h = C.build_tail_hedge(chain, CFG, inten, expected_move_pts=em,
                               dte_days=days_to_expiry(now, expiry))
        if h:
            lp = h["long_put"]
            print(f"  HEDGE: buy {h['lots']}x {lp['strike']}P ({h['sigma_otm']}sigma) "
                  f"~{lp['premium_pts']}pts, cost Rs{lp['cost_inr_total']:,.0f}")
        else:
            print("  HEDGE: none (below trigger)")
    else:
        print("  (no option chain for this session -> intensity only, no hedge sizing)")


if __name__ == "__main__":
    dates = sys.argv[1:] or ["2026-07-08", "2026-07-07"]
    db = os.environ.get("NIFTY_DB") or resolve_db_path()
    print(f"DB: {db}")
    for d in dates:
        run(d, db)
