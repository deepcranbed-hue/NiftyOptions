"""
strategy_framework/signals/bundle.py
====================================
Evaluate every signal family as-of one decision timestamp.

This is the single entry point the strategy constructor and the backtest call.
It loads the chain snapshot once (backward as-of, D-MA-01), passes it to the
signal modules in `ctx`, and returns a SignalBundle. No signal here ever sees a
bar or a capture stamped after `now`.
"""
from __future__ import annotations
from .base import SignalBundle
from .data_access import DataAccess, days_to_expiry
from . import registry as _registry   # the single roster source; drives the loop below


def evaluate(db_path: str, now: str, expiry: str,
             veto_days: float = 1.0, bar_cache=None, momentum=None) -> SignalBundle:
    da = DataAccess(db_path, bar_cache=bar_cache)
    chain = da.chain_as_of(now, expiry)
    spot = chain.spot if chain else None
    dte = days_to_expiry(now, expiry)
    # The shared momentum window (config/settings.MomentumWindow) travels in ctx so
    # every price-return signal measures the SAME lookback and derives its tanh
    # scale from it. One knob, set in config, honoured everywhere — never a
    # per-signal literal. See CLAUDE.md (DRY) / MomentumWindow docstring.
    from ..config.settings import FrameworkConfig as _FC
    _mom = (momentum or _FC().momentum)
    ctx = {"chain": chain, "expiry": expiry, "dte_days": dte,
           "vix": (chain.vix if chain else da.latest_vix(now)),
           "momentum": _mom, "lookback_bars": _mom.bars()}

    # ATM straddle premium ≈ the market's priced expected move to expiry — a live,
    # chain-derived alternative to the VIX estimate for the regime's expected move.
    atm_straddle = None
    atm_iv = None
    if chain:
        atm = chain.atm_strike()
        c = chain.call_ltp.get(atm, 0.0) or 0.0
        p = chain.put_ltp.get(atm, 0.0) or 0.0
        if c > 0 and p > 0:
            atm_straddle = float(c + p)
        # Chain-native ATM implied vol, inverted from the SAME LTPs at the SAME
        # expiry (bs.py — the one BS implementation). This is the expected-move
        # fallback when the straddle is unavailable, e.g. one side prints below
        # intrinsic on a stale untraded strike so `c > 0 and p > 0` fails.
        #
        # It replaces the old VIX fallback, which was wrong twice over (D-SC-04):
        # INDIAVIX is a 30-day constant-maturity, whole-smile measure, so using it
        # for a 4-day expiry is a maturity AND strike-weighting mismatch — measured
        # over 21,708 captures the straddle/VIX ratio swings 0.861 (4 DTE) to 1.205
        # (1 DTE) with the term structure. And `chain.vix` reads captures.vix, a
        # constant 12.0 placeholder, so that branch returned a fabricated number.
        # ATM IV tracks the straddle to 0.3% (median straddle/atm_iv = 1.0030).
        from ..bs import implied_vol as _iv
        T = max(dte / 365.0, 1e-5)
        _ivs = [v for v in (_iv(c, chain.spot, atm, T, call=True) if c > 0 else None,
                            _iv(p, chain.spot, atm, T, call=False) if p > 0 else None)
                if v is not None]
        if _ivs:
            atm_iv = float(sum(_ivs) / len(_ivs))

    bundle = SignalBundle(now=now, spot=spot or 0.0, context={
        "expiry": expiry, "dte_days": round(dte, 4),
        "vix": ctx["vix"], "atm_straddle_pts": atm_straddle,
        "atm_iv": atm_iv,
        "capture_ts": chain.ts if chain else None,
        "capture_id": chain.capture_id if chain else None,
        # stamped so a cached score can never be mistaken for one computed at a
        # different window (see features/store.py invalidation)
        "lookback_bars": _mom.bars()})

    # Evaluate EVERY registered signal (directional votes + gates + risk overlays)
    # by iterating the single roster. Adding a signal is one SignalSpec row in
    # signals/registry.py — no edit here. earnings_events is the one signal that
    # takes the veto window.
    for spec in _registry.REGISTRY:
        if spec.name == "earnings_events":
            bundle.add(spec.compute(da, now, ctx, veto_days=veto_days))
        else:
            bundle.add(spec.compute(da, now, ctx))
    return bundle
