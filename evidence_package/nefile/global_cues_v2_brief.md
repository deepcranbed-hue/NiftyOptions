# Antigravity Implementation Brief — Global Cues v2
**Module:** `global_cues.py` (fetcher + signal engine) | **Consumers:** daily_report.py, strategy suggester (`magnitude_corroboration`), Global Cues panel UI
**Blast-radius tier:** Tier 2 — changes a decision-support signal consumed by the suggester; human reviews output before any trade action. No order-path code touched.
**DECISIONS.md entries required:** D-GC-01 (dead-band → z-score method), D-GC-02 (silver regime rule), D-GC-03 (netting weights source), D-GC-04 (USDINR dual-target split).

---

## 0. Motivation — observed defects in production output (04-Jul-2026 snapshot)

1. **Zero forced into a direction, inconsistently.** S&P 500 (0%) rendered *headwind* while DXY (0%) rendered *tailwind*. Root cause: `bullish = (pct > 0) != inverse` — at pct=0 the arrow is decided entirely by the `inverse` flag. The ±0.05% dead-band exists only on the `tone` variable, not the arrow.
2. **Holiday artifact.** US markets were closed Fri 03-Jul (July 4th observed). The 0% prints are no-session artifacts that the panel converted into fabricated directional cues.
3. **Contradictory metals read.** Gold +1.49% → headwind (fear) while silver +2.87% → tailwind (industrial), same session, copper only +0.89%. A joint gold/silver rally with weak copper confirmation is one safe-haven bid being reported as both fear and greed.
4. **USDINR single arrow on a dual target.** "FII vs IT exporters" cannot take one arrow: INR strength is a tailwind for FII/EM flows and a headwind for IT exporter revenue.
5. **Rates units.** India 2Y "+0.13%" is ambiguous (percent-change of yield ≈ <1bp noise vs +13bp real move) and thresholded with an equity-calibrated band.
6. **No per-target netting.** SOX −5.44% and Kospi +5.76% both target Nifty IT with opposite arrows and no magnitude weighting; the −5.44% semis move should dominate. Divergence of that size also indicates session mixing (stale SOX print vs fresh Kospi print).

Design philosophy constraints (house rules): continuous conditional functions over hard cutoffs; empirically calibrated parameters over judgment-set defaults; single source of truth with explicit provenance; checked-and-absent ≠ silently zero.

---

## 1. Data layer — quotes fetcher with session metadata

**Replace news-parsed numbers entirely.** Numbers come from a quotes API (yfinance acceptable for v2; broker feed later). News is narrative-only.

### 1.1 Instrument registry (single source of truth)

```python
# global_cues_config.py
INSTRUMENTS = {
    #  key        ticker      asset_class  calendar        inverse  targets
    "SPX":      ("^GSPC",    "equity",    "NYSE",          False,  ["BROAD_FII"]),
    "NASDAQ":   ("^IXIC",    "equity",    "NYSE",          False,  ["NIFTY_IT"]),
    "SOX":      ("^SOX",     "equity",    "NYSE",          False,  ["NIFTY_IT"]),
    "KOSPI":    ("^KS11",    "equity",    "XKRX",          False,  ["NIFTY_IT"]),
    "NIKKEI":   ("^N225",    "equity",    "XTKS",          False,  ["BROAD_ASIA"]),
    "HSI":      ("^HSI",     "equity",    "XHKG",          False,  ["NIFTY_METAL"]),
    "CSI300":   ("000300.SS","equity",    "XSHG",          False,  ["NIFTY_METAL"]),
    "DAX":      ("^GDAXI",   "equity",    "XFRA",          False,  ["NIFTY_AUTO"]),
    "BRENT":    ("BZ=F",     "commodity", "CME_24H",       True,   ["ENERGY_IMPORT"]),
    "DXY":      ("DX-Y.NYB", "fx",        "ICE_FX",        True,   ["BROAD_FII"]),
    "USDINR":   ("INR=X",    "fx",        "FX_24H",        None,   ["FII_FLOWS", "IT_EXPORTERS"]),  # dual — see §4
    "COPPER":   ("HG=F",     "commodity", "CME_24H",       False,  ["NIFTY_METAL"]),
    "GOLD":     ("GC=F",     "commodity", "CME_24H",       True,   ["RISK_APPETITE"]),
    "SILVER":   ("SI=F",     "commodity", "CME_24H",       None,   ["REGIME_DEPENDENT"]),           # see §5
    "IN_2Y":    ("manual",   "rates",     "NSE",           True,   ["RATE_SENSITIVES"]),            # bp units — see §6
    "IN_5Y":    ("manual",   "rates",     "NSE",           True,   ["CORP_BORROWING"]),             # bp units — see §6
    "IN_10Y":   ("manual",   "rates",     "NSE",           True,   ["SOVEREIGN_BENCHMARK"]),        # bp units — see §6
    "IN_2S10S": ("derived",  "rates",     "NSE",           None,   ["CURVE_SHAPE"]),                # slope change in bp — see §6
}
```

`inverse=None` means the sign convention is resolved dynamically (silver regime, USDINR dual target), never defaulted.

### 1.2 Per-cue record (API contract → SQLite `global_cues` table)

Every fetch returns, per instrument:

```json
{
  "key": "SPX",
  "as_of": "2026-07-02T20:00:00-04:00",        // timestamp of the close/quote used
  "session_state": "HOLIDAY",                   // LIVE | CLOSED_FINAL | HOLIDAY | STALE | ERROR
  "pct_change": null,                           // null when HOLIDAY/STALE — never 0.0
  "bp_change": null,                            // rates only
  "ref_window": "prior_close_to_close",
  "trailing_vol_20d": 0.83,                     // daily % vol (bp vol for rates), for z-scoring
  "z": null,
  "strength": null,                             // signed continuous signal, see §2
  "provenance": "yfinance:^GSPC"
}
```

**Hard rule: checked-and-absent ≠ zero.** A closed or stale market yields `pct_change = null` and is excluded from netting. It never enters the pipeline as 0.0.

### 1.3 Staleness / holiday detection

Use `exchange_calendars` (pip: `exchange_calendars`) per instrument:

```python
def session_state(cal, last_bar_date, now_ist):
    expected_prev = cal.previous_session(now_ist.date())
    if not cal.is_session(expected_prev):          # defensive
        return "STALE"
    if last_bar_date < expected_prev:
        # market had a session we don't have data for → data problem
        return "STALE"
    if last_bar_date == expected_prev:
        return "CLOSED_FINAL"                       # fresh final close
    return "LIVE"                                   # intraday bar (Asia concurrent w/ India)
```

Holiday: if the calendar shows no session on the date the panel expects one (e.g., NYSE 03-Jul-2026), state = `HOLIDAY` and the UI renders "Closed — US holiday (Jul 4)" instead of an arrow. **Regression test: the 03/04-Jul-2026 case must render exactly this.**

### 1.4 Session alignment relative to the 9:15 IST open

Tag each instrument with its information band at India's open (store as computed field `band`):

| Band | Instruments | State at 9:15 IST | Role |
|---|---|---|---|
| **T-1 final (overnight)** | SPX, NASDAQ, SOX, DXY (NY close) | Closed ~01:30–02:00 IST — final | **Leading** signal for the open; feeds `magnitude_corroboration` pre-open |
| **Concurrent-leading** | KOSPI, NIKKEI (open 05:30 IST), HSI/CSI (open ~06:45–07:00 IST) | Live, 2–4h of trading elapsed | Leading-ish; use *intraday* change, mark `LIVE`, refresh during India session |
| **Lagging** | DAX (opens 12:30 IST summer) | Previous day's close — one full session stale at India open | At the open, DAX is a T-1 echo of the same US move already counted. **Down-weight or suppress DAX pre-open; activate after 12:30 IST** |
| **Near-24h** | Brent, gold, silver, copper, USDINR (offshore/NDF) | Rolling | Measure change since **India's prior close (15:30 IST)** for a consistent reference window |
| **Onshore** | IN_2Y, USDINR onshore | Prior onshore close until ~09:00 | bp change vs prior close |

The DAX double-counting point matters for netting: at the India open, DAX's overnight print is largely a function of the prior US session — including it alongside SPX/NASDAQ at full weight double-counts one shock. Pre-open weight for DAX → 0.25× (judgment prior, flag for empirical calibration); full weight after Europe opens.

---

## 2. Signal engine — continuous strength, not binary arrows

Replace the dead-band + binary arrow with z-scored continuous strength (house rule: continuous conditional functions over hard cutoffs):

```python
import math

def cue_strength(pct, trailing_vol_20d, inverse):
    """Signed strength in [-1, 1]. Positive = tailwind for the target."""
    if pct is None or trailing_vol_20d in (None, 0):
        return None                       # excluded upstream; never 0.0
    z = pct / trailing_vol_20d            # move in units of that instrument's own daily vol
    raw = math.tanh(z / 2.0)              # saturates: ±2σ → ±0.76, ±4σ → ±0.96
    return -raw if inverse else raw
```

- `trailing_vol_20d` = stdev of daily % changes over 20 sessions, computed from the same quotes feed and stored (rates: stdev of daily bp changes).
- **Display arrow derives from strength:** `|strength| < 0.10` → neutral; else tailwind/headwind, with a magnitude chip (e.g., "headwind ●●●" or the raw strength).
- This fixes the SOX/Kospi problem automatically: SOX −5.44% on ~1.4% daily vol is z ≈ −3.9 → strength ≈ −0.96; Kospi +5.76% on ~1.2% vol is z ≈ +4.8 → +0.98. Comparable strengths — which is exactly why netting weights (§3) and session-freshness checks decide the IT verdict, not the raw arrow list.
- Why per-instrument vol rather than a fixed % band: a 0.4% move is noise for SOX and meaningful for USDINR. Same threshold logic everywhere, calibrated by the instrument's own distribution. Zero judgment-set % bands survive except the ±0.10 display neutral zone.

---

## 3. Per-target netting — sector verdicts with provenance

New function `net_by_target()` producing one verdict per target (NIFTY_IT, NIFTY_METAL, BROAD_FII, RATE_SENSITIVES, RISK_APPETITE, …):

```python
net_score[target] = Σ_i  w[i, target] × freshness[i] × strength[i]     over cues with strength ≠ None
```

- **Weights `w`:** start with judgment priors (SOX 0.45 / NASDAQ 0.35 / KOSPI 0.20 for NIFTY_IT, etc.) but the brief's required end-state is **empirical calibration**: OLS of next-day sector index return on lagged cue z-scores, 250-session window, refit monthly — same pattern as the yield-curve macro factor calibration. Store fitted betas with fit date and R² in a `cue_betas` table. UI shows whether weights are `PRIOR` or `FITTED(date, R²)`.
- **Freshness multiplier:** `CLOSED_FINAL`=1.0, `LIVE`=1.0, lagging-band pre-open (DAX)=0.25, `HOLIDAY`/`STALE`=excluded (not 0-weighted — excluded, with the exclusion listed).
- **Output contract** (this is what the suggester consumes instead of 15 rows):

```json
{
  "target": "NIFTY_IT",
  "net_score": -0.31,
  "verdict": "headwind",
  "contributions": [
    {"key": "SOX",    "strength": -0.96, "weight": 0.45, "contrib": -0.43},
    {"key": "KOSPI",  "strength":  0.98, "weight": 0.20, "contrib":  0.20},
    {"key": "NASDAQ", "strength": -0.35, "weight": 0.35, "contrib": -0.12}
  ],
  "excluded": [{"key": "SPX", "reason": "HOLIDAY"}],
  "divergence_flag": true    // see below
}
```

- **Divergence flag:** if two cues on the same target have `|strength| > 0.5` with opposite signs (today's SOX vs Kospi), set `divergence_flag` and surface it in the UI — this is information, not noise: it usually means session mixing, a local idiosyncratic driver (e.g., Samsung earnings), or a rebound leg. The panel should say *why it's conflicted*, not average silently.

---

## 4. USDINR — split the dual target

One instrument, two rows, opposite conventions:

| Target | Convention | INR strengthens (USDINR ↓) |
|---|---|---|
| `FII_FLOWS` | inverse=True | tailwind (EM allocation, currency-hedged returns) |
| `IT_EXPORTERS` | inverse=False | headwind (rupee revenue compression) |

Both rows share the same quote record; only the sign map differs. Additionally compute an **idiosyncrasy flag**: if sign(USDINR move) disagrees with sign(DXY move) beyond noise (both |z| > 0.5), the INR move is local (RBI action, flows, oil-import hedging) rather than a dollar story — tag the row `IDIOSYNCRATIC_INR` for the narrative panel.

---

## 5. Silver regime classifier — the gold/copper arbitration

Silver is dual-natured (~50% industrial demand, precious-metal beta). Never a static `direct` flag. Resolve per session, continuously:

```python
def silver_regime(z_gold, z_silver, z_copper, gsr_change_pct):
    """
    Returns industrial_share in [0,1]:
      1.0 -> fully industrial read (direct, copper-like)
      0.0 -> fully precious/safe-haven read (inverse, gold-like)
    """
    if z_silver is None:
        return None

    # Copper confirmation: same sign as silver AND comparable magnitude
    if z_copper is not None and z_silver != 0 and (z_copper * z_silver) > 0:
        copper_confirm = min(abs(z_copper) / max(abs(z_silver), 1e-9), 1.0)
    else:
        copper_confirm = 0.0

    # Gold leadership: gold moving same direction with real magnitude
    if z_gold is not None and (z_gold * z_silver) > 0:
        gold_lead = min(abs(z_gold) / max(abs(z_silver), 1e-9), 1.0)
    else:
        gold_lead = 0.0

    industrial_share = copper_confirm / (copper_confirm + gold_lead + 1e-9)

    # Gold/silver ratio tiebreak: GSR falling while copper confirms = industrial bid;
    # GSR falling while gold leads = silver is high-beta fear, not industry.
    if gsr_change_pct is not None and abs(copper_confirm - gold_lead) < 0.15:
        industrial_share += 0.15 if (gsr_change_pct < 0 and copper_confirm > 0) else -0.15
    return min(max(industrial_share, 0.0), 1.0)
```

Then silver contributes to **both** targets, blended:

```
strength_industrial = +tanh(z_silver/2) × industrial_share        → NIFTY_METAL / industrials
strength_fear       = −tanh(z_silver/2) × (1 − industrial_share)  → RISK_APPETITE (gold-side)
```

**Today's snapshot as the worked test case:** gold z ≈ +2.1, silver z ≈ +2.3, copper z ≈ +0.9 → copper_confirm ≈ 0.39, gold_lead ≈ 0.91 → industrial_share ≈ 0.30. Silver reads ~70% safe-haven: the panel would show a mild industrial tailwind and a larger risk-appetite headwind — consistent with gold instead of contradicting it. UI shows the regime chip: `SILVER: 30% industrial / 70% precious (gold-led)`.

Document rule + parameters in DECISIONS.md (D-GC-02); the 0.15 tiebreak and blend form are judgment priors, flagged for later empirical review against silver/metal-index betas conditioned on GSR.

---

## 6. India 2Y — basis points, not percent-change

- Store `yield_level` and `bp_change = (yield_t − yield_{t−1}) × 100`.
- z-score against trailing 20-session stdev of daily bp changes (typically ~2–4bp for the 2Y; a +13bp day is a real z ≈ +4 event, a +0.5bp day is nothing).
- Display: `India 2Y +13bp (z +3.8) → headwind` — never "+0.13%".
- Same treatment for 5Y and 10Y (each z-scored against its own trailing bp vol).
- **2s10s slope cue (derived):** `slope_bp = y10 − y2` in bp; signal = daily change in slope, z-scored. Sign is regime-dependent, so `inverse=None`: bear-steepening (10Y up faster) reads headwind for duration/rate-sensitives; bull-steepening (2Y down faster, easing expectations) reads tailwind for financials/broad. Emit the decomposition (Δ2Y, Δ10Y, Δslope) so the narrative panel can say *which kind* of steepening — do not collapse to one arrow without it.
- **Weekend/holiday behavior (observed 04-Jul Saturday run):** all three yield rows printed 0% and, being inverse-flagged, all resolved to "tailwind" — the entire Indian yield curve reported as supportive from no session at all. NSE calendar gating (§1.3) must cover these rows identically: Saturday/Sunday/market holiday → `HOLIDAY`, null, excluded.

## 6a. Fetch-failure coercion (second observed defect class)

In the 04-Jul run, S&P printed exactly 0.00% while Nasdaq printed −0.8% in the same session — a near-impossible decoupling. Diagnosis: a failed or empty fetch is being coerced to `0.0` somewhere between the fetcher and the panel (the reference `fetch_yf` returned `None` on exception; the current implementation is defaulting it). **Requirement:** audit the full path fetcher → store → API → UI for any `or 0`, `fillna(0)`, `.get(key, 0)`, or float coercion of null. A fetch failure must surface as `session_state=ERROR` with the exception string in provenance — never as a zero that then acquires a direction from the inverse flag. Silent-and-directional is the worst possible failure mode for a signal panel.

---

## 7. UI changes (Global Cues panel)

1. Row states: arrow chip (tailwind/headwind/neutral) + strength magnitude; `HOLIDAY`/`STALE` rows render greyed with the reason ("Closed — US holiday (Jul 4)"), no arrow.
2. New **sector verdicts strip** above the row list: one card per target with net_score, verdict, divergence badge, expandable contributions table (provenance requirement).
3. USDINR renders two rows; silver renders the regime chip; 2Y renders bp.
4. Band tag on each row (T-1 / concurrent / lagging) so the freshness of every cue is visible at a glance.

---

## 8. Acceptance tests (must pass before merge)

| # | Case | Expected |
|---|---|---|
| 1 | NYSE holiday (03-Jul-2026 fixture) | SPX/NASDAQ/SOX/DXY → `HOLIDAY`, rendered "Closed", excluded from netting, `excluded[]` populated. **No 0.0 anywhere.** |
| 2 | Flat print, |z| small (SPX +0.03%) | strength ≈ 0 → neutral chip; identical result whether inverse flag is True or False |
| 3 | SOX −5.44% + KOSPI +5.76% fixture | NIFTY_IT net negative (prior weights), `divergence_flag=true`, contributions table shows both |
| 4 | Gold +1.49 / silver +2.87 / copper +0.89 fixture | industrial_share ≈ 0.3; silver's net risk-appetite contribution is a headwind; no standalone bullish-industrial arrow |
| 5 | USDINR −0.19% | FII_FLOWS row tailwind, IT_EXPORTERS row headwind, same quote record |
| 6 | USDINR −0.4% with DXY +0.5% | `IDIOSYNCRATIC_INR` flag set |
| 7 | 2Y +13bp vs +0.5bp | headwind (z≈4) vs neutral |
| 8 | DAX at 09:15 IST | freshness weight 0.25 pre-open; 1.0 after 12:30 IST refresh |
| 9 | Quotes API failure for one ticker | row `ERROR`, excluded with reason; panel still renders remaining cues (no cascade) |
| 10 | Saturday run, NSE closed (04-Jul-2026 fixture) | IN_2Y/5Y/10Y → `HOLIDAY`, no arrows; **regression: must not show three tailwinds** |
| 11 | S&P fetch returns empty while Nasdaq succeeds | S&P row `ERROR` with provenance message; **never 0.0 → headwind**. Grep-audit test: no `fillna(0)`/`or 0`/`.get(k, 0)` on the pct path |
| 12 | Mixed-session Saturday snapshot (Thu US + Fri Asia + closed India) | every row's band/`as_of` visible; netting uses only same-decision-window cues or flags the mix |

---

## 9. Sequencing for Antigravity

1. **PR-1 (data):** instrument registry, quotes fetcher, session/holiday states, SQLite schema (`global_cues`, `cue_betas`), trailing-vol computation. Kills bugs #1 and #2.
2. **PR-2 (signal):** `cue_strength`, per-target netting with prior weights, divergence flag, USDINR split, 2Y bp. Kills #4, #5, #6.
3. **PR-3 (regimes):** silver regime classifier + risk-appetite blending. Kills #3.
4. **PR-4 (UI):** verdicts strip, row states, regime/band chips.
5. **Later (flagged, not in scope):** OLS beta calibration job replacing prior weights; DECISIONS.md updated when fitted betas go live.

Each PR ships with its fixture tests from §8. Do not collapse PR-1 and PR-2: the null-propagation contract (`checked-and-absent ≠ zero`) must be verified in isolation before signal math consumes it.
