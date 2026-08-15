"""
Coverage for the RND calibration guard (D-SC-02) and the optimizer's halt on an
uncalibrated RND — the paths that let a 25%-mis-centred band survive undetected.

The core identity under test: an ATM straddle prices the MEAN ABSOLUTE move,
E|S_T - F| = sigma*sqrt(2/pi) ~= 0.7979*sigma. It is NOT a 1-sigma move. `sd` from
rnd_stats IS a 1 sigma, so the straddle must be converted UP (divide by 0.7979,
equivalently x1.2533) before the two are compared.

Every fixture here is SELF-CONSISTENT: the option prices are integrated from the same
density that defines the true sigma, so "correct" means correct by construction rather
than by a hand-tuned constant.

Run:  PYTHONPATH=./ pytest backend/tests/test_rnd_calibration.py
"""
import math

import numpy as np
import pytest

from backend.quant.rnd import implied_vol, rnd_stats
from strategy_framework.bs import implied_vol as bs_implied_vol

SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)      # 0.797885
SIGMA_PER_STRADDLE = 1.0 / SQRT_2_OVER_PI      # 1.253314

_trap = getattr(np, "trapezoid", getattr(np, "trapz", None))
F = 24606.2
SIGMA_PTS = 183.06
STRIKES = np.arange(F - 1200, F + 1250, 50.0)


def _density(sigma_pts, grid):
    return np.exp(-0.5 * ((grid - F) / sigma_pts) ** 2) / (sigma_pts * math.sqrt(2 * math.pi))


def _grid():
    return np.linspace(F - 8 * SIGMA_PTS, F + 8 * SIGMA_PTS, 20001)


def _prices_from(sigma_pts):
    """Option prices implied BY a density of this width — self-consistent by construction."""
    g = _grid()
    d = _density(sigma_pts, g)
    calls = np.array([_trap(np.maximum(g - k, 0.0) * d, g) for k in STRIKES])
    puts = np.array([_trap(np.maximum(k - g, 0.0) * d, g) for k in STRIKES])
    return calls, puts


@pytest.fixture(scope="module")
def market():
    """The MARKET prices options off the true sigma. This never changes across tests."""
    return _prices_from(SIGMA_PTS)


def _stats(sigma_pts_of_density, market):
    calls, puts = market
    g = _grid()
    return rnd_stats(g, _density(sigma_pts_of_density, g), F, STRIKES, calls, puts)


# --------------------------------------------------------------------------------
# the identity itself
# --------------------------------------------------------------------------------
@pytest.mark.parametrize("sigma_pts", [100.0, 183.06, 300.0, 450.0])
def test_straddle_is_the_mean_absolute_move_not_one_sigma(sigma_pts):
    """A density of width sigma prices its own ATM straddle at exactly 0.7979*sigma.

    This is the fact the whole guard rests on. If this ever fails, every threshold
    downstream is meaningless.
    """
    g = np.linspace(F - 8 * sigma_pts, F + 8 * sigma_pts, 40001)
    d = _density(sigma_pts, g)
    straddle = _trap(np.abs(g - F) * d, g)     # = C(F) + P(F), undiscounted
    sd = math.sqrt(_trap((g - F) ** 2 * d, g))
    assert straddle / sd == pytest.approx(SQRT_2_OVER_PI, rel=1e-3)
    assert sd / straddle == pytest.approx(SIGMA_PER_STRADDLE, rel=1e-3)


# --------------------------------------------------------------------------------
# the guard is centred where it claims to be
# --------------------------------------------------------------------------------
def test_correct_rnd_reads_ratio_one_and_passes(market):
    """A CORRECT RND must sit at 1.00, not 1.25.

    Regression guard for D-SC-02: `ratio = sd / straddle` put a correct RND at 1.2533,
    leaving the [0.7, 1.4] band skewed — it accepted a 44%-understated RND while
    rejecting one only 12% overstated.
    """
    res = _stats(SIGMA_PTS, market)
    assert res["calibration_ratio"] == pytest.approx(1.00, abs=0.02)
    assert res["calibrated"] is True
    assert res["provenance"] == "PRIMARY"
    assert res["warning"] == ""


@pytest.mark.parametrize("inflation,should_pass", [
    (1.00, True),    # correct
    (0.75, True),    # 25% understated — inside the documented +/-30% band
    (0.70, True),    # exactly at the lower edge
    (0.56, False),   # 44% understated — MUST fail (it did not, before D-SC-02)
    (1.20, True),    # 20% overstated — inside the band
    (1.40, True),    # at the upper edge
    (1.75, False),   # 75% overstated — an inflated move buries iron condors
])
def test_band_accepts_and_rejects_symmetrically(inflation, should_pass, market):
    """Only the DENSITY is perturbed; the option prices stay at the true sigma."""
    res = _stats(SIGMA_PTS * inflation, market)
    assert res["calibrated"] is should_pass, (
        f"RND at {inflation:.2f}x true sigma read ratio {res['calibration_ratio']}")
    assert res["provenance"] == ("PRIMARY" if should_pass else "FALLBACK")


def test_ratio_tracks_inflation_linearly(market):
    """ratio should BE the inflation factor — that is what makes the band readable."""
    for infl in (0.8, 1.0, 1.25, 1.5):
        res = _stats(SIGMA_PTS * infl, market)
        assert res["calibration_ratio"] == pytest.approx(infl, abs=0.03)


# --------------------------------------------------------------------------------
# the emission cannot conflate the two quantities again
# --------------------------------------------------------------------------------
def test_emission_carries_both_straddle_figures(market):
    """straddle_pts and straddle_1sigma_pts are different numbers and both are emitted.

    Emitting only one is how the two got confused in the first place.
    """
    res = _stats(SIGMA_PTS, market)
    assert "straddle_pts" in res and "straddle_1sigma_pts" in res
    assert res["straddle_1sigma_pts"] == pytest.approx(
        res["straddle_pts"] * SIGMA_PER_STRADDLE, rel=1e-3)
    assert res["straddle_1sigma_pts"] > res["straddle_pts"]
    # and the 1-sigma figure is what `sd` is actually compared against
    assert res["sd"] == pytest.approx(res["straddle_1sigma_pts"], rel=0.03)


def test_skew_guard_is_independent_of_calibration(market):
    """|skew| <= 1.0 is its own gate; a well-scaled but contaminated density still fails."""
    g = _grid()
    d = _density(SIGMA_PTS, g)
    d = d * (1.0 + 0.9 * np.tanh((g - F) / (3 * SIGMA_PTS)))   # inject asymmetry
    d = d / _trap(d, g)
    calls, puts = market
    res = rnd_stats(g, d, F, STRIKES, calls, puts)
    if not res["skew_ok"]:
        assert res["calibrated"] is False
        assert "skew" in res["warning"]


def test_calibration_fields_absent_without_chain_inputs():
    """No strikes/prices -> no calibration claim at all (checked-and-absent, not zero)."""
    g = _grid()
    res = rnd_stats(g, _density(SIGMA_PTS, g), F)
    assert "calibrated" not in res
    assert "calibration_ratio" not in res
    assert "sd" in res and res["sd"] > 0


# --------------------------------------------------------------------------------
# the optimizer refuses to rank off a FALLBACK RND
# --------------------------------------------------------------------------------
def _min_chain():
    ks = np.arange(24300.0, 24900.0, 50.0)
    calls, puts = [], []
    for k in ks:
        calls.append(max(F - k, 0.0) + 60.0)
        puts.append(max(k - F, 0.0) + 60.0)
    return {"strikes": list(ks), "call_ltp": calls, "put_ltp": puts,
            "spot": 24583.8, "lot_size": 75}


def test_optimizer_halts_on_uncalibrated_rnd(market):
    from backend.quant.strike_optimizer import optimize
    g = _grid()
    bad = _stats(SIGMA_PTS * 1.75, market)
    assert bad["provenance"] == "FALLBACK"
    out = optimize(_min_chain(),
                   {"grid": list(g), "dens": list(_density(SIGMA_PTS * 1.75, g)),
                    "provenance": bad["provenance"], "warning": bad["warning"]})
    assert out["status"] == "rnd_uncalibrated"
    assert out["rnd_provenance"] == "FALLBACK"
    assert "allow_bad_rnd" in out["note"]


def test_optimizer_proceeds_on_primary_rnd(market):
    from backend.quant.strike_optimizer import optimize
    g = _grid()
    good = _stats(SIGMA_PTS, market)
    assert good["provenance"] == "PRIMARY"
    out = optimize(_min_chain(),
                   {"grid": list(g), "dens": list(_density(SIGMA_PTS, g)),
                    "provenance": good["provenance"], "warning": good["warning"]})
    assert out.get("status") != "rnd_uncalibrated"


def test_allow_bad_rnd_overrides_the_halt(market):
    from backend.quant.strike_optimizer import optimize
    g = _grid()
    out = optimize(_min_chain(),
                   {"grid": list(g), "dens": list(_density(SIGMA_PTS * 1.75, g)),
                    "provenance": "FALLBACK", "warning": "x"},
                   allow_bad_rnd=True)
    assert out.get("status") != "rnd_uncalibrated"


# --------------------------------------------------------------------------------
# rnd.implied_vol is an adapter over bs.py, not a second implementation (D-SC-03)
# --------------------------------------------------------------------------------
@pytest.mark.parametrize("K,px,T", [
    (24100, 507.05, 0.833 / 365), (24500, 139.60, 0.833 / 365),
    (24600, 76.20, 0.833 / 365), (25000, 38.15, 7.833 / 365),
    (25100, 22.70, 7.833 / 365),
])
def test_rnd_implied_vol_matches_bs(K, px, T):
    S, r = 24583.8, 0.0655
    assert implied_vol(px, S, K, T, r) == pytest.approx(
        bs_implied_vol(px, S, K, T, r, True), rel=1e-4)


def test_rnd_implied_vol_returns_nan_not_none_below_no_arb_bound():
    """extract_rnd feeds these into a numpy grid — None would raise, nan propagates."""
    v = implied_vol(1.0, 24583.8, 20000.0, 0.833 / 365, 0.0655)
    assert isinstance(v, float) and math.isnan(v)


def test_rnd_no_arb_gate_is_forward_discounted():
    """rnd.py keeps a STRICTER gate than bs.py: max(0, S - K*e^{-rT}), correct for a call."""
    S, r, T = 24583.8, 0.0655, 30 / 365
    K = 24000.0
    disc = max(0.0, S - K * math.exp(-r * T))
    undisc = max(0.0, S - K)
    assert disc > undisc                       # the discounted bound really is tighter
    v = implied_vol(disc - 1.0, S, K, T, r)    # between the two bounds
    assert math.isnan(v)
