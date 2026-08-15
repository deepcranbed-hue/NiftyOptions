import re

with open("backend/quant/rnd.py", "r") as f:
    content = f.read()

# 1. Add _trap at the top of the file
import_insert = "import numpy as np\n\n# NumPy 2.0-safe integrator (BUG 3)\n_trap = getattr(np, 'trapezoid', getattr(np, 'trapz', None))\n"
content = content.replace("import numpy as np\n", import_insert)

# 2. Replace extract_rnd
new_extract_rnd = '''def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0, band_pts=500):
    """Return (grid, density) for the risk-neutral terminal distribution.
    `smooth` = moving-average window on the IV smile before repricing.

    IMPORTANT: pass `put_prices` too. Breeden-Litzenberger needs OUT-OF-THE-MONEY
    options -- ITM prices are intrinsic-dominated and invert to garbage IV, which
    erases the put-skew left wing and flips the density's skew sign. When puts are
    given we use puts for K<S and calls for K>=S, converting puts to synthetic
    calls via put-call parity (C = P + S - K*e^{-rT}). Call-only input is kept for
    backward compatibility but is WRONG below spot."""
    K = np.asarray(strikes, float)
    C = np.asarray(call_prices, float)
    
    if put_prices is None:
        raise ValueError("put_prices is required for hardened RND extraction")
    P = np.asarray(put_prices, float)
    disc = math.exp(-r * T)
    
    # 1) OTM legs -> call-equivalent via put-call parity for K<spot
    Ceq = np.where(K < S, P + S - K * disc, C)
    
    # 2) raw density = discounted 2nd derivative wrt K
    dens = np.gradient(np.gradient(Ceq, K), K) / disc
    
    # 3) CLIP negatives (numerical noise can go below 0)
    dens = np.maximum(dens, 0.0)
    
    # 4) TRIM unreliable deep-OTM wings
    keep = (C >= min_price) & (P >= min_price) & (np.abs(K - S) <= band_pts)
    if keep.sum() < 5:
        keep = (np.abs(K - S) <= band_pts)
    Kk, dk = K[keep], dens[keep]
    
    # 5) optional light smoothing of the density to tame 2nd-derivative noise
    if smooth and len(dk) >= 5:
        kern = np.array([1, 2, 3, 2, 1], float)
        kern /= kern.sum()
        dk = np.convolve(dk, kern, mode="same")
        
    # 6) interpolate to a fine grid and RENORMALIZE
    grid = np.linspace(Kk.min(), Kk.max(), grid_pts)
    di = np.interp(grid, Kk, dk)
    area = _trap(di, grid)
    if area > 0:
        di = di / area
        
    return grid, di'''

content = re.sub(r'def extract_rnd\(.*?return grid, di', new_extract_rnd, content, flags=re.DOTALL)

# 3. Replace prob_in_range
new_prob = '''def prob_in_range(grid, dens, low, high):
    m = (grid >= low) & (grid <= high)
    return float(_trap(dens[m], grid[m]))'''
content = re.sub(r'def prob_in_range\(.*?return float\(np.trapz\(dens\[m\], grid\[m\]\)\)', new_prob, content, flags=re.DOTALL)

# 4. Replace rnd_stats
new_stats = '''def rnd_stats(grid, dens, spot, strikes=None, call_ltp=None, put_ltp=None):
    mean = float(_trap(grid * dens, grid))
    var = float(_trap((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var) if var > 0 else 0.0
    skew = float(_trap(((grid - mean) / sd) ** 3 * dens, grid)) if sd else 0.0
    p_down = prob_in_range(grid, dens, grid.min(), spot)
    
    res = {"mean": mean, "sd": sd, "skew": skew,
           "p_below_spot": p_down, "p_above_spot": 1 - p_down}
           
    # skew sanity guard (independent of calibration)
    res["skew_ok"] = abs(skew) <= 1.0
    if not res["skew_ok"]:
        res["skew_warning"] = (f"⚠ |skew| {skew:.2f} > 1.0 — density CONTAMINATED "
                               f"(deep-OTM tail noise). Do not trust this RND.")
           
    if strikes is not None and call_ltp is not None and put_ltp is not None:
        strikes_arr = np.asarray(strikes, float)
        call_arr = np.asarray(call_ltp, float)
        put_arr = np.asarray(put_ltp, float)
        
        atm_c = float(np.interp(spot, strikes_arr, call_arr))
        atm_p = float(np.interp(spot, strikes_arr, put_arr))
        straddle = atm_c + atm_p
        
        ratio = sd / straddle if straddle > 0 else float("nan")
        calibrated = (0.7 <= ratio <= 1.4) and res["skew_ok"]
        
        res.update({
            "straddle_pts": round(straddle, 1),
            "calibration_ratio": round(ratio, 2),
            "calibrated": bool(calibrated),
            "provenance": "PRIMARY" if calibrated else "FALLBACK",
            "warning": ("" if calibrated else
                f"⚠ RND move {sd:.0f} vs straddle {straddle:.0f} "
                f"(ratio {ratio:.2f})" +
                (f", skew {skew:.2f}" if not res["skew_ok"] else "") +
                " — do NOT trust the ranking off this RND.")
        })
                          
    return res'''
content = re.sub(r'def rnd_stats\(.*?return res', new_stats, content, flags=re.DOTALL)

with open("backend/quant/rnd.py", "w") as f:
    f.write(content)
