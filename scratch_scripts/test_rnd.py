import numpy as np
import math
from backend.quant.rnd import implied_vol, bs_call

def test_extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0):
    strikes = np.asarray(strikes, float)
    call_prices = np.asarray(call_prices, float)
    
    min_k = strikes.min()
    max_k = strikes.max()
    
    if put_prices is not None:
        P = np.asarray(put_prices, float)
        disc = math.exp(-r * T)
        call_prices = np.where(strikes < S,
                               P + S - strikes * disc,   # parity
                               call_prices)
                               
        keep = (call_prices >= min_price) | (P >= min_price)
        keep = keep | (np.abs(strikes - S) <= 1.5 * (strikes.max() - strikes.min()) * 0.0)
        valid_strikes = strikes[keep]
        min_k, max_k = valid_strikes.min(), valid_strikes.max()

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
    
    # Trim the wings based on the keep mask boundaries
    m = (grid >= min_k) & (grid <= max_k)
    grid = grid[m]
    dens = dens[m]
    
    # 6. RENORMALIZE
    area = np.trapz(dens, grid)
    if area > 0:
        dens = dens / area
        
    return grid, dens

def test_rnd_stats(grid, dens, spot):
    mean = float(np.trapz(grid * dens, grid))
    var = float(np.trapz((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var)
    return sd

if __name__ == "__main__":
    rows = """23400 701.60 3.30
23450 653.65 4.15
23500 601.45 4.90
23550 556.00 6.10
23600 503.65 7.50
23650 455.40 9.85
23700 409.75 12.95
23750 364.30 16.75
23800 317.70 22.10
23850 276.50 29.45
23900 234.40 38.15
23950 196.00 49.50
24000 160.95 64.50
24050 129.15 82.80
24100 102.45 105.40
24150 78.10 131.20
24200 58.70 161.15
24250 43.55 196.70
24300 31.15 234.50
24350 22.55 275.85
24400 16.40 320.40
24450 12.25 365.90
24500 9.00 413.00
24550 6.65 460.05
24600 5.15 510.00"""
    K = []; C = []; P = []
    for ln in rows.strip().split("\n"):
        s, c, p = ln.split(); K.append(int(s)); C.append(float(c)); P.append(float(p))
        
    grid, dens = test_extract_rnd(K, C, 24050, 3/365.0, 0.0655, put_prices=P)
    sd = test_rnd_stats(grid, dens, 24050)
    print(f"TEST Expected Move: {sd:.2f}")
