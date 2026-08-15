"""
Tests for the constituent correlation / dispersion engine (brief §2, D-MA-02 / D-MA-02a).

These exercise the REAL sklearn.covariance.LedoitWolf path and the no-fabrication
missing-minute policy. No mocked dependencies (evidence rule: a mocked scipy/sklearn
would invalidate the run).
"""
import math

import numpy as np
import pandas as pd
import pytest

from backend.quant.dispersion_engine import (
    compute_ledoit_wolf_correlation,
    compute_dispersion,
    zscore_stat,
    PRIOR,
    THRESHOLDS,
    thresholds_manifest,
)


def _factor_returns(T, N, beta, noise, seed):
    """One common factor F plus idiosyncratic noise → known positive correlation."""
    rng = np.random.default_rng(seed)
    F = rng.normal(0.0, 1.0, T)
    eps = rng.normal(0.0, noise, (T, N))
    r = beta * F[:, None] + eps
    cols = [f"STK{i}" for i in range(N)]
    return pd.DataFrame(r, columns=cols)


# ---------- real Ledoit-Wolf recovers structure ----------

def test_high_common_factor_gives_high_positive_correlation():
    df = _factor_returns(T=180, N=12, beta=0.9, noise=0.4, seed=1)
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "OK"
    # analytic pairwise corr ~ 0.81/(0.81+0.16) ≈ 0.83; shrinkage pulls it in a little
    assert 0.4 < res["corr_avg"] < 0.95
    assert res["n_constituents"] == 12


def test_independent_names_give_near_zero_correlation():
    df = _factor_returns(T=180, N=12, beta=0.0, noise=1.0, seed=2)
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "OK"
    assert abs(res["corr_avg"]) < 0.3


def test_shrinkage_is_estimated_not_constant():
    """Regression guard: the old mock hardcoded 0.20. A real estimate is data-dependent."""
    dense = _factor_returns(T=200, N=15, beta=0.7, noise=0.5, seed=3)
    sparse = _factor_returns(T=40, N=15, beta=0.7, noise=0.5, seed=4)
    s_dense = compute_ledoit_wolf_correlation(dense)["shrinkage_intensity"]
    s_sparse = compute_ledoit_wolf_correlation(sparse)["shrinkage_intensity"]
    for s in (s_dense, s_sparse):
        assert 0.0 <= s <= 1.0
    # a shorter, noisier window should need at least as much shrinkage
    assert s_sparse >= s_dense
    # and it must not be pinned to the old constant on both
    assert not (s_dense == pytest.approx(0.20) and s_sparse == pytest.approx(0.20))


# ---------- no-fabrication missing-minute policy ----------

def test_gappy_constituent_dropped_by_coverage_gate_not_filled():
    df = _factor_returns(T=180, N=8, beta=0.8, noise=0.5, seed=5)
    # blank 50% of one column → coverage 0.5 < 0.80 → that name is dropped, never imputed
    df.iloc[:90, 3] = np.nan
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "OK"
    assert res["n_constituents"] == 7          # the gappy name removed
    assert res["n_obs"] == 180                 # remaining names still fully observed


def test_small_gap_drops_rows_listwise_no_forward_fill():
    df = _factor_returns(T=180, N=8, beta=0.8, noise=0.5, seed=6)
    df.iloc[100:110, 2] = np.nan               # 10-min halt, coverage still > 0.80
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "OK"
    assert res["n_constituents"] == 8          # name kept (coverage ok)
    assert res["n_obs"] == 170                 # the 10 NaN minutes deleted, not filled


def test_insufficient_observations_returns_null_and_flag():
    df = _factor_returns(T=10, N=8, beta=0.8, noise=0.5, seed=7)  # < corr_min_obs (20)
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "INSUFFICIENT_DATA"
    assert res["flag"] == "TOO_FEW_OBS"
    assert res["corr_avg"] is None
    assert res["shrinkage_intensity"] is None


def test_single_constituent_returns_null():
    df = _factor_returns(T=180, N=1, beta=0.8, noise=0.5, seed=8)
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "INSUFFICIENT_DATA"
    assert res["corr_avg"] is None


def test_empty_frame_returns_null():
    res = compute_ledoit_wolf_correlation(pd.DataFrame())
    assert res["status"] == "INSUFFICIENT_DATA"
    assert res["flag"] == "NO_RETURNS"
    assert res["corr_avg"] is None


def test_zero_variance_column_excluded():
    df = _factor_returns(T=180, N=6, beta=0.8, noise=0.5, seed=9)
    df["FLAT"] = 0.0                            # a stuck/flat name
    res = compute_ledoit_wolf_correlation(df)
    assert res["status"] == "OK"
    assert res["n_constituents"] == 6          # FLAT dropped, 6 real names remain


# ---------- dispersion: exclusion, not imputation ----------

def test_dispersion_excludes_missing_not_imputes_zero():
    # minute 0: three names present; minute 1: one name halted (NaN)
    df = pd.DataFrame({
        "A": [0.01, 0.02],
        "B": [-0.01, 0.00],
        "C": [0.00, np.nan],
    })
    honest = compute_dispersion(df)
    # fabricated comparison: if we (wrongly) imputed 0.0 for the halt, the minute-1
    # cross-section would include a spurious 0-return name → a different dispersion.
    fabricated = compute_dispersion(df.fillna(0.0))
    assert honest is not None
    assert honest != pytest.approx(fabricated)


def test_dispersion_none_on_empty():
    assert compute_dispersion(pd.DataFrame()) is None


def test_dispersion_skips_thin_minutes():
    # only one name ever present per minute → cross-section too thin every row → None
    df = pd.DataFrame({
        "A": [0.01, np.nan],
        "B": [np.nan, 0.02],
    })
    assert compute_dispersion(df) is None


# ---------- threshold registry provenance (Immutable Rule #3) ----------

def test_thresholds_registry_has_provenance():
    for key, meta in THRESHOLDS.items():
        assert meta["tag"] in {"PRIOR", "STRUCT", "DERIVED", "OVERRIDE"}
        assert meta["graduation"]
        assert meta["rationale"]


def test_manifest_marks_overrides():
    over = dict(PRIOR)
    over["corr_min_obs"] = 999
    manifest = thresholds_manifest(over)
    assert manifest["corr_min_obs"]["tag"] == "OVERRIDE"
    assert manifest["corr_min_coverage_frac"]["tag"] == "PRIOR"


# ---------- trailing z-scores (D-MA-02b) ----------

def test_zscore_ok_computes_z_and_tanh_strength():
    history = [0.0] * 15 + [1.0] * 15   # mean 0.5, well-defined std
    res = zscore_stat(2.0, history)
    assert res["status"] == "OK"
    assert res["n_history"] == 30
    # z = (2.0 - mean) / std ; strength = tanh(z/2) and same sign as z
    assert res["z"] > 0
    assert res["strength"] == pytest.approx(math.tanh(res["z"] / 2.0))
    assert -1.0 <= res["strength"] <= 1.0


def test_zscore_insufficient_history_returns_null():
    res = zscore_stat(0.6, [0.5, 0.55, 0.52])   # < z_min_history (20)
    assert res["status"] == "INSUFFICIENT_HISTORY"
    assert res["z"] is None
    assert res["strength"] is None


def test_zscore_none_current_returns_null():
    res = zscore_stat(None, [0.1] * 30)
    assert res["status"] == "NO_CURRENT"
    assert res["z"] is None


def test_zscore_zero_variance_history_returns_null():
    res = zscore_stat(0.5, [0.5] * 30)          # flat history → std 0
    assert res["status"] == "ZERO_VARIANCE_HISTORY"
    assert res["z"] is None


def test_zscore_ignores_nulls_in_history():
    history = [None] * 5 + list(np.linspace(0.0, 1.0, 25))
    res = zscore_stat(0.5, history)
    assert res["status"] == "OK"
    assert res["n_history"] == 25               # the 5 Nones excluded, not counted
