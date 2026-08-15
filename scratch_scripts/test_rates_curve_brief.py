import sys
import os
import math

# Add root folder to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.quant.global_cues import curve_regime

def run_tests():
    print("Running Yield Curve Regime Classifier Tests...\n")
    
    # NEUTRAL = 0.25
    # typical G-sec vol = 3.0bp
    # Typical slope vol = 1.5bp
    
    test_cases = [
        {
            "id": 1,
            "desc": "2Y +0.6bp, 10Y +6.3bp (Bear Steepening Anchored)",
            "d2": 0.6,
            "d10": 6.3,
            "expected_regime": "BEAR_STEEPENING_ANCHORED",
            "expected_verdict": "headwind"
        },
        {
            "id": 4,
            "desc": "2Y -5bp, 10Y -1bp (Bull Steepening)",
            "d2": -5.0,
            "d10": -1.0,
            "expected_regime": "BULL_STEEPENING",
            "expected_verdict": "tailwind"
        },
        {
            "id": 5,
            "desc": "2Y +5bp, 10Y +1bp (Bear Flattening)",
            "d2": 5.0,
            "d10": 1.0,
            "expected_regime": "BEAR_FLATTENING",
            "expected_verdict": "headwind"
        },
        {
            "id": 6,
            "desc": "2Y +0.4bp, 10Y +0.3bp (Quiet/Neutral)",
            "d2": 0.4,
            "d10": 0.3,
            "expected_regime": "QUIET",
            "expected_verdict": "neutral"
        },
        {
            "id": 7,
            "desc": "10Y leg missing",
            "d2": 0.5,
            "d10": None,
            "expected_regime": "UNAVAILABLE",
            "expected_verdict": "unavailable"
        }
    ]
    
    for tc in test_cases:
        d2 = tc["d2"]
        d10 = tc["d10"]
        
        # Calculate z-scores (using standard vol = 3.0bp)
        z2 = d2 / 3.0 if d2 is not None else None
        z10 = d10 / 3.0 if d10 is not None else None
        
        regime, strength, note = curve_regime(z2, z10)
        
        verdict = "neutral"
        if strength is not None and strength > 0.10:
            verdict = "tailwind"
        elif strength is not None and strength < -0.10:
            verdict = "headwind"
        elif strength is None:
            verdict = "unavailable"
            
        print(f"Test Case {tc['id']}: {tc['desc']}")
        print(f"  Resulting Regime  : {regime}")
        print(f"  Continuous Strength: {strength}")
        print(f"  Regime Note       : {note}")
        print(f"  Verdict           : {verdict}")
        
        assert regime == tc["expected_regime"], f"Expected {tc['expected_regime']}, got {regime}"
        print(f"  --> PASSED ✅\n")

if __name__ == "__main__":
    run_tests()
