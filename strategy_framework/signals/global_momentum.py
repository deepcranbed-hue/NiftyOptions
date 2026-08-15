"""
strategy_framework/signals/global_momentum.py
=============================================
Cross-asset / global momentum and forex-flow tilt.

NIFTY does not trade in a vacuum: metals and USDINR lead risk appetite and
foreign-flow direction. This signal reads the same cross-asset cockpit the
Global Cues engine uses and folds in overnight index momentum.

Components (all as-of `now`, backward-joined):
  * Metals barometer : copper up + gold down  => growth-on (bullish equities);
                        gold up  + copper down => fear-on (bearish).
  * USDINR (forex)   : rupee weakness (USDINR up) => FII outflow pressure
                        (bearish for NIFTY); rupee strength => inflow (bullish).
                        This is the D-GC-04 inverse-target read for FII flows.
  * Overnight gap /   : index drift since prior session close, a momentum carry.
    session drift

If the live Global Cues cache (global_cues_cache.json) is present and fresh, we
prefer its net verdicts; otherwise we derive the tilt from cross-asset 1m bars.
"""
from __future__ import annotations
import os, json
import numpy as np
from .base import Signal, squash, clamp

_CUES_CACHE = os.path.join(
    os.path.dirname(__file__), "..", "..", "global_cues_cache.json")


def _pct_change_from_bars(da, symbol: str, now: str, lookback: int = 60):
    bars = da.bars(symbol, "1m", end=now, limit=lookback + 5)
    if len(bars) < 3:
        return None, 0
    close = np.array([b["close"] for b in bars], float)
    return float((close[-1] / close[0] - 1.0) * 100.0), len(bars)


_CUES_MEMO: dict = {"mtime": None, "data": None}


def _from_cache() -> dict | None:
    """Read the cues cache, memoised by file mtime — re-parse only when the file
    actually changes, instead of opening + json.loading it on every signal call."""
    p = os.path.abspath(_CUES_CACHE)
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return None
    if _CUES_MEMO["mtime"] == mtime:
        return _CUES_MEMO["data"]
    data = None
    try:
        with open(p) as f:
            j = json.load(f)
        data = j if j.get("success") else None
    except Exception:
        data = None
    _CUES_MEMO["mtime"] = mtime; _CUES_MEMO["data"] = data
    return data


def compute(da, now: str, ctx: dict) -> Signal:
    detail: dict = {}
    parts: list[tuple[float, float]] = []   # (score, weight)

    # ---- 1. Prefer live cues cache when available -------------------------
    cache = _from_cache()
    if cache:
        verdicts = cache.get("net_verdicts", {})
        # RISK_APPETITE net verdict maps most directly onto index direction.
        ra = verdicts.get("RISK_APPETITE")
        fii = verdicts.get("BROAD_FII")
        if isinstance(ra, (int, float)):
            parts.append((squash(ra, 1.0), 0.5)); detail["risk_appetite"] = ra
        if isinstance(fii, (int, float)):
            parts.append((squash(fii, 1.0), 0.3)); detail["broad_fii"] = fii
        mb = cache.get("metals_barometer")
        if isinstance(mb, dict) and "score" in mb:
            parts.append((squash(mb["score"], 1.0), 0.2)); detail["metals_score"] = mb["score"]

    # ---- 2. Fall back to / corroborate with cross-asset 1m bars -----------
    if not parts:
        copper, _ = _pct_change_from_bars(da, "COPPER", now)
        gold, _ = _pct_change_from_bars(da, "GOLD", now)
        usdinr, _ = _pct_change_from_bars(da, "USDINR", now)

        if copper is not None and gold is not None:
            # growth-on = copper outperforming gold
            metals = squash(copper - gold, scale=1.0)
            parts.append((metals, 0.5)); detail.update(copper_pct=copper, gold_pct=gold)
        if usdinr is not None:
            # rupee weaker (USDINR up) -> bearish NIFTY (inverse target, D-GC-04)
            forex = squash(-usdinr, scale=0.5)
            parts.append((forex, 0.5)); detail["usdinr_pct"] = usdinr

    # ---- 3. Overnight / session index drift (momentum carry) --------------
    drift, n = _pct_change_from_bars(da, "NIFTY", now, lookback=375)
    if drift is not None:
        parts.append((squash(drift, scale=0.6), 0.3)); detail["nifty_drift_pct"] = drift

    if not parts:
        return Signal.no_data("global_momentum", "no cross-asset bars or cues cache")

    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)
    # confidence: more independent corroborating streams -> higher.
    n_streams = len(parts)
    confidence = clamp(0.30 + 0.18 * n_streams + (0.1 if cache else 0.0), 0.0, 0.9)

    return Signal("global_momentum", score, confidence, "PRIOR",
                  detail={**detail, "n_streams": n_streams,
                          "source": "cues_cache" if cache else "bars"})
