import sqlite3
import sys
from datetime import datetime

sys.path.append('.')
sys.path.append('./backend/quant/skew')

from chain_store import DB_PATH
from backend.quant.skew.adapter import load_chain_snapshot
from backend.quant.skew.skew_engine import forward_from_parity, parity_gate, decompose_skew, PRIOR

conn = sqlite3.connect(DB_PATH)
captures = [r[0] for r in conn.execute(
    "SELECT captured_at FROM captures WHERE captured_at LIKE '2026-07-06%' OR captured_at LIKE '2026-07-07%' ORDER BY captured_at ASC"
).fetchall()]
conn.close()

custom_pr = PRIOR.copy()
custom_pr["parity_gap_tol_vpt"] = 3.0

print(f"Checking {len(captures)} captures...")
for cap_ts in captures:
    date = cap_ts.split("T")[0]
    
    # We want to check this specific snapshot as target_time
    expiry = "2026-07-07T06:00:00.000Z"
    next_expiry = "2026-07-14T06:00:00.000Z"
    
    open_df, open_ts, spot_open = load_chain_snapshot(expiry, target_time=cap_ts, is_open=True)
    curr_df, curr_ts, spot_curr = load_chain_snapshot(expiry, target_time=cap_ts, is_open=False)
    next_df, next_ts, spot_next = load_chain_snapshot(next_expiry, target_time=cap_ts, is_open=False)
    
    if open_df is None or curr_df is None or next_df is None:
        continue
        
    open_dt = datetime.fromisoformat(open_ts.replace('Z', '+00:00'))
    curr_dt = datetime.fromisoformat(curr_ts.replace('Z', '+00:00'))
    
    # Option B: Proposed logic (forcing 10:00Z)
    exp_dt_b = datetime.fromisoformat(expiry.replace('Z', '+00:00')).replace(hour=10, minute=0, second=0, microsecond=0)
    T_open_b = (exp_dt_b - open_dt).total_seconds() / (365.25 * 86400.0)
    T_curr_b = (datetime.fromisoformat(next_expiry.replace('Z', '+00:00')).replace(hour=10, minute=0, second=0, microsecond=0) - curr_dt).total_seconds() / (365.25 * 86400.0)
    dte_days_b = (exp_dt_b - curr_dt).total_seconds() / 86400.0
    
    # If dte_days_b < 0.20, it is degenerate, skip
    if dte_days_b < 0.20:
        continue
        
    em = decompose_skew(open_df, curr_df, T_open_b, T_curr_b, dte_days_b, spot_open, spot_curr, next_expiry_chain=next_df, pr=custom_pr)
    if "legs_fixed_vpt" in em:
        dp = em['legs_fixed_vpt'].get('d_put', 0)
        dc = em['legs_fixed_vpt'].get('d_call', 0)
        if abs(dp) > 10.0 or abs(dc) > 10.0:
            print(f"VIOLATION FOUND AT {cap_ts} (DTE {dte_days_b:.4f}): d_put={dp:.3f}, d_call={dc:.3f}")
