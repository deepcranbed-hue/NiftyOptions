# Market Health — Metrics Reference & Interpretation Guide

A plain-language guide to every number the Market Health gauge produces: what it
measures, how it is computed, and how to read it. The gauge is a **daily, positional**
view of where NIFTY sits in its trend/cycle — a slow companion to the intraday
directional desk, not part of it.

Everything here is descriptive. It is **not financial advice**; it tells you the
*condition* of the trend, not what to do about it.

---

## 1. The headline number

**`score` (0–100)** and its **`band`**.

The score is the sum of points *awarded* across every component that had data,
divided by the points that were *available*, times 100:

```
score = 100 × (awarded points) / (available points)
```

It is normalised over **available** points — not the full model — so a score built
on the index alone is still on a 0–100 scale and comparable to a fully-covered one.
How much of the intended model was actually available is reported separately as
`coverage_pct`.

### Bands

| Band | Score | What it means |
|---|---|---|
| **Strong uptrend** | 80–100 | Broad, confirmed advance — price, breadth, rotation and leaders all aligned up. |
| **Healthy uptrend** | 65–79 | Uptrend intact with minor soft spots. |
| **Neutral / consolidation** | 50–64 | No clear edge; the market is ranging or transitioning. |
| **Weakening** | 35–49 | Trend is deteriorating — more components failing than holding. |
| **Defensive / downtrend** | 0–34 | Broad risk-off; most components are bearish. |

A score near 50 is genuinely *neutral*, not "half bullish" — the sub-scores are
built so that 0.5 means "on the fence" (e.g. price sitting exactly on a moving
average), not "weak".

---

## 2. How each component is scored

Every component produces:

- **`score01`** — a smooth 0…1 strength. `_lin(x, lo, hi)` maps a raw reading `x`
  linearly onto 0…1, clamped: at `lo` it is 0, at `hi` it is 1, halfway is 0.5.
  The band edges are chosen so the score is *responsive* in the range that matters
  and doesn't saturate on ordinary moves.
- **`points`** — the PRIOR budget for that component (see §7).
- **`awarded`** = `score01 × points`.
- **`data_ready`** — `false` when there isn't enough history to compute it honestly;
  the component then shows *pending* and contributes nothing (rather than a fake 0).

---

## 3. Layer A — Index Trend (price structure, from the NIFTY daily series)

The "is price itself in an uptrend" layer. Available as soon as NIFTY has enough
daily history (a 200-DMA needs ~200 sessions).

| Component | Points | Measures | Maps to 0→1 over | Read it as |
|---|---|---|---|---|
| **px_vs_200dma** | 20 | Distance of spot from its 200-day MA, in % | −6% … +6% | The primary structural trend. Above the 200-DMA = long-term uptrend; below = downtrend. This carries the most weight in the layer. |
| **px_vs_50dma** | 10 | Distance from the 50-day MA, in % | −4% … +4% | The intermediate trend. Above 50 but below 200 = a bounce inside a longer downtrend. |
| **cross_50_200** | 12 | Gap between the 50- and 200-DMA, in % | −3% … +3% | The regime. 50 above 200 = **golden** (bullish structure); 50 below 200 = **death** (bearish structure). The size of the gap scales the score. |
| **slope_200dma** | 10 | % change of the 200-DMA over the last 20 sessions | −1% … +1% | Is the long trend itself *turning*. A rising 200-DMA confirms an uptrend; a falling one means the base is still eroding even if price bounces. |
| **momentum** | 8 | RSI(14) blended with the MACD histogram sign | RSI 30…70 | Short-term thrust. RSI>70 region and a positive MACD histogram = strong momentum. Uses RSI alone until there is enough history for MACD (~35 sessions), and says so in the detail. |

**Detail fields you'll see:** `dist_pct` (distance from the MA), `ma200`/`ma50`
(the MA levels), `gap_pct` and `regime` (golden/death), `slope_pct_20d` and
`turning` (rising/falling), `rsi14`, `macd_hist`.

**Interpreting the layer:** a high Index-Trend score with a *falling* `slope_200dma`
is a classic "bounce in a downtrend" — price has rallied above the averages but the
long base is still sinking. Trust `px_vs_200dma` and `slope_200dma` for the
structural picture; treat `momentum` as the fast, noisy overlay.

---

## 4. Layer B — Trend Breadth (participation, from the constituents)

The "how many stocks are actually in the advance" layer. Activates once ≥10
constituents have ≥200 daily sessions.

| Component | Points | Measures | Read it as |
|---|---|---|---|
| **breadth_above_200** | 20 | % of the 50 members trading above their **own** 200-DMA (equal-weighted) | Broad participation. 70%+ is healthy; below ~40% the advance is narrow. |
| **breadth_above_50** | 10 | % above their own 50-DMA | The faster participation read; turns before the 200 version. |
| **breadth_weighted_200** | 10 | **Cap-weighted** share above the 200-DMA | Whether the *heavyweights* are participating, not just the count. Diverges from the equal-weighted version when big names lead or lag the crowd. |

**Detail fields:** `pct` (the percentage), `weighted_pct`, `n` (members counted).

**The key divergence to watch:** compare Breadth to Index Trend. If the index score
is high but breadth is low, the rally is **narrow** — a handful of names are holding
the index up while most stocks are already rolling over. That is fragile. When
`breadth_weighted_200` is much lower than `breadth_above_200`, the heavyweights
specifically are the weak part — which is exactly the "heavyweights have given up
their gains" condition.

---

## 5. Layer C — Sector Rotation (risk-on vs risk-off leadership)

Which *kind* of sector is leading. Cyclicals lead when growth and risk appetite are
rising; defensives lead when the market turns cautious.

| Component | Points | Measures | Read it as |
|---|---|---|---|
| **sector_rotation** | 14 | Cyclical strength − Defensive strength (each = cap-weighted share of that side above its 200-DMA), mapped over −0.5…+0.5 | The risk tilt. `leaning: risk-on (cyclicals)` is healthy; `risk-off (defensives)` means money is hiding even if the index looks fine. |
| **sector_breadth** | 6 | Fraction of sectors where a majority (by weight) of members are above their 200-DMA | How many sectors are participating, not just which side. |

**Cyclical (risk-on):** Financial Services, Metals & Mining, Automobile, Consumer
Durables, Cement, Oil & Gas, Capital Goods, Construction, Services, Consumer Services.
**Defensive (risk-off):** FMCG, Healthcare, Information Technology, Power,
Telecommunication.

This classification is a **documented convention** (in `trend.CYCLICAL` /
`trend.DEFENSIVE`), not a law — IT (a USD/export play) and Oil & Gas are the
debatable ones. Edit those two sets if your view differs; nothing else needs to change.

**Detail fields:** `cyclical_strength`, `defensive_strength`, `tilt`, `leaning`,
`sectors_participating_pct`.

**Interpreting it:** a risk-off tilt *while the index is still up* is an early
caution flag — the composition of the market is defensive before the headline
number admits it. A risk-on tilt with rising breadth is the healthiest combination.

---

## 6. Layer D — Leadership Quality (are the leaders still leading)

Whether the market's generals — the index heavyweights in `K.HEAVYWEIGHTS` (the ~10
biggest names: HDFC Bank, Reliance, ICICI, Infosys, Bharti Airtel, TCS, ITC, L&T,
SBI, Axis) — are making higher highs or breaking down.

| Component | Points | Measures | Read it as |
|---|---|---|---|
| **leaders_near_high** | 12 | Cap-weighted share of the heavyweights within 5% of their trailing 100-day high | Are the leaders confirming the trend by making new highs. High = leadership intact. |
| **leaders_uptrend** | 8 | Cap-weighted share of the heavyweights above their 50-DMA | The faster read on whether leadership is still in gear. |

**Detail fields:** `weighted_pct_near_high`, `weighted_pct_above_50dma`,
`n_leaders`, `window_days`.

**The warning this exists to catch:** leaders rolling over *while the index holds up*
is a textbook late-stage top. When `leaders_near_high` falls while Index Trend stays
high, the generals are retreating and the index is being carried by second-line
names — historically a precursor to broader weakness.

---

## 7. Coverage, PRIOR weights, and what's omitted

**`coverage_pct`** = available points ÷ total points. It tells you how much of the
intended model actually had data. Index-only ≈ 43%; with constituents synced, 100%.
Always read the score *together with* coverage — a 60 at 43% coverage is a
narrower statement than a 60 at 100%.

**`prior: true`** — the point weights (in the single `trend.POINTS` dict) are a
**reasonable prior, not calibrated truth**. The proportions echo the framework this
was built from (price "technical" ≈ 40%, internals ≈ 60%), but whether that split
actually predicts forward returns is a question for the CalibrationAgent, not an
assertion. Treat the *composition* (which components are strong/weak) as more
reliable than the exact headline number.

**`omitted_layers`** — Macro (oil/INR/inflation), Fundamentals (earnings/valuation)
and Institutional Flows (FII/DII) are deliberately **not scored**, because there is
no trusted daily feed for them yet. They are listed explicitly so the gap is visible
rather than hidden. Each becomes a component here the day a feed exists.

---

## 8. Reading the whole gauge — divergence patterns

The single number is a summary; the **relationships between the four layers** carry
most of the signal. The common patterns:

- **Confirmed uptrend** — Index high, Breadth high, Rotation risk-on, Leaders at
  highs. All four aligned. The advance is broad and led from the front.

- **Narrow / fragile rally** — Index high but **Breadth low**. A few names are
  holding the index up while most stocks lag. Vulnerable to a sharp mean-reversion.

- **Broadening weakness** — Index near its averages but **Breadth collapsing**. The
  correction is spreading beneath a still-calm headline. This is the "deterioration
  has broadened" condition.

- **Late-stage warning** — Index holding but **Leadership Quality falling**. The
  generals are retreating; the index is being carried by the rank and file.

- **Defensive rotation** — **Sector Rotation risk-off** even while the index looks
  fine. Money is repositioning into safety ahead of the tape.

- **Washout / basing** — Everything low but **momentum ticking up** and breadth
  starting to improve. Selling pressure may be easing; a base may be forming. (This
  is a *possible* turn, never a certainty — read it with coverage and confirm over
  several sessions.)

The gauge earns its keep when the layers **disagree**. When Index Trend says one
thing and Breadth, Rotation or Leadership say another, the internals usually lead
and the headline follows.

---

## 9. Data honesty rules (why to trust the numbers)

- **No fabricated moving averages.** A 200-DMA on fewer than 200 sessions is not
  computed — the component reports *pending*, never a partial number dressed as a
  verdict.
- **No lookahead.** A read `as_of` a past date uses only bars at or before that date,
  so a historical read never peeks at the future.
- **Honest coverage.** The score states how much of the model it could actually
  compute, so an index-only read announces itself as such.
- **PRIOR weights.** The point budget is a prior awaiting calibration, kept in one
  place so it can be revised from evidence rather than re-asserted.

---

## 10. Where the numbers come from

- Core logic: `strategy_framework/market_health/trend.py` (scoring) and
  `daily_bars.py` (daily OHLC + MA/RSI/MACD primitives).
- Run it: `python -m MarketHealthAgent.run` (add `--json`, `--as-of DATE`, `--report`).
- Live: `GET /api/strategy/market-health`, rendered by the **Market Health** tab.
- Activate the constituent layers: `python -m MarketHealthAgent.sync_daily --source <db>`.

*Descriptive market-health gauge. Not financial advice; decisions are yours.*
