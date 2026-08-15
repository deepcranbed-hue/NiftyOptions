import numpy as np
import math
from backend.quant.rnd import implied_vol, bs_call

def test_extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0):
    strikes = np.asarray(strikes, float)
    call_prices = np.asarray(call_prices, float)
    orig_call = call_prices.copy()
    
    if put_prices is not None:
        P = np.asarray(put_prices, float)
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
    
    # Trim the wings based on the keep mask boundaries using ORIGINAL strikes
    if put_prices is not None:
        keep = (orig_call >= min_price) | (P >= min_price)
        # We need the original `strikes` variable from the argument, not the one overwritten on line 18
        pass

