"""
strategy_framework/signals/rel_volume.py
========================================
Relative-volume-confirmed momentum — is the move backed by participation?

A price move on heavy volume means conviction; the same move on light volume is
noise that tends to fade. This signal takes NIFTY's recent DIRECTION (from price,
which is valid even though the index itself has no volume) and scales it by
RELATIVE VOLUME.

    Because the NIFTY *index* carries no volume, "market volume" is ESTIMATED from
    its CONSTITUENTS: the aggregate traded volume of the available 50-stock members,
    recent window vs the prior baseline window.

    The constituent volume is INDEX-WEIGHTED (a surge in a heavyweight counts more
    than the same surge in a 0.4%-weight name), via the shared
    index_volume.per_bar_index_volume:

        index_volumeₜ = Σᵢ ( wᵢ × volumeᵢ,ₜ )

        rel_vol = mean(index_volume, last n) / mean(index_volume, prior n)
                = Σᵢ wᵢ·mean(volᵢ, last n) / Σᵢ wᵢ·mean(volᵢ, prior n)

    score   = squash(recent NIFTY return) × participation_boost(rel_vol)

    The UNWEIGHTED ratio Σᵢ mean(volᵢ,·) is also computed, but only reported as
    detail["rel_volume_unweighted"] for divergence comparison — it does NOT feed
    the score. (An earlier version of this docstring showed the unweighted form as
    the score input; that was wrong — the score has always used the weighted one.)

so a rally on 1.5× normal constituent volume reads strongly bullish, the same rally
on 0.6× volume reads weakly. + = up on participation, − = down on participation.

Falls back to plain momentum (low confidence) if constituent volume can't be
estimated; never returns a misleading score.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp


def _rel_volume_from_constituents(da, ts_list, n: int):
    """Recent-window vs prior-baseline volume ratio, INDEX-WEIGHTED (a surge in a
    heavyweight counts more) and unweighted → (rel_vol_weighted, rel_vol_unweighted,
    n_used). Index volume comes from the shared per_bar_index_volume (single source
    of truth); mean over bars of Σ(w·vol) equals Σ(w·mean(vol)), so this matches the
    old per-stock aggregation exactly."""
    from .index_volume import per_bar_index_volume
    iv, uv, used = per_bar_index_volume(da, ts_list)
    if used < 3 or len(iv) < n + 2:
        return None, None, used

    def _ratio(arr):
        if arr.sum() <= 0:
            return None
        recent = float(arr[-n:].mean())
        base = float(arr[-2 * n:-n].mean()) if len(arr) >= 2 * n else float(arr[:-n].mean() or recent)
        return (recent / base) if base > 0 else None

    return _ratio(iv), _ratio(uv), used


def compute(da, now: str, ctx: dict, n: int | None = None) -> Signal:
    # Window comes from the shared MomentumWindow via ctx (explicit `n` still wins,
    # for tests/sweeps). The tanh scale moves with it as base×√(n/ref) so a longer
    # window improves signal-to-noise WITHOUT making the signal read hotter.
    mom = ctx.get("momentum")
    n = int(n if n is not None else (ctx.get("lookback_bars") or 15))
    scale = mom.scale_for("rel_volume", n) if mom else 0.12 * (n / 15.0) ** 0.5

    bars = da.bars("NIFTY", "1m", end=now, limit=2 * n + 5)
    if len(bars) < n + 3:
        return Signal("rel_volume", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"n_bars": len(bars)})
    c = np.array([b["close"] for b in bars], float)
    recent_ret = (c[-1] / c[-n - 1] - 1.0) * 100.0 if len(c) > n else (c[-1] / c[0] - 1.0) * 100.0

    rel_w, rel_u, n_used = _rel_volume_from_constituents(da, [b["ts"] for b in bars], n)
    vol_ok = rel_w is not None
    rel_vol = rel_w if vol_ok else 1.0                 # index-weighted drives the boost

    # participation boost: 1.0× volume → ~1; 2× → ~1.6; 0.5× → ~0.7
    boost = clamp(0.4 + 0.6 * rel_vol, 0.3, 1.6)
    score = clamp(squash(recent_ret, scale=scale) * boost)

    confidence = clamp((0.30 + 0.30 * min(1.0, abs(recent_ret) * 4)
                        + 0.20 * clamp(rel_vol - 1.0, 0.0, 1.0))
                       * (1.0 if vol_ok else 0.5), 0.0, 0.85)

    return Signal("rel_volume", score, confidence, "PRIOR",
                  detail={"lookback_bars": n, "tanh_scale": round(scale, 4),
                          "recent_ret_pct": round(float(recent_ret), 3),
                          "rel_volume_weighted": round(float(rel_vol), 3),      # index-weighted (boost)
                          "rel_volume_unweighted": round(float(rel_u), 3) if rel_u is not None else None,
                          "participation_boost": round(float(boost), 3),
                          "vol_source": f"constituents ({n_used})" if vol_ok else "unavailable",
                          "convention": "move on high (weighted) constituent-volume = conviction"})
