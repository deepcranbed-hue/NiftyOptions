"""
strategy_framework/strategy/constructor.py
==========================================
Turn a directional family + a live chain snapshot into a concrete, priced
option structure with an expiry payoff curve.

Leg encoding matches the project convention used by strategy_compare.py and
portfolio.py:  legs = list of (side, strike, sign)  with
    side  in {"call", "put"}
    sign  = +1 long / -1 short
Premiums are looked up from the snapshot LTPs so the structure is priced as-of
the decision time. Payoff is computed on an underlying grid at expiry.

Everything here is pure numpy — no scipy — so it runs anywhere.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Structure:
    family: str
    legs: list                     # [(side, strike, sign), ...]
    premiums: dict                 # (side, strike) -> ltp at entry
    net_debit: float               # +ve = you pay (debit), -ve = you receive (credit)
    max_profit: float              # points
    max_loss: float                # points (positive number)
    breakevens: list
    spot_at_entry: float
    lot_size: int
    detail: dict = field(default_factory=dict)

    def rupees(self) -> dict:
        return {"max_profit": round(self.max_profit * self.lot_size, 0),
                "max_loss": round(self.max_loss * self.lot_size, 0),
                "net_debit": round(self.net_debit * self.lot_size, 0)}

    def as_dict(self) -> dict:
        return {"family": self.family,
                "legs": [{"side": s, "strike": k, "sign": g} for s, k, g in self.legs],
                "net_debit_pts": round(self.net_debit, 2),
                "max_profit_pts": round(self.max_profit, 2),
                "max_loss_pts": round(self.max_loss, 2),
                "breakevens": [round(b, 1) for b in self.breakevens],
                "rupees": self.rupees(), "detail": self.detail}


def _leg_payoff(grid: np.ndarray, side: str, strike: float, sign: int,
                premium: float) -> np.ndarray:
    if side == "future":                          # linear: P&L = sign·(spot − entry)
        return sign * (grid - premium)
    intrinsic = np.maximum(grid - strike, 0) if side == "call" else np.maximum(strike - grid, 0)
    # long pays premium (-), short receives premium (+); at expiry add sign*intrinsic
    return sign * intrinsic - sign * premium


def _nearest(strikes, target):
    return min(strikes, key=lambda k: abs(k - target))


def expiry_payoff(legs, premiums, grid):
    """Public: expiry P&L (points) of a leg set on an underlying `grid`, given the
    entry `premiums` dict {(side,strike): ltp}. Used by the action evaluator to
    integrate each candidate action's payoff against the forecast distribution."""
    g = np.asarray(grid, float)
    out = np.zeros_like(g)
    for side, k, sign in legs:
        out = out + _leg_payoff(g, side, k, sign, float(premiums.get((side, k), 0.0)))
    return out


def _premium(chain, side, strike):
    book = chain.call_ltp if side == "call" else chain.put_ltp
    return float(book.get(strike, 0.0))


def _anchor_short(chain, side, atm, grid_step, otm_fallback):
    """Pick the short strike for a credit wing: the OI wall on that side if it is
    OTM and carries a price, otherwise a fixed OTM offset from ATM."""
    spot = chain.spot
    if side == "put":
        cands = [(k, chain.put_oi.get(k, 0)) for k in chain.strikes
                 if k < spot and chain.put_ltp.get(k, 0) > 0]
    else:
        cands = [(k, chain.call_oi.get(k, 0)) for k in chain.strikes
                 if k > spot and chain.call_ltp.get(k, 0) > 0]
    if cands:
        return max(cands, key=lambda x: x[1])[0]        # the OI wall
    off = otm_fallback * grid_step
    return _nearest(chain.strikes, atm + (off if side == "call" else -off))


def _expected_move_pts(chain, expected_move_pts, dte_days):
    """Resolve an expected move (≈1σ to expiry) in points. Prefer an explicit value
    from the caller (regime); else derive it from THIS chain at THIS expiry; else
    None (callers fall back to OI-wall / fixed-offset placement).

    The VIX branch was removed here for the same two reasons as in regime.py
    (D-SC-04): `chain.vix` is `captures.vix`, a constant 12.0 placeholder, and even a
    real INDIAVIX is a 30-day constant-maturity whole-smile index whose ratio to the
    traded expiry swings 0.861–1.205 with DTE. Inverting ATM IV off the chain's own
    LTPs is exact-maturity and matches the ATM straddle to 0.3%.

    Returning None is deliberate: a caller that cannot get a real expected move must
    fall back to OI-wall placement, NOT to a fabricated volatility.
    """
    if expected_move_pts and expected_move_pts > 0:
        return float(expected_move_pts)
    if not (dte_days and dte_days > 0) or not getattr(chain, "spot", None):
        return None
    import math
    from ..bs import implied_vol as _iv
    atm = chain.atm_strike()
    T = max(dte_days / 365.0, 1e-5)
    c = (chain.call_ltp.get(atm) or 0.0)
    p = (chain.put_ltp.get(atm) or 0.0)
    if c > 0 and p > 0:                                   # tier 1: the traded straddle
        return float((c + p) / math.sqrt(2.0 / math.pi))  # -> 1σ  (×1.2533)
    ivs = [v for v in (_iv(c, chain.spot, atm, T, call=True) if c > 0 else None,
                       _iv(p, chain.spot, atm, T, call=False) if p > 0 else None)
           if v is not None]
    if ivs:                                               # tier 2: chain-native ATM IV
        return float(chain.spot * (sum(ivs) / len(ivs)) * math.sqrt(T))
    return None


def build(family: str, chain, cfg, expected_move_pts=None, dte_days=None) -> "Structure | None":
    """Construct legs for the family, anchored on OI walls / expected move.

    For iron condors the shorts are placed ~`condor_short_em_mult`×(expected move)
    OTM when an expected move is available (via `expected_move_pts` or VIX+`dte_days`),
    so they aren't left near-ATM at longer DTE. An OI wall is honoured only if it
    sits within tolerance of that target."""
    if family in ("stand_aside", None):
        return None

    # ---- NIFTY futures: a linear directional position (no options) -----------
    if family in ("long_future", "short_future"):
        sign = 1 if family == "long_future" else -1
        entry = float(chain.spot)
        gstep = _infer_step(chain.strikes)
        lo, hi = min(chain.strikes) - 10 * gstep, max(chain.strikes) + 10 * gstep
        grid = np.linspace(lo, hi, 2001)
        payoff = sign * (grid - entry)            # linear P&L in points vs entry spot
        ek = round(entry, 1)
        return Structure(
            family=family, legs=[("future", ek, sign)],
            premiums={("future", ek): entry},     # 'price' = entry spot (marks vs spot)
            net_debit=0.0,                          # margin, not premium
            max_profit=float(payoff.max()), max_loss=float(-payoff.min()),
            breakevens=[ek], spot_at_entry=entry, lot_size=cfg.lot_size,
            detail={"note": f"{'long' if sign > 0 else 'short'} NIFTY future @ {ek}",
                    "linear": True, "entry_spot": ek})

    spot = chain.spot
    step = cfg.strikes.spread_width_strikes
    otm = cfg.strikes.long_otm_strikes
    grid_step = _infer_step(chain.strikes)
    atm = _nearest(chain.strikes, spot)
    em = _expected_move_pts(chain, expected_move_pts, dte_days)

    legs: list = []
    # ---- pick strikes per family -----------------------------------------
    if family == "bull_call_spread":
        long_k = _nearest(chain.strikes, atm)                 # ATM long call
        short_k = _nearest(chain.strikes, atm + step * grid_step)
        legs = [("call", long_k, +1), ("call", short_k, -1)]
    elif family == "bear_put_spread":
        long_k = _nearest(chain.strikes, atm)
        short_k = _nearest(chain.strikes, atm - step * grid_step)
        legs = [("put", long_k, +1), ("put", short_k, -1)]
    elif family == "bull_put_spread":                          # credit, bullish
        short_k = _nearest(chain.strikes, atm - otm * grid_step)
        long_k = _nearest(chain.strikes, short_k - step * grid_step)
        legs = [("put", short_k, -1), ("put", long_k, +1)]
    elif family == "bear_call_spread":                         # credit, bearish
        short_k = _nearest(chain.strikes, atm + otm * grid_step)
        long_k = _nearest(chain.strikes, short_k + step * grid_step)
        legs = [("call", short_k, -1), ("call", long_k, +1)]
    elif family == "long_call":
        k = _nearest(chain.strikes, atm + otm * grid_step)
        legs = [("call", k, +1)]
    elif family == "long_put":
        k = _nearest(chain.strikes, atm - otm * grid_step)
        legs = [("put", k, +1)]
    elif family == "iron_condor":
        # Short OTM put spread + short OTM call spread. Anchor short strikes on
        # the OI walls when they sit OTM and priced; else a fixed OTM offset.
        # Then clamp the shorts inward so a `spread_width` wing always fits inside
        # the available strike band (avoids the wall-at-edge degenerate case).
        lo, hi = min(chain.strikes), max(chain.strikes)
        wing = step * grid_step
        if em and em > 0:
            # place each short ~condor_short_em_mult × expected move OTM; honour an
            # OI wall only if it falls within tolerance of that target (else it
            # would drag the short back near-ATM, as it did at 8 DTE).
            tgt = cfg.strikes.condor_short_em_mult * em
            tol = cfg.strikes.condor_wall_tol
            lo_d, hi_d = (1 - tol) * tgt, (1 + tol) * tgt

            def _place(side):
                wall = _anchor_short(chain, side, atm, grid_step, otm)
                wall_d = (spot - wall) if side == "put" else (wall - spot)
                if lo_d <= wall_d <= hi_d:
                    return wall                        # wall sits sensibly → keep it
                target = spot - tgt if side == "put" else spot + tgt
                return _nearest(chain.strikes, target)
            put_short, call_short = _place("put"), _place("call")
        else:
            put_short = _anchor_short(chain, "put", atm, grid_step, otm)
            call_short = _anchor_short(chain, "call", atm, grid_step, otm)
        put_short = _nearest(chain.strikes, max(put_short, lo + wing))
        call_short = _nearest(chain.strikes, min(call_short, hi - wing))
        put_long = _nearest(chain.strikes, put_short - wing)
        call_long = _nearest(chain.strikes, call_short + wing)
        legs = [("put", put_long, +1), ("put", put_short, -1),
                ("call", call_short, -1), ("call", call_long, +1)]
    elif family == "iron_butterfly":
        # Short ATM straddle + protective wings.
        wing = max(step, 2) * grid_step
        put_long = _nearest(chain.strikes, atm - wing)
        call_long = _nearest(chain.strikes, atm + wing)
        legs = [("put", put_long, +1), ("put", atm, -1),
                ("call", atm, -1), ("call", call_long, +1)]
    elif family in ("long_straddle", "short_straddle"):
        sign = +1 if family.startswith("long") else -1
        legs = [("put", atm, sign), ("call", atm, sign)]
    elif family in ("long_strangle", "short_strangle"):
        sign = +1 if family.startswith("long") else -1
        pk = _nearest(chain.strikes, atm - otm * grid_step)
        ck = _nearest(chain.strikes, atm + otm * grid_step)
        legs = [("put", pk, sign), ("call", ck, sign)]
    else:
        return None

    return _finalize(family, legs, chain, cfg,
                     detail={"atm": atm, "grid_step": grid_step})


def _finalize(family, legs, chain, cfg, detail=None) -> "Structure | None":
    """Validate, price, and compute the payoff of an explicit leg set.

    Shared by build() and by the adjustment engine (from_legs). Rejects
    degenerate structures (legs collapsed onto one (side,strike)) and any leg
    that has no price in this snapshot.
    """
    grid_step = _infer_step(chain.strikes)
    # Merge same (side,strike) legs by summing signs; drop net-zero legs.
    merged: dict = {}
    for side, k, sign in legs:
        merged[(side, k)] = merged.get((side, k), 0) + sign
    legs = [(s, k, g) for (s, k), g in merged.items() if g != 0]
    if not legs:
        return None

    premiums = {(s, k): _premium(chain, s, k) for s, k, _ in legs}
    if any(v <= 0 for v in premiums.values()):
        return None

    lo = min(chain.strikes) - 5 * grid_step
    hi = max(chain.strikes) + 5 * grid_step
    grid = np.linspace(lo, hi, 2001)
    payoff = np.zeros_like(grid)
    net_debit = 0.0
    for side, k, sign in legs:
        p = premiums[(side, k)]
        payoff += _leg_payoff(grid, side, k, sign, p)
        net_debit += sign * p
    d = {"strikes": [k for _, k, _ in legs]}
    if detail:
        d.update(detail)
    return Structure(family=family, legs=legs, premiums=premiums,
                     net_debit=net_debit, max_profit=float(payoff.max()),
                     max_loss=float(-payoff.min()),
                     breakevens=_zero_crossings(grid, payoff),
                     spot_at_entry=chain.spot, lot_size=cfg.lot_size, detail=d)


def build_tail_hedge(chain, cfg, intensity: float,
                     expected_move_pts=None, dte_days=None) -> "dict | None":
    """Max-drawdown insurance: a long OTM put sized/struck by the derisk intensity.

    Strike is placed between hedge.sigma_hi (further OTM, cheaper) at the trigger
    and hedge.sigma_lo (closer, costlier) at intensity 1.0, in multiples of the
    expected move (≈1σ). Lots scale 1..max_lots across [trigger, 1.0]. Also prices
    a cost-reduced put-SPREAD variant (short a put hedge.spread_sigma×σ further out)
    for reference. Returns None if hedging is disabled/below trigger or unpriceable.
    """
    hc = getattr(cfg, "hedge", None)
    if hc is None or not hc.enabled or intensity < hc.trigger:
        return None
    em = _expected_move_pts(chain, expected_move_pts, dte_days) or (chain.spot * 0.01)
    grid_step = _infer_step(chain.strikes)
    spot = chain.spot

    # interpolate strike distance (σ) and lot count across [trigger, 1.0]
    t = _clip01((intensity - hc.trigger) / max(1e-6, 1.0 - hc.trigger))
    sig = hc.sigma_hi + (hc.sigma_lo - hc.sigma_hi) * t          # hi -> lo as intensity rises
    lots = int(round(1 + (hc.max_lots - 1) * t))
    put_k = _nearest(chain.strikes, spot - sig * em)

    long_put = _finalize("tail_put_hedge", [("put", put_k, +1)], chain, cfg,
                         detail={"sigma": round(sig, 2), "expected_move_pts": round(em, 1)})
    if long_put is None:
        return None

    # reference cost-reduced put spread: short a put spread_sigma×σ further OTM
    spread = None
    short_k = _nearest(chain.strikes, spot - hc.spread_sigma * em)
    if short_k < put_k:
        spread = from_legs("tail_put_spread", [("put", put_k, +1), ("put", short_k, -1)],
                           chain, cfg)

    lot = cfg.lot_size
    debit_pts = long_put.net_debit
    out = {
        "recommended": True,
        "intensity": round(float(intensity), 3),
        "lots": max(1, lots),
        "sigma_otm": round(sig, 2),
        "expected_move_pts": round(em, 1),
        "long_put": {
            "strike": put_k,
            "premium_pts": round(long_put.premiums.get(("put", put_k), debit_pts), 2),
            "cost_pts_per_lot": round(debit_pts, 2),
            "cost_inr_total": round(debit_pts * lot * max(1, lots), 0),
            "protection": "uncapped below strike",
        },
    }
    if spread is not None:
        out["put_spread_ref"] = {
            "long": put_k, "short": short_k,
            "cost_pts_per_lot": round(spread.net_debit, 2),
            "cost_inr_total": round(spread.net_debit * lot * max(1, lots), 0),
            "max_payoff_pts": round(spread.max_profit, 1),
            "protection": f"capped at {short_k} ({hc.spread_sigma}σ)",
        }
    return out


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def from_legs(family: str, legs: list, chain, cfg) -> "Structure | None":
    """Public: build a priced Structure from an explicit (side,strike,sign) list
    as-of a chain snapshot. Used by the adjustment engine to price morphed
    positions (rolled wings, added straddle legs, butterfly conversions)."""
    return _finalize(family, legs, chain, cfg, detail={"source": "adjustment"})


def _infer_step(strikes) -> float:
    ks = sorted(set(strikes))
    if len(ks) < 2:
        return 50.0
    diffs = np.diff(ks)
    return float(np.median(diffs))


def _zero_crossings(grid, payoff) -> list:
    outs = []
    s = np.sign(payoff)
    idx = np.where(np.diff(s) != 0)[0]
    for i in idx:
        x0, x1 = grid[i], grid[i + 1]
        y0, y1 = payoff[i], payoff[i + 1]
        if y1 != y0:
            outs.append(float(x0 - y0 * (x1 - x0) / (y1 - y0)))
    return outs


def payoff_at(structure: Structure, underlying: float) -> float:
    """Realized P&L (points) if the underlying settles at `underlying`."""
    total = 0.0
    for side, k, sign in structure.legs:
        p = structure.premiums[(side, k)]
        intrinsic = max(underlying - k, 0) if side == "call" else max(k - underlying, 0)
        total += sign * intrinsic - sign * p
    return total
