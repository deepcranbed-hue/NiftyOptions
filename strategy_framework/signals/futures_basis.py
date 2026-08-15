"""
strategy_framework/signals/futures_basis.py
============================================
NIFTY futures basis & term-structure signal — the *positioning / leverage* read
that the cash-tape signals (heavyweight_leadership, technical_momentum) cannot see.

Inputs (from `price_bars`, 1m, backward as-of, ts <= now — D-MA-01):
    NIFTY_FUT_1   near-month future  (e.g. 30-Jul)   exchange NFO
    NIFTY_FUT_2   far-month future   (e.g. 27-Aug)   exchange NFO
    NIFTY         cash spot                          exchange NSE

What it reads:
  * BASIS = near-future − spot (points and %). Nifty futures normally trade at a
    small PREMIUM (cost of carry). Premium EXPANDING vs its own recent norm =
    leveraged longs building => bullish. Premium COMPRESSING, or the future
    flipping to a DISCOUNT (future < spot) = hedging / de-risking => bearish.
    A discount is the classic panic/hedging tell — e.g. an 8-Jul-type selloff.
  * PREMIUM TREND over ~30m — is the basis widening or narrowing right now.
  * CALENDAR SPREAD = far − near (term structure / carry). Positive & firm =
    bullish carry; backwardation (far < near) = stress.

No open-interest in the captured schema (OHLCV only), so this is a
price+volume signal: no OI-based long/short-buildup or rollover. Volume only
modulates CONFIDENCE, never direction.

Sign convention matches the framework: score in [-1, +1], + = bullish NIFTY.
Returns NO_DATA cleanly when the futures series are absent (e.g. a DB copy
without the NFO sync), so it never disturbs the blend. PRIOR until calibrated.
"""
from __future__ import annotations
import statistics
from .base import Signal, squash, clamp

NEAR = "NIFTY_FUT_1"   # near-month future
FAR = "NIFTY_FUT_2"    # far-month future
SPOT = "NIFTY"         # cash

_WIN = 375             # ~1 trading day of 1m bars for the basis norm


def _closes(da, sym: str, now: str, limit: int) -> list:
    bars = da.bars(sym, "1m", end=now, limit=limit)
    return [b["close"] for b in bars if b.get("close")]


def _vols(da, sym: str, now: str, limit: int) -> list:
    bars = da.bars(sym, "1m", end=now, limit=limit)
    return [(b.get("volume") or 0.0) for b in bars]


def compute(da, now: str, ctx: dict) -> Signal:
    fut = _closes(da, NEAR, now, _WIN)
    spot = _closes(da, SPOT, now, _WIN)
    if len(fut) < 3 or len(spot) < 3:
        return Signal.no_data("futures_basis", f"no {NEAR}/{SPOT} bars as-of now")

    # align trailing overlap (both are 1m NIFTY-family from the same capture grid)
    n = min(len(fut), len(spot))
    fut, spot = fut[-n:], spot[-n:]
    basis = [f - s for f, s in zip(fut, spot)]      # points
    basis_now = basis[-1]
    spot_now = spot[-1] or 1.0
    basis_pct = basis_now / spot_now * 100.0

    # level vs its own recent norm
    if len(basis) >= 20:
        mu = statistics.fmean(basis)
        sd = statistics.pstdev(basis) or 1e-6
        z_level = (basis_now - mu) / sd
    else:
        z_level = 0.0

    # premium trend over ~30 bars (widening vs narrowing)
    look = min(30, n - 1)
    d_basis = basis_now - basis[-1 - look]

    parts = [
        (squash(z_level, scale=1.5), 0.5),      # basis vs its norm
        (squash(d_basis, scale=5.0), 0.3),      # premium expanding/compressing (pts)
    ]

    # calendar spread far - near (carry / term structure)
    far = _closes(da, FAR, now, 5)
    cal = (far[-1] - fut[-1]) if far else None
    if cal is not None:
        parts.append((squash(cal, scale=25.0), 0.2))

    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)

    # discount override: future below spot = risk-off / hedging pressure
    discount = basis_now < 0
    if discount:
        score = clamp(min(score, -0.2) - 0.3 * min(1.0, abs(basis_pct) / 0.3))

    # volume confirmation -> confidence only
    v = _vols(da, NEAR, now, 60)
    vol_ok = len(v) >= 20 and sum(v[-10:]) > 0
    mag = max(abs(z_level), abs(d_basis) / 5.0)
    confidence = clamp(
        0.30 + (0.15 if cal is not None else 0.0) + min(0.25, mag * 0.10) + (0.10 if vol_ok else 0.0),
        0.0, 0.85,
    )

    detail = {
        "basis_pts": round(basis_now, 2),
        "basis_pct": round(basis_pct, 4),
        "basis_z": round(z_level, 2),
        "basis_trend_30m_pts": round(d_basis, 2),
        "calendar_far_minus_near_pts": round(cal, 2) if cal is not None else None,
        "regime": "DISCOUNT (risk-off)" if discount else "PREMIUM",
        "convention": "premium expanding = bullish; discount = bearish",
        "note": "no OI in schema -> basis/term-structure only; volume feeds confidence",
    }
    return Signal("futures_basis", score, confidence, "PRIOR", detail=detail)
