from typing import Optional
import numpy as np
import pandas as pd
import math
from sklearn.covariance import LedoitWolf
from backend.quant.alignment.reconstruction import get_constituent_weights

# --- Threshold registry (mirrors backend/quant/skew/skew_engine.py convention) ---
# Every gate used by the correlation/dispersion engine lives here with a provenance
# tag and a graduation path. No numeric literals in signal code outside this registry
# (Immutable Rule #3). See D-MA-02a in DECISIONS.md.
THRESHOLDS = {
    "corr_min_obs": {
        "value": 20, "tag": "PRIOR",
        "rationale": "minimum surviving 1-min rows after listwise NaN deletion before a "
                     "Ledoit-Wolf estimate is trusted. Below this the shrunk correlation "
                     "is dominated by the prior, not the data.",
        "graduation": "set to the row count at which shrinkage_ stabilises, measured over "
                      "60 sessions of real minute history"},
    "corr_min_coverage_frac": {
        "value": 0.80, "tag": "PRIOR",
        "rationale": "a constituent must have valid returns on at least this fraction of "
                     "window minutes to enter the covariance estimate; gappier names are "
                     "dropped rather than filled (halted-stock handling, brief AT #3).",
        "graduation": "z-score each name's coverage vs its own trailing coverage after 60 sessions"},
    "corr_min_constituents": {
        "value": 2, "tag": "STRUCT",
        "rationale": "a pairwise correlation is undefined with fewer than 2 surviving "
                     "constituents. Structural, not calibratable.",
        "graduation": "n/a"},
    "z_min_history": {
        "value": 20, "tag": "PRIOR",
        "rationale": "minimum non-null trailing observations before a z-score of corr_avg / "
                     "dispersion is emitted; below this the trailing mean/std are unstable and "
                     "the z is meaningless. Status INSUFFICIENT_HISTORY is shown instead.",
        "graduation": "replace with trailing distribution over >= 60 sessions (D-MA-04)"},
}
PRIOR = {k: v["value"] for k, v in THRESHOLDS.items()}   # runtime values


def thresholds_manifest(pr: dict) -> dict:
    """Per-parameter provenance block for the emission. Overridden values are tagged OVERRIDE."""
    out = {}
    for k, meta in THRESHOLDS.items():
        v = pr.get(k, meta["value"])
        out[k] = {"value": v, "tag": meta["tag"] if v == meta["value"] else "OVERRIDE",
                  "graduation": meta["graduation"]}
    return out


def compute_ledoit_wolf_correlation(returns_df: pd.DataFrame, pr: Optional[dict] = None) -> dict:
    """
    Weighted average pairwise constituent correlation from 1-min returns, using a real
    Ledoit-Wolf shrunk covariance (sklearn.covariance.LedoitWolf) per D-MA-02 / D-MA-02a.

    Missing-minute policy (NO fabrication — Immutable Rules #1/#2):
      1. Drop constituents whose valid-return coverage < corr_min_coverage_frac.
      2. Listwise-delete any remaining rows containing NaN (no fillna, no forward-fill).
      3. If < corr_min_obs rows or < corr_min_constituents columns survive, return
         corr_avg=None, shrinkage_intensity=None, status="INSUFFICIENT_DATA" + flag.

    Returns a dict:
      {corr_avg, shrinkage_intensity, n_obs, n_constituents, status, flag}
    corr_avg / shrinkage_intensity are None (→ NULL for callers) whenever unevaluable.
    """
    pr = pr or PRIOR
    min_obs = pr["corr_min_obs"]
    min_cov = pr["corr_min_coverage_frac"]
    min_cons = pr["corr_min_constituents"]

    def _insufficient(flag: str, n_obs: int, n_cons: int) -> dict:
        return {"corr_avg": None, "shrinkage_intensity": None,
                "n_obs": int(n_obs), "n_constituents": int(n_cons),
                "status": "INSUFFICIENT_DATA", "flag": flag}

    if returns_df is None or returns_df.empty:
        return _insufficient("NO_RETURNS", 0, 0)

    # (1) coverage gate — drop gappy constituents, never fill them
    coverage = returns_df.notna().mean(axis=0)
    kept_cols = coverage[coverage >= min_cov].index
    df = returns_df[kept_cols]
    if df.shape[1] < min_cons:
        return _insufficient("TOO_FEW_CONSTITUENTS", df.shape[0], df.shape[1])

    # (2) listwise deletion of rows still holding any NaN — no fabrication
    df = df.dropna(axis=0, how="any")
    n_obs, n_cons = df.shape[0], df.shape[1]
    if n_obs < min_obs:
        return _insufficient("TOO_FEW_OBS", n_obs, n_cons)
    if n_cons < min_cons:
        return _insufficient("TOO_FEW_CONSTITUENTS", n_obs, n_cons)

    # (3) real Ledoit-Wolf shrinkage on the standardized returns
    std = df.std(axis=0)
    if (std <= 0).any():
        # a zero-variance (flat) column makes the correlation undefined for that name
        df = df.loc[:, std > 0]
        n_cons = df.shape[1]
        if n_cons < min_cons:
            return _insufficient("ZERO_VARIANCE_COLUMNS", n_obs, n_cons)

    lw = LedoitWolf().fit(df.values)
    cov = lw.covariance_
    shrinkage = float(lw.shrinkage_)          # analytically ESTIMATED, not hardcoded

    d_sqrt = np.sqrt(np.diag(cov))
    corr = cov / np.outer(d_sqrt, d_sqrt)
    corr = np.clip(corr, -1.0, 1.0)

    # weighted mean of off-diagonal correlations (weights = index constituent weights)
    weights = get_constituent_weights()
    cols = df.columns
    w_vec = np.array([weights.get(c, 1.0 / len(cols)) for c in cols], dtype=float)
    w_vec = w_vec / w_vec.sum()

    iu = np.triu_indices(n_cons, k=1)
    pair_w = w_vec[iu[0]] * w_vec[iu[1]]
    avg_corr = float(np.sum(corr[iu] * pair_w) / np.sum(pair_w))

    return {"corr_avg": avg_corr, "shrinkage_intensity": shrinkage,
            "n_obs": int(n_obs), "n_constituents": int(n_cons),
            "status": "OK", "flag": None}

def compute_effective_correlation(rv_index: float, rv_basket: float) -> float:
    """
    ρ_eff(t) = RV²_index / RV²_basket
    """
    if rv_basket == 0:
        return 0.0
    return float((rv_index ** 2) / (rv_basket ** 2))


def zscore_stat(current: Optional[float], history, pr: Optional[dict] = None) -> dict:
    """
    Z-score of `current` against its trailing distribution `history` (brief §2, D-MA-02b),
    with the shared tanh-strength convention (strength = tanh(z/2), same as
    global_cues.cue_strength).

    NO fabrication: a z is only emitted when there is a real distribution to score against.
    Returns {z, strength, status, n_history}:
      - current is None                          -> z=None, status="NO_CURRENT"
      - < z_min_history non-null trailing points -> z=None, status="INSUFFICIENT_HISTORY"
      - trailing std == 0                         -> z=None, status="ZERO_VARIANCE_HISTORY"
      - otherwise                                 -> z, strength, status="OK"
    """
    pr = pr or PRIOR
    min_hist = pr["z_min_history"]

    if current is None:
        return {"z": None, "strength": None, "status": "NO_CURRENT", "n_history": 0}

    clean = [float(h) for h in (history or []) if h is not None and not (isinstance(h, float) and math.isnan(h))]
    if len(clean) < min_hist:
        return {"z": None, "strength": None, "status": "INSUFFICIENT_HISTORY", "n_history": len(clean)}

    mu = float(np.mean(clean))
    sd = float(np.std(clean, ddof=1))
    if sd <= 0.0:
        return {"z": None, "strength": None, "status": "ZERO_VARIANCE_HISTORY", "n_history": len(clean)}

    z = (float(current) - mu) / sd
    return {"z": float(z), "strength": float(math.tanh(z / 2.0)),
            "status": "OK", "n_history": len(clean)}

def compute_dispersion(returns_df: pd.DataFrame, pr: Optional[dict] = None) -> Optional[float]:
    """
    Dispersion D(t): cross-sectional weighted standard deviation of constituent returns,
    averaged over the window (CSAD-style, brief §2).

    NO fabrication (Immutable Rules #1/#2): a constituent that is halted/missing in a
    given minute is *excluded* from that minute's cross-section (weights renormalised over
    present names) — it is never imputed as a 0.0 return. Minutes with fewer than
    corr_min_constituents present names are dropped. Returns None if no minute survives.
    """
    pr = pr or PRIOR
    min_cons = pr["corr_min_constituents"]

    if returns_df is None or returns_df.empty:
        return None

    weights = get_constituent_weights()
    cols = list(returns_df.columns)
    w_full = np.array([weights.get(c, 1.0 / len(cols)) for c in cols], dtype=float)

    vals = returns_df.values  # rows=minutes, cols=constituents; NaN = absent that minute
    var_list = []
    for row in vals:
        present = ~np.isnan(row)
        if present.sum() < min_cons:
            continue  # too thin a cross-section this minute — skip, do not fabricate
        r = row[present]
        w = w_full[present]
        w = w / w.sum()  # renormalise over names actually present
        mean = float(np.dot(r, w))
        var_t = float(np.dot((r - mean) ** 2, w))
        var_list.append(var_t)

    if not var_list:
        return None
    return float(np.sqrt(np.mean(var_list)))

def amihud_illiquidity_breadth(returns_df: pd.DataFrame, rupee_vol_df: pd.DataFrame) -> float:
    """
    Amihud breadth = fraction of constituents whose Amihud z exceeds +1.
    Amihud per stock = |r| / rupee_volume
    """
    if returns_df.empty or rupee_vol_df.empty:
        return 0.0
        
    amihud_vals = {}
    for col in returns_df.columns:
        if col in rupee_vol_df.columns:
            r = returns_df[col].abs()
            vol = rupee_vol_df[col]
            # Avoid divide by zero
            ratio = r / (vol + 1.0)
            amihud_vals[col] = ratio.mean()
            
    if not amihud_vals:
        return 0.0
        
    s = pd.Series(amihud_vals)
    z = (s - s.mean()) / (s.std() + 1e-8)
    
    # Fraction above +1 z-score
    high_illiquid = (z > 1.0).sum()
    return float(high_illiquid / len(s))

def detect_concentration(returns_df: pd.DataFrame, index_returns: pd.Series, rupee_vol_df: pd.DataFrame) -> float:
    """
    Detector 1: move concentration.
    Herfindahl H of contributions (w_i * r_i) * mean abnormal volume z-score of top-3 contributors.
    """
    if returns_df.empty or len(index_returns) == 0:
        return 0.0
        
    weights = get_constituent_weights()
    contributions = {}
    
    for col in returns_df.columns:
        w = weights.get(col, 0.02)
        r = returns_df[col].sum() # Total move contribution in the window
        contributions[col] = w * r
        
    s = pd.Series(contributions)
    total_index_move = index_returns.sum()
    
    if abs(total_index_move) < 0.001:
        return 0.0
        
    # Normalize shares
    shares = s.abs() / (s.abs().sum() + 1e-8)
    herfindahl = float((shares ** 2).sum())
    
    # Top 3 volume z-score
    vol_means = rupee_vol_df.mean()
    vol_stds = rupee_vol_df.std() + 1e-8
    top_3_vols = s.abs().nlargest(3).index
    
    z_vols = []
    for col in top_3_vols:
        if col in rupee_vol_df.columns:
            latest_vol = rupee_vol_df[col].iloc[-1]
            z = (latest_vol - vol_means[col]) / vol_stds[col]
            z_vols.append(z)
            
    mean_z_vol = np.mean(z_vols) if z_vols else 1.0
    
    return float(herfindahl * max(0.0, mean_z_vol))

def detect_pump_reverse(returns_df: pd.DataFrame, rupee_vol_df: pd.DataFrame) -> float:
    """
    Detector 2: concentrated pump-and-reverse flow anomaly score.
    """
    if returns_df.empty or len(returns_df) < 30:
        return 0.0
        
    # Standard 30-min pump and 30-min reverse check
    half = len(returns_df) // 2
    r_first_half = returns_df.iloc[:half].sum()
    r_second_half = returns_df.iloc[half:].sum()
    
    # Check for reversal (opposite signs and significant magnitude retracement)
    scores = []
    for col in returns_df.columns:
        m1 = r_first_half[col]
        m2 = r_second_half[col]
        
        if np.sign(m1) != np.sign(m2) and abs(m1) > 0.002:
            retracement = min(1.0, abs(m2) / abs(m1))
            if retracement >= 0.60:
                scores.append(abs(m1) * retracement)
                
    return float(np.mean(scores)) if scores else 0.0
