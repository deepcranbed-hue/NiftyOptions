with open("backend/quant/rnd.py", "r") as f:
    rnd = f.read()

# Replace extract_rnd
old_extract_rnd = """def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None):
    \"\"\"Return (grid, density) for the risk-neutral terminal distribution.
    `smooth` = moving-average window on the IV smile before repricing.

    IMPORTANT: pass `put_prices` too. Breeden-Litzenberger needs OUT-OF-THE-MONEY
    options -- ITM prices are intrinsic-dominated and invert to garbage IV, which
    erases the put-skew left wing and flips the density's skew sign. When puts are
    given we use puts for K<S and calls for K>=S, converting puts to synthetic
    calls via put-call parity (C = P + S - K*e^{-rT}). Call-only input is kept for
    backward compatibility but is WRONG below spot.\"\"\"
    strikes = np.asarray(strikes, float)
    call_prices = np.asarray(call_prices, float)

    # 0. build an OTM call-price curve (synthetic calls from OTM puts below spot)
    if put_prices is not None:
        put_prices = np.asarray(put_prices, float)
        disc = math.exp(-r * T)
        call_prices = np.where(strikes < S,
                               put_prices + S - strikes * disc,   # parity
                               call_prices)

    # 1. invert to IV
    ivs = np.array([implied_vol(c, S, k, T, r) for c, k in zip(call_prices, strikes)])
    good = ~np.isnan(ivs)
    strikes, ivs = strikes[good], ivs[good]

    # 2. smooth the smile (IV is far smoother than price)
    if smooth > 1 and len(ivs) >= smooth:
        kernel = np.ones(smooth) / smooth
        ivs = np.convolve(ivs, kernel, mode="same")

    # 3. fine grid, interpolate IV, reprice (extrapolate IV flat at the wings)
    grid = np.linspace(strikes.min(), strikes.max(), grid_pts)
    iv_grid = np.interp(grid, strikes, ivs)
    C = np.array([bs_call(S, k, T, r, sig) for k, sig in zip(grid, iv_grid)])

    # 4. Breeden-Litzenberger: density = e^{rT} * C''(K)
    dK = grid[1] - grid[0]
    dens = np.gradient(np.gradient(C, dK), dK) * math.exp(r * T)
    dens = np.clip(dens, 0, None)                 # kill tiny negative noise
    area = np.trapz(dens, grid)
    dens = dens / area if area > 0 else dens
    return grid, dens"""

new_extract_rnd = """def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0):
    \"\"\"Return (grid, density) for the risk-neutral terminal distribution.
    `smooth` = moving-average window on the IV smile before repricing.

    IMPORTANT: pass `put_prices` too. Breeden-Litzenberger needs OUT-OF-THE-MONEY
    options -- ITM prices are intrinsic-dominated and invert to garbage IV, which
    erases the put-skew left wing and flips the density's skew sign. When puts are
    given we use puts for K<S and calls for K>=S, converting puts to synthetic
    calls via put-call parity (C = P + S - K*e^{-rT}). Call-only input is kept for
    backward compatibility but is WRONG below spot.\"\"\"
    strikes = np.asarray(strikes, float)
    call_prices = np.asarray(call_prices, float)
    
    # Trim unreliable deep-OTM wings before computing IV
    # Keep strikes where both legs have meaningful value (>= min_price) or are near the money
    if put_prices is not None:
        P = np.asarray(put_prices, float)
        keep = (call_prices >= min_price) | (P >= min_price)
        keep = keep | (np.abs(strikes - S) <= 1.5 * (strikes.max() - strikes.min()) * 0.0)
        strikes = strikes[keep]
        call_prices = call_prices[keep]
        P = P[keep]
        
        disc = math.exp(-r * T)
        call_prices = np.where(strikes < S,
                               P + S - strikes * disc,   # parity
                               call_prices)

    # 1. invert to IV
    ivs = np.array([implied_vol(c, S, k, T, r) for c, k in zip(call_prices, strikes)])
    good = ~np.isnan(ivs)
    strikes, ivs = strikes[good], ivs[good]

    # 2. smooth the smile (IV is far smoother than price)
    if smooth > 1 and len(ivs) >= smooth:
        kernel = np.ones(smooth) / smooth
        ivs = np.convolve(ivs, kernel, mode="same")

    # 3. fine grid, interpolate IV, reprice (extrapolate IV flat at the wings)
    grid = np.linspace(strikes.min(), strikes.max(), grid_pts)
    iv_grid = np.interp(grid, strikes, ivs)
    C = np.array([bs_call(S, k, T, r, sig) for k, sig in zip(grid, iv_grid)])

    # 4. Breeden-Litzenberger: density = e^{rT} * C''(K)
    dK = grid[1] - grid[0]
    dens = np.gradient(np.gradient(C, dK), dK) * math.exp(r * T)
    
    # 5. CLIP negatives
    dens = np.maximum(dens, 0.0)
    
    # 6. RENORMALIZE: this fixes the inflated variance!
    area = np.trapz(dens, grid)
    if area > 0:
        dens = dens / area
        
    return grid, dens"""

rnd = rnd.replace(old_extract_rnd, new_extract_rnd)

# Replace rnd_stats
old_rnd_stats = """def rnd_stats(grid, dens, spot):
    mean = float(np.trapz(grid * dens, grid))
    var = float(np.trapz((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var)
    
    print("\\n" + "="*50)
    print("[DEBUG] RND Expected Move Calculation (Math/Formula):")
    print(f"  1) Mean (∫ K*dens dK) = {mean:.2f}")
    print(f"  2) Variance (∫ (K - Mean)² * dens dK) = {var:.2f}")
    print(f"  3) Expected Move (1σ) = √Variance = {sd:.2f} pts")
    print("="*50 + "\\n")
    
    skew = float(np.trapz(((grid - mean) / sd) ** 3 * dens, grid)) if sd else 0.0
    p_down = prob_in_range(grid, dens, grid.min(), spot)
    return {"mean": mean, "sd": sd, "skew": skew,
            "p_below_spot": p_down, "p_above_spot": 1 - p_down}"""

new_rnd_stats = """def rnd_stats(grid, dens, spot, strikes=None, call_ltp=None, put_ltp=None):
    mean = float(np.trapz(grid * dens, grid))
    var = float(np.trapz((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var)
    
    print("\\n" + "="*50)
    print("[DEBUG] RND Expected Move Calculation (Math/Formula):")
    print(f"  1) Mean (∫ K*dens dK) = {mean:.2f}")
    print(f"  2) Variance (∫ (K - Mean)² * dens dK) = {var:.2f}")
    print(f"  3) Expected Move (1σ) = √Variance = {sd:.2f} pts")
    print("="*50 + "\\n")
    
    skew = float(np.trapz(((grid - mean) / sd) ** 3 * dens, grid)) if sd else 0.0
    p_down = prob_in_range(grid, dens, grid.min(), spot)
    
    res = {"mean": mean, "sd": sd, "skew": skew,
           "p_below_spot": p_down, "p_above_spot": 1 - p_down}
           
    if strikes is not None and call_ltp is not None and put_ltp is not None:
        strikes_arr = np.asarray(strikes, float)
        call_arr = np.asarray(call_ltp, float)
        put_arr = np.asarray(put_ltp, float)
        
        atm_c = float(np.interp(spot, strikes_arr, call_arr))
        atm_p = float(np.interp(spot, strikes_arr, put_arr))
        straddle = atm_c + atm_p
        straddle_move = 0.8 * straddle
        
        ratio = sd / straddle_move if straddle_move > 0 else np.nan
        calibrated = 0.7 <= ratio <= 1.4
        
        res["straddle_pts"] = round(straddle, 1)
        res["straddle_implied_move"] = round(straddle_move, 1)
        res["calibration_ratio"] = round(ratio, 2)
        res["calibrated"] = bool(calibrated)
        res["provenance"] = "PRIMARY" if calibrated else "FALLBACK"
        res["warning"] = ("" if calibrated else
                          f"⚠ RND expected move {sd:.0f} diverges from straddle-implied "
                          f"{straddle_move:.0f} (ratio {ratio:.2f}). Density likely "
                          f"mis-normalized or tail-contaminated — DO NOT trust the "
                          f"optimizer ranking off this RND.")
                          
    return res"""

rnd = rnd.replace(old_rnd_stats, new_rnd_stats)

with open("backend/quant/rnd.py", "w") as f:
    f.write(rnd)
