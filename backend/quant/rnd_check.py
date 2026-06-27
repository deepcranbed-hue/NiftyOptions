"""
rnd_check.py
------------
Paste-in diagnostic for the RND extractor. Run it with the SAME inputs you feed
extract_rnd in your app; it tells you which input is wrong by cross-checking
against the ATM straddle (a model-free, T-independent yardstick) and by running
the call-only vs two-sided paths side by side.

    from rnd_check import diagnose
    diagnose(strikes, call_ltp, put_ltp, spot, days, r)
"""

from __future__ import annotations

import numpy as np
from rnd import extract_rnd, rnd_stats


def diagnose(strikes, call_ltp, put_ltp, spot, days, r=0.0655):
    sk = np.asarray(strikes, float)
    call = np.asarray(call_ltp, float)
    put = np.asarray(put_ltp, float) if put_ltp is not None else None

    print("── INPUTS ───────────────────────────────")
    print(f"  spot        : {spot}")
    print(f"  days/expiry : {days}   (T = {days/365:.4f} yr)")
    print(f"  r           : {r}")
    print(f"  #strikes    : {len(sk)}   range {sk.min():.0f}–{sk.max():.0f} "
          f"(±{(sk.max()-spot):.0f}/{(spot-sk.min()):.0f} around spot)")
    print(f"  put_ltp     : {'PROVIDED' if put is not None else '*** MISSING ***'}")

    # input integrity checks
    issues = []
    if not np.all(np.diff(sk) > 0):
        issues.append("strikes not strictly ascending (sort them + align prices)")
    if put is None:
        issues.append("put_ltp MISSING → call-only path → shallow skew + wrong P(below)")
    if days > 20:
        issues.append(f"days={days} looks too large for a weekly (June-30 ≈ 5 days)")
    span_lo, span_hi = spot - sk.min(), sk.max() - spot
    if max(span_lo, span_hi) > 6 * 211:   # very wide wings vs a ~211 straddle
        issues.append("very wide strike wings → call-only ITM inversion inflates tails/SD")

    # straddle yardstick (model-free)
    c_atm = float(np.interp(spot, sk, call))
    p_atm = float(np.interp(spot, sk, put if put is not None else call))
    straddle = c_atm + p_atm
    one_sigma = straddle / 0.798
    print("\n── STRADDLE YARDSTICK (no model, no T) ──")
    print(f"  ATM straddle ≈ {straddle:.0f} pts → expected 1-sigma ≈ {one_sigma:.0f} pts")
    print("  (your extractor's 'move' MUST land near this; if not, density is malformed)")

    # run both paths
    print("\n── EXTRACTOR OUTPUT ─────────────────────")
    g1, d1 = extract_rnd(sk, call, spot, days/365, r)               # call-only
    s1 = rnd_stats(g1, d1, spot)
    print(f"  call-only : move ±{s1['sd']:.0f} | P(below) {s1['p_below_spot']:.0%} "
          f"| skew {s1['skew']:+.2f}   (BUGGY reference)")
    if put is not None:
        g2, d2 = extract_rnd(sk, call, spot, days/365, r, put_prices=put)
        s2 = rnd_stats(g2, d2, spot)
        print(f"  two-sided : move ±{s2['sd']:.0f} | P(below) {s2['p_below_spot']:.0%} "
              f"| skew {s2['skew']:+.2f}   (CORRECT)")
        move, dens, grid = s2['sd'], d2, g2
    else:
        move, dens, grid = s1['sd'], d1, g1

    # output sanity
    area = float(np.trapezoid(dens, grid))
    print("\n── OUTPUT SANITY ────────────────────────")
    print(f"  density integral : {area:.3f}  ({'OK' if abs(area-1) < 0.02 else 'NOT 1 → renormalize!'})")
    ratio = move / one_sigma
    verdict = "OK" if 0.7 < ratio < 1.6 else "*** OFF *** (tails inflated → clip<0 & renormalize)"
    print(f"  move vs straddle : {move:.0f} vs {one_sigma:.0f}  (ratio {ratio:.2f}) {verdict}")

    print("\n── LIKELY FIX ───────────────────────────")
    if not issues:
        print("  inputs look clean; numbers should match the corrected reference.")
    for i in issues:
        print(f"  • {i}")


if __name__ == "__main__":
    strikes = list(range(23400, 24650, 50))
    call = [701.60, 653.65, 601.45, 556.00, 503.65, 455.40, 409.75, 364.30,
            317.70, 276.50, 234.40, 196.00, 160.95, 129.15, 102.45, 78.10,
            58.70, 43.55, 31.15, 22.55, 16.40, 12.25, 9.00, 6.65, 5.15]
    put = [3.30, 4.15, 4.90, 6.10, 7.50, 9.85, 12.95, 16.75, 22.10, 29.45, 38.15,
           49.50, 64.50, 82.80, 105.40, 131.20, 161.15, 196.70, 234.50, 275.85,
           320.40, 365.90, 413.00, 460.05, 510.00]

    print("########## CORRECT INPUTS ##########")
    diagnose(strikes, call, put, 24056.0, 5)
    print("\n\n########## WRONG: put_ltp missing, days=30 ##########")
    diagnose(strikes, call, None, 24056.0, 30)
