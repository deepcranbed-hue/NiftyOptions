# backend/quant — Analysis Pipeline

The quant module is the **analysis engine behind the Strategy Desk / Global Macro**
views. It ingests the option chain, news, global cross-asset cues, flows and macro
state, and produces a regime read, a strategy view, an optimizer output, and — for
every input — a **provenance record** describing where the number came from and how
much to trust it.

It is invoked as one call, `run_pipeline(...)` (`pipeline.py`), exposed to the
frontend via `POST /api/run-pipeline` (see `backend/main.py`). See `SKILL.md` for
the strict execution order and `REFERENCE.md` for the data contracts.

## Pipeline flow (`run_pipeline`)

```
chain + news + cues/flows/macro state
        │
        ▼
complacency + risk gate → regime assessment (news window)
        │
        ▼
sector/entity sentiment → index bias + coverage → news momentum
        │
        ▼
RND extraction (rnd.py, scipy) → market-view synthesis (regime vs bias)
        │
        ▼
strategy suggestion + strike optimizer + position sizing
        │
        ▼
result{ regime, momentum, optimizer, sizing, cues, provenance, timestamps, … }
```

State (news / flows / events / macro / cues) is **decoupled** and persisted via
`state_manager.py`; each has its own `/api/update-*` endpoint that refreshes and
writes it. `run_pipeline` reads the latest persisted state — so to refresh a part of
the analysis you refresh *its* state, then re-run the pipeline.

## Provenance & data freshness (the trust layer)

Every component the pipeline consumes carries a `Provenance` record
(`provenance.py`) with a `quality`:

| quality | meaning | UI |
|---|---|---|
| `PRIMARY` | straight from the real/live source — trust it | green |
| `PARTIAL` | real source but incomplete/degraded | amber |
| `STALE` | reused value older than its freshness budget | orange |
| `FALLBACK` | real source failed → synthetic/default estimate | red |
| `UNAVAILABLE` | no data for this component | grey |

`overall_provenance()` rolls the records into `{overall, headline, degraded,
records}` for the `ProvenanceBadge` UI. The point is honesty: a guessed VIX or a
two-day-old crude print is *labelled*, never passed off as solid.

**Global-cues freshness** is a separate, per-ticker axis (`global_cues.py
::get_session_state`): each cross-asset symbol is `LIVE` / `CLOSED_FINAL` (market
closed, showing last close — normal) / `STALE` (last quote older than the previous
session — genuinely behind) / `HOLIDAY` / `ERROR`. `STALE`/`HOLIDAY` cues are
down-weighted (freshness × 0.25) in `net_by_target`. The panel surfaces
`session_states` and `cue_as_of` (per-cue last-quote timestamp) so you can see which
feed is behind and how old.

## News tagging guardrails (`news_provenance.py`)

Guardrails wrap the LLM tagger with **zero extra model calls** (junk is quarantined
*before* it reaches the model, which saves calls):

- **Source trust tiers** (`source_tier`): `exchange_filing` 1.0 > `established_wire`
  0.85 > `aggregator` 0.60 > `syndicated_pr` 0.35 > `live_blog` 0.20 — down-weighting
  PR wires and price-ticker "live" blogs breaks the circular price→sentiment loop.
- **Pre-LLM ingest scan** (`scan_batch`): quarantines — never silently drops —
  prompt-injection ("ignore previous instructions", role tokens), zero-width / RTL /
  homoglyph obfuscation, base64 blobs and oversized bodies, each *with a reason*.
- **Relevance filter** (`is_relevant`): keeps India news + India/US macro + global
  cues, drops foreign single-stock / crypto noise before tagging.
- **Cross-feed dedup** (`cluster_signature`): `company | event-class | day`, so N
  syndications of one event collapse to one.

`sector_tagging.py` aggregates with a **weighted median** (robust to a single planted
item), applies the tier multiplier, one vote per article per sector, clamps to
`[-1,1]`, flags thin reads `low_confidence`, and emits a run-level `__audit` block.
`llm_tag.py`'s always-available keyword fallback now uses a wide word-boundary lexicon
and emits coarse `sectors_affected`, so degraded mode isn't near-empty.

**Meta-key convention:** sector dicts carry `__`-prefixed meta keys (`__drilldown`,
`__audit`); every sector consumer skips ANY `"__"`-prefixed key (not just
`__drilldown`) so `__audit` can't KeyError the sector render.

## Module map (grouped)

- **Orchestration:** `pipeline.py` (run_pipeline), `decision_engine.py`,
  `market_view.py`, `regime_synthesis.py`, `state_manager.py`.
- **Options / vol:** `rnd.py` (+ `rnd_check.py`), `skew/`, `vrp_pipeline.py`,
  `vol_attribution.py`, `dispersion_engine.py`, `complacency.py`,
  `strategy_probability.py`, `strategy_suggester.py`, `strike_optimizer.py`,
  `recommended_strikes.py`, `risk_budget.py`, `portfolio.py`.
- **Cross-asset / macro:** `global_cues.py`, `bond_cues.py`, `india_macro.py`,
  `us_macro.py`, `market_regime.py`, `index_attribution.py`.
- **News / sentiment:** `rss_news.py`, `news_window.py`, `news_provenance.py`,
  `gemini_tag.py` / `llm_tag.py` / `llm_config.py`, `sector_tagging.py`,
  `sector_map.py` / `sector_tree.py`, `flows_fetcher.py`, `fundamentals.py`.
- **Views (standalone, self-contained HTML):** `gold_cycles.py` — the **Gold view**.
- **Integrity:** `provenance.py`, `data_quality_agent.py`, `formulas.py`.
- **Loaders:** `nse_csv_loader.py`, `breeze_loader.py`, `nse_csv_loader.py`.

## Views

A **view** here is a standalone page built from `price_bars`, not a pipeline stage: one
self-contained HTML file with inline CSS/JS and no CDN, so it opens years from now with no
network. Views are DERIVED — regenerated on every sync, written to `reports/` (gitignored),
and it is the generator that is tracked, never the render.

### Gold view — `gold_cycles.py` → `reports/gold_inr_view.html`

Gold in rupees at **landed cost**, 2018 to now:

    parity = GOLD_USD / 31.1035 * 10 * USDINR
    landed = parity * (1 + import_duty_on_that_date) * (1 + 3% GST)

- **Why reconstructed:** native MCX daily history starts 2025-10-16 — ten months, which
  cannot show a cycle. `GOLD_USD` (COMEX) and `USDINR` both run to 2018-01-02.
- **Why landed:** naked parity is not transactable in India, and the duty is not a constant.
  `DUTY_SCHEDULE` carries five dated changes; the 2026-05-13 hike from 6% to 15% falls INSIDE
  the current drawdown and accounts for 6.5pp of it.
- **Validated, not asserted:** scored against the continuous front contract `GOLD` on days
  with volume above `MIN_VOL` — 78 traded days, residual median -0.60%, sd 1.72pp. The
  residual that remains is India's domestic basis, not model error; a persistent drift in it
  is the first sign `DUTY_SCHEDULE` has gone stale, so check policy before the market.
- **Refuses rather than renders** when USDINR sits outside 20-200 (C39). The page multiplies
  by FX, so a repeat of that scale flip would move every level tenfold and still draw a
  perfectly smooth chart.
- **Runs as** the `gold-view` step in `sync_all.py`, macro phase, **after `mirror`** — it
  reads the mirror, so running it earlier renders yesterday's data under today's date.

## Run / validate

```bash
python backend/quant/sector_map.py         # sector-weight smoke test (no orphans)
python -m backend.quant.harness evaluate   # offline historical evaluation
python backend/quant/_redteam_asr.py       # adversarial ASR harness (injection/spoof, control ablations)
python backend/quant/_sentiment_audit_demo.py  # print the __audit trail for a sample batch
# RND: python debug_rnd.py  (or rnd_check.diagnose)
```

## Key invariants

- **No T=0 RND.** `days`/`spot` are caller-supplied; a chain at expiry is rejected.
- **Sector map is source of truth** (`sector_map.py`); unmatched tags weight to ~0.
- **Provenance is never silently upgraded.** A FALLBACK/STALE value stays labelled;
  the UI shows the degraded badge rather than presenting it as PRIMARY.
- **Pseudo-probabilities are heuristic**, not calibrated win rates.
- **Flagged news is quarantined, never silently dropped** — excluded with a reason
  recorded in `__audit`, not passed through as a neutral 0.0.
