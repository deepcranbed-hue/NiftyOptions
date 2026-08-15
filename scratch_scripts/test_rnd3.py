import numpy as np
import math

def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0):
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
    
    # 3) CLIP negatives
    dens = np.maximum(dens, 0.0)
    
    # 4) TRIM unreliable deep-OTM wings
    keep = (C >= min_price) | (P >= min_price)
    keep = keep | (np.abs(K - S) <= 1.5 * (K.max() - K.min()) * 0.0)
    Kk, dk = K[keep], dens[keep]
    
    # 5) smooth density
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
        
    return grid, di

def rnd_stats(grid, dens, spot):
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
        
    grid, dens = extract_rnd(K, C, 24050, 3/365.0, 0.0655, put_prices=P)
    sd = rnd_stats(grid, dens, 24050)
    print(f"TEST Expected Move: {sd:.2f}")
