"""
strategy_framework/strategy/regime.py
=====================================
Classify the market into a tradeable regime, then map it to a structure family.

    TREND_UP / TREND_DOWN : directional momentum confirmed (strong net score,
                            agreeing breadth or a volume-backed heavyweight lead,
                            amplified in the opening/power-hour windows).
                            -> directional structures (spreads / long options).

    RANGE                 : no directional edge, but premium is not cheap and the
                            tape is contained (weak momentum, offsetting breadth,
                            IV >= RV). -> premium-harvest structures:
                              * near a pin / very tight expected move -> iron_butterfly
                              * otherwise                             -> iron_condor

    NO_TRADE              : event veto, or no edge in either direction (weak
                            momentum AND cheap premium AND no range signature),
                            or data too thin. -> stand aside.

The classifier reads the SignalBundle (including heavyweight_leadership,
time_of_day, vrp) and applies the config gates. It does not itself size or price.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

# Rosters derive from the single source (signals/registry.py) — never hardcode.
from ..signals import registry as _reg
# Momentum-family signals whose confidence the time-of-day phase amplifies.
_MOMENTUM_FAMILY = _reg.momentum_names()
# All directional contributors to the blended score (the weighted core).
_DIRECTIONAL = _reg.blended_names()


@dataclass
class Regime:
    label: str                     # TREND_UP | TREND_DOWN | RANGE | NO_TRADE
    direction: int                 # +1 / -1 / 0
    family: str
    net_score: float
    net_confidence: float
    expected_move_pts: float
    reasons: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


_SQRT_2_OVER_PI = np.sqrt(2.0 / np.pi)      # 0.797885


def _expected_move_pts(bundle) -> float:
    """~1-sigma move to expiry, in points, from the chain the desk will actually trade.

    SCALE (D-SC-01): the ATM straddle prices the MEAN ABSOLUTE move,
    E|S_T - F| = sigma*sqrt(2/pi) ~= 0.7979*sigma. It is NOT 1 sigma. To convert a
    straddle UP to 1 sigma you DIVIDE by 0.7979 (x1.2533). This returned
    `0.8 * straddle` = 0.638 sigma until 2026-08-15.

    SOURCE (D-SC-04): both tiers are chain-native and priced at the EXACT expiry
    being traded. The VIX tier was removed — it was wrong twice over:
      * INDIAVIX is a 30-day constant-maturity, whole-smile index. Using it for a
        4-day expiry mismatches both maturity and strike weighting; over 21,708
        captures straddle/VIX swings 0.861 (4 DTE) to 1.205 (1 DTE) with the term
        structure, converging to 1.0 only as DTE approaches 30. That is a structural,
        regime-dependent bias, not noise.
      * `chain.vix` reads `captures.vix`, a constant 12.0 across all 13,126 captures,
        so the branch returned `spot * 0.12 * sqrt(dte/365)` — a fabricated number
        that ignored the market. The real INDIAVIX series lives in
        `price_bars(symbol='INDIAVIX')` and is a different quantity from this one.

    The two chain tiers agree to 0.3% (median straddle/atm_iv = 1.0030), so tier 2 is
    a genuine substitute rather than a downgrade. It exists because the straddle needs
    BOTH sides quoted, and a deep-ITM strike can print below intrinsic on a stale last
    trade — leaving one solvable side and no straddle.

    `diagnostics["em_source"]` records which tier produced the number, so a fabricated
    last-resort can never be mistaken for a measurement (D-MA-04 provenance).
    """
    ctx = bundle.context
    spot = bundle.spot or 0.0

    straddle = ctx.get("atm_straddle_pts")
    if straddle and straddle > 0:
        return float(straddle / _SQRT_2_OVER_PI)   # straddle -> 1 sigma  (x1.2533)

    atm_iv = ctx.get("atm_iv")
    dte = max(ctx.get("dte_days", 1.0), 1e-4)
    if atm_iv and atm_iv > 0 and spot:
        return float(spot * atm_iv * np.sqrt(dte / 365.0))

    return 0.004 * spot                       # ~0.4% PRIOR last resort, tagged below


def classify(bundle, weights, gates) -> Regime:
    wmap = weights.as_dict()

    tod = bundle.get("time_of_day").detail
    mom_mult = tod.get("momentum_multiplier", 1.0)
    pin_risk = tod.get("pin_risk", False)
    phase = tod.get("phase", "MIDDAY")

    # ---- confidence-weighted blend, time-of-day amplifying momentum -------
    # Math lives in strategy/blend.py (ONE definition, shared with the Calibration
    # Agent) so offline validation can never score a different formula than we trade.
    from .blend import blend_net
    _scores = {n: bundle.get(n).score for n in _DIRECTIONAL}
    _confs = {n: bundle.get(n).confidence for n in _DIRECTIONAL}
    net_score, net_conf, contributions = blend_net(
        _DIRECTIONAL, wmap, _scores, _confs,
        momentum_names=_MOMENTUM_FAMILY, mom_mult=mom_mult)
    for name in _DIRECTIONAL:                      # keep status in the diagnostics
        contributions[name]["status"] = bundle.get(name).status

    hv = bundle.get("heavyweight_leadership").detail
    breadth = hv.get("breadth", 0.0)
    concentration = hv.get("concentration", 0.0)
    hv_surge = hv.get("hv_vol_surge")

    vrp = bundle.get("vrp").detail
    vrp_ratio = vrp.get("vrp_ratio")
    vrp_regime = vrp.get("regime", "FAIR")

    em = _expected_move_pts(bundle)
    _c = bundle.context
    em_source = ("atm_straddle" if (_c.get("atm_straddle_pts") or 0) > 0
                 else "atm_iv" if (_c.get("atm_iv") or 0) > 0
                 else "pct_fallback")
    reasons = [f"phase={phase}"]

    # ---- event veto short-circuits everything -----------------------------
    veto = bundle.get("earnings_events").detail
    if veto.get("veto"):
        return Regime("NO_TRADE", 0, "stand_aside", net_score, net_conf, em,
                      [f"event veto: {veto.get('reason')}"],
                      {"contributions": contributions, "tod": tod})

    # ---- data-sufficiency / conviction floor ------------------------------
    # If the signals are too cold or sparse to carry any conviction (e.g. during
    # warm-up, before enough bars/constituent/chain history exist), stand aside.
    # Without this, empty signals (net_conf≈0) get misread — weak score + zero
    # breadth + no VRP data look like a "range" and wrongly sell a condor.
    n_live = sum(1 for name in _DIRECTIONAL
                 if bundle.get(name).status == "OK")
    if net_conf < gates.min_confidence or n_live < 2:
        reasons.append(f"insufficient signal ({n_live}/5 live, conf {net_conf:.2f} "
                       f"< {gates.min_confidence}) — stand aside")
        return Regime("NO_TRADE", 0, "stand_aside", net_score, net_conf, em, reasons,
                      {"contributions": contributions, "tod": tod, "em_source": em_source, "n_live": n_live})

    strong = abs(net_score) >= gates.min_abs_score and net_conf >= gates.min_confidence
    # a volume-backed heavyweight lead can confirm a trend even if breadth is narrow
    hv_lead = (concentration >= 0.6 and hv_surge is not None and hv_surge >= 1.2
               and abs(net_score) >= gates.min_abs_score * 0.8)
    breadth_agrees = abs(breadth) >= 0.34 and np.sign(breadth) == np.sign(net_score)

    premium_not_cheap = vrp_ratio is None or vrp_ratio >= gates.vrp_cheap_ratio
    premium_rich = vrp_ratio is not None and vrp_ratio >= gates.vrp_rich_ratio

    # ---- TREND ------------------------------------------------------------
    if strong and (breadth_agrees or hv_lead):
        direction = 1 if net_score > 0 else -1
        reasons.append(f"trend: |score|={abs(net_score):.2f} conf={net_conf:.2f} "
                       f"breadth={breadth:.2f} conc={concentration:.2f} "
                       f"{'hv_lead ' if hv_lead else ''}")
        fam = _directional_family(direction, vrp_regime, gates, net_score, net_conf)
        return Regime("TREND_UP" if direction > 0 else "TREND_DOWN", direction,
                      fam, net_score, net_conf, em, reasons,
                      {"contributions": contributions, "breadth": breadth,
                       "concentration": concentration, "hv_vol_surge": hv_surge,
                       "vrp_regime": vrp_regime, "tod": tod})

    # ---- RANGE (premium harvest) -----------------------------------------
    weak_dir = abs(net_score) < gates.min_abs_score
    offsetting = abs(breadth) < 0.34
    if weak_dir and premium_not_cheap and offsetting:
        # avoid short-gamma structures into an expiry-close pin print
        if pin_risk:
            reasons.append("range signature but EXPIRY_CLOSE pin risk -> stand aside")
            return Regime("NO_TRADE", 0, "stand_aside", net_score, net_conf, em,
                          reasons, {"contributions": contributions, "tod": tod})
        # ---- TREND-EXPANSION VETO (do NOT sell ATM premium into expansion) ----
        # The July-2026 condor studies' core lesson: short gamma gets overrun when
        # the market is REPRICING a bigger move, and the chain says so before the
        # blend does. Veto premium-harvest when the ATM straddle is EXPANDING
        # (straddle_flow > +0.33) or the tape is travelling efficiently in one
        # direction (choppiness reads trending, chop < 0.35). PRIOR thresholds —
        # the regime-class sensors exist precisely for this gate.
        strad = bundle.get("straddle_flow")
        chop = bundle.get("choppiness")
        expanding = strad.status == "OK" and strad.score >= 0.33
        trending_tape = chop.status == "OK" and chop.score <= 0.35
        if expanding or trending_tape:
            why = []
            if expanding:
                why.append(f"straddle expanding ({strad.detail.get('change_pct')}%)")
            if trending_tape:
                why.append(f"tape trending (chop index {chop.detail.get('choppiness_index')})")
            reasons.append("range signature but TREND-EXPANSION veto — do not sell "
                           "ATM premium into a repricing market: " + ", ".join(why))
            return Regime("NO_TRADE", 0, "stand_aside", net_score, net_conf, em,
                          reasons, {"contributions": contributions, "tod": tod, "em_source": em_source,
                                    "trend_expansion_veto": why})
        # pin support (diagnostic): a strong, concentrated pin corroborates RANGE.
        pin = bundle.get("pin_pressure")
        pin_read = (pin.detail.get("regime") if pin.status == "OK" else None)
        tight = em <= 0.006 * (bundle.spot or 1)            # very tight expected move
        fam = "iron_butterfly" if tight else "iron_condor"
        reasons.append(f"range: weak dir ({net_score:.2f}), breadth {breadth:.2f}, "
                       f"vrp={vrp_regime}, EM={em:.0f}pts -> {fam}")
        return Regime("RANGE", 0, fam, net_score, net_conf, em, reasons,
                      {"contributions": contributions, "breadth": breadth,
                       "vrp_regime": vrp_regime, "expected_move_pts": round(em, 1),
                       "pin_regime": pin_read, "tod": tod})

    # ---- NO_TRADE ---------------------------------------------------------
    reasons.append("no edge: weak/mixed momentum and no clean range signature")
    return Regime("NO_TRADE", 0, "stand_aside", net_score, net_conf, em, reasons,
                  {"contributions": contributions, "breadth": breadth,
                   "vrp_regime": vrp_regime, "tod": tod})


def _directional_family(direction, vrp_regime, gates, net_score, net_conf):
    rich = vrp_regime == "RICH"
    cheap = vrp_regime == "CHEAP"
    strong = abs(net_score) >= 0.45 and net_conf >= 0.55
    if direction > 0:
        if rich:
            return "bull_put_spread"
        if cheap and strong:
            return "long_call"
        return "bull_call_spread"
    else:
        if rich:
            return "bear_call_spread"
        if cheap and strong:
            return "long_put"
        return "bear_put_spread"
