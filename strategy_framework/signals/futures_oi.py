"""
strategy_framework/signals/futures_oi.py
========================================
ADAPTER over `backend.quant.intraday_oi` — NOT a second implementation.

The futures price×open-interest positioning rule (short_buildup / long_unwinding /
long_buildup / short_covering / churn) lives in exactly ONE place: `intraday_oi._label`,
which also backs the Macro Shock view in the intelligence tab. This module does not
re-derive that rule — it delegates to it and adds the strategy-framework decorations
the blend cares about: a directional `lean`, a `conviction` flag, and a `reliability`
multiplier (how much to trust a directional signal in this positioning regime).

Single source of truth (CLAUDE.md DRY rule): if the OI rule ever changes, it changes
in `intraday_oi._label` and both the Macro Shock view and this adapter follow.
"""
from __future__ import annotations

from backend.quant.intraday_oi import _label as _oi_label   # the ONE classifier

# regime → (directional lean, has-conviction). Derived from the canonical kinds.
_LEAN = {
    "long_buildup":   ("bull",    True),
    "short_covering": ("bull",    False),
    "short_buildup":  ("bear",    True),
    "long_unwinding": ("bear",    False),
    # coiled = heavy positioning but no net price direction: real conviction is being
    # committed (conviction=True) yet it points nowhere yet (neutral lean).
    "coiled":         ("neutral", True),
    "churn":          ("neutral", False),
    "oi_unavailable": ("neutral", False),
}


def classify_positioning(dp_pct: float, doi_pct: float | None) -> dict:
    """Classify a (price %, OI %) change into a positioning regime.

    Delegates the rule to `intraday_oi._label` (single source); returns an enriched
    dict {regime, lean, conviction, note}. `regime` is the canonical kind with spaces
    for display (e.g. 'short buildup')."""
    kind, read = _oi_label(dp_pct, doi_pct)
    lean, conviction = _LEAN.get(kind, ("neutral", False))
    return {"regime": kind.replace("_", " "), "kind": kind, "lean": lean,
            "conviction": conviction, "note": read}


def regime_score(kind: str) -> tuple[float, float]:
    """Map a positioning regime to a directional (score, confidence) for the OI
    regime SIGNAL. The rule embodies "buildup → trade WITH the move (momentum,
    conviction); covering/unwinding → FADE the move (it's hollow, low conviction);
    churn → nothing":

        long buildup   → +0.70, conf 0.70   fresh longs, ride it up
        short buildup  → −0.70, conf 0.70   fresh shorts, ride it down
        short covering → −0.35, conf 0.35   hollow up-move — fade (expect it to give back)
        long unwinding → +0.35, conf 0.35   hollow down-move — fade (expect a bounce)
        coiled         →  0.00, conf 0.40   heavy 2-sided positioning, no direction yet
        churn          →  0.00, conf 0.15   no positioning

    coiled and churn share score 0 (neither points a direction) but carry DISTINCT
    confidence — coiled is meaningful positioning (0.40), churn is noise (0.15). The
    regime-matrix axis reads that confidence to tell the two apart. PRIOR magnitudes,
    in one place, calibratable later."""
    return {"long_buildup": (0.70, 0.70), "short_buildup": (-0.70, 0.70),
            "short_covering": (-0.35, 0.35), "long_unwinding": (0.35, 0.35),
            "coiled": (0.0, 0.40), "churn": (0.0, 0.15)}.get(kind, (0.0, 0.1))


def reliability(kind: str) -> float:
    """A 0..1 trust multiplier the blend can apply to a directional read given the
    positioning regime — conviction phases (fresh buildup) earn full trust, hollow
    phases (covering / unwinding) are discounted, churn heavily so. PRIOR values,
    kept in one place, calibratable later. Accepts the canonical underscore kind."""
    # coiled: positioning is heavy but two-sided and unresolved, so a DIRECTIONAL read
    # taken during it is risky (it can break either way) — trust it little, below hollow.
    return {"long_buildup": 1.0, "short_buildup": 1.0,
            "short_covering": 0.5, "long_unwinding": 0.5,
            "coiled": 0.3, "churn": 0.25}.get(kind, 1.0)
