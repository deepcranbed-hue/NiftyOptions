# Signal Study — NIFTY @ 60m

- expiry: `2026-07-07T06:00:00.000Z` · source: `auto` · generated: 2026-07-18T22:16
- n_obs: **63** 

```
n_obs=63   effective-independent bets≈4.22 of 9 signals
excluded (NO_DATA): crude_energy, usdinr, global_gap, futures_basis, futures_calendar, futures_flow

families (correlated → share a budget):
  family 1: heavyweight_leadership, breadth_oi
  family 2: technical_momentum, vwap, rel_volume
  family 3: global_momentum
  family 4: skew_rnd
  family 5: vrp
  family 6: vol_index

redundancy (avg |corr|; high = duplicative) | IC (skill vs fwd return):
  technical_momentum       red=0.435  IC=-0.20
  breadth_oi               red=0.398  IC=-0.07
  heavyweight_leadership   red=0.354  IC=-0.14
  rel_volume               red=0.328  IC=-0.09
  vol_index                red=0.325  IC=-0.20
  vwap                     red=0.319  IC=+0.00
  vrp                      red=0.258  IC=-0.09
  skew_rnd                 red=0.22   IC=+0.11
  global_momentum          red=0.217  IC=-0.04

proposed weights (compare):
                         inverse_redu        mv_ic       family
  heavyweight_leadership        0.099        0.000        0.083
  technical_momentum            0.084        0.000        0.000
  global_momentum               0.142        0.094        0.167
  breadth_oi                    0.090        0.249        0.083
  skew_rnd                      0.141        0.273        0.167
  vrp                           0.126        0.000        0.167
  vwap                          0.107        0.205        0.167
  vol_index                     0.106        0.000        0.167
  rel_volume                    0.105        0.180        0.000

weights are non-negative and sum to 1 (importance, not direction). mv_ic is null when no usable IC (no/thin forward returns). All PRIOR/descriptive — the CALLER decides if n_obs is enough to trust.
```
