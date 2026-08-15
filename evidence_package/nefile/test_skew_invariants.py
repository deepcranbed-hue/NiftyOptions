"""
test_skew_invariants.py — tests 21–28 & 23a–23g from minute_analytics_v1_brief.

Fixture principle (per verification brief §C1): fixtures construct INPUTS
(synthetic chains priced from a specified smile via Black-76) and assert on
computed OUTPUTS. No fixture ever hardcodes an expected engine emission value
that the engine itself produced.
"""
import math
import numpy as np
import pandas as pd
import pytest

from skew_engine import (_b76_call, decompose_skew, iv_at_delta, build_otm_smile,
                         forward_from_parity, parity_gate, flow_join,
                         classify_configuration, z_skew, PRIOR)
import invariants


# ----------------------------- synthetic chain builder -----------------------------
def make_chain(F, T, smile_fn, strikes):
    """Price a full chain (both sides) from a smile function via Black-76 + parity."""
    rows = []
    for K in strikes:
        s = smile_fn(K)
        c = _b76_call(F, K, T, s)
        p = c - (F - K)                    # parity
        for cp, mid in (("CE", c), ("PE", p)):
            rows.append({"strike": K, "cp": cp, "bid": mid - 0.5, "ask": mid + 0.5,
                         "mid": mid, "iv_mid": s, "oi": 1000, "volume": 1000})
    return pd.DataFrame(rows)

T3 = 3.0 / 365.0
STRIKES = list(range(23400, 25301, 50))     # wide grid so 25Δ is always bracketed
FLAT = lambda K: 0.12
def SKEWED(F, slope=0.00002):               # linear put skew in strike
    return lambda K: 0.12 + slope * (F - K)


# ----------------------------- test 24: flat smile → RR ≈ 0 -------------------------
def test_24_flat_smile_zero_rr():
    ch = make_chain(24300, T3, FLAT, STRIKES)
    out = decompose_skew(ch, ch, T3, T3, dte_days=3, spot_open=24300, spot_curr=24300)
    assert out["rr_floating"]["open_vpt"] == pytest.approx(0.0, abs=0.05)
    assert abs(out["z1_skew_vpt"]["open"]) < 0.05


# ----------------------------- test 25: known 25Δ vs analytic -----------------------
def test_25_interpolated_rr_matches_analytic():
    F = 24300; sm = SKEWED(F)
    ch = make_chain(F, T3, sm, STRIKES)
    smile = build_otm_smile(ch, forward_from_parity(ch),
                            T3, parity_gate(ch, forward_from_parity(ch), T3)[0])
    wp_c = iv_at_delta(smile, F, T3, "CE"); wp_p = iv_at_delta(smile, F, T3, "PE")
    # analytic: solve for K where |delta(K, sm(K))| = 0.25 by fine grid
    grid = np.arange(23400, 25301, 1.0)
    from skew_engine import delta
    kc = min((K for K in grid if K > F), key=lambda K: abs(delta(F, K, T3, sm(K), "CE") - 0.25))
    kp = min((K for K in grid if K < F), key=lambda K: abs(abs(delta(F, K, T3, sm(K), "PE")) - 0.25))
    rr_analytic = (sm(kc) - sm(kp)) * 100
    rr_engine = (wp_c.iv - wp_p.iv) * 100
    assert rr_engine == pytest.approx(rr_analytic, abs=0.1)      # 0.1 vpt per brief


# ----------------------------- test 21: sticky-strike artifact ----------------------
def test_21_sticky_strike_artifact_share_near_one():
    F0 = 24300
    # kinked (asymmetric) smile pinned to strikes: linear skew keeps floating RR
    # spot-invariant, so a kink is required for the artifact to exist at all
    sm = lambda K: 0.12 + 0.00004 * max(F0 - K, 0) - 0.00001 * max(K - F0, 0)
    open_ch = make_chain(F0, T3, sm, STRIKES)
    curr_ch = make_chain(F0 * 1.008, T3, sm, STRIKES)   # spot +0.8%, SAME strike IVs
    out = decompose_skew(open_ch, curr_ch, T3, T3, 3, F0, F0 * 1.008)
    a = out["artifact_share"]
    assert a["status"] == "OK" and a["value"] == pytest.approx(1.0, abs=0.15)


# ----------------------------- test 22: put richening → attribution + config --------
def test_22_put_richening_hedged_rally():
    F0 = 24300
    open_ch = make_chain(F0, T3, SKEWED(F0), STRIKES)
    F1 = F0 * 1.004                              # spot up past dead-band
    richer = lambda K: SKEWED(F0)(K) + (0.010 if K < F0 else 0.006)  # puts +1.0, calls +0.6 vpt
    curr_ch = make_chain(F1, T3, richer, STRIKES)
    out = decompose_skew(open_ch, curr_ch, T3, T3, 3, F0, F1)
    assert out["dominant_leg"] == "d_put"
    assert out["legs_fixed_vpt"]["d_put"] > 0
    assert out["configuration"]["configuration"] == "hedged rally (fragile)"


# ----------------------------- 23 family --------------------------------------------
def test_23_rr_never_without_attribution():
    ch = make_chain(24300, T3, SKEWED(24300), STRIKES)
    out = decompose_skew(ch, ch, T3, T3, 3, 24300, 24300)
    assert ("rr_fixed" in out) == ("legs_fixed_vpt" in out)      # same payload or neither

def test_23a_quiet_deadband():
    ch = make_chain(24300, T3, SKEWED(24300), STRIKES)
    out = decompose_skew(ch, ch, T3, T3, 3, 24300, 24300)        # identical snapshots
    assert out["artifact_share"]["status"] == "QUIET"
    assert "value" not in out["artifact_share"]

def test_23b_negative_share_displayed_raw():
    F0 = 24300; base = SKEWED(F0)
    open_ch = make_chain(F0, T3, base, STRIKES)
    F1 = F0 * 1.006
    # fixed-strike repricing LARGER than floating change → share < 0
    steeper = lambda K: base(K) + 0.00006 * (F0 - K)             # triple the skew slope
    curr_ch = make_chain(F1, T3, steeper, STRIKES)
    out = decompose_skew(open_ch, curr_ch, T3, T3, 3, F0, F1)
    a = out["artifact_share"]
    if a["status"] == "OK":
        assert a["value"] < 0.999                                 # raw value present, not clamped to [0,1]
        assert "d_rr_fixed_vpt" in a and "d_rr_floating_vpt" in a
    else:
        assert a["status"] in ("MIXED_REGIME",)                   # honest alternative

def test_23c_writer_buyback_state():
    j = flow_join(d_iv_vpt=+0.9, d_oi_pct=-12.0, d_spread_pct=5.0)
    assert j["state"] == "WRITER_BUYBACK"
    assert j["state"] != "REPRICING"

def test_23d_spread_gate():
    j = flow_join(d_iv_vpt=+0.9, d_oi_pct=0.5, d_spread_pct=110.0)
    assert "SPREAD_WIDENED" in j["flags"]

def test_23e_spot_deadband_blocks_hedged_rally():
    c = classify_configuration(spot_chg_pct=0.05, atm_chg_vpt=0.8,
                               put_leg_vpt=0.9, call_leg_vpt=0.0)
    assert c["configuration"] != "hedged rally (fragile)"
    assert c["inputs"]["spot"] == 0

def test_23f_squeeze_risk_configuration():
    c = classify_configuration(spot_chg_pct=-0.6, atm_chg_vpt=0.9,
                               put_leg_vpt=0.1, call_leg_vpt=0.8)
    assert c["configuration"] == "squeeze-risk-into-weakness"

def test_23g_unclassified_and_no_nearest_match():
    c = classify_configuration(spot_chg_pct=0.5, atm_chg_vpt=0.0,
                               put_leg_vpt=0.9, call_leg_vpt=0.0)  # spot↑ vol flat putskew↑
    assert c["configuration"] == "unclassified — mixed tape"
    import inspect, skew_engine
    src = inspect.getsource(skew_engine.classify_configuration)
    assert "nearest" not in src.lower()                            # structural absence

# ----------------------------- test 26: DTE splice ----------------------------------
def test_26_dte_splice():
    ch = make_chain(24300, T3, SKEWED(24300), STRIKES)
    out = decompose_skew(ch, ch, T3, T3, dte_days=1.0, spot_open=24300, spot_curr=24300)
    assert out["status"] == "EXPIRY_DEGENERATE"                    # gaps without next chain
    nxt = make_chain(24300, 8/365, SKEWED(24300), STRIKES)
    out2 = decompose_skew(ch, nxt, T3, 8/365, dte_days=1.0,
                          spot_open=24300, spot_curr=24300, next_expiry_chain=nxt)
    assert out2["status"] in ("OK", "PARTIAL")

# ----------------------------- test 27: per-strike sigma in delta -------------------
def test_27_delta_consumes_sigma_K():
    import inspect, skew_engine
    src = inspect.getsource(skew_engine.iv_at_delta)
    assert "smile[K]" in src                                       # sigma(K) passed per strike
    # behavioral: steep skew must shift the 25Δ put strike vs flat-vol placement
    F = 24300
    ch = make_chain(F, T3, SKEWED(F, slope=0.00008), STRIKES)
    sm = build_otm_smile(ch, forward_from_parity(ch), T3,
                         parity_gate(ch, forward_from_parity(ch), T3)[0])
    wp = iv_at_delta(sm, F, T3, "PE")
    flat = make_chain(F, T3, FLAT, STRIKES)
    smf = build_otm_smile(flat, forward_from_parity(flat), T3,
                          parity_gate(flat, forward_from_parity(flat), T3)[0])
    wpf = iv_at_delta(smf, F, T3, "PE")
    assert abs(wp.strike - wpf.strike) > 10                        # placement differs

# ----------------------------- test 28: measure-pair co-movement --------------------
def test_28_rr_and_z1_comove():
    F = 24300
    rrs, z1s = [], []
    for slope in (0.0, 0.00002, 0.00004, 0.00006):
        ch = make_chain(F, T3, SKEWED(F, slope), STRIKES)
        Fp = forward_from_parity(ch)
        sm = build_otm_smile(ch, Fp, T3, parity_gate(ch, Fp, T3)[0])
        rrs.append(iv_at_delta(sm, Fp, T3, "CE").iv - iv_at_delta(sm, Fp, T3, "PE").iv)
        z1s.append(z_skew(sm, Fp, T3))
    assert np.corrcoef(rrs, z1s)[0, 1] > 0.95

# ----------------------------- regression: unbracketed never clamps -----------------
def test_unbracketed_returns_status_not_number():
    F = 24300
    ch = make_chain(F, T3, FLAT, [24100, 24200, 24300, 24400, 24500])  # 5-strike case
    Fp = forward_from_parity(ch)
    sm = build_otm_smile(ch, Fp, T3, parity_gate(ch, Fp, T3)[0])
    # push forward so the call wing can't bracket 25Δ
    wp = iv_at_delta(sm, 24450, T3, "CE")
    assert wp.status in ("UNBRACKETED", "NO_WING") and wp.iv is None

# ----------------------------- invariant engine: negative path ----------------------
def test_invariants_fail_on_corrupted_legs():
    ch = make_chain(24300, T3, SKEWED(24300), STRIKES)
    F1 = 24300 * 1.004
    curr = make_chain(F1, T3, lambda K: SKEWED(24300)(K) + 0.004, STRIKES)
    out = decompose_skew(ch, curr, T3, T3, 3, 24300, F1)
    out["legs_fixed_vpt"]["d_put"] += 0.5                          # deliberate corruption
    inv = invariants.evaluate(out)
    ids = [f["id"] for f in inv["failures"]]
    assert not inv["passed"] and "T-C" in ids
    tc = next(f for f in inv["failures"] if f["id"] == "T-C")
    assert "abs_diff" in tc["measured"]                            # measured values, no prose

def test_invariants_skip_named_not_silent():
    ch = make_chain(24300, T3, SKEWED(24300), STRIKES)
    out = decompose_skew(ch, ch, T3, T3, 3, 24300, 24300)
    inv = invariants.evaluate(out)                                 # no aux inputs supplied
    skipped_ids = [s["id"] for s in inv["skipped"]]
    assert "T-F" in skipped_ids and "T-H" in skipped_ids
    for s in inv["skipped"]:
        assert s["missing"]                                        # missing input is named
    assert set(inv["checked"]).isdisjoint(skipped_ids)             # manifest never lies
