# Signal Study — NIFTY @ all

- expiry: `2026-07-21T06:00:00.000Z` · source: `auto` · generated: 2026-07-19T00:17
- n_obs: **5245** 

```
Signal × horizon SHARPE  (target=NIFTY, n_obs=5245, source=auto):
  signal                      5m     15m     30m     60m      2h      3h    best
  global_gap              +0.06   +0.12   +0.18   +0.13   +0.20   +0.23*      3h
  skew_rnd                +0.06   +0.10   +0.12*  +0.12   +0.17   +0.23      30m
  vwap                    -0.04   -0.08   -0.10   -0.11*  +0.00   +0.02      60m
  usdinr                  +0.01   -0.01   -0.01   -0.07   -0.05   -0.10*      3h
  futures_basis           -0.03   -0.05   -0.05   -0.05   -0.10*  -0.08       2h
  crude_energy            +0.01   +0.04   +0.06   +0.04   +0.05   +0.08*      3h
  heavyweight_leadership  -0.01   -0.01   -0.04   -0.08*  -0.04   -0.04      60m
  breadth_oi              +0.01   +0.02   +0.02   -0.02   +0.05   +0.07*      3h
  technical_momentum      -0.03   +0.02   +0.06*  -0.01   -0.02   -0.01      30m
  vol_index               -0.03   +0.03   +0.05*  -0.06   -0.04   -0.01      30m
  futures_calendar        +0.01   +0.00   -0.01   -0.02   -0.00   -0.04*      3h
  futures_flow            -0.02   -0.02   +0.00   +0.01   -0.03*  +0.01       2h
  global_momentum         +0.01   +0.00   +0.01   -0.03*  +0.02   -0.00      60m
  vrp                     +0.01   +0.03   +0.05   +0.06   +0.03*  +0.02       2h
  rel_volume              -0.02   -0.01   +0.02*  +0.01   -0.03   +0.02      30m

  metric=sharpe: ic=Pearson · rank_ic=Spearman · spread=top−bottom-half fwd · sharpe=median-split consistency · hit=direction agreement. signal × horizon skill grid (shared metric definition). Descriptive/PRIOR until ≥60 sessions (D-MA-04).
```
