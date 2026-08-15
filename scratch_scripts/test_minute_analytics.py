import sys
import os
import pandas as pd
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.quant.alignment.alignment_engine import project_to_grid, bad_tick_filter, join_asof
from backend.quant.alignment.reconstruction import reconstruct_index_returns, verify_reconstruction_identity
from backend.quant.dispersion_engine import compute_ledoit_wolf_correlation, compute_dispersion, detect_concentration, detect_pump_reverse
from backend.quant.vrp_pipeline import vix_rv_spread, calculate_vrp

def run_tests():
    print("Running Minute-Data Analytics test suite...\n")
    
    # 1. Test Grid Projection & NaNs
    raw_bars = [
        {"ts": "2026-07-06T09:15:00.000Z", "open": 24300, "high": 24310, "low": 24295, "close": 24305, "volume": 100},
        {"ts": "2026-07-06T09:17:00.000Z", "open": 24305, "high": 24320, "low": 24300, "close": 24315, "volume": 150}, # 09:16 is missing
    ]
    df_grid = project_to_grid(raw_bars, "NIFTY")
    print(f"Grid projection shape: {df_grid.shape}")
    assert len(df_grid) == 376, "Grid must project to exactly 376 minute bars (inclusive of 09:15 and 15:30) for a single day"
    assert pd.isna(df_grid.loc["2026-07-06 09:16:00", "close"]), "Missing bar must render NaN close"
    print("  Grid Projection & NaN test --> PASSED ✅\n")
    
    # 2. Test Bad Tick Filter
    returns = pd.Series([0.001, -0.002, 0.05, -0.048, 0.001])
    vol = pd.Series([0.002, 0.002, 0.002, 0.002, 0.002])
    mask = bad_tick_filter(returns, vol)
    assert mask.iloc[2] == True, "Spike + reversal tick must be filtered"
    assert mask.iloc[0] == False, "Normal tick must not be filtered"
    print("  Bad Tick Filter test --> PASSED ✅\n")
    
    # 3. Test As-Of Join backward constraint
    grid_df = pd.DataFrame({"dummy": [1, 2, 3]}, index=pd.to_datetime(["2026-07-06T10:00:00", "2026-07-06T10:01:00", "2026-07-06T10:02:00"]))
    snap_df = pd.DataFrame({"iv": [12.5, 13.0]}, index=pd.to_datetime(["2026-07-06T10:00:30", "2026-07-06T10:01:30"]))
    snap_df.index.name = 'ts'
    joined = join_asof(grid_df, snap_df)
    assert pd.isna(joined.loc["2026-07-06T10:00:00", "iv"]), "10:00:00 bar cannot see 10:00:30 snapshot"
    assert joined.loc["2026-07-06T10:01:00", "iv"] == 12.5, "10:01:00 bar must see 10:00:30 snapshot"
    print("  As-Of Join Backward Contract test --> PASSED ✅\n")
    
    # 4. Test Reconstruction Identity
    stock_returns = pd.DataFrame({
        "RELIANCE": [0.001, -0.001],
        "HDFCBANK": [0.002, -0.002]
    })
    recon = reconstruct_index_returns(stock_returns)
    assert abs(recon.iloc[0] - (0.096*0.001 + 0.089*0.002)) < 1e-6, "Reconstructed return math incorrect"
    print("  Index Return Reconstruction math test --> PASSED ✅\n")
    
    # 5. Test Ledoit-Wolf correlation and dispersion
    synth_returns = pd.DataFrame(np.random.normal(0, 0.01, (60, 50)))
    avg_corr, shrinkage = compute_ledoit_wolf_correlation(synth_returns)
    disp = compute_dispersion(synth_returns)
    print(f"Ledoit-Wolf avg correlation: {avg_corr:.4f}, shrinkage intensity: {shrinkage:.4f}")
    print(f"Dispersion: {disp:.4f}")
    assert avg_corr is not None
    assert disp > 0
    print("  Ledoit-Wolf & Dispersion test --> PASSED ✅\n")
    
    # 6. Test Flow Anomaly Detectors
    # Pump and reverse sequence for a single heavyweight
    pump_rev_returns = pd.DataFrame(0.0, index=range(60), columns=["RELIANCE"])
    pump_rev_returns.loc[:29, "RELIANCE"] = 0.01
    pump_rev_returns.loc[30:, "RELIANCE"] = -0.008
    pump_rev_score = detect_pump_reverse(pump_rev_returns, pd.DataFrame(1.0, index=range(60), columns=["RELIANCE"]))
    print(f"Pump and reverse score: {pump_rev_score:.4f}")
    assert pump_rev_score > 0, "Pump & reverse detector failed to fire on reversal sequence"
    print("  Flow Anomaly Detector test --> PASSED ✅\n")
    
    # 7. Test Skew Invariant validations (T-A to T-I)
    from backend.quant.vrp_pipeline import decompose_skew
    
    # Run decompose_skew
    skew_res = decompose_skew(pd.DataFrame(), pd.DataFrame(), 24300, 24355)
    print(f"Skew Invariants Results: {skew_res['invariants']}")
    
    # Verify invariants format and checks
    assert "invariants" in skew_res, "Emission payload must contain 'invariants' key"
    assert "passed" in skew_res["invariants"], "invariants payload must have 'passed'"
    assert "failures" in skew_res["invariants"], "invariants payload must have 'failures' list"
    
    # Verify positive-path checks
    assert skew_res["invariants"]["passed"] == True, "Default skew estimation should pass validation"
    print("  Skew Invariant Validation test --> PASSED ✅\n")
    
    print("All tests PASSED successfully! 🎉")

if __name__ == "__main__":
    run_tests()
