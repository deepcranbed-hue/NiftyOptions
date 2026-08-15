import sys
sys.path.append('.')
from backend.quant.skew.skew_engine import iv_at_delta

smile_c = {
  23950: 0.1121,
  24000: 0.1097,
  24050: 0.1086,
  24100: 0.1064,
  24150: 0.1056,
  24200: 0.1043,
  24250: 0.1042,
  24300: 0.1040,
  24350: 0.1032,
  24400: 0.1033,
  24450: 0.1022,
  24500: 0.1016,
  24550: 0.1011,
  24600: 0.1018,
  24650: 0.1015,
  24700: 0.1009,
  24750: 0.1035,
  24800: 0.1041,
  24850: 0.1055,
  24900: 0.1077,
  24950: 0.1091
}
F_c = 24360.45
T_curr = 0.018710548330671532

res = iv_at_delta(smile_c, F_c, T_curr, "PE")
print("PE status:", res.status)
print("PE strike:", res.strike)
print("PE iv:", res.iv)

res_ce = iv_at_delta(smile_c, F_c, T_curr, "CE")
print("CE status:", res_ce.status)
print("CE strike:", res_ce.strike)
print("CE iv:", res_ce.iv)
