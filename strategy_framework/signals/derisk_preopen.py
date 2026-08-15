"""
strategy_framework/signals/derisk_preopen.py
=============================================
LEADING (pre-open) liquidity-derisk warning.

The companion `derisk_liquidity` signal is COINCIDENT — it reads the Indian cash
session as it falls, so by the time it arms, the drawdown has largely happened
and the puts are dear. This signal reads a DIFFERENT clock: the OVERNIGHT
cross-asset tape that moves while NSE is shut, so it can arm BEFORE the 09:15
open — when a tail hedge is still cheap.

Everything here is an overnight move as-of a PRE-OPEN timestamp (≈09:10 IST):
each instrument's latest bar ≤ now versus its previous-session close. No cash
constituent is read, so there is no look-ahead into the session it is trying to
predict.

    crude_shock    CRUDEOIL up overnight        — the causal trigger (0.25)
    gift_gap       GIFT-NIFTY down overnight     — leads the cash open (0.30)
    haven_selloff  GOLD/SILVER down overnight    — the liquidation tell (0.25)
    ndf_usd        USDINR up overnight           — dash for USD (0.20)
Blended, then damped by a risk-setup gate so a calm overnight reads ~0.

Sign/shape mirror `derisk_liquidity` (intensity 0..1, `hedge_recommended`) so the
same tail-hedge machinery consumes it. Returns NO_DATA cleanly when the overnight
cross-asset series aren't in the DB copy.
"""
from __future__ import annotations
from .base import Signal, clamp

OVERNIGHT = ["CRUDEOIL_MCX", "GIFTNIFTY", "GOLD", "SILVER", "USDINR"]


def _relu(x: float) -> float:
    return x if x > 0 else 0.0


def _c01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def _overnight_ret(da, sym: str, now: str):
    """% move of `sym` from its PREVIOUS-session close to the latest bar as-of now
    (the overnight gap in that instrument). All bars ts <= now — no look-ahead."""
    bars = da.bars(sym, "1m", end=now, limit=5000)
    close = [(b["ts"], b["close"]) for b in bars if b["close"]]
    if len(close) < 2:
        return None
    today = now[:10]
    latest = close[-1][1]
    prev = [c for t, c in close if t[:10] < today]
    if not prev or not prev[-1]:
        return None
    return (latest / prev[-1] - 1.0) * 100.0


_PRIOR_CLOSE_UTC = "10:00:00"   # 15:30 IST prior-session close


def _level_at(closes, ts_max: str):
    xs = [c for t, c in closes if t <= ts_max]
    return xs[-1] if xs else None


def _split_overnight(da, sym: str, now: str):
    """Split the overnight move at the prior Indian close (10:00Z = 15:30 IST):
      at_close_pct = the instrument's move DURING the prior session (already visible
                     by the 3:30 close), and
      overnight_pct = its move AFTER that close up to pre-open (emerged overnight).
    This is what distinguishes 'hedgeable at the prior close' from 'GIFT-only overnight'."""
    bars = da.bars(sym, "1m", end=now, limit=8000)
    closes = [(b["ts"], b["close"]) for b in bars if b["close"]]
    if len(closes) < 2:
        return None
    today = now[:10]
    dates = sorted({t[:10] for t, _ in closes if t[:10] < today})
    if not dates:
        return None
    prior = dates[-1]
    lvl_preopen = closes[-1][1]
    lvl_prior_close = _level_at(closes, f"{prior}T{_PRIOR_CLOSE_UTC}Z")
    overnight_pct = ((lvl_preopen / lvl_prior_close - 1.0) * 100.0) if lvl_prior_close else None
    at_close_pct = None
    if len(dates) >= 2 and lvl_prior_close:
        lvl_preprior = _level_at(closes, f"{dates[-2]}T{_PRIOR_CLOSE_UTC}Z")
        if lvl_preprior:
            at_close_pct = (lvl_prior_close / lvl_preprior - 1.0) * 100.0
    return {"at_close_pct": round(at_close_pct, 3) if at_close_pct is not None else None,
            "overnight_pct": round(overnight_pct, 3) if overnight_pct is not None else None,
            "prior_close_date": prior}


def _classify_tell_timing(crude_s, gift_s):
    """Where did the tell appear — by the prior close, overnight, or both?

    Decided by WHICH WINDOW CARRIES THE BULK of the move (dominance), not by a small
    drift crossing a low bar — so a minor pre-close wobble (e.g. crude +0.9%) followed
    by a big overnight spike (+4.5%) correctly reads 'overnight', not 'both'.
    """
    def total(s):
        if not s:
            return 0.0
        return abs(s.get("at_close_pct") or 0.0) + abs(s.get("overnight_pct") or 0.0)
    # driver = the instrument that actually moved the most across the whole window
    driver = crude_s if total(crude_s) >= total(gift_s) else gift_s
    if not driver or total(driver) < 0.8:            # nothing meaningful moved
        return "none", "no meaningful overnight tell in crude / GIFT"
    ac = abs(driver.get("at_close_pct") or 0.0)
    on = abs(driver.get("overnight_pct") or 0.0)
    tot = ac + on
    share_on = on / tot if tot else 0.0
    if share_on >= 0.70:
        return "overnight", ("the bulk of the move emerged AFTER the prior close (only a minor drift by "
                             "3:30) — GIFT Nifty overnight or a standing hedge were the only routes")
    if share_on <= 0.30:
        return "at_close", "already visible by the prior 3:30 close — a hedge carried into the close would have caught it"
    return "both", "a genuine build had started by the prior close and then worsened overnight — carry a hedge into the close, top up via GIFT overnight"


def compute(da, now: str, ctx: dict) -> Signal:
    crude = _overnight_ret(da, "CRUDEOIL_MCX", now)
    gift = _overnight_ret(da, "GIFTNIFTY", now)
    gold = _overnight_ret(da, "GOLD", now)
    silver = _overnight_ret(da, "SILVER", now)
    usd = _overnight_ret(da, "USDINR", now)

    if all(x is None for x in (crude, gift, gold, silver, usd)):
        return Signal.no_data("derisk_preopen", "no overnight cross-asset bars as-of now")

    # component scores (0..1)
    crude_shock = _c01(_relu(crude or 0.0) / 4.0)               # +4% crude -> full
    gift_gap = _c01(_relu(-(gift or 0.0)) / 1.0)                # -1% GIFT  -> full
    haven = 0.5 * _c01(_relu(-(gold or 0.0)) / 1.5) + \
            0.5 * _c01(_relu(-(silver or 0.0)) / 3.0)           # havens sold overnight
    ndf = _c01(_relu(usd or 0.0) / 0.5)                         # +0.5% USDINR -> full

    intensity_raw = 0.25 * crude_shock + 0.30 * gift_gap + 0.25 * haven + 0.20 * ndf
    # risk-setup gate: needs a genuine overnight risk-off (GIFT down / crude spike /
    # rupee weak). A quiet or risk-ON overnight damps toward 0.
    risk_setup = _c01(max((_relu(-(gift or 0.0)) / 0.5),
                          (_relu(crude or 0.0) / 3.0),
                          (_relu(usd or 0.0) / 0.5)))
    intensity = _c01(intensity_raw * (0.15 + 0.85 * risk_setup))

    trigger = float(ctx.get("derisk_trigger", 0.45))
    hedge = intensity >= trigger

    # WHEN did the tell appear: at the prior close (hedgeable then) vs overnight (GIFT-only)?
    crude_split = _split_overnight(da, "CRUDEOIL_MCX", now)
    gift_split = _split_overnight(da, "GIFTNIFTY", now)
    tell_kind, tell_note = _classify_tell_timing(crude_split, gift_split)

    # Data-quality guard, two cases:
    #  (a) STALE: the newest crude/GIFT bar predates the session we're scoring — the sync
    #      didn't run for this date, so "latest" and "prior close" resolve to the SAME old
    #      candle and the move is a spurious 0.0. Report when the data actually ends.
    #  (b) FLAT/absent: crude AND GIFT both ~0 — flat or not captured.
    def _latest_ts(sym):
        bs = da.bars(sym, "1m", end=now, limit=5000)
        return bs[-1]["ts"] if bs else None
    last_ts = max([t for t in (_latest_ts("CRUDEOIL_MCX"), _latest_ts("GIFTNIFTY")) if t], default=None)
    stale = bool(last_ts and last_ts[:10] < now[:10])
    overnight_insufficient = stale or ((crude in (None, 0.0)) and (gift in (None, 0.0)))
    dq_reason = (("no overnight bars for %s — sync ends %s" % (now[:10], last_ts)) if stale
                 else ("crude/GIFT overnight ~flat or not captured" if overnight_insufficient else "ok"))

    detail = {
        "intensity": round(intensity, 3),
        "hedge_recommended": hedge,
        "trigger": trigger,
        "lead": "pre-open (overnight cross-asset, before 09:15)",
        "components": {
            "crude_shock": round(crude_shock, 3),
            "gift_gap": round(gift_gap, 3),
            "haven_selloff": round(haven, 3),
            "ndf_usd": round(ndf, 3),
        },
        "reads": {
            "crude_overnight_pct": round(crude, 3) if crude is not None else None,
            "giftnifty_overnight_pct": round(gift, 3) if gift is not None else None,
            "gold_overnight_pct": round(gold, 3) if gold is not None else None,
            "silver_overnight_pct": round(silver, 3) if silver is not None else None,
            "usdinr_overnight_pct": round(usd, 3) if usd is not None else None,
        },
        "window_split": {"crude": crude_split, "gift": gift_split},
        "tell_timing": tell_kind,
        "tell_timing_note": tell_note,
        "data_quality": "insufficient" if overnight_insufficient else "ok",
        "data_quality_reason": dq_reason,
        "last_data_ts": last_ts,
        "note": "overnight crude spike + GIFT gap + haven selling = derisk setup BEFORE the open",
    }
    return Signal("derisk_preopen", clamp(-intensity),
                  _c01(0.30 + 0.60 * intensity), "PRIOR", detail=detail)
