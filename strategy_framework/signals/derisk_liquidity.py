"""
strategy_framework/signals/derisk_liquidity.py
==============================================
Liquidity-driven DE-RISK detector  ("max-drawdown insurance" trigger).

This is NOT a directional-blend signal — it is a RISK OVERLAY. It measures the
probability that the tape is in a broad, liquidity-driven de-risking event (a
deleveraging / dash-for-cash cascade) rather than an orderly sector rotation.
That is the exact regime that blows through a premium-seller's short strikes, so
when it fires the desk should HOLD A TAIL HEDGE (long OTM puts).

The distinguishing fingerprint — learned from 08-Jul-2026 (NIFTY −2.06%, crude
+6.8%, but gold −1.9% and silver −4.1%) — is HAVEN FAILURE: gold and silver sell
off *with* equities and cross-asset correlation collapses to ~1. In a normal
risk-off, gold RISES; when even havens are being sold, it is forced liquidation,
and everything (energy included) goes down together.

Five components, each mapped to 0..1, blended into an `intensity` 0..1:
    haven_failure     gold/silver falling while equities fall   (THE tell, 0.30)
    breadth_collapse  fraction of constituents down / down >1%   (0.25)
    cross_asset_comove NIFTY, gold, silver, GIFT-NIFTY all down  (0.20)
    persistence       selling still accelerating (recent thrust) (0.15)
    usdinr_up         rupee weakening / dash for USD             (0.10)
Then damped by an equities-down gate so quiet / up days read ~0.

`intensity >= hedge_trigger` recommends a tail hedge; the size scales with it.
Returns cleanly with intensity 0 (status NO_DATA) when the cross-asset series
aren't in the DB copy, so it never disturbs anything.
"""
from __future__ import annotations
from .base import Signal, clamp
from ..config import constituents as K

HAVEN = ["GOLD", "SILVER"]
RISK_ASSETS = ["NIFTY", "GOLD", "SILVER", "GIFTNIFTY"]
_CROSS = set(HAVEN) | {"GIFTNIFTY", "USDINR", "CRUDEOIL", "COPPER"}


def _relu(x: float) -> float:
    return x if x > 0 else 0.0


def _c01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def _ret_pct(da, sym: str, now: str, lookback: int):
    """% change of `sym` over the last `lookback` 1m bars, as-of now (backward)."""
    bars = da.bars(sym, "1m", end=now, limit=lookback + 2)
    close = [b["close"] for b in bars if b["close"]]
    if len(close) < 3 or not close[0]:
        return None
    return (close[-1] / close[0] - 1.0) * 100.0


def _breadth(da, now: str, lookback: int):
    """Fraction of index constituents down, and fraction moving >=1%, as-of now."""
    syms = [s for s in K.symbols() if s != "NIFTY" and s not in _CROSS]
    rets = []
    for s in syms:
        r = _ret_pct(da, s, now, lookback)
        if r is not None:
            rets.append(r)
    if len(rets) < 8:
        return None
    n = len(rets)
    frac_down = sum(1 for r in rets if r < 0) / n
    frac_big = sum(1 for r in rets if abs(r) >= 1.0) / n
    avg = sum(rets) / n
    return {"n": n, "frac_down": frac_down, "frac_big": frac_big, "avg_ret": avg}


def compute(da, now: str, ctx: dict, session_bars: int = 375,
            thrust_bars: int = 30) -> Signal:
    eq = _ret_pct(da, "NIFTY", now, session_bars)
    if eq is None:
        return Signal.no_data("derisk_liquidity", "no NIFTY bars as-of now")

    gold = _ret_pct(da, "GOLD", now, session_bars)
    silver = _ret_pct(da, "SILVER", now, session_bars)
    gift = _ret_pct(da, "GIFTNIFTY", now, session_bars)
    usd = _ret_pct(da, "USDINR", now, session_bars)
    thrust = _ret_pct(da, "NIFTY", now, thrust_bars)
    br = _breadth(da, now, session_bars)

    eq_down = eq < 0

    # 1) HAVEN FAILURE — gated on equities down: how hard are the havens ALSO
    #    falling? (silver is ~2x as volatile as gold, so scale it down.)
    hf = 0.0
    if eq_down and (gold is not None or silver is not None):
        g = _c01(_relu(-(gold or 0.0)) / 2.0)
        s = _c01(_relu(-(silver or 0.0)) / 4.0)
        # if only one haven present, use it; else average
        present = [x for x in (gold, silver) if x is not None]
        hf = (g + s) / 2.0 if len(present) == 2 else (g if gold is not None else s)

    # 2) BREADTH COLLAPSE
    bc = 0.0
    if br is not None:
        bc = 0.6 * _c01((br["frac_down"] - 0.6) / 0.35) + \
             0.4 * _c01((br["frac_big"] - 0.3) / 0.5)

    # 3) CROSS-ASSET CO-MOVEMENT — how many risk assets are down together
    present_assets = {"NIFTY": eq, "GOLD": gold, "SILVER": silver, "GIFTNIFTY": gift}
    have = {k: v for k, v in present_assets.items() if v is not None}
    cac = 0.0
    if len(have) >= 2:
        frac = sum(1 for v in have.values() if v < 0) / len(have)
        cac = _c01((frac - 0.5) / 0.5)

    # 4) PERSISTENCE — is the selling still accelerating (recent thrust down)?
    persist = 0.0
    if eq_down and thrust is not None:
        persist = _c01(-thrust / 0.4)          # ~0.4% drop in last 30m -> full

    # 5) USDINR UP — rupee weakening / dash for USD
    usd_c = _c01((usd or 0.0) / 1.0) if usd is not None else 0.0

    intensity = (0.30 * hf + 0.25 * bc + 0.20 * cac + 0.15 * persist + 0.10 * usd_c)
    # equities-down gate: quiet / up days damp toward 0
    gate = _c01(-eq / 0.5)                      # -0.5% session -> full weight
    intensity = _c01(intensity * (0.15 + 0.85 * gate))

    trigger = float(ctx.get("derisk_trigger", 0.45))
    hedge = intensity >= trigger

    score = clamp(-intensity)                  # directional lean: bearish (overlay only)
    confidence = _c01(0.30 + 0.60 * intensity)

    detail = {
        "intensity": round(intensity, 3),
        "hedge_recommended": hedge,
        "trigger": trigger,
        "components": {
            "haven_failure": round(hf, 3),
            "breadth_collapse": round(bc, 3),
            "cross_asset_comove": round(cac, 3),
            "persistence": round(persist, 3),
            "usdinr_up": round(usd_c, 3),
        },
        "reads": {
            "nifty_session_pct": round(eq, 3),
            "nifty_thrust_30m_pct": round(thrust, 3) if thrust is not None else None,
            "gold_pct": round(gold, 3) if gold is not None else None,
            "silver_pct": round(silver, 3) if silver is not None else None,
            "usdinr_pct": round(usd, 3) if usd is not None else None,
            "giftnifty_pct": round(gift, 3) if gift is not None else None,
            "breadth": ({"frac_down": round(br["frac_down"], 2),
                         "frac_big": round(br["frac_big"], 2),
                         "avg_ret_pct": round(br["avg_ret"], 2), "n": br["n"]}
                        if br else None),
        },
        "note": "haven failure (gold/silver down WITH equities) = liquidation, not rotation",
    }
    return Signal("derisk_liquidity", score, confidence, "PRIOR", detail=detail)
