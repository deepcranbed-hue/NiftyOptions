# MarketHealthAgent

A daily, slow-clock **market-health / trend gauge** (0-100) — a companion to the
intraday directional desk, not part of it. Where the signal engine answers "which
way in the next minutes to hours", this answers "where are we in the trend/cycle
over days to weeks".

## What it does

Reads **daily** OHLC (`price_bars`, `timeframe='1d'`) and scores two components:

- **Index trend** (NIFTY daily): price vs 200-DMA, price vs 50-DMA, the 50/200
  golden-vs-death regime, the 200-DMA slope, and momentum (RSI-14 + MACD histogram).
- **Trend breadth** (constituents): % of members above their own 200-/50-DMA and the
  cap-weighted share above the 200-DMA (heavyweight participation). Lights up once
  constituent daily bars are present locally.

The two roll into a 0-100 score with interpretation bands (Strong / Healthy /
Neutral / Weakening / Defensive).

## Deliberately omitted

Macro, Fundamentals and Institutional-Flow layers are **not** scored — there is no
trusted feed for oil/INR/inflation, earnings, or FII/DII yet. They are listed in
`omitted_layers` so the gap is explicit. Each becomes a component here the day a feed
exists, exactly like a signal joining the registry.

## Honesty rules (why to trust it)

- A 200-DMA on fewer than 200 sessions is **not** computed — the sub-score reports
  as pending, never a partial number dressed as a verdict (PRIOR-until-data / D-MA-04).
- The headline is normalised over the points that **have** data, and `coverage_pct`
  states how much of the intended model was available. An index-only score says so.
- The point weights (`trend.POINTS`) are a **PRIOR**, in one place, ready for a future
  calibration step to revise — they are not asserted as calibrated truth.

## Run

```
python -m MarketHealthAgent.run                 # latest, text report
python -m MarketHealthAgent.run --as-of 2026-07-01
python -m MarketHealthAgent.run --json
python -m MarketHealthAgent.run --report        # writes reports/*.md
```

Served live at `GET /api/strategy/market-health` and rendered by the Market Health
panel in the UI.

## Not financial advice

A descriptive gauge of trend condition. It does not tell you to buy or sell; that
decision is yours.
