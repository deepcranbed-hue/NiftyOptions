"""
bond_cues.py
------------
Indian bond market (10Y G-sec yield) as an equity-direction and FII-sentiment
signal — the gap not covered by flows.py / global_cues / metals_cues.

Why it matters:
  * The 10Y G-sec yield is the equity discount rate. FALLING yields support
    equities (esp. rate-sensitives: Banks, Auto, Realty); RISING yields pressure
    them. Today's Realty/Bank leadership is consistent with stable/easing yields.
  * FIIs trade bonds AND equities together. The yield DISAMBIGUATES an FII equity
    sell: yields UP + equity selling = risk-off (FIIs exiting India); yields flat
    + equity selling = rotation/profit-booking, not an India-exit. This is the
    bond analogue of "breadth disambiguates the index."
  * On EXPIRY day (news = profit-booking noise), the bond+FX+gold complex is the
    cleaner read of WHY FIIs are positioned as they are.

Data: 10Y G-sec yield (CCIL / RBI / investing.com IN10Y), the USDINR rate, and
the existing FII flow. All slow/daily — flag lags.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class BondDay:
    yield_10y: float          # India 10Y G-sec yield, %
    prev_yield_10y: float     # previous close, %
    usdinr: float | None = None
    prev_usdinr: float | None = None


def read_bonds(b: BondDay) -> dict:
    """Yield direction -> equity tilt + rate-sensitive sector read."""
    dy_bps = round((b.yield_10y - b.prev_yield_10y) * 100, 1)   # change in basis points
    if dy_bps <= -3:
        tilt, read = "supportive", (
            f"10Y yield FELL {abs(dy_bps):.0f}bps to {b.yield_10y:.2f}% — supportive "
            f"for equities; positive for rate-sensitives (Banks, Auto, Realty). "
            f"Often signals FII bond inflows / RBI-dovish expectations.")
    elif dy_bps >= 3:
        tilt, read = "headwind", (
            f"10Y yield ROSE {dy_bps:.0f}bps to {b.yield_10y:.2f}% — headwind for "
            f"equities; pressures rate-sensitives and can accompany FII outflows.")
    else:
        tilt, read = "neutral", (
            f"10Y yield ~flat at {b.yield_10y:.2f}% ({dy_bps:+.0f}bps) — no rate "
            f"impulse for equities today.")

    # rupee read (FII flow proxy)
    fx = None
    if b.usdinr and b.prev_usdinr:
        dfx = b.usdinr - b.prev_usdinr
        if dfx >= 0.05:
            fx = (f"Rupee WEAKER ({b.prev_usdinr:.2f}->{b.usdinr:.2f}) — consistent "
                  f"with FII outflow / risk-off pressure.")
        elif dfx <= -0.05:
            fx = (f"Rupee STRONGER ({b.prev_usdinr:.2f}->{b.usdinr:.2f}) — consistent "
                  f"with FII inflow / risk-on.")
        else:
            fx = f"Rupee ~stable ({b.usdinr:.2f})."

    return {"yield_10y": b.yield_10y, "change_bps": dy_bps, "equity_tilt": tilt,
            "read": read, "rupee_read": fx,
            "caveat": "G-sec yield is daily/slow; USDINR intraday. A single day's "
                      "yield move is a tilt, not a trend."}


def fii_disambiguation(fii_cash_cr: float, yield_change_bps: float,
                       rupee_weaker: bool | None = None) -> dict:
    """The key cross-check: is FII equity selling a RISK-OFF EXIT or just ROTATION?
    fii_cash_cr: today's FII net cash (₹cr, sell = negative).
    yield_change_bps: today's 10Y move. rupee_weaker: True if INR depreciated."""
    selling = fii_cash_cr < -500
    buying = fii_cash_cr > 500
    risk_off_corroborated = yield_change_bps >= 3 or rupee_weaker is True

    if selling and risk_off_corroborated:
        verdict, read = "fii_exit_risk_off", (
            f"FII selling ₹{abs(fii_cash_cr):.0f}cr CORROBORATED by rising yields/"
            f"weaker rupee — a genuine RISK-OFF India exit, not just expiry booking. "
            f"Defensive.")
    elif selling and not risk_off_corroborated:
        verdict, read = "fii_sell_rotation", (
            f"FII selling ₹{abs(fii_cash_cr):.0f}cr BUT yields stable / rupee firm — "
            f"likely profit-booking / rotation, NOT an India exit. The equity sell "
            f"OVERSTATES the bearishness (esp. on expiry day). Less alarming.")
    elif buying and not risk_off_corroborated:
        verdict, read = "fii_inflow", (
            f"FII buying ₹{fii_cash_cr:.0f}cr with supportive rates/rupee — risk-on "
            f"India inflow.")
    else:
        verdict, read = "mixed", "FII flow and rates/rupee not clearly aligned — mixed."

    return {"verdict": verdict, "read": read,
            "note": "On EXPIRY day this cross-check matters most: it separates "
                    "'FIIs leaving India' from 'profit-booking that looks scary but "
                    "isn't'. The bond/rupee complex is the tell."}


if __name__ == "__main__":
    # today-like: yields ~flat, rupee firm, FIIs sold into expiry
    print("BONDS:")
    print(" ", read_bonds(BondDay(yield_10y=6.28, prev_yield_10y=6.29,
                                  usdinr=85.4, prev_usdinr=85.5))["read"])
    print("\nFII DISAMBIGUATION (FII sold ₹1843cr, yields flat, rupee firm):")
    print(" ", fii_disambiguation(-1843, yield_change_bps=-1, rupee_weaker=False)["read"])
