"""
chain_compare.py
----------------
Compare two stored option-chain captures the way a trader reads them — not a raw
number diff, but WHAT CHANGED and WHAT IT MEANS.

The best comparison surfaces the DECISION-RELEVANT deltas:
  1. Spot & context move (where price went, VIX change).
  2. WALL migration — did support/resistance shift? (walls are strikes with max
     OI; if they moved, the expected range moved).
  3. Fresh writing/unwinding — where did OI BUILD or LEAVE between snapshots?
     (this is the real "what are writers doing now" signal — direction of flow).
  4. IV / expected-move change — did the priced move expand or compress?
  5. PCR & skew shift — did the directional lean change?

Aligns by STRIKE (fixed) so it works even as spot moves between captures.
"""
from __future__ import annotations
from chain_store import load_capture, days_from_capture, DB_PATH


def _at(chain, strike, key):
    try:
        i = chain["strikes"].index(strike)
        return chain[key][i]
    except (ValueError, KeyError, IndexError):
        return None


def _wall(chain, side, ref_spot):
    """Strike with max OI on one side of spot. side='put'(support)/'call'(resist)."""
    ks = chain["strikes"]; oi = chain[f"{side}_oi"]
    if side == "put":
        cand = [(oi[i] or 0, ks[i]) for i in range(len(ks)) if ks[i] < ref_spot]
    else:
        cand = [(oi[i] or 0, ks[i]) for i in range(len(ks)) if ks[i] > ref_spot]
    return max(cand)[1] if cand else None


def compare(cap_id_a: int, cap_id_b: int, db=DB_PATH) -> dict:
    """Compare capture A (older) vs B (newer). Returns a decision-focused diff."""
    a = load_capture(cap_id_a, db=db); b = load_capture(cap_id_b, db=db)
    if not a or not b:
        return {"error": "one or both captures not found"}

    # 1. context
    spot_move = round((b["spot"] or 0) - (a["spot"] or 0), 1)
    ctx = {"from": a["captured_at"][:16], "to": b["captured_at"][:16],
           "spot_a": a["spot"], "spot_b": b["spot"], "spot_move": spot_move}

    # 2. wall migration (use each capture's own spot)
    sup_a, sup_b = _wall(a, "put", a["spot"]), _wall(b, "put", b["spot"])
    res_a, res_b = _wall(a, "call", a["spot"]), _wall(b, "call", b["spot"])
    walls = {"support": {"was": sup_a, "now": sup_b,
                         "shift": (sup_b - sup_a) if (sup_a and sup_b) else None},
             "resistance": {"was": res_a, "now": res_b,
                            "shift": (res_b - res_a) if (res_a and res_b) else None}}

    # 3. fresh writing / unwinding by strike (OI delta B-A), near spot
    spot = b["spot"]; flow = []
    for k in b["strikes"]:
        if abs(k - spot) > 300:
            continue
        d_poi = (_at(b, k, "put_oi") or 0) - (_at(a, k, "put_oi") or 0)
        d_coi = (_at(b, k, "call_oi") or 0) - (_at(a, k, "call_oi") or 0)
        flow.append({"strike": k, "put_oi_delta": round(d_poi),
                     "call_oi_delta": round(d_coi)})
    # biggest put build (support forming) and call build (resistance forming)
    put_build = max(flow, key=lambda x: x["put_oi_delta"], default=None)
    call_build = max(flow, key=lambda x: x["call_oi_delta"], default=None)

    # 4. IV / expected move: ATM IV each side
    def atm_iv(ch):
        s = ch["spot"]; i = min(range(len(ch["strikes"])),
                                key=lambda j: abs(ch["strikes"][j] - s))
        civ = ch["call_iv"][i]; piv = ch["put_iv"][i]
        vals = [v for v in (civ, piv) if v]
        return round(sum(vals) / len(vals), 1) if vals else None
    iv_a, iv_b = atm_iv(a), atm_iv(b)
    iv_change = round((iv_b or 0) - (iv_a or 0), 1) if (iv_a and iv_b) else None

    # 5. PCR shift
    def pcr(ch):
        tp = sum(v or 0 for v in ch["put_oi"]); tc = sum(v or 0 for v in ch["call_oi"]) or 1
        return round(tp / tc, 2)
    pcr_a, pcr_b = pcr(a), pcr(b)

    # ── plain-English read ──
    reads = [f"Spot moved {spot_move:+.0f} ({a['spot']:.0f}→{b['spot']:.0f})."]
    if walls["resistance"]["shift"]:
        reads.append(f"Resistance wall shifted {walls['resistance']['shift']:+.0f} "
                     f"({res_a:.0f}→{res_b:.0f}) — the market's ceiling {'rose' if walls['resistance']['shift']>0 else 'dropped'}.")
    if walls["support"]["shift"]:
        reads.append(f"Support wall shifted {walls['support']['shift']:+.0f} "
                     f"({sup_a:.0f}→{sup_b:.0f}) — the floor {'rose' if walls['support']['shift']>0 else 'dropped'}.")
    if call_build and call_build["call_oi_delta"] > 0:
        reads.append(f"Heaviest fresh CALL writing at {call_build['strike']:.0f} "
                     f"(+{call_build['call_oi_delta']:,}) — resistance building there.")
    if put_build and put_build["put_oi_delta"] > 0:
        reads.append(f"Heaviest fresh PUT writing at {put_build['strike']:.0f} "
                     f"(+{put_build['put_oi_delta']:,}) — support building there.")
    if iv_change is not None:
        reads.append(f"ATM IV {iv_a}%→{iv_b}% ({iv_change:+.1f}) — expected move "
                     f"{'expanded' if iv_change>0 else 'compressed'}.")
    reads.append(f"PCR {pcr_a}→{pcr_b} — lean {'more bullish (put-heavy)' if pcr_b>pcr_a else 'more bearish (call-heavy)'}.")

    return {"context": ctx, "walls": walls,
            "fresh_writing": {"biggest_put_build": put_build,
                              "biggest_call_build": call_build,
                              "flow": flow},
            "iv": {"atm_iv_a": iv_a, "atm_iv_b": iv_b, "change": iv_change},
            "pcr": {"a": pcr_a, "b": pcr_b},
            "read": reads,
            "caveat": "Aligned by fixed strike (works as spot moves). OI delta = "
                      "B−A; positive = fresh writing/building, negative = "
                      "unwinding. Reads are positioning shifts, not predictions."}


if __name__ == "__main__":
    import os, json
    if os.path.exists("cmp.db"): os.remove("cmp.db")
    from chain_store import save_from_nse_csv
    # same file twice with different spot to demo the comparison mechanics
    a = save_from_nse_csv("/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv",
                          expiry="2026-06-30", spot=24050,
                          captured_at="2026-06-28T09:30:00+05:30", db="cmp.db")
    b = save_from_nse_csv("/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv",
                          expiry="2026-06-30", spot=23883,
                          captured_at="2026-06-30T09:30:00+05:30", db="cmp.db")
    out = compare(a, b, db="cmp.db")
    print("COMPARISON READ:")
    for r in out["read"]:
        print("  •", r)
    os.remove("cmp.db")


# ── bid-ask spread + volume analysis (combined liquidity read) ──────────────
def _spread(chain, strike, side):
    """Absolute and % bid-ask spread for one strike/side."""
    bid = _at(chain, strike, f"{side}_bid"); ask = _at(chain, strike, f"{side}_ask")
    if bid is None or ask is None or ask <= 0:
        return None, None
    mid = (bid + ask) / 2
    return round(ask - bid, 2), (round((ask - bid) / mid * 100, 1) if mid > 0 else None)


def liquidity_volume_analysis(cap_id_a, cap_id_b, db=DB_PATH,
                              band=300):
    """Combine bid-ask SPREAD + VOLUME across two captures, per strike near spot.
    Reveals: liquidity trajectory, whether OI shifts were volume-CONFIRMED, and
    the death-zone strikes (illiquid AND expensive)."""
    a = load_capture(cap_id_a, db=db); b = load_capture(cap_id_b, db=db)
    if not a or not b:
        return {"error": "capture(s) not found"}
    spot = b["spot"]
    rows = []
    for k in b["strikes"]:
        if abs(k - spot) > band:
            continue
        for side in ("call", "put"):
            sp_a, sppct_a = _spread(a, k, side)
            sp_b, sppct_b = _spread(b, k, side)
            vol_b = _at(b, k, f"{side}_volume") or 0
            oi_a = _at(a, k, f"{side}_oi") or 0
            oi_b = _at(b, k, f"{side}_oi") or 0
            oi_delta = oi_b - oi_a
            # classify
            liq = None
            if sppct_b is not None:
                if sppct_b <= 1 and vol_b > 0:
                    liq = "liquid"          # tight + traded
                elif sppct_b <= 1:
                    liq = "tight_quiet"     # tight but low volume
                elif vol_b > 0:
                    liq = "active_wide"     # traded but wide
                else:
                    liq = "avoid"          # wide AND untraded (death zone)
            # OI-change conviction: big OI move needs volume to be real
            conviction = None
            if abs(oi_delta) > 20000:
                conviction = ("volume_confirmed" if vol_b >= abs(oi_delta) * 0.5
                              else "SUSPECT_low_volume")
            rows.append({"strike": k, "side": side,
                         "spread_pct_now": sppct_b, "spread_pct_was": sppct_a,
                         "spread_trend": (round(sppct_b - sppct_a, 1)
                                          if (sppct_a and sppct_b) else None),
                         "volume": round(vol_b), "oi_delta": round(oi_delta),
                         "liquidity": liq, "oi_conviction": conviction})

    # highlights
    death_zone = [r for r in rows if r["liquidity"] == "avoid"]
    suspect_oi = [r for r in rows if r["oi_conviction"] == "SUSPECT_low_volume"]
    confirmed_oi = [r for r in rows if r["oi_conviction"] == "volume_confirmed"]
    widening = sorted([r for r in rows if r["spread_trend"] and r["spread_trend"] > 0.5],
                      key=lambda x: -x["spread_trend"])[:3]
    most_active = sorted(rows, key=lambda x: -x["volume"])[:3]

    reads = []
    if confirmed_oi:
        top = max(confirmed_oi, key=lambda x: abs(x["oi_delta"]))
        reads.append(f"Volume-CONFIRMED positioning: {top['side']} {top['strike']:.0f} "
                     f"OI {top['oi_delta']:+,} on {top['volume']:,} volume — real writing/flow.")
    if suspect_oi:
        s = suspect_oi[0]
        reads.append(f"⚠ SUSPECT OI shift: {s['side']} {s['strike']:.0f} OI "
                     f"{s['oi_delta']:+,} but only {s['volume']:,} volume — "
                     f"unconfirmed (block trade or artifact?), don't over-read the wall.")
    if death_zone:
        ks = ", ".join(f"{r['side']} {r['strike']:.0f}" for r in death_zone[:4])
        reads.append(f"AVOID (illiquid + wide spread): {ks} — bad fills, your order "
                     f"moves the price. Keep legs out of these.")
    if widening:
        w = widening[0]
        reads.append(f"Liquidity draining: {w['side']} {w['strike']:.0f} spread "
                     f"widened {w['spread_trend']:+.1f}% — getting costlier to trade.")
    if most_active:
        m = most_active[0]
        reads.append(f"Most active strike: {m['side']} {m['strike']:.0f} "
                     f"({m['volume']:,} vol) — market's focus.")

    return {"rows": rows, "read": reads,
            "highlights": {"death_zone": death_zone, "suspect_oi": suspect_oi,
                           "confirmed_oi": confirmed_oi, "widening": widening,
                           "most_active": most_active},
            "caveat": "Spread = execution cost (tighter = cheaper). Volume "
                      "CONFIRMS OI shifts (big OI move on low volume is suspect). "
                      "'avoid' = illiquid AND wide — keep trade legs out of these."}
