"""
strategy_framework/signals/futures_calendar.py
===============================================
NIFTY futures TERM-STRUCTURE / roll-pressure signal — a standalone candidate,
separate from `futures_basis`, so the Horizon map / Attribution can decide which
(if either) earns a non-zero weight. "Evaluate before you trust."

Inputs (from `price_bars`, 1m, backward as-of, ts <= now — D-MA-01):
    NIFTY_FUT_1   near-month future  (e.g. 30-Jul)   exchange NFO
    NIFTY_FUT_2   far-month future   (e.g. 27-Aug)   exchange NFO

What it reads:
  * CALENDAR SPREAD = far − near (points). Index futures normally sit in mild
    CONTANGO (far > near, more carry). A WIDENING spread = benign/positive-carry
    regime (mild bullish); a NARROWING spread or BACKWARDATION (far < near) =
    near-dated stress / hedging demand => bearish.
  * SPREAD TREND over ~30m — is the term structure steepening or flattening now.
  * ROLL PRESSURE (volume proxy) — as 30-Jul expiry nears, the share of volume
    migrating from near to far. No OI in the schema, so this is a VOLUME proxy:
    it feeds CONFIDENCE and context only, never direction (rollover tells you
    positions are carried, not which way they lean).

Sign convention: score in [-1, +1], + = bullish NIFTY. Overlaps slightly with the
calendar sub-term inside `futures_basis` — keep that in mind if both ever get
non-zero weight (don't double-count). Returns NO_DATA cleanly when either future
series is absent. PRIOR until calibrated.
"""
from __future__ import annotations
import statistics
from .base import Signal, squash, clamp

NEAR = "NIFTY_FUT_1"
FAR = "NIFTY_FUT_2"
_WIN = 375   # ~1 trading day of 1m bars for the spread norm


def _rows(da, sym: str, now: str, limit: int) -> list:
    return da.bars(sym, "1m", end=now, limit=limit)


def compute(da, now: str, ctx: dict) -> Signal:
    near = _rows(da, NEAR, now, _WIN)
    far = _rows(da, FAR, now, _WIN)
    nc = [b["close"] for b in near if b.get("close")]
    fc = [b["close"] for b in far if b.get("close")]
    if len(nc) < 3 or len(fc) < 3:
        return Signal.no_data("futures_calendar", f"no {NEAR}/{FAR} bars as-of now")

    n = min(len(nc), len(fc))
    nc, fc = nc[-n:], fc[-n:]
    spread = [f - c for f, c in zip(fc, nc)]        # far - near, points
    spread_now = spread[-1]
    near_now = nc[-1] or 1.0
    spread_pct = spread_now / near_now * 100.0

    # spread vs its own recent norm
    if len(spread) >= 20:
        mu = statistics.fmean(spread)
        sd = statistics.pstdev(spread) or 1e-6
        z_level = (spread_now - mu) / sd
    else:
        z_level = 0.0

    # steepening / flattening over ~30 bars
    look = min(30, n - 1)
    d_spread = spread_now - spread[-1 - look]

    parts = [
        (squash(z_level, scale=1.5), 0.6),      # spread vs norm (steep = benign/bullish)
        (squash(d_spread, scale=8.0), 0.4),     # steepening (+) vs flattening (-)
    ]
    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)

    # backwardation override: far below near = near-dated stress => bearish
    backwardation = spread_now < 0
    if backwardation:
        score = clamp(min(score, -0.2) - 0.3 * min(1.0, abs(spread_pct) / 0.5))

    # roll pressure (VOLUME proxy) — confidence/context only, not direction
    nv = [(b.get("volume") or 0.0) for b in near[-30:]]
    fv = [(b.get("volume") or 0.0) for b in far[-30:]]
    tot = sum(nv) + sum(fv)
    far_vol_share = (sum(fv) / tot) if tot > 0 else None
    vol_ok = tot > 0

    mag = max(abs(z_level), abs(d_spread) / 8.0)
    confidence = clamp(0.30 + min(0.30, mag * 0.12) + (0.12 if vol_ok else 0.0), 0.0, 0.80)

    detail = {
        "calendar_spread_pts": round(spread_now, 2),
        "calendar_spread_pct": round(spread_pct, 4),
        "spread_z": round(z_level, 2),
        "spread_trend_30m_pts": round(d_spread, 2),
        "far_volume_share": round(far_vol_share, 3) if far_vol_share is not None else None,
        "structure": "BACKWARDATION (stress)" if backwardation else "CONTANGO",
        "convention": "steepening contango = bullish; backwardation = bearish",
        "note": "no OI -> roll pressure is a volume proxy (confidence only, not direction)",
    }
    return Signal("futures_calendar", score, confidence, "PRIOR", detail=detail)
