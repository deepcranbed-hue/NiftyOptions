import sys
import os
import math
import types

# Add nefile folder to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Mock pytest
mock_pytest = types.ModuleType("pytest")
def approx(expected, abs=1e-6):
    abs_val = abs
    class ApproxVal:
        def __init__(self, val, abs_tol):
            self.val = val
            self.abs_tol = abs_tol
        def __eq__(self, other):
            diff = self.val - other
            if diff < 0:
                diff = -diff
            return diff <= self.abs_tol
        def __repr__(self):
            return f"approx({self.val}, abs={self.abs_tol})"
    return ApproxVal(expected, abs_val)

mock_pytest.approx = approx
sys.modules["pytest"] = mock_pytest

# Mock scipy
mock_scipy = types.ModuleType("scipy")
mock_stats = types.ModuleType("scipy.stats")
class MockNorm:
    @staticmethod
    def cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    @staticmethod
    def pdf(x):
        return math.exp(-x**2 / 2.0) / math.sqrt(2.0 * math.pi)

mock_stats.norm = MockNorm
sys.modules["scipy"] = mock_scipy
sys.modules["scipy.stats"] = mock_stats

mock_optimize = types.ModuleType("scipy.optimize")
def brentq_mock(f, a, b, **kwargs):
    fa = f(a)
    fb = f(b)
    if fa * fb > 0:
        return (a + b) / 2.0
    for _ in range(100):
        mid = (a + b) / 2.0
        fmid = f(mid)
        if abs(fmid) < 1e-12 or abs(b - a) < 1e-12:
            return mid
        if fa * fmid < 0:
            b = mid
            fb = fmid
        else:
            a = mid
            fa = fmid
    return (a + b) / 2.0

mock_optimize.brentq = brentq_mock
sys.modules["scipy.optimize"] = mock_optimize

import test_skew_invariants as ts

def run():
    print("Running adopting reference skew implementation tests (18/18 checks)...")
    tests = [
        ts.test_24_flat_smile_zero_rr,
        ts.test_25_interpolated_rr_matches_analytic,
        ts.test_21_sticky_strike_artifact_share_near_one,
        ts.test_22_put_richening_hedged_rally,
        ts.test_23_rr_never_without_attribution,
        ts.test_23a_quiet_deadband,
        ts.test_23b_negative_share_displayed_raw,
        ts.test_23c_writer_buyback_state,
        ts.test_23d_spread_gate,
        ts.test_23e_spot_deadband_blocks_hedged_rally,
        ts.test_23f_squeeze_risk_configuration,
        ts.test_23g_unclassified_and_no_nearest_match,
        ts.test_26_dte_splice,
        ts.test_27_delta_consumes_sigma_K,
        ts.test_28_rr_and_z1_comove,
        ts.test_unbracketed_returns_status_not_number,
        ts.test_invariants_fail_on_corrupted_legs,
        ts.test_invariants_skip_named_not_silent
    ]
    
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  {t.__name__} --> PASSED ✅")
            passed += 1
        except Exception as e:
            print(f"  {t.__name__} --> FAILED ❌: {e}")
            import traceback
            traceback.print_exc()
            
    print(f"\nResult: {passed}/{len(tests)} tests passed.")
    if passed == len(tests):
        print("Adoption validation: SUCCESS! 🎉")
    else:
        sys.exit(1)

if __name__ == "__main__":
    run()
