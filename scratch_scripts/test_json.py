from backend.main import run_pipeline, sanitize_floats
import json

chain = {
    "spot": 24050,
    "days": 3,
    "strikes": [24000, 24100],
    "call_ltp": [160.95, 102.45],
    "put_ltp": [64.50, 105.40],
    "r": 0.0655
}

class FakeRequest:
    chain = chain
    prev_regime = None
    half_life_hours = 12.0
    log_harness = False

try:
    res = run_pipeline(
        chain=chain,
        prev_regime="unknown",
        half_life_hours=12.0
    )
    safe = sanitize_floats(res)
    out = json.dumps(safe)
    if "NaN" in out or "Infinity" in out:
        print("FOUND BAD FLOAT IN JSON!")
        print(out[:500])
    else:
        print("JSON is clean.")
except Exception as e:
    print(f"Exception: {e}")
