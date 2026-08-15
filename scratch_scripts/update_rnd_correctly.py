with open("backend/quant/rnd.py", "r") as f:
    rnd = f.read()

import re

# We will completely replace extract_rnd to use the hardened logic
old_extract_rnd_pattern = re.compile(r'def extract_rnd.*?return grid, dens', re.DOTALL)

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
    K = np.asarray(strikes, float)
    C = np.asarray(call_prices, float)
    
    if put_prices is None:
        raise ValueError("put_prices is required for hardened RND extraction")
    P = np.asarray(put_prices, float)
    disc = math.exp(-r * T)
    
    # 1) OTM legs -> call-equivalent via put-call parity for K<spot
    Ceq = np.where(K < S, P + S - K * disc, C)
    
    # 2) raw density = discounted 2nd derivative wrt K
    d1 = np.gradient(Ceq, K)
    d2 = np.gradient(d1, K)
    dens = d2 / disc
    
    # 3) CLIP negatives (numerical noise can go below 0)
    dens = np.maximum(dens, 0.0)
    
    # 4) TRIM unreliable deep-OTM wings
    keep = (C >= min_price) | (P >= min_price)
    keep = keep | (np.abs(K - S) <= 1.5 * (K.max() - K.min()) * 0.0)
    Kk, dk = K[keep], dens[keep]
    
    # 5) optional light smoothing of the density to tame 2nd-derivative noise
    if smooth and len(dk) >= 5:
        kern = np.array([1, 2, 3, 2, 1], float)
        kern /= kern.sum()
        dk = np.convolve(dk, kern, mode="same")
        
    # 6) interpolate to a fine grid and RENORMALIZE
    grid = np.linspace(Kk.min(), Kk.max(), grid_pts)
    di = np.interp(grid, Kk, dk)
    area = np.trapz(di, grid)
    if area > 0:
        di = di / area
        
    return grid, di"""

rnd = re.sub(old_extract_rnd_pattern, new_extract_rnd, rnd)

with open("backend/quant/rnd.py", "w") as f:
    f.write(rnd)
