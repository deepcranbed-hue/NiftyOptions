"""
strategy_framework/signals/heavyweight_leadership_persistent.py
==============================================================
PERSISTENT heavyweight leadership — the signal-to-noise version of
`heavyweight_leadership`.

The raw signal thresholds a single cumulative weighted return, so a small,
oscillating leadership sits near zero and its buy/sell flips whenever the tape
wiggles. This one asks a different, more stable question:

    "Has the weighted leadership been steadily in one direction RELATIVE TO its own
     bar-to-bar noise?"

i.e. it scores the t-statistic (SNR) of the per-bar weighted-constituent leadership
return over the window, not its raw level:

    per-bar leadership_t = Σᵢ wᵢ·rᵢ,t / Σᵢ wᵢ          (free-float-weighted, per minute)
    z = mean(leadership) / ( std(leadership) / √n )    = mean·√n / std   (a t-stat)
    score = squash(z)

Sustained, low-noise leadership → large |z| → decisive score. Choppy leadership
that averages ~0 with high variance → z ≈ 0 → NEUTRAL, so it stops flipping on
noise. This is the same normalisation `technical_momentum` uses (thrust_z =
return/(vol·√n)); heavyweight_leadership is the one core signal that lacked it.

Two corroborations fold into confidence (not the sign): CONSISTENCY (fraction of
bars agreeing with the mean direction) and BREADTH (how many heavyweights, by
weight, moved in the leadership direction over the window — one name or eight).

Sign convention: + = heavyweights net leading UP. NO_DATA when too few constituent
bars. Candidate (weight 0) until it proves out — validated side-by-side with the
raw signal.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp
from ..config import constituents as K


def _per_bar_leadership(da, now: str, n: int):
    """Free-float-weighted per-bar constituent return series over the last ~n bars,
    aligned on NIFTY's 1m timestamps. Returns (series, n_syms_used, breadth_dir)."""
    ref = da.bars("NIFTY", "1m", end=now, limit=n + 5)
    if len(ref) < 5:
        return None, 0, None
    ts_list = [b["ts"] for b in ref]
    want = set(ts_list)
    acc = {t: 0.0 for t in ts_list}
    wsum = {t: 0.0 for t in ts_list}
    syms = sorted((set(da.available_symbols("1m")) & set(K.symbols())) - {"NIFTY"})
    cum_by_sym = {}          # window cumulative return per stock (for breadth)
    used = 0
    for s in syms:
        w = K.weight_of(s)
        bars = da.bars(s, "1m", end=now, start=ts_list[0], limit=n + 5)
        if len(bars) < 3:
            continue
        used += 1
        prev = None
        first = bars[0]["close"]
        for b in bars:
            t, c = b.get("ts"), b.get("close")
            if prev and prev > 0 and t in want:
                acc[t] += w * (c / prev - 1.0)
                wsum[t] += w
            prev = c
        if first and bars[-1]["close"]:
            cum_by_sym[s] = (bars[-1]["close"] / first - 1.0)
    series = [acc[t] / wsum[t] for t in ts_list if wsum[t] > 0]
    return (np.array(series, float) if series else None), used, cum_by_sym


def compute(da, now: str, ctx: dict, lookback: int | None = None) -> Signal:
    # shared momentum window (same knob as the other windowed signals)
    n = int(lookback if lookback is not None else (ctx.get("lookback_bars") or 30))
    n = max(n, 8)                                   # need enough bars for a stable t-stat
    series, n_syms, cum_by_sym = _per_bar_leadership(da, now, n)
    if series is None or len(series) < 5 or n_syms < 1:
        return Signal.no_data("heavyweight_leadership_persistent",
                              "not enough constituent bars for a persistence read")

    m = float(series.mean())
    v = float(series.std())
    n_eff = len(series)
    # t-statistic of "is leadership steadily directional" — mean over its own noise
    z = (m / (v / np.sqrt(n_eff))) if v > 1e-12 else 0.0
    score = clamp(squash(z, scale=2.0))             # t≈2 sustained → ~0.76

    # CONSISTENCY: fraction of bars whose sign agrees with the mean direction
    if m != 0:
        consistency = float(np.mean(np.sign(series) == np.sign(m)))
    else:
        consistency = 0.5
    # BREADTH: cap-weighted share of heavyweights leading the SAME way as the mean
    hv = {s: c for s, c in (cum_by_sym or {}).items() if s in K.HEAVYWEIGHTS}
    w_dir = w_tot = 0.0
    for s, c in hv.items():
        w = K.weight_of(s); w_tot += w
        if (c > 0) == (m > 0):
            w_dir += w
    breadth = (w_dir / w_tot) if w_tot > 0 else 0.5

    # confidence: strong |z| + consistent-through-time + broad participation + coverage
    coverage = clamp(n_syms / 30.0, 0.0, 1.0)
    confidence = clamp(0.20
                       + 0.35 * clamp(abs(z) / 2.0, 0.0, 1.0)
                       + 0.20 * (consistency - 0.5) * 2.0
                       + 0.15 * abs(breadth - 0.5) * 2.0
                       + 0.10 * coverage, 0.0, 0.95)

    return Signal("heavyweight_leadership_persistent", float(score), float(confidence), "PRIOR",
                  detail={"z_tstat": round(z, 3), "mean_ret_pct": round(m * 100, 4),
                          "vol_ret_pct": round(v * 100, 4), "consistency": round(consistency, 3),
                          "breadth_weighted": round(breadth, 3), "n_bars": n_eff,
                          "n_constituents": n_syms, "lookback_bars": n,
                          "convention": "z = mean/(vol/sqrt(n)) of weighted leadership; "
                                        "+ = heavyweights steadily leading UP; ~0 = choppy/neutral"})
