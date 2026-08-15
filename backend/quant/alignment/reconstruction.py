import numpy as np
import pandas as pd

# Default fallback constituent weights (Sept 2025 weights approximate for top heavyweights)
DEFAULT_WEIGHTS = {
    "RELIANCE": 0.096,
    "HDFCBANK": 0.089,
    "ICICIBANK": 0.078,
    "INFY": 0.055,
    "LARTENT": 0.042,
    "TCS": 0.040,
    "ITC": 0.038,
    "BHARTIARTL": 0.035,
    "SBIN": 0.032,
    "KOTAKBANK": 0.029,
    "AXISBANK": 0.028,
    "HINDUNILVR": 0.025,
    # (other constituent weights default to equal splits of remaining weight to total 1.0)
}

def get_constituent_weights() -> dict:
    """
    Load Nifty constituent weights. Real system should read from a DB or config,
    but here we construct a normalized dict summing to 1.0.
    """
    total_assigned = sum(DEFAULT_WEIGHTS.values())
    rem_weight = 1.0 - total_assigned
    rem_count = 50 - len(DEFAULT_WEIGHTS)
    equal_share = rem_weight / rem_count if rem_count > 0 else 0.0
    
    weights = DEFAULT_WEIGHTS.copy()
    # Fill remaining constituents up to 50
    mock_names = [
        "LT", "M&M", "MARUTI", "TATASTEEL", "JIOFIN", "HCLTECH", "SUNPHARMA", "ADANIENT",
        "NTPC", "POWERGRID", "COALINDIA", "HINDALCO", "ULTRACEMCO", "GRASIM", "NESTLEIND",
        "JSWSTEEL", "ADANIPORTS", "ONGC", "TATACOMM", "BAJAJFINSV", "BAJFINANCE", "TITAN",
        "ASIANPAINT", "BPCL", "CIPLA", "DIVISLAB", "DRREDDY", "EICHERMOT", "HEROMOTOCO",
        "INDUSINDBK", "IOC", "LTIM", "SHRIRAMFIN", "TATAMOTORS", "TECHM", "WIPRO",
        "APOLLOHOSP", "BRITANNIA", "TRENT", "BEL", "HAL", "JINDALSTEL"
    ]
    for name in mock_names:
        if name not in weights and len(weights) < 50:
            weights[name] = equal_share
            
    # Normalize to sum to exactly 1.0
    s = sum(weights.values())
    return {k: v / s for k, v in weights.items()}

def reconstruct_index_returns(constituent_returns_df: pd.DataFrame) -> pd.Series:
    """
    Reconstruct index returns: Σ w_i * r_i
    constituent_returns_df: DataFrame where index is Datetime, columns are stock symbols, values are minute returns
    """
    weights = get_constituent_weights()
    reconstructed = pd.Series(0.0, index=constituent_returns_df.index)
    
    # Calculate weighted returns
    for sym, weight in weights.items():
        if sym in constituent_returns_df.columns:
            r = constituent_returns_df[sym].fillna(0.0)
            reconstructed += weight * r
            
    return reconstructed

def verify_reconstruction_identity(actual_returns: pd.Series, reconstructed_returns: pd.Series) -> dict:
    """
    Regress reconstructed return against actual index return using OLS in pure numpy.
    Assert R^2 > 0.97. Intercept should be close to 0.
    """
    # Align both series
    df = pd.DataFrame({'actual': actual_returns, 'recon': reconstructed_returns}).dropna()
    
    if len(df) < 30:
        return {"r2": 0.0, "intercept": 0.0, "success": False, "reason": "Insufficient data points"}
        
    x = df['recon'].values
    y = df['actual'].values
    
    # Simple linear regression parameters
    n = len(x)
    sum_x = np.sum(x)
    sum_y = np.sum(y)
    sum_xx = np.sum(x * x)
    sum_xy = np.sum(x * y)
    
    denom = (n * sum_xx - sum_x * sum_x)
    if denom == 0:
        return {"r2": 0.0, "intercept": 0.0, "success": False}
        
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    
    r2 = 1.0 - (ss_res / (ss_tot + 1e-12))
    success = r2 >= 0.97
    
    result = {
        "r2": float(r2),
        "intercept": float(intercept),
        "success": bool(success),
        "data_count": n
    }
    
    if not success:
        print(f"[RECONSTRUCTION DRIFT WARNING] R^2 is {r2:.4f} (under the 0.97 dead-band threshold)!")
        
    return result
