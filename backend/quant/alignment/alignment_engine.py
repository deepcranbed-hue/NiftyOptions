import numpy as np
import pandas as pd
from datetime import datetime, time, timedelta

def project_to_grid(bars: list, symbol: str) -> pd.DataFrame:
    """
    Project raw minute bars onto a canonical daily trading grid.
    Grid: 09:15 to 15:30 IST (375 bars per trading day).
    Missing minutes will have volume = 0/NaN and price as NaN (no forward-filling).
    bars: list of tuples/dicts with keys (ts, open, high, low, close, volume)
          ts is in ISO format or datetime
    """
    if not bars:
        return pd.DataFrame()
        
    df = pd.DataFrame(bars)
    df['ts'] = pd.to_datetime(df['ts'])
    if df['ts'].dt.tz is not None:
        df['ts'] = df['ts'].dt.tz_convert(None)
    df.set_index('ts', inplace=True)
    
    # Filter active market sessions
    df = df.between_time('09:15', '15:30')
    if df.empty:
        return pd.DataFrame()
        
    # Build complete grid for all days represented in the data
    days = np.unique(df.index.date)
    all_minutes = []
    for d in days:
        start_dt = datetime.combine(d, time(9, 15))
        end_dt = datetime.combine(d, time(15, 30))
        curr = start_dt
        while curr <= end_dt:
            all_minutes.append(curr)
            curr += timedelta(minutes=1)
            
    grid_idx = pd.DatetimeIndex(all_minutes)
    
    # Reindex to grid (creates NaNs for missing minutes)
    df_grid = df.reindex(grid_idx)
    df_grid.index.name = 'ts'
    
    return df_grid

def bad_tick_filter(returns: pd.Series, rolling_vol: pd.Series) -> pd.Series:
    """
    Reject bad ticks: returns where |r| > 8 * rolling_60m_vol AND there is
    an immediate full reversal next bar (i.e. signs are opposite and magnitude matches within 20%).
    Returns a boolean mask where True means rejected/bad print.
    """
    rejected = pd.Series(False, index=returns.index)
    
    for i in range(1, len(returns) - 1):
        r_t = returns.iloc[i]
        r_next = returns.iloc[i+1]
        vol = rolling_vol.iloc[i]
        
        if pd.isna(r_t) or pd.isna(r_next) or pd.isna(vol) or vol == 0:
            continue
            
        if abs(r_t) > 8 * vol:
            # Reversal check: opposite sign and comparable magnitude (within 20% tolerance)
            if np.sign(r_t) != np.sign(r_next) and abs(abs(r_t) - abs(r_next)) / abs(r_t) < 0.20:
                rejected.iloc[i] = True
                # Log bad prints for provenance
                print(f"[BAD PRINT DETECTED] at {returns.index[i]}: r={r_t:.4f}, rolling_vol={vol:.4f}")
                
    return rejected

def join_asof(left_df: pd.DataFrame, right_df: pd.DataFrame, direction="backward") -> pd.DataFrame:
    """
    Join option chain snapshots to the canonical minute grid.
    left_df: canonical minute grid (index is 'ts' datetime)
    right_df: snapshots (index or column is 'ts' datetime)
    """
    if left_df.empty or right_df.empty:
        return left_df
        
    left_sorted = left_df.sort_index()
    
    if 'ts' not in right_df.columns and right_df.index.name == 'ts':
        right_sorted = right_df.sort_index()
    else:
        right_sorted = right_df.sort_values('ts').set_index('ts')
        
    joined = pd.merge_asof(
        left_sorted,
        right_sorted,
        left_index=True,
        right_index=True,
        direction=direction
    )
    
    return joined
