"""
strategy_framework/strategy/tape_regime.py
==========================================
Signal-INDEPENDENT tape regime: is PRICE itself trending or chopping?

This is deliberately separate from `strategy/regime.py`, which is a STRUCTURE-family
classifier (TREND_UP / RANGE → which option strategy to build) and partly keys off
the blended net score. For the momentum-vs-reversion GATE we need a detector that
looks only at price, so it can tell us WHEN to follow a directional signal (trend)
vs when following it would be buying-high/selling-low (chop → fade or stand aside).

The measure is Kaufman's EFFICIENCY RATIO over the last n prices:

    ER = |Pₜ − Pₜ₋ₙ|  /  Σ |Pᵢ − Pᵢ₋₁|

    ER → 1  price moved in a straight line              → TRENDING (momentum works)
    ER → 0  lots of back-and-forth, little net travel   → CHOPPY (momentum buys high)

Why ER and not the net score: ER is orthogonal to the signals — it can't be fooled by
a strong-but-wrong reading, and on the mean-reverting minute tape we measured, it is
exactly the quantity that separates "follow" days from "fade" days.
"""
from __future__ import annotations


def efficiency_ratio(prices: list[float], n: int | None = None) -> float | None:
    """Kaufman efficiency ratio over the last n prices (all if n is None). None when
    there are too few points or the path had zero total travel."""
    p = prices if n is None else prices[-(n + 1):]
    if len(p) < 3:
        return None
    net = abs(p[-1] - p[0])
    travel = sum(abs(p[i] - p[i - 1]) for i in range(1, len(p)))
    if travel <= 0:
        return None
    return net / travel


def classify_tape(prices: list[float], n: int = 8,
                  trend_thr: float = 0.45, chop_thr: float = 0.30) -> dict:
    """Classify the tape over the last n prices into trend / chop / neutral.

    trend_thr / chop_thr are on the efficiency ratio (0..1). Between them is
    'neutral' — no clear regime, so the gate should stand aside. Defaults are a
    reasonable PRIOR; calibrate against your data. Returns {regime, er}."""
    er = efficiency_ratio(prices, n)
    if er is None:
        return {"regime": "unknown", "er": None}
    if er >= trend_thr:
        regime = "trend"
    elif er <= chop_thr:
        regime = "chop"
    else:
        regime = "neutral"
    return {"regime": regime, "er": round(er, 3)}


def er_series(prices: list[float], n: int = 8) -> list[float | None]:
    """Efficiency ratio at each point, NO LOOKAHEAD (trailing window of n). None for
    points with too little history. Feeds the data-driven (median-split) regime cut."""
    return [efficiency_ratio(prices[max(0, i - n):i + 1], n=n) for i in range(len(prices))]


def split_trend_chop(er_values: list[float | None]) -> tuple[list[str | None], float]:
    """Two-way tape split at the MEDIAN of the (non-null) efficiency ratios — no fuzzy
    neutral band, and the cut sits where the data actually divides rather than at a
    guessed threshold. Returns (labels aligned to input, the median used). Points with
    no ER are labelled None (excluded downstream)."""
    import statistics as _st
    valid = [e for e in er_values if e is not None]
    med = _st.median(valid) if valid else 0.0
    labels = [None if e is None else ("trend" if e >= med else "chop") for e in er_values]
    return labels, med


def series_regime(prices: list[float], n: int = 8,
                  trend_thr: float = 0.45, chop_thr: float = 0.30) -> list[dict]:
    """Per-point tape regime for a price series, NO LOOKAHEAD: point i uses only
    prices[:i+1] (a trailing window of n). Points with too little history are
    'unknown'. Returns a list aligned to `prices`."""
    out = []
    for i in range(len(prices)):
        window = prices[max(0, i - n):i + 1]
        out.append(classify_tape(window, n=n, trend_thr=trend_thr, chop_thr=chop_thr))
    return out
