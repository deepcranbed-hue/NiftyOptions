# Antigravity Corrective Brief — Global Cues: Rates Rows & Curve Regime
**Scope:** rates rows only (IN_2Y, IN_5Y, IN_10Y, IN_2S10S). Supersedes nothing; completes §6/§6a of Global Cues v2 brief.
**Status check from 06-Jul output:** neutral dead-band ✅ working (S&P 0%, Nikkei −0.01%, CSI 0% all render neutral). Holiday/null path ✅ assumed working. Rates §6 ❌ not implemented — this brief is the punch list.

---

## Defect 1 — rates rows still in percent-change

Observed: `India 2Y (+0.1%)`, `India 5Y (+0.5%)`, `India 10Y (+1%)`. Percent-change of a yield is a ratio of two rates — meaningless across instruments and wrongly thresholded by the equity dead-band.

**Fix (per §6, restated precisely):**
- Store `yield_level` (e.g., 6.32) and compute `bp_change = (level_t − level_prev_close) × 100`.
- z-score against trailing 20-session stdev of daily bp changes per tenor (typical IN 2Y: ~2–4bp/day). Strength = `tanh(z/2) × (−1)` (yields up = headwind for the level rows; inverse retained).
- Display format: `India 10Y 6.32% (+6.3bp, z +1.9) → headwind`. The `%` shown is the **level**, never the change.

## Defect 2 — 2s10s computed as difference of percent-changes

Observed: `India 2S10S (+0.9%)` = 1% − 0.1%, i.e., subtracting the percent-changes of two different levels. This is not a slope move.

**Fix:**
```python
slope_bp        = (y10_level − y2_level) × 100          # level of slope, in bp
d_slope_bp      = d10_bp − d2_bp                         # today's slope change, in bp
```
Worked check on today (assume 2Y ≈ 5.80, 10Y ≈ 6.30): d2 ≈ +0.6bp, d10 ≈ +6.3bp → d_slope ≈ +5.7bp steepening. Not "+0.9%".
- z-score `d_slope_bp` against its own trailing 20-session bp-change vol.

## Defect 3 — slope arrow hardcoded "steepening = tailwind"

Observed: three yield rows headwind + slope tailwind, no explanation. Steepening's equity meaning is **not sign-stable**; today's move (anchored short end, long end selling) is the headwind variety. `inverse=None` was specified for IN_2S10S for exactly this reason — do not sign-map it.

**Fix — curve regime classifier (continuous, house rules):**

```python
def curve_regime(z2, z10):
    """
    z2, z10: bp-vol-normalized daily changes of 2Y and 10Y.
    Returns (regime_label, equity_strength in [-1, 1], note).
    """
    NEUTRAL = 0.25
    if z2 is None or z10 is None:
        return ("UNAVAILABLE", None, "leg missing")
    if abs(z2) < NEUTRAL and abs(z10) < NEUTRAL:
        return ("QUIET", 0.0, "curve unchanged")

    d_slope_z = z10 - z2
    mag = math.tanh(abs(d_slope_z) / 2)          # size of the shape move

    if z10 > z2:                                  # steepening
        if z2 <= NEUTRAL:                         # short end anchored or falling
            if z2 < -NEUTRAL:
                return ("BULL_STEEPENING", +mag,  # easing expectations lead
                        "short end rallying — easing expectations; equity tailwind")
            return ("BEAR_STEEPENING_ANCHORED", -0.7 * mag,
                    "long end selling, policy expectations unchanged — term premium/supply/global spillover; duration headwind, NOT a growth signal")
        return ("BEAR_STEEPENING", -0.5 * mag,
                "both legs up, long end faster — inflation/supply premium; mild headwind")
    else:                                         # flattening
        if z2 > NEUTRAL:
            return ("BEAR_FLATTENING", -mag,
                    "short end pricing hikes — hawkish; headwind")
        return ("BULL_FLATTENING", -0.6 * mag,
                "long end rallying on growth fear — risk-off; defensive headwind")
```

- The 0.7/0.5/0.6 severity weights are judgment priors (`PRIOR` tag, D-GC-05 in DECISIONS.md); refine against sector-return betas when history permits.
- Today's fixture: z2 ≈ +0.2, z10 ≈ +1.9 → `BEAR_STEEPENING_ANCHORED`, strength ≈ −0.5 → **headwind**, chip text as above. This directly answers the "rising yields = equity rotation?" question in the panel itself: no — the short end says policy expectations are unchanged, so the long-end selloff is compensation demand, not growth pricing.

**Display contract:** the 2s10s row always shows the decomposition and regime, never a bare arrow:
`2s10s 50bp (+5.7bp, bear steepening — anchored short) → headwind` with an expandable note. Banks/financials get a caveat line in the note (steeper curve helps NIM, hurts bond books — mixed), but the single arrow reflects the broad-equity read.

## Enhancement (small) — flows cross-check hook for the daily report

On any day where |z10| > 1.5, the daily report appends one line joining the Flows panel data:
- FII debt outflow **and** equity inflow same day → "debt→equity rotation consistent" 
- Both segments outflow → "global risk reduction, not rotation"
- Mixed/unavailable → state which leg is missing (checked-and-absent, never silent).
This is a join of two existing state pillars — no new data source.

## Acceptance tests

| # | Fixture | Expected |
|---|---|---|
| 1 | 2Y 5.80→5.806, 10Y 6.30→6.363 | rows show +0.6bp / +6.3bp with levels; **no percent-changes anywhere** |
| 2 | Same fixture, slope | `d_slope ≈ +5.7bp`; regression: output must NOT be "0.9%" or any %-difference |
| 3 | Same fixture, regime | `BEAR_STEEPENING_ANCHORED`, headwind, chip contains "not a growth signal" |
| 4 | 2Y −5bp, 10Y −1bp | `BULL_STEEPENING`, tailwind |
| 5 | 2Y +5bp, 10Y +1bp | `BEAR_FLATTENING`, headwind |
| 6 | 2Y +0.4bp, 10Y +0.3bp | `QUIET`, neutral (leg dead-band works in bp space) |
| 7 | 10Y leg missing | slope row `UNAVAILABLE` with reason; level rows unaffected (no cascade) |
| 8 | |z10| = 1.8 day, flows available | daily report contains the rotation-vs-risk-reduction line |
| 9 | Grep audit | no rates row consumes `pct_change`; all consume `bp_change` |

## Sequencing
Single PR — all four fixes are one module (`rates_cues.py` or the rates branch of the signal engine) plus the report hook. Ship with fixtures 1–7; fixture 8 can follow if the flows join needs a session of plumbing.
