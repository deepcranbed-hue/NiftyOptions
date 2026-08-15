# Knowledge-base changelog — governance record

**Rule (model governance): the ARCHITECTURE is frozen; the KNOWLEDGE is living.** The
inference engine, research engine, and dashboard are stable infrastructure. This file —
factor definitions, sensor roles, and their evidence — changes ONLY when the research
engine provides sufficient evidence, and every change records four things:

1. **What changed** (e.g., promoted `adx` from confidence to supporting in `trend_quality`)
2. **Why** (e.g., within-factor incremental IC exceeded the current primary over the window)
3. **Evidence window** (e.g., 2026-06-29 → 2026-09-30, 62 sessions)
4. **Expected impact** (e.g., stronger trend detection; no change to other factors)

Bump `_meta.version` in `factor_map.yaml` on every change (minor = role changes,
major = factor added/removed/redefined) so every backtest can state exactly which
market-state definition it used.

---

## v1.0 — 2026-07-29 (initial)

- **What:** Initial factor map: 6 factors (trend_direction, trend_quality,
  market_internals, dealer_positioning, macro_risk, vol_structure) covering all 21
  directional signals with four-role taxonomy.
- **Why:** Seeded from the signal taxonomy + the Part-E factor-discovery audit on the
  available history. Roles are PRIORS — within-factor incremental IC is not yet
  trustworthy at this sample size.
- **Evidence window:** 2026-06-29 → 2026-07-24 (20 sessions — PROVISIONAL).
- **Expected impact:** Baseline market-state definition for V1. All promotions/demotions
  await ≥60-session evidence.
