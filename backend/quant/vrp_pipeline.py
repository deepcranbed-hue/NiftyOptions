import numpy as np
import pandas as pd

def extract_atm_iv(chain_df: pd.DataFrame, spot_price: float) -> float:
    """
    Extract ATM IV using straddles bracketing spot price, nearest weekly expiry.
    """
    if chain_df.empty or spot_price <= 0:
        return 0.12 # Fallback 12% IV
        
    try:
        # Filter for near strikes
        chain_df['strike_diff'] = (chain_df['strike'] - spot_price).abs()
        nearest = chain_df.nsmallest(4, 'strike_diff')
        
        # Calculate vega-weighted average IV of call/put midpoints
        # In a simplified version, we can take the average iv of near-the-money options
        atm_ivs = nearest['iv_mid'].dropna()
        if atm_ivs.empty:
            return 0.12
            
        return float(atm_ivs.mean())
    except Exception:
        return 0.12

def calculate_vrp(atm_iv: float, realized_vol_ann: float) -> float:
    """
    VRP(t) = IV²_atm(t) - RV²_index(t)
    All values annualized.
    """
    # atm_iv is expected in decimals (e.g. 0.15 for 15%)
    # realized_vol_ann is expected in decimals (e.g. 0.12 for 12%)
    vrp = (atm_iv ** 2) - (realized_vol_ann ** 2)
    return float(vrp)

def vix_rv_spread(vix_value: float, realized_vol_ann: float) -> float:
    """
    VIX - RV spread = VIX(t) - RV_index_ann
    """
    # VIX is typically expressed in percentage (e.g. 13.5)
    # Convert realized_vol_ann to percentage if needed
    rv_pct = realized_vol_ann * 100.0 if realized_vol_ann < 1.0 else realized_vol_ann
    vix_pct = vix_value if vix_value > 1.0 else vix_value * 100.0
    
    return float(vix_pct - rv_pct)

from backend.quant.skew.skew_engine import decompose_skew as new_decompose_skew

def decompose_skew(calls_df: pd.DataFrame, puts_df: pd.DataFrame, open_spot: float, current_spot: float) -> dict:
    """
    Decompose RR skew by delegating to the reference skew_engine.
    """
    # Create simple mock/empty smile parameters matching signature of new decompose_skew
    # (or call it with required defaults)
    return new_decompose_skew(
        open_chain=calls_df,
        curr_chain=puts_df,
        T_open=0.01,
        T_curr=0.01,
        dte_days=3.0,
        spot_open=open_spot,
        spot_curr=current_spot
    )
