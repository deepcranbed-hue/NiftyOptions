"""
Adapter + negative-path tests for the skew integration (skew_integration_brief §3, §7 gate 3).

- The reference engine itself is covered by test_skew_invariants.py (18/18, unmodified).
- These tests cover the ONLY new code Antigravity writes: the adapter's store→engine
  contract, and the live-negative path (corrupt one leg of a REAL emission → T-C FAILED
  → the card's DATA_INCONSISTENT condition). No mocked scipy/sklearn; the emission is
  produced by the real engine on a synthetic-but-real bracketed chain.

Run with the skew package importable:
    PYTHONPATH=./backend/quant/skew:./ pytest backend/tests/test_skew_adapter.py
"""
import math

import numpy as np
import pandas as pd
import pytest

from backend.quant.skew.skew_engine import _b76_call, decompose_skew, PRIOR
from backend.quant.skew.invariants import evaluate as evaluate_invariants
from backend.quant.skew.adapter import _CP_MAP


# ---------- build a REAL bracketed chain priced from a known smile (same principle as
#            test_skew_invariants: construct INPUTS, assert on computed OUTPUTS) ----------

def _make_chain(F, T, smile_fn, strikes):
    """Price a full chain (BOTH sides at every strike, via Black-76 + parity) so the
    engine can form a forward from parity and bracket the 25Δ wings. Same construction
    as test_skew_invariants.make_chain."""
    rows = []
    for K in strikes:
        s = smile_fn(K)
        c = _b76_call(F, K, T, s)
        p = c - (F - K)  # parity (r≈0 fixture)
        for cp, mid in (("CE", c), ("PE", p)):
            rows.append({"strike": K, "cp": cp, "bid": mid - 0.5, "ask": mid + 0.5,
                         "mid": mid, "oi": 1000})
    return pd.DataFrame(rows)


def _real_emission():
    F = 24300.0
    T = 30 / 365.0
    strikes = list(range(23400, 25301, 50))     # wide grid so 25Δ is always bracketed
    open_sm = lambda K: 0.12 + 0.00002 * (F - K)   # put skew
    curr_sm = lambda K: 0.13 + 0.00003 * (F - K)   # vol up, skew steeper
    open_chain = _make_chain(F, T, open_sm, strikes)
    curr_chain = _make_chain(F, T * 0.9, curr_sm, strikes)
    return decompose_skew(open_chain, curr_chain, T_open=T, T_curr=T * 0.9,
                          dte_days=27.0, spot_open=F, spot_curr=F * 1.002, pr=PRIOR)


def test_engine_produces_bracketed_emission_for_fixture():
    em = _real_emission()
    assert em["status"] in ("OK", "PARTIAL")
    # this fixture is dense enough to bracket the 25Δ wings on both snapshots
    assert em.get("rr_fixed") is not None
    assert em.get("legs_fixed_vpt") is not None


def test_cp_map_covers_store_and_engine_vocab():
    assert _CP_MAP["call"] == "CE"
    assert _CP_MAP["put"] == "PE"
    assert _CP_MAP["CE"] == "CE" and _CP_MAP["PE"] == "PE"


# ---------- GATE 3: live negative path — corrupt one fixed leg → T-C FAILED ----------

def test_corrupted_fixed_leg_fails_TC_and_flags_data_inconsistent():
    em = _real_emission()

    # sanity: on the untouched real emission, T-C is an accounting identity that holds
    clean = evaluate_invariants(em)
    tc_clean = next((r for r in clean["checked"] if r == "T-C"), None)
    assert tc_clean == "T-C"  # T-C was actually evaluated (not skipped)
    assert all(f["id"] != "T-C" for f in clean["failures"])

    # corrupt exactly one leg of the REAL emission so the RR-vs-leg identity breaks
    em_bad = {**em, "legs_fixed_vpt": {**em["legs_fixed_vpt"],
                                       "d_call": em["legs_fixed_vpt"]["d_call"] + 5.0}}
    bad = evaluate_invariants(em_bad)

    assert bad["passed"] is False
    tc = next((f for f in bad["failures"] if f["id"] == "T-C"), None)
    assert tc is not None, "T-C must FAIL when a fixed leg is corrupted"
    assert tc["result"] == "FAILED"
    assert "measured" in tc and "rule" in tc          # measured values + rule, never prose
    # this is exactly the condition the UI switches DATA_INCONSISTENT on:
    assert bad["passed"] is False


def test_missing_aux_inputs_report_skipped_not_fabricated():
    em = _real_emission()
    block = evaluate_invariants(em)  # all aux streams None
    # T-A needs floating legs; with none supplied it must be SKIPPED, never silently passed
    skipped_ids = {s["id"] for s in block["skipped"]}
    assert "T-A" in skipped_ids
    ta = next(s for s in block["skipped"] if s["id"] == "T-A")
    assert ta["missing"]  # names the missing input
