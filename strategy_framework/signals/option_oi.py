"""
strategy_framework/signals/option_oi.py
=======================================
Per-strike option ΔOI, reconstructed from the OI LEVELS across captures.

Why this exists: the capture stores per-strike open-interest LEVELS (call_oi,
put_oi) at every snapshot, but the `call_oi_chg` / `put_oi_chg` columns are never
populated (all zero). So any consumer that reads those change columns is silently
running on zeros. The ΔOI is trivially recoverable from the levels — ΔOI at strike k
= oi_now(k) − oi_prior(k) — which is exactly how `backend.quant.intraday_oi` already
derives FUTURES ΔOI from futures OI levels. This is the option-chain analogue, kept
in ONE place so `breadth_oi` (wall reinforcement) and `strike_role` (role-flip
detection) both consume the same reconstruction rather than each rolling their own.

No lookahead: uses the current snapshot and the nearest snapshot ~lookback_min
EARLIER (both ≤ now), never anything from the future.
"""
from __future__ import annotations
from datetime import datetime, timedelta


def prior_chain(da, cur_chain, now: str, lookback_min: int = 30):
    """The chain snapshot ~lookback_min minutes BEFORE now (same expiry), or None if
    there is no distinct earlier snapshot. One place, so straddle-flow and OI-migration
    difference against exactly the same prior the ΔOI reconstruction uses. No lookahead."""
    if cur_chain is None:
        return None
    t = datetime.fromisoformat(now.replace("Z", "+00:00"))
    prior_ts = (t - timedelta(minutes=lookback_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev = da.chain_as_of(prior_ts, cur_chain.expiry)
    if prev is None or prev.ts == cur_chain.ts:
        return None
    return prev


def atm_straddle(chain):
    """ATM straddle S = C_ATM + P_ATM (call+put LTP at the nearest-to-spot strike).
    Returns (S, atm_strike). This is the market's priced expected move."""
    k = chain.atm_strike()
    c = chain.call_ltp.get(k, 0.0) or 0.0
    p = chain.put_ltp.get(k, 0.0) or 0.0
    return (c + p), k


def oi_cog(chain, side: str):
    """Open-interest CENTER OF GRAVITY: Σ strike·OI / Σ OI over one side ('call'|'put').
    Where the option market's mass sits; its migration is stronger than any single
    strike's OI. None if that side carries no OI."""
    oi = chain.call_oi if side == "call" else chain.put_oi
    num = sum(k * (oi.get(k, 0) or 0) for k in chain.strikes)
    den = sum((oi.get(k, 0) or 0) for k in chain.strikes)
    return (num / den) if den > 0 else None


def pin_strike(chain):
    """Strike with the largest TOTAL (call+put) OI — where dealer gamma pins price.
    Returns (strike, its_total_oi, its_share_of_all_oi)."""
    tot = {k: (chain.call_oi.get(k, 0) or 0) + (chain.put_oi.get(k, 0) or 0)
           for k in chain.strikes}
    grand = float(sum(tot.values())) or 1.0
    k = max(tot, key=tot.get)
    return k, tot[k], tot[k] / grand


def oi_concentration(chain):
    """Shape of the total-OI (call+put) distribution across strikes — how PINNED vs
    diffuse positioning is. Returns {cog, std, entropy_norm, n} or None:
      * std          : OI-weighted standard deviation in strike-POINTS (√dispersion) —
                       small = crowded into a strike (pin), large = spread out.
      * entropy_norm : Shannon entropy of the OI distribution / log(N), in [0,1] —
                       0 = all OI on one strike, 1 = perfectly uniform.
    One home so oi_dispersion and oi_entropy share the same distribution build."""
    import numpy as _np
    strikes = chain.strikes
    if not strikes:
        return None
    tot = _np.array([(chain.call_oi.get(k, 0) or 0) + (chain.put_oi.get(k, 0) or 0)
                     for k in strikes], float)
    T = float(tot.sum())
    if T <= 0:
        return None
    s = _np.array(strikes, float)
    p = tot / T
    cog = float((s * p).sum())
    std = float((p * (s - cog) ** 2).sum()) ** 0.5
    nz = p[p > 0]
    ent = float(-(nz * _np.log(nz)).sum())
    ent_norm = float(ent / _np.log(len(strikes))) if len(strikes) > 1 else 0.0
    return {"cog": float(cog), "std": float(std), "entropy_norm": ent_norm, "n": int(len(strikes))}


def reconstruct_doi(da, cur_chain, now: str, lookback_min: int = 30):
    """Per-strike ΔOI over the last `lookback_min` minutes for the current chain's
    expiry. Returns {"call_doi": {k: Δ}, "put_doi": {k: Δ}, "prior_ts", "cur_ts"} or
    None when there is no DISTINCT earlier snapshot to difference against (e.g. the
    first captures of the day) — in which case a delta cannot honestly be formed."""
    prev = prior_chain(da, cur_chain, now, lookback_min)
    if prev is None:
        return None                       # no earlier snapshot → no honest delta
    call_doi, put_doi = {}, {}
    for k in cur_chain.strikes:
        c0, c1 = prev.call_oi.get(k), cur_chain.call_oi.get(k)
        p0, p1 = prev.put_oi.get(k), cur_chain.put_oi.get(k)
        if c0 is not None and c1 is not None:
            call_doi[k] = c1 - c0
        if p0 is not None and p1 is not None:
            put_doi[k] = p1 - p0
    return {"call_doi": call_doi, "put_doi": put_doi,
            "prior_ts": prev.ts, "cur_ts": cur_chain.ts}
