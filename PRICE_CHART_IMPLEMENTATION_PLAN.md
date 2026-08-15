# Implementation Plan — Price Chart Panel (1-minute + daily bars, platform overlays)

**Goal:** a NEW "Price Chart" panel: candlestick chart of NIFTY from stored bars
(4 days of 1m, 1 year of 1d), with timeframe switching (1m/5m/15m/1d) — and,
the differentiator, THE PLATFORM'S OWN DATA OVERLAID: OI walls, open-position
strikes + trigger ladder, RND expected-move band, and capture markers. This is
not a generic chart; it is the framework's read drawn ON price.

**Reference (built + tested):** `bar_store.py` — save_bars, get_bars (resamples
5m/15m from stored 1m at query time), realized_vol.
**Read first:** `bar_store.py`, `chain_store.py`, `portfolio.py`,
`vol_attribution.py`, frontend chart setup, `REFERENCE.md`.

---

## 1. Storage & ingestion (bar_store.py — same principles as chain_store)
- Store ONLY ground truth: raw 1m bars and raw 1d bars (`price_bars` table,
  PK symbol+timeframe+ts, idempotent INSERT OR REPLACE — re-downloads are safe).
- 5m/15m/60m are RESAMPLED from 1m at query time (session-anchored 09:15).
  Never store derived timeframes — no duplicate truths to drift.
- Ingestion: an upload/download path that calls `save_bars(rows, timeframe=...)`.
  Backfill: 4 days × ~375 bars = ~1,500 rows (1m) + ~250 rows (1d) — trivial.
- Daily refresh: append yesterday's 1d bar + today's 1m bars as available.

## 2. Backend endpoint
- `GET /api/bars?symbol=NIFTY&tf=5m&start=...&end=...` → `get_bars(...)` JSON.
- `GET /api/bars/realized-vol?days=20` → `realized_vol(...)`.
- Serve overlay data from EXISTING modules (no new computation):
  walls (chain analysis), open positions + their trigger ladders (portfolio),
  RND expected move (rnd_history latest PRIMARY), capture timestamps (captures).

## 3. Frontend chart — use TradingView `lightweight-charts` (the right tool)
- Library: `lightweight-charts` (free, canvas, built for exactly this; handles
  thousands of candles smoothly; native whitespace handling so overnight gaps
  between sessions don't draw as fake flat lines). Do NOT hand-roll candles in
  recharts — wrong tool for OHLC.
- Candlestick series + volume histogram beneath. Timeframe toggle 1m/5m/15m/1d.
- EMA 20 / EMA 50 overlays (client-side from the served closes) — the crossover
  the technical read keys on becomes visible.
- Optional VWAP for intraday timeframes (from 1m closes × volume).

## 4. PLATFORM OVERLAYS (the point of the panel)
All from existing data, drawn as price-lines / markers on the chart:
- **OI walls:** support/resistance from the latest capture's chain analysis —
  horizontal lines, labeled ("Put wall 24,000 · vol-confirmed"). Historical
  captures' walls optionally ghosted to SEE whether price respected them.
- **Open positions:** each portfolio position's short strikes + wings as
  horizontal lines with the TRIGGER LADDER states (short-strike touch /
  breakeven / wing) color-coded by fired/not-fired. The position card's ladder,
  drawn on price.
- **Expected-move band:** latest PRIMARY RND move as a shaded band
  (spot ± move) from the capture time to expiry — shows whether price is
  traveling inside or outside what options priced.
- **Capture markers:** small markers on the time axis where chain snapshots
  exist — click → jump to that capture in the comparison panel.
- Legend toggles for each overlay; caveats in a text strip (walls as-of capture
  time; expected move is risk-neutral, not a forecast).

## 5. Realized-vol wiring (bonus this data unlocks)
- `realized_vol(days=20)` now feeds `vol_attribution.py`'s `realized_vol` input
  (previously always None). Show IV-vs-realized in the vol panel:
  IV >> realized = variance premium rich; IV < realized = vol underpriced.
- The daily report's skew section can add one line: "20d realized 11.2% vs ATM
  IV 9.8% — premium thin."

## 6. Honest limits (render them)
- Index 1m bars often have NO true volume (index is computed, not traded) —
  if the feed gives index volume, verify what it is; else hide the volume pane
  for the index and show it only for futures bars if/when added.
- Bars are as-downloaded; gaps (missing minutes) are shown as gaps, not
  interpolated. "Checked and missing" rule applies.

---

## Acceptance criteria
1. 1m/5m/15m/1d all render; 5m/15m are derived from stored 1m (never stored).
2. Walls, open-position strikes + ladder states, expected-move band, capture
   markers all draw from existing module data with legend toggles.
3. EMA 20/50 visible; overnight gaps don't render as flat lines.
4. realized_vol feeds vol_attribution; IV-vs-realized shown.
5. Re-downloading the same bars is idempotent (no duplicates).
6. Missing minutes render as gaps; volume pane hidden if index volume is not real.

## Rules
- Store 1m + 1d ground truth only; resample on demand (single source of truth).
- Use lightweight-charts; do not hand-roll OHLC rendering.
- Overlays read EXISTING module outputs — no recomputation, no new math.
- Every overlay carries its as-of provenance; expected move labeled risk-neutral.
