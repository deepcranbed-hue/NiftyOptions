"""
strategy_framework/strategy/directional.py
==========================================
Blend the signal bundle into a directional decision.

Pipeline:
    signals --(weighted blend)--> net_score, net_confidence
           --(gates)-->            ACT / STAND_ASIDE
           --(VRP regime)-->       structure family (debit vs credit vs long)

The combiner is confidence-weighted: a signal with high confidence pulls the
blend harder than a hesitant one, on top of its static importance weight. This
keeps stale or data-starved signals (confidence -> 0) from voting.

Direction convention throughout: +1 bullish NIFTY, -1 bearish.
"""
from __future__ import annotations
from dataclasses import dataclass, field


from . import regime as _regime
from ..config.settings import LOT_SIZE   # single source of truth (exchange_config)


# Round-trip leg counts per structure family (open + close = 2× this).
_FAMILY_LEGS = {
    "long_call": 1, "long_put": 1,
    "bull_call_spread": 2, "bear_put_spread": 2,
    "bull_put_spread": 2, "bear_call_spread": 2,
    "iron_condor": 4, "iron_butterfly": 4,
}


def _round_trip_cost_inr(family, costs, lot_size) -> float:
    """Cost to open AND close the structure, in ₹ (each leg charged twice)."""
    legs = _FAMILY_LEGS.get(family, 2)
    return costs.legs_cost_inr(legs, lot_size) * 2.0


@dataclass
class Decision:
    now: str
    spot: float
    direction: int              # +1 bull, -1 bear, 0 none
    net_score: float
    net_confidence: float
    action: str                 # "ACT" | "STAND_ASIDE"
    family: str                 # chosen structure family or "stand_aside"
    regime: str = "NO_TRADE"    # TREND_UP | TREND_DOWN | RANGE | NO_TRADE
    expected_move_pts: float = 0.0
    reasons: list = field(default_factory=list)
    vrp_regime: str = "FAIR"
    phase: str = "MIDDAY"
    contributions: dict = field(default_factory=dict)
    veto: dict = field(default_factory=dict)
    edge_ratio: float | None = None      # 1σ move ÷ round-trip cost
    edge_cost_mult: float = 0.0          # the threshold applied (0 = gate off)
    cost_gated: bool = False             # True if gate flipped ACT -> STAND_ASIDE

    def as_dict(self) -> dict:
        return {"now": self.now, "spot": self.spot, "direction": self.direction,
                "regime": self.regime, "net_score": round(self.net_score, 4),
                "net_confidence": round(self.net_confidence, 4),
                "action": self.action, "family": self.family,
                "expected_move_pts": round(self.expected_move_pts, 1),
                "phase": self.phase, "vrp_regime": self.vrp_regime,
                "reasons": self.reasons, "veto": self.veto,
                "edge_ratio": round(self.edge_ratio, 3) if self.edge_ratio is not None else None,
                "edge_cost_mult": self.edge_cost_mult, "cost_gated": self.cost_gated,
                "contributions": self.contributions}


def decide(bundle, weights, gates, costs=None, lot_size: int = LOT_SIZE) -> Decision:
    """Delegate to the regime classifier, then wrap as a Decision.

    TREND_* -> directional family, RANGE -> condor/butterfly, NO_TRADE -> aside.

    Cost-edge gate (Salov "do-nothing threshold"): when `costs` is supplied and
    `gates.min_edge_cost_mult` > 0, a would-be trade is stood aside if the expected
    1σ move (₹/lot) does not clear that multiple of the structure's round-trip cost
    — i.e. don't pay fees for a move too small to matter. Off by default.
    """
    reg = _regime.classify(bundle, weights, gates)
    action = "STAND_ASIDE" if reg.label == "NO_TRADE" else "ACT"
    reasons = list(reg.reasons)
    veto = bundle.get("earnings_events").detail
    phase = bundle.get("time_of_day").detail.get("phase", "MIDDAY")
    vrp_regime = bundle.get("vrp").detail.get("regime", "FAIR")

    mult = float(getattr(gates, "min_edge_cost_mult", 0.0) or 0.0)
    edge_ratio = None
    cost_gated = False
    if action == "ACT" and costs is not None and mult > 0:
        rtc = _round_trip_cost_inr(reg.family, costs, lot_size)
        capture_inr = abs(reg.expected_move_pts) * lot_size
        # rtc<=0 (free trading) => infinite edge, never gate (guards div-by-zero
        # AND the wrong semantics of treating zero cost as zero edge).
        edge_ratio = (capture_inr / rtc) if rtc > 0 else float("inf")
        if edge_ratio < mult:
            action = "STAND_ASIDE"
            cost_gated = True
            reasons.append(f"BELOW_COST_EDGE: 1σ≈₹{capture_inr:.0f} < {mult:g}× "
                           f"round-trip cost ₹{rtc:.0f} (ratio {edge_ratio:.2f})")
        else:
            reasons.append(f"cost-edge OK: 1σ≈₹{capture_inr:.0f} ≥ {mult:g}×₹{rtc:.0f} "
                           f"(ratio {edge_ratio:.2f})")

    return Decision(now=bundle.now, spot=bundle.spot, direction=reg.direction,
                    net_score=reg.net_score, net_confidence=reg.net_confidence,
                    action=action, family=reg.family, regime=reg.label,
                    expected_move_pts=reg.expected_move_pts, reasons=reasons,
                    vrp_regime=vrp_regime, phase=phase,
                    contributions=reg.diagnostics.get("contributions", {}),
                    veto=veto, edge_ratio=edge_ratio, edge_cost_mult=mult,
                    cost_gated=cost_gated)
