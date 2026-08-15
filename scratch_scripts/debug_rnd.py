import sys
from backend.quant.rnd import extract_rnd, rnd_stats

strikes = list(range(23750, 24850, 50))
call_ltp = [478.60, 430.00, 383.85, 340.05, 295.50, 252.25, 212.40, 175.20,
            141.65, 112.70, 86.90, 65.35, 48.40, 35.70, 26.30, 19.35, 14.00,
            10.75, 8.30, 6.30, 5.20, 3.95]
put_ltp = [1.2, 2.5, 4.1, 6.2, 9.8, 14.5, 21.0, 29.5,
           41.0, 56.0, 75.5, 99.5, 128.5, 161.5, 198.5, 239.5, 284.5,
           332.5, 384.5, 438.5, 495.5, 554.5] # Made up put data
S, T, r = 24_200.0, 7 / 365, 0.0655

# Without puts
g1, d1 = extract_rnd(strikes, call_ltp, S, T, r)
st1 = rnd_stats(g1, d1, S)
print("Without Puts:", st1)

# With puts
g2, d2 = extract_rnd(strikes, call_ltp, S, T, r, put_prices=put_ltp)
st2 = rnd_stats(g2, d2, S)
print("With Puts:", st2)
