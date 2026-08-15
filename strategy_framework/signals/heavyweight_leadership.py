"""
strategy_framework/signals/heavyweight_leadership.py
===================================================
Index direction from its own constituents — the "ground truth" tape.

The index is a weight-sum of its members: index_return ~= Σ wᵢ·rᵢ. News or a
government-spending / big-deal catalyst only moves the index if it hits a
*heavyweight* (Reliance, HDFC Bank, ICICI, Infosys, ...). So this signal:

  1. reads 1m bars for every constituent we have data for (as-of `now`);
  2. computes each name's return + volume surge over the lookback;
  3. forms the free-float-weighted directional contribution (the leadership read);
  4. measures CONCENTRATION — is the move driven by a few heavyweights on heavy
     volume (a real, tradeable lead) or is it broad-but-shallow / offsetting;
  5. reports the leading SECTORS.

`score` is the weighted direction (+ bullish). Volume surge on the leaders raises
confidence — that's the "deal/volume/spending trigger in a heavyweight" the user
asked to exploit. `detail["concentration"]` and `detail["breadth"]` are consumed
by the regime classifier to separate TREND from RANGE.
"""
from __future__ import annotations
import numpy as np
from collections import defaultdict
from .base import Signal, squash, clamp
from ..config import constituents as K


def _stock_read(da, sym, now, lookback):
    bars = da.bars(sym, "1m", end=now, limit=lookback + 5)
    if len(bars) < 3:
        return None
    c = np.array([b["close"] for b in bars], float)
    v = np.array([b["volume"] or 0.0 for b in bars], float)
    ret = float(c[-1] / c[0] - 1.0)
    n = max(len(v) // 2, 1)
    vol_surge = (v[-n:].mean() / (v[:n].mean() + 1e-9)) if v.sum() > 0 else 1.0
    return {"ret": ret, "vol_surge": float(vol_surge), "n": len(bars)}


def compute(da, now: str, ctx: dict, lookback: int | None = None) -> Signal:
    # Shared momentum window from ctx; scale = base×√(n/ref). `base_scale` is set so
    # that at the historical 60-bar window this reproduces the old scale of 0.60.
    mom = ctx.get("momentum")
    lookback = int(lookback if lookback is not None else (ctx.get("lookback_bars") or 60))
    scale = (mom.scale_for("heavyweight_leadership", lookback) if mom
             else 0.60 * (lookback / 60.0) ** 0.5)

    have = set(da.available_symbols("1m")) & set(K.symbols())
    have.discard("NIFTY")
    if not have:
        return Signal.no_data("heavyweight_leadership", "no constituent bars")

    contribs = {}          # symbol -> weighted contribution (w% * ret)
    reads = {}
    sector_contrib = defaultdict(float)
    total_w = 0.0
    hv_vol_surge = []      # volume surge among heavyweights that are moving

    for sym in have:
        r = _stock_read(da, sym, now, lookback)
        if r is None:
            continue
        w = K.weight_of(sym)
        contribs[sym] = w * r["ret"]
        reads[sym] = r
        total_w += w
        sector_contrib[K.sector_of(sym)] += w * r["ret"]
        if sym in K.HEAVYWEIGHTS and abs(r["ret"]) > 1e-4:
            hv_vol_surge.append(r["vol_surge"])

    if not contribs or total_w <= 0:
        return Signal.no_data("heavyweight_leadership", "no weighted constituents")

    # --- weighted directional contribution (normalise to covered weight) ---
    weighted_ret = sum(contribs.values()) / total_w        # ~ index return proxy
    score = squash(weighted_ret * 100.0, scale=scale)      # % -> [-1,1]

    # --- concentration: how much of the |move| comes from the top few names? -
    abs_contrib = {k: abs(v) for k, v in contribs.items()}
    tot_abs = sum(abs_contrib.values()) + 1e-12
    top = sorted(abs_contrib.values(), reverse=True)
    concentration = sum(top[:3]) / tot_abs                 # 1 = one name drives all

    # --- breadth: signed agreement across names ----------------------------
    signs = [np.sign(v) for v in contribs.values() if abs(v) > 1e-9]
    breadth = float(np.mean(signs)) if signs else 0.0      # +1 all up, -1 all down

    # --- leaders & laggards -------------------------------------------------
    leaders = sorted(contribs.items(), key=lambda x: -abs(x[1]))[:5]
    sector_tilt = sorted(sector_contrib.items(), key=lambda x: -abs(x[1]))[:4]

    # --- volume corroboration among heavyweights ---------------------------
    vol_conf = 0.5
    if hv_vol_surge:
        avg_surge = float(np.mean(hv_vol_surge))
        vol_conf = clamp(0.4 + 0.3 * (avg_surge - 1.0), 0.2, 0.95)

    coverage = clamp(total_w / 90.0, 0.0, 1.0)             # ~% index weight covered
    # concentrated + volume-backed + broad-agreeing -> high confidence
    confidence = clamp(0.25 + 0.35 * coverage + 0.2 * vol_conf +
                       0.2 * abs(breadth), 0.0, 0.95)

    return Signal("heavyweight_leadership", float(score), float(confidence), "PRIOR",
                  detail={"lookback_bars": lookback, "tanh_scale": round(scale, 4),
                          "weighted_ret_pct": round(weighted_ret * 100, 4),
                          "concentration": round(concentration, 3),
                          "breadth": round(breadth, 3),
                          "coverage_weight_pct": round(total_w, 1),
                          "hv_vol_surge": round(float(np.mean(hv_vol_surge)), 2) if hv_vol_surge else None,
                          "leaders": [{"sym": s, "contrib": round(c, 4),
                                       "ret_pct": round(reads[s]["ret"] * 100, 3),
                                       "vol_surge": round(reads[s]["vol_surge"], 2)}
                                      for s, c in leaders],
                          "sector_tilt": [{"sector": s, "contrib": round(c, 4)}
                                          for s, c in sector_tilt],
                          "n_constituents": len(contribs)})
