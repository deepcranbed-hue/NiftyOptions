"""
`breeze_loader.implied_volatility` must never invent a number (D-SC-03).

This function WRITES call_iv / put_iv into the store, so a fabricated value is
indistinguishable from a real reading forever after. It previously had two ways to
produce one:

  * `return 0.0` on a sub-intrinsic price. Deep-ITM strikes routinely print an LTP
    below intrinsic — a stale last trade on an untraded strike. Measured at 8 of 21
    puts on a real near-expiry chain (capture 17989).
  * `return v` after max_iter with NO convergence check. Newton with no bracket
    diverges at low vega, hits the max(0.0001, v) floor, and returns it.

Both violate the framework's "checked-and-absent != silently zero" rule. The contract
is now None, which the module already expected: strike_map seeds these to None and the
row builder branches on `is not None`.

Run:  PYTHONPATH=./ pytest backend/tests/test_breeze_iv.py
"""
import math

import pytest

from backend.quant.breeze_loader import implied_volatility, process_breeze_chain
from strategy_framework.bs import implied_vol as bs_implied_vol

SPOT = 24583.8
R = 0.0655
T = 0.833 / 365


# real sub-intrinsic puts from capture 17989 (2026-08-10 10:00, expiry 2026-08-11)
SUB_INTRINSIC_PUTS = [
    (24750.0, 165.60),   # intrinsic 166.20 — 0.60 below
    (24800.0, 206.30),   # intrinsic 216.20 — 9.90 below
    (24900.0, 298.80),   # intrinsic 316.20 — 17.40 below
    (25100.0, 494.00),   # intrinsic 516.20 — 22.20 below
]


@pytest.mark.parametrize("K,px", SUB_INTRINSIC_PUTS)
def test_sub_intrinsic_price_returns_none_not_zero(K, px):
    assert px < K - SPOT                       # genuinely below intrinsic
    assert implied_volatility('p', px, SPOT, K, T, R) is None


@pytest.mark.parametrize("px", [0.0, -1.0, None])
def test_non_positive_price_returns_none_not_zero(px):
    assert implied_volatility('c', px, SPOT, 24600.0, T, R) is None


def test_low_vega_strike_solves_instead_of_returning_a_floor_value():
    """K=26000, px=0.05, T=0.5d: the old Newton returned 0.000100 (the clamp floor);
    bs.py's bisection fallback solves it at ~0.4549."""
    v = implied_volatility('c', 0.05, SPOT, 26000.0, 0.5 / 365, R)
    assert v is not None
    assert v == pytest.approx(0.45493, rel=1e-3)
    assert v > 0.01                            # not the 0.0001 clamp


@pytest.mark.parametrize("K,px,cp,call", [
    (24100.0, 507.05, 'c', True), (24500.0, 139.60, 'c', True),
    (24600.0, 76.20, 'c', True), (25100.0, 1.60, 'c', True),
    (24100.0, 2.05, 'p', False), (24500.0, 33.55, 'p', False),
    (24600.0, 70.00, 'p', False),
])
def test_matches_bs_exactly_on_solvable_strikes(K, px, cp, call):
    """It is an ADAPTER over bs.py — not an approximation of it."""
    assert implied_volatility(cp, px, SPOT, K, T, R) == pytest.approx(
        bs_implied_vol(px, SPOT, K, T, R, call), rel=1e-9)


def test_never_returns_exactly_zero_across_a_real_chain():
    """The whole near-expiry chain from capture 17989: zero silent zeros."""
    chain = [
        (24100.0, 507.05, 2.05), (24300.0, 312.35, 6.00), (24500.0, 139.60, 33.55),
        (24600.0, 76.20, 70.00), (24700.0, 34.70, 127.90), (24750.0, 22.05, 165.60),
        (24800.0, 13.30, 206.30), (24900.0, 4.65, 298.80), (25000.0, 2.70, 398.85),
        (25100.0, 1.60, 494.00),
    ]
    vals = []
    for K, c, p in chain:
        vals.append(implied_volatility('c', c, SPOT, K, T, R))
        vals.append(implied_volatility('p', p, SPOT, K, T, R))
    assert not any(v == 0.0 for v in vals), "a silent zero reached the store"
    assert any(v is None for v in vals), "the sub-intrinsic puts should report absent"
    assert all(v is None or v > 0.001 for v in vals)


def _breeze_row(strike, right, ltp):
    return {"strike_price": str(strike), "right": right, "ltp": str(ltp),
            "open_interest": "1", "best_bid_price": None, "best_offer_price": None,
            "total_quantity_traded": "1", "spot_price": str(SPOT)}


def test_process_chain_stores_none_not_zero_for_unsolvable_strikes():
    """End-to-end: an unsolvable strike must reach the store as None."""
    rows, spot = process_breeze_chain(
        [_breeze_row(24600, "call", 76.20), _breeze_row(24750, "put", 165.60)], 0.833)
    assert spot == pytest.approx(SPOT)
    by_k = {r["strike"]: r for r in rows}
    assert by_k[24750.0].get("put_iv") is None or by_k[24750.0].get("put_iv") != 0.0
    solved = by_k[24600.0]
    assert solved.get("call_iv") is None or solved["call_iv"] > 1.0


def test_module_does_not_require_scipy():
    """The local bs_price/bs_vega died with the Newton solver they served; so did the
    scipy import. Keeps a heavy dep out of the capture path."""
    import backend.quant.breeze_loader as bl
    src = open(bl.__file__).read()
    assert "scipy" not in src
    assert "def bs_price(" not in src and "def bs_vega(" not in src
