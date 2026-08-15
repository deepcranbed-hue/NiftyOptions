"""
strategy_framework/strategy/suggester.py
========================================
Top-level API: given a decision time + expiry, return a full strategy suggestion.

    suggest(cfg, now, expiry) -> Suggestion

A Suggestion bundles the signal evidence, the directional Decision, and (if the
gates pass and a tradeable structure exists) the concrete priced Structure. This
is what both a live UI panel and the backtest consume.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ..signals import bundle as signal_bundle
from ..signals.data_access import DataAccess, days_to_expiry
from . import directional, constructor, candidates as candidates_mod


@dataclass
class Suggestion:
    now: str
    expiry: str
    decision: dict
    structure: Optional[dict]
    signals: dict
    tradeable: bool
    note: str = ""
    candidates: list = field(default_factory=list)
    hedge: Optional[dict] = None          # tail-hedge / drawdown-insurance overlay

    def as_dict(self) -> dict:
        return {"now": self.now, "expiry": self.expiry, "tradeable": self.tradeable,
                "note": self.note, "decision": self.decision,
                "structure": self.structure, "signals": self.signals,
                "candidates": self.candidates, "hedge": self.hedge}


def _derisk_hedge(bundle, chain, cfg, expected_move_pts, dte_days) -> Optional[dict]:
    """If the liquidity-derisk overlay fires, build the recommended tail hedge and
    return a compact dict (intensity + fingerprint + the put to buy). Else None."""
    sig = bundle.signals.get("derisk_liquidity")
    if sig is None or sig.status == "NO_DATA" or chain is None:
        return None
    intensity = float(sig.detail.get("intensity", 0.0))
    hedge = constructor.build_tail_hedge(chain, cfg, intensity,
                                         expected_move_pts=expected_move_pts,
                                         dte_days=dte_days)
    out = {"intensity": intensity,
           "components": sig.detail.get("components", {}),
           "reads": sig.detail.get("reads", {}),
           "fired": bool(hedge),
           "trigger": sig.detail.get("trigger", cfg.hedge.trigger)}
    if hedge:
        out.update(hedge)
    return out


def suggest(cfg, now: str, expiry: str) -> Suggestion:
    b = signal_bundle.evaluate(cfg.db_path, now, expiry,
                               veto_days=cfg.gates.event_veto_days)
    if b.spot == 0.0:
        return Suggestion(now, expiry, {}, None, b.as_dict()["signals"],
                          tradeable=False, note="no chain snapshot as-of now")

    dec = directional.decide(b, cfg.weights, cfg.gates,
                             costs=cfg.costs, lot_size=cfg.lot_size)
    da = DataAccess(cfg.db_path)
    chain = da.chain_as_of(now, expiry)

    # Always build the ranked candidate list — even when gated to NO_TRADE — so
    # the desk shows what the analysis leans toward and lets the user act anyway.
    cand = candidates_mod.generate(dec, chain, cfg) if chain else []

    structure = None
    tradeable = False
    if dec.action == "ACT":
        st = constructor.build(dec.family, chain, cfg,
                               expected_move_pts=getattr(dec, "expected_move_pts", None))
        if st is not None:
            structure = st.as_dict(); tradeable = True
            note = "; ".join(dec.reasons)
        else:
            note = f"{dec.family} not priceable in snapshot (missing leg premiums)"
    else:
        # gated: surface the top aligned candidate as the informational lean.
        note = "below conviction gate — " + "; ".join(dec.reasons)
        if cand:
            structure = cand[0]["structure"]

    # risk overlay: tail-hedge / max-drawdown insurance (independent of direction)
    hedge = _derisk_hedge(b, chain, cfg,
                          getattr(dec, "expected_move_pts", None),
                          days_to_expiry(now, expiry))

    return Suggestion(now=now, expiry=expiry, decision=dec.as_dict(),
                      structure=structure, signals=b.as_dict()["signals"],
                      tradeable=tradeable, note=note, candidates=cand, hedge=hedge)
