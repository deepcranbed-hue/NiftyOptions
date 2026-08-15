"""
strategy_compare.py
-------------------
Two comparison types the chain-diff doesn't cover:

  1. price_comparison() — per-strike LTP change between two captures: how each
     option's PRICE moved (and premium decay). "How did prices move?"

  2. strategy_pnl_comparison() — take a SPECIFIC structure (the legs) and see how
     its VALUE / P&L changed between two snapshots, decomposed into WHY:
     spot move, IV change, time decay. "How is my trade doing, and why?"

Both align by fixed strike and use the stored LTP/IV. The P&L attribution is a
practical decomposition (not a full Greeks re-pricing) — honest about that.
"""
from __future__ import annotations
from chain_store import load_capture, days_to_expiry, DB_PATH
import numpy as np
from backend.quant.strike_optimizer import _leg_payoff


def _at(ch, strike, key):
    try:
        return ch[key][ch["strikes"].index(strike)]
    except (ValueError, KeyError, IndexError):
        return None


# ── 1. per-strike price movement ────────────────────────────────────────────
def price_comparison(cap_a, cap_b, db=DB_PATH, band=300):
    """How each option's LTP moved between captures, near spot."""
    a = load_capture(cap_a, db=db); b = load_capture(cap_b, db=db)
    if not a or not b:
        return {"error": "capture(s) not found"}
    spot = b["spot"]; rows = []
    for k in b["strikes"]:
        if abs(k - spot) > band:
            continue
        ca, cb = _at(a, k, "call_ltp"), _at(b, k, "call_ltp")
        pa, pb = _at(a, k, "put_ltp"), _at(b, k, "put_ltp")
        rows.append({"strike": k,
                     "call_ltp_a": ca, "call_ltp_b": cb,
                     "call_move": (round(cb - ca, 2) if ca is not None and cb is not None else None),
                     "call_move_pct": (round((cb-ca)/ca*100,1) if ca else None),
                     "put_ltp_a": pa, "put_ltp_b": pb,
                     "put_move": (round(pb - pa, 2) if pa is not None and pb is not None else None),
                     "put_move_pct": (round((pb-pa)/pa*100,1) if pa else None)})
    # biggest movers
    cm = max((r for r in rows if r["call_move"] is not None),
             key=lambda r: abs(r["call_move"]), default=None)
    pm = max((r for r in rows if r["put_move"] is not None),
             key=lambda r: abs(r["put_move"]), default=None)
    reads = [f"Spot {a['spot']:.0f}→{b['spot']:.0f} ({b['spot']-a['spot']:+.0f})."]
    if cm: reads.append(f"Biggest call move: {cm['strike']:.0f} {cm['call_move']:+.1f} pts.")
    if pm: reads.append(f"Biggest put move: {pm['strike']:.0f} {pm['put_move']:+.1f} pts.")
    return {"rows": rows, "read": reads,
            "caveat": "Per-strike LTP change B−A. On a held position, a falling "
                      "short-option price = premium decaying in your favour (theta)."}


# ── 2. strategy P&L comparison + attribution ────────────────────────────────
def strategy_pnl_comparison(legs, cap_a, cap_b, expiry_date,
                            db=DB_PATH, lot_size=65):
    """
    legs: [(side, strike, sign)] where side='call'/'put', sign=+1 long/-1 short.
          e.g. an iron condor: [('put',bp,+1),('put',sp,-1),('call',sc,-1),('call',bc,+1)]
    Compares the structure's VALUE at capture A vs B and attributes the change.
    """
    if isinstance(cap_a, int):
        cap_a = load_capture(cap_a, expiry=expiry_date, db=db)
    if isinstance(cap_b, int):
        cap_b = load_capture(cap_b, expiry=expiry_date, db=db)
    a, b = cap_a, cap_b
    if not a or not b:
        return {"error": "capture(s) not found"}

    def value(ch):
        """Net value of the structure to the HOLDER (long=+ltp, short=-ltp)."""
        v = 0.0; ok = True
        leg_prices = []
        for (side, strike, sign) in legs:
            side_key = "call" if side.lower() in ("ce", "call") else "put"
            ltp = _at(ch, strike, f"{side_key}_ltp")
            leg_prices.append(ltp)
            if ltp is None:
                ok = False; continue
            v += sign * ltp        # long position worth +ltp, short worth -ltp
        return (v if ok else None, leg_prices)

    (va, prices_a), (vb, prices_b) = value(a), value(b)
    if va is None or vb is None:
        return {
            "error": "some legs missing in a capture — can't value the structure",
            "prices_a": prices_a,
            "prices_b": prices_b
        }

    # P&L to holder = change in structure value (what it's worth now vs then)
    pnl_pts = round(vb - va, 2)
    pnl_rupees = round(pnl_pts * lot_size)

    # attribution: decompose the move into spot, IV, time
    spot_move = (b["spot"] or 0) - (a["spot"] or 0)
    def atm_iv(ch):
        s = ch["spot"]; i = min(range(len(ch["strikes"])), key=lambda j: abs(ch["strikes"][j]-s))
        vals = [x for x in (ch["call_iv"][i], ch["put_iv"][i]) if x]
        return sum(vals)/len(vals) if vals else None
    iv_a, iv_b = atm_iv(a), atm_iv(b)
    iv_change = (iv_b - iv_a) if (iv_a and iv_b) else None
    days_a = days_to_expiry(expiry_date, as_of=a["captured_at"])
    days_b = days_to_expiry(expiry_date, as_of=b["captured_at"])
    time_passed = round(days_a - days_b, 3)

    # net short/long premium exposure sign (for reading theta direction)
    net_short = va < 0  # <0 = net credit received at entry = net short premium
    
    # Identify short strikes for breach detection
    short_calls = [strike for (side, strike, sign) in legs if sign < 0 and side.lower() in ("ce", "call")]
    short_puts = [strike for (side, strike, sign) in legs if sign < 0 and side.lower() in ("pe", "put")]
    spot_b = b["spot"] or 0

    reads = [f"Structure P&L: {pnl_pts:+.1f} pts = ₹{pnl_rupees:+,} (lot {lot_size}).",
             f"Drivers over {time_passed:.2f} days:"]
             
    if net_short:
        breached_call = any(spot_b > k for k in short_calls) if short_calls else False
        breached_put = any(spot_b < k for k in short_puts) if short_puts else False
        if breached_call and breached_put:
            spot_text = "spot BREACHED both sides — highly volatile."
        elif breached_call:
            spot_text = "spot BREACHED the call side — the damaging driver."
        elif breached_put:
            spot_text = "spot BREACHED the put side — the damaging driver."
        else:
            spot_text = "spot stayed in-range — helps."
    else:
        # Default behavior for long
        spot_text = "moves a directional/long structure with the move."
        
    reads.append(f"  • Spot moved {spot_move:+.0f} — {spot_text}")
    
    if iv_change is not None:
        if iv_change < 0:
            iv_text = "IV drop HELPS a net-short structure (vega)." if net_short else "IV drop hurts a net-long structure."
        elif iv_change > 0:
            iv_text = "IV rise HURTS a net-short structure." if net_short else "IV rise helps a net-long structure."
        else:
            iv_text = "IV unchanged."
        reads.append(f"  • ATM IV {iv_a:.1f}%→{iv_b:.1f}% ({iv_change:+.1f}) — {iv_text}")
        
    if time_passed > 0:
        theta_text = "HELPS you (net short premium collects decay)." if net_short else "works against a net-long structure."
        reads.append(f"  • {time_passed:.2f} days decayed — theta {theta_text}")

    # build payoff grid around Spot A
    spot_a = a["spot"] or 0
    grid = np.linspace(max(0, spot_a - 1500), spot_a + 1500, 301)
    payoff_array = np.zeros_like(grid)
    for (side, strike, sign) in legs:
        side_key = "call" if side.lower() in ("ce", "call") else "put"
        prem = _at(a, strike, f"{side_key}_ltp")
        if prem is not None:
            payoff_array += _leg_payoff(grid, side_key, strike, prem, sign)
            
    payoff_curve = [{"underlying": int(u), "pnl": round(float(p) * lot_size, 2)} for u, p in zip(grid, payoff_array)]

    return {"pnl_pts": pnl_pts, "pnl_rupees": pnl_rupees,
            "value_a": round(va, 2), "value_b": round(vb, 2),
            "prices_a": prices_a, "prices_b": prices_b,
            "spot_a": spot_a, "spot_b": b["spot"] or 0,
            "spot_move": round(spot_move, 1),
            "avg_iv_a": round(iv_a, 1) if iv_a else None,
            "avg_iv_b": round(iv_b, 1) if iv_b else None,
            "iv_move": round(iv_change, 1) if iv_change else None,
            "time_decay_days": time_passed,
            "pnl_spot_rupees": 0, # Note: a true greeks approx is complex, passing placeholders or directional
            "pnl_vega_rupees": 0,
            "pnl_theta_rupees": 0,
            "attribution": {"spot_move": round(spot_move, 1),
                            "iv_change": (round(iv_change, 1) if iv_change is not None else None),
                            "days_decayed": time_passed,
                            "net_short_premium": bool(net_short)},
            "read": reads,
            "payoff_curve": payoff_curve,
            "caveat": "P&L = change in structure value (mark-to-market), net of "
                      "nothing (add costs separately). Attribution is directional "
                      "(spot/IV/time), not an exact Greeks decomposition."}


if __name__ == "__main__":
    import os
    if os.path.exists("s.db"): os.remove("s.db")
    from chain_store import save_from_nse_csv
    a = save_from_nse_csv("/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv",
                          expiry="2026-06-30", spot=24050,
                          captured_at="2026-06-28T09:30:00+05:30", db="s.db")
    b = save_from_nse_csv("/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv",
                          expiry="2026-06-30", spot=23883,
                          captured_at="2026-06-30T09:30:00+05:30", db="s.db")
    # an iron condor: buy 23800P, sell 23900P, sell 24200C, buy 24300C
    legs = [("put",23800,+1),("put",23900,-1),("call",24200,-1),("call",24300,+1)]
    out = strategy_pnl_comparison(legs, a, b, "2026-06-30", db="s.db")
    print("STRATEGY P&L COMPARISON (iron condor, 28th→30th):")
    for r in out["read"]: print("  "+r)
    os.remove("s.db")
