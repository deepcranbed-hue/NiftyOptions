"""
Scale AND source contract for the expected move (D-SC-01, D-SC-04).

SCALE (D-SC-01): the ATM straddle prices the MEAN ABSOLUTE move,
E|S_T - F| = sigma*sqrt(2/pi) ~= 0.7979*sigma. It is NOT 1 sigma. `_expected_move_pts`
returned `0.8 * straddle` = 0.638 sigma until 2026-08-15. Consumers treat the value AS
sigma (action_eval.sigma, adjustment.em, constructor placing iron-condor shorts at
condor_short_em_mult x em, that multiplier defaulting to 1.0 against a documented
intent of "~1 sigma OTM").

SOURCE (D-SC-04): both tiers must be chain-native and priced at the EXACT expiry being
traded. The VIX tier was removed because it was wrong twice over:
  * INDIAVIX is a 30-day constant-maturity, whole-smile index. Over 21,708 captures
    straddle/VIX ran 0.861 (4 DTE) to 1.205 (1 DTE), converging to 1.0 only as DTE
    approached 30 — a structural, regime-dependent bias.
  * `chain.vix` reads `captures.vix`, a constant 12.0 across all 13,126 captures, so
    the branch returned `spot * 0.12 * sqrt(dte/365)`, ignoring the market entirely.
A VIX-based expected move is also not a tradeable quantity; the straddle is.

Run:  PYTHONPATH=./ pytest strategy_framework/tests/test_expected_move_scale.py
"""
import math

import pytest

from strategy_framework.strategy.regime import _expected_move_pts

SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)      # 0.797885
SIGMA_PER_STRADDLE = 1.0 / SQRT_2_OVER_PI      # 1.253314
OLD_FACTOR = 0.8                               # what the scale used to be


class _Bundle:
    """Minimal stand-in for the signal bundle — only .context and .spot are read."""
    def __init__(self, context, spot):
        self.context = context
        self.spot = spot


def _sigma_pts(spot, iv, dte):
    return spot * iv * math.sqrt(dte / 365.0)


# --------------------------------------------------------------------------------
# tier 1 — the traded straddle, and the scale factor
# --------------------------------------------------------------------------------
def test_straddle_converts_up_to_one_sigma():
    """1 sigma = straddle / 0.7979 = 1.2533 x straddle.

    0.8 x straddle (the old value) is 0.638 sigma — neither 1 sigma NOR the expected
    move, so it cannot be defended as either convention.
    """
    straddle = 401.8
    em = _expected_move_pts(_Bundle({"atm_straddle_pts": straddle, "dte_days": 8.0}, 25000.0))
    assert em == pytest.approx(straddle * SIGMA_PER_STRADDLE, rel=1e-9)
    assert em == pytest.approx(503.6, abs=0.5)
    assert em > straddle, "1 sigma must exceed the straddle, not fall below it"


def test_old_factor_would_fail_this():
    """Regression lock so a well-meaning 'restore the 0.8' cannot pass silently."""
    assert OLD_FACTOR * SIGMA_PER_STRADDLE == pytest.approx(1.0027, rel=1e-3)
    assert SIGMA_PER_STRADDLE / OLD_FACTOR == pytest.approx(1.5666, rel=1e-3)
    straddle, spot = 401.8, 25000.0
    em = _expected_move_pts(_Bundle({"atm_straddle_pts": straddle, "dte_days": 8}, spot))
    assert em / (OLD_FACTOR * straddle) == pytest.approx(1.5666, rel=1e-3)


# --------------------------------------------------------------------------------
# tier 2 — chain-native ATM IV, and its agreement with tier 1
# --------------------------------------------------------------------------------
@pytest.mark.parametrize("spot,iv,dte", [
    (25000.0, 0.1361, 8), (24583.8, 0.1225, 1), (24000.0, 0.1800, 30),
    (26000.0, 0.1130, 15), (23000.0, 0.2500, 3),
])
def test_atm_iv_tier_returns_one_sigma(spot, iv, dte):
    """spot * IV * sqrt(dte/365) is 1 sigma by construction, at the traded expiry."""
    em = _expected_move_pts(_Bundle({"atm_iv": iv, "dte_days": dte}, spot))
    assert em == pytest.approx(_sigma_pts(spot, iv, dte), rel=1e-9)


@pytest.mark.parametrize("spot,iv,dte", [
    (25000.0, 0.1361, 8), (24000.0, 0.1800, 30), (23000.0, 0.2500, 3),
])
def test_the_two_chain_tiers_agree(spot, iv, dte):
    """Give both tiers the SAME volatility and they must produce the same 1 sigma.

    This is what makes tier 2 a substitute rather than a downgrade. On real data the
    median straddle/atm_iv ratio is 1.0030 across 21,708 captures.
    """
    sigma = _sigma_pts(spot, iv, dte)
    straddle = sigma * SQRT_2_OVER_PI            # what a market at that IV would price
    em_straddle = _expected_move_pts(
        _Bundle({"atm_straddle_pts": straddle, "atm_iv": iv, "dte_days": dte}, spot))
    em_iv = _expected_move_pts(_Bundle({"atm_iv": iv, "dte_days": dte}, spot))
    assert em_straddle == pytest.approx(em_iv, rel=1e-9)


def test_straddle_is_preferred_over_atm_iv_when_both_present():
    """The traded instrument wins over the inverted one."""
    ctx = {"atm_straddle_pts": 401.8, "atm_iv": 0.99, "dte_days": 8}
    assert _expected_move_pts(_Bundle(ctx, 25000.0)) == pytest.approx(
        401.8 * SIGMA_PER_STRADDLE, rel=1e-9)


def test_atm_iv_used_when_one_side_of_the_straddle_is_unquotable():
    """The case tier 2 exists for: a deep-ITM strike prints below intrinsic on a stale
    last trade, so bundle.py's `c > 0 and p > 0` gate leaves atm_straddle_pts None
    while the other side still inverts cleanly."""
    em = _expected_move_pts(
        _Bundle({"atm_straddle_pts": None, "atm_iv": 0.1361, "dte_days": 8}, 25000.0))
    assert em == pytest.approx(_sigma_pts(25000.0, 0.1361, 8), rel=1e-9)


# --------------------------------------------------------------------------------
# VIX must not influence the expected move at all (D-SC-04)
# --------------------------------------------------------------------------------
def test_vix_is_ignored_entirely():
    """A context carrying a vix and nothing chain-native must NOT produce a
    VIX-derived move. It falls to the tagged percent last-resort instead."""
    spot = 25000.0
    em = _expected_move_pts(_Bundle({"vix": 13.61, "dte_days": 8}, spot))
    assert em == pytest.approx(0.004 * spot)
    vix_derived = spot * (13.61 / 100.0) * math.sqrt(8 / 365.0)
    assert em != pytest.approx(vix_derived, rel=0.01)


def test_placeholder_vix_cannot_reach_the_expected_move():
    """captures.vix is a constant 12.0. Two very different vix values with no chain
    data must give the SAME answer — proving vix is not read at all."""
    a = _expected_move_pts(_Bundle({"vix": 12.0, "dte_days": 8}, 25000.0))
    b = _expected_move_pts(_Bundle({"vix": 45.0, "dte_days": 8}, 25000.0))
    assert a == b == pytest.approx(100.0)


def test_vix_does_not_override_a_chain_tier():
    ctx = {"atm_iv": 0.1361, "vix": 45.0, "dte_days": 8}
    assert _expected_move_pts(_Bundle(ctx, 25000.0)) == pytest.approx(
        _sigma_pts(25000.0, 0.1361, 8), rel=1e-9)


# --------------------------------------------------------------------------------
# fallbacks and degenerate inputs
# --------------------------------------------------------------------------------
def test_percent_fallback_when_no_chain_data_at_all():
    assert _expected_move_pts(_Bundle({"dte_days": 8}, 25000.0)) == pytest.approx(100.0)


@pytest.mark.parametrize("bad", [None, 0, 0.0, -5.0])
def test_non_positive_straddle_falls_through_to_atm_iv(bad):
    """A zero/absent straddle must not be treated as a real reading of zero."""
    em = _expected_move_pts(
        _Bundle({"atm_straddle_pts": bad, "atm_iv": 0.1361, "dte_days": 8}, 25000.0))
    assert em == pytest.approx(_sigma_pts(25000.0, 0.1361, 8), rel=1e-9)


@pytest.mark.parametrize("bad", [None, 0, 0.0, -0.2])
def test_non_positive_atm_iv_falls_through_to_percent(bad):
    em = _expected_move_pts(
        _Bundle({"atm_straddle_pts": None, "atm_iv": bad, "dte_days": 8}, 25000.0))
    assert em == pytest.approx(100.0)


# --------------------------------------------------------------------------------
# downstream contract
# --------------------------------------------------------------------------------
def test_condor_shorts_land_at_one_sigma_with_default_multiplier():
    """condor_short_em_mult defaults to 1.0 against a documented intent of ~1 sigma OTM
    (SKILL.md:83, REFERENCE.md:176). Under the old factor the shorts sat at 0.638 sigma.
    """
    from strategy_framework.config.settings import StrikeConfig
    straddle, spot = 401.8, 25000.0
    em = _expected_move_pts(_Bundle({"atm_straddle_pts": straddle, "dte_days": 8}, spot))
    target = StrikeConfig().condor_short_em_mult * em
    assert target == pytest.approx(straddle * SIGMA_PER_STRADDLE, rel=1e-9)
    assert target == pytest.approx(503.6, abs=0.5)      # not 321.4


def test_constructor_hierarchy_matches_regime_and_ignores_vix():
    """constructor._expected_move_pts carried the same VIX fallback off the same
    placeholder. It must now use the chain, and return None rather than fabricate."""
    from strategy_framework.strategy import constructor

    class _Chain:
        spot = 25000.0
        vix = 12.0                      # the placeholder — must be ignored
        strikes = [24800.0, 25000.0, 25200.0]
        def __init__(self, c, p):
            self.call_ltp = {25000.0: c}
            self.put_ltp = {25000.0: p}
        def atm_strike(self): return 25000.0

    both = constructor._expected_move_pts(_Chain(200.0, 200.0), None, 8)
    assert both == pytest.approx(400.0 * SIGMA_PER_STRADDLE, rel=1e-6)

    one_side = constructor._expected_move_pts(_Chain(200.0, 0.0), None, 8)
    assert one_side is not None and one_side > 0

    neither = constructor._expected_move_pts(_Chain(0.0, 0.0), None, 8)
    assert neither is None, "must return None, not a VIX-derived or fabricated number"

    explicit = constructor._expected_move_pts(_Chain(200.0, 200.0), 999.0, 8)
    assert explicit == pytest.approx(999.0)
