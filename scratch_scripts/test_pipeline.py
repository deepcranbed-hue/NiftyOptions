import sys
from backend.main import api_run_pipeline, PipelineRequest

req = PipelineRequest(
    articles=[],
    chain={
        "strikes": [24000],
        "call_ltp": [100.0],
        "put_ltp": [100.0],
        "spot": 24000.0,
        "days": 5.0,
        "r": 0.0655
    },
    prev_regime=None
)

try:
    print(api_run_pipeline(req))
except Exception as e:
    print(f"Error: {e}")
