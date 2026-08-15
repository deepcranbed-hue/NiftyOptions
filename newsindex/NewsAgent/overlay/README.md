# overlay — economics enrichment layer (Phase 1)

Post-processes the Deterministic Core's outputs to address the institutional review, **without
editing `market_scan.py`**. The orchestrator calls `enrich.enrich_mio(mio, core)` after the
Core builds the MIO; the reporter renders the new sections.

## What each review point maps to

| Review point | Module | What it does |
|---|---|---|
| "Never Oil→Banks direct — always Oil→Inflation→RBI→Yields→…→Banks" | `chains.py` | canonical multi-hop **economic-prior** pathways; every chain ≥ 3 hops |
| "Oil→Fiscal Deficit→Bond Yield is missing" | `chains.py` | explicit fiscal/borrowing branch on oil |
| "Banks also *finance* AI, not just benefit from productivity" | `chains.py` | AI-financing branch: SOX→corporate AI investment→bank corporate lending→capital goods |
| "Extend the level amplifier to USDINR / VIX" | `amplifiers.py` | level bands for rupee (82/83/84/85) and VIX (calm→panic) |
| "Interaction 3% is generic — compute Oil×USD, FII×VIX, …" | `interactions.py` | named second-order terms from Core driver values, tagged PRIOR |
| "Oil 7% at $92 is too low" | `interactions.py` + `enrich.py` | interaction-adjusted dominance — oil's effective weight rises via interactions, not a bigger direct coefficient |
| "BPCL is not ONGC — split Energy" | `sectors.py` | adds a distinct **Energy (OMC)** line (short crude) opposite Upstream |
| "Company beat should feed the Sector model" | `sectors.py` | folds company catalysts into the matching sector score |
| "'Rule' sounds deterministic — markets are probabilistic" | `terminology.py` | Economic Prior / **Supported** / **Dominated by stronger drivers** |
| "Build a live causal market graph with rich nodes" | `causal_graph.py` | nodes carry current state · economic confidence · historical reliability · today's activation · supporting news · observed confirmation |
| "SOX↓⇒IT↓ is not a law — classify WHY semis moved first" | `semis_regime.py` | conditional causal engine: classifies the cause (profit-taking / earnings / AI-demand-slowdown / valuation-rotation / higher-yields / rotation-into-software / risk-off), reasons on two orthogonal dims (AI Demand × Capital Allocation), asks the 5 diagnostic questions, infers the Indian-IT read, and supersedes the fixed direction rule |
| "Auto/Banking/Pharma/FMCG are too broad — split them" | `subsectors.py` | sub-sector factor models: Auto (PV/CV/2W/EV/components), Banking (deposit/credit/CASA/NIM/provisions), Pharma (USFDA/API/USD/export), FMCG (monsoon/palm/food-inflation/rural); computes from tape + news, and names fundamental factors that "need data" with their sign |

## Guarantees

- **No engine edits.** Everything reads the Core's outputs and adds fields.
- **Numbers still from the Core.** Interaction terms and dominance boosts are computed from
  the Core's driver values; economic-confidence and reliability are tagged `PRIOR`.
- **MIO stays schema-valid.** The overlay ADDS fields (`transmission_multihop`, `interactions`,
  `driver_dominance_adjusted`, `affected_sectors_enriched`, `causal_graph`, amplifiers) and a
  `status_label` next to the original `status`; the original schema-required fields are untouched.

## Relationship hierarchy (Primary → Secondary → Idiosyncratic)

* `relationship_tiers.py` — the Barra-style decomposition **return = market + sector +
  idiosyncratic**, made explicit:
  **① Primary (systematic)** — drivers that move the WHOLE index (FII, US rates/Fed, VIX/risk,
  USD, global equities, geopolitics), each with its market-effect direction, contribution and
  mechanism, summing to the **market tilt (beta)**;
  **② Secondary (sector)** — from the per-sector library (which groups move today);
  **③ Tertiary (idiosyncratic)** — company-specific catalysts (earnings, USFDA, scheme
  beneficiary, order win, broker upgrade) that move ONE name.
  Its headline feature is the **⚡ decoupling detector**: when a name's idiosyncratic catalyst
  points opposite to the market tilt (and is strong enough), it's flagged "rises **despite** a
  falling market" (or vice versa) — exactly the alpha-vs-beta case a desk watches for.

## `common.py` — single source of truth (consistency)

All shared logic lives in `common.py` and every module imports it — no more copy-pasted
helpers drifting apart. One place to change a cap, a driver label, or a cue word:
`clamp` / `norm` / `CAPS` (normalization), `DRIVER_LABELS` (sourced from the engine),
`UP_WORDS` / `DOWN_WORDS` + `news_direction` (one direction detector), `news_text` (one
field-combiner), `sentences`, `dedupe`. (`calibration._tokens` is deliberately separate —
it's name-matching, not numeric normalization.)

## News acquisition — read BODIES, not just headlines (news_fetch.py)

RSS only carries a headline + link + short summary; the signals the classifiers actually need
(IBM redirecting budgets, cloud growth, a NIM figure, a USFDA 483, a Cabinet scheme) live in
the article **body**. `news_fetch.py` (the first enrich step) fixes both discovery and depth:

* **Targeted search** — `search_google_news()` queries topics the reasoning modules need (IBM/
  capex/cloud, US CPI/PPI, PLI schemes, USFDA…) via Google News RSS (**keyless**), so we FIND
  the right articles, not just whatever the fixed market feeds happen to carry. `NEWSAGENT_SEARCH=0`
  disables it.
* **Full bodies, robustly** — `fetch_body()` pulls the article body with a three-tier fallback:
  trafilatura → Playwright → **plain requests + HTML-strip (keyless, always works)**. So bodies
  are read even when trafilatura/Playwright aren't installed — no more silent degradation to
  headlines. `NEWSAGENT_FULLTEXT_LIMIT` caps how many (default 20).

The report header shows the coverage ("news: N items, K full-text, +M from search") so you can
see at a glance that bodies are actually being read.

## Full-text enrichment (why body-only signals were missed)

`market_scan.fetch_news()` stores only the **headline** (it computes `title + summary` for
tagging but discards the summary). So a signal living in the article BODY — e.g. "softer US
wholesale inflation … eased concerns over further policy tightening" under a "Sensex rises…"
headline — never reached the scanners, and the macro block showed "no fresh US inflation print".

Fixed: `enrich.py` runs a **full-text step first** — `extract.enrich_news_fulltext()` pulls
article bodies (via the engine's `fetch_article`) for the top macro/market items, so every
scanner (macro-expectations, semis, policy, numeric parser) sees the body. Config:
`NEWSAGENT_FULLTEXT=0` disables it; `NEWSAGENT_FULLTEXT_LIMIT` caps how many articles are
fetched (default 12).

> **Action required:** `fetch_article` needs `trafilatura` (or the engine's Playwright
> fallback). Install it in the venv — `pip install trafilatura` — otherwise the fetch no-ops
> and you only get headline-level signals (which is exactly what caused the miss).

## Calibration & live data (wired to the existing pipeline)

The overlay does **not** invent reliability or leave fundamentals as placeholders — it
consumes what the project's crawler and calibration already produce:

* `calibration.py` reads **`events.db`** (built by `build_events.py`): `linkage_conf`
  gives the real historical **hit-rate per relationship** (e.g. SOX→Indian IT 57%, n=1323;
  Kospi→IT 61%, n=408), and `event_stats` gives **historical analogues** (sox_drop_3,
  oil_up_3, vix_spike_5, riskoff_combo). `historical_reliability` is graduated
  **PRIOR → CALIBRATED** when n ≥ 60. It flows into the MIO confidence, every validation
  row, the causal-graph nodes, and the semis read. Drivers with no historical series
  (FII) fall back to the mean of the active calibrated linkages.
  Refresh the numbers with `python build_events.py` (and `calibrate.py` for coefficients).

* `subsectors.py` fundamentals (deposit growth, CASA, NIM, provisions/slippage) are now
  **detected from the crawler's news / full-article text** with direction — "NIM expands"
  activates the NIM factor +, "slippages rose" activates asset-quality −. Only when a
  factor isn't mentioned today does it stay listed as "needs data" with its sign.

* `impact_scoring.py` answers **"which numbers actually matter?"** — a raw value is not
  impact. It scores each parsed metric by **surprise** (σ from a sector baseline, or vs a
  supplied consensus), a **materiality band** (USFDA warning-letter, GNPA stress, big NIM
  delta), and the **entity's Nifty weight**, into an `impact_score` + Low/Moderate/High/
  Very-High label + signed `index_impact`. So a 22% deposit beat at a 13%-weight bank is
  Very High, while an in-line 3.3% NIM at a no-weight small-cap is Low. Metrics are ranked by
  index impact in the report. It also feeds two consumers: sub-sector fundamentals are
  **magnitude-scaled by impact** (not a flat ±0.1), and the parsed **hyperscaler-capex
  guidance** (raised vs cut) is routed into the semis classifier's AI-demand dimension.

* `extract.py` is a **numeric parser** that pulls EXACT figures from the crawled text —
  NIM 3.6%, deposits +15%, GNPA 1.2%, provisions ₹1,200 cr, PAT +12%, USFDA "5 observations",
  hyperscaler capex $80 bn, order-book TCV $12.5 bn — each with unit, direction, and a
  quality-sign read (up-is-good vs up-is-bad, e.g. GNPA↑ is negative). Output lands in the
  MIO as `extracted_fundamentals` and renders as its own report table. It reads whatever
  text the snapshot carries; `extract.enrich_news_fulltext(news, core)` optionally pulls the
  full article body via `market_scan.fetch_article` first (needs network + trafilatura).

## Macro expectations & policy catalysts (always-on forward view)

* `macro_expectations.py` — a **standing block, always included, split by GEOGRAPHY** so a US
  labor print is never confused with an India rate signal. News is geo-classified (US vs India
  markers) and produces two clearly-labelled blocks:
  **🇺🇸 US** — labor/jobs (payrolls, jobless claims), inflation (US CPI + **PPI/wholesale** +
  PCE), and the **Fed/FOMC** rate expectation (hike bias / on hold / cut bias);
  **🇮🇳 India** — inflation (CPI/WPI + food + oil pass-through) and the **RBI/MPC** rate
  expectation. Each with evidence + a one-line policy path. Evidence-based from news cues +
  tape (US10Y, dollar, oil); negation-aware ("eased concerns over further policy tightening"
  reads dovish). On a quiet day it still prints, tagged "read from the tape".

* `policy_catalysts.py` — **generalized, NOT hardcoded**. Two maintainable keyword layers:
  (1) `POLICY_THEMES` maps policy/scheme keywords → the **sector(s) they affect** (semiconductor/
  electronics, defence, EV/battery, pharma, metals, power, telecom, cement, realty, agri…),
  with tailwind vs headwind (a duty hike / ban reads headwind). (2) generic broker/analyst cues.
  The affected **companies are resolved dynamically from the engine's `COMPANY_GAZETTEER`**
  (97 names, each sector-tagged) — so "Cabinet approves semiconductor manufacturing" resolves
  to whatever is tagged EMS/Electronics/Semiconductor (Dixon, Kaynes…), and a broker view
  ("<any broker> on <any stock>") resolves the stock from the universe. Add a name to the
  gazetteer and it's picked up automatically; nothing in this module changes. Word-boundary
  matching avoids false hits (e.g. "PG" inside "upgrades").

## Executive dashboard (top-of-report, environment first)

* `macro_dashboard.py` — the institutional summary a desk reads first, built by aggregating
  the MIO's existing signals (no new numbers):
  a **Market Phase card** (phase · liquidity · growth · inflation · AI · oil · market bias ·
  confidence); a **Macro regime card** of 7 themed scores (Liquidity, Growth, Inflation,
  Monetary Policy, Geopolitics, Technology/AI, Valuation/Risk) each with view + drivers; a
  ranked **Dominant themes** table (strength% + beneficiaries/losers, flipped by score sign);
  and an **Institutional dashboard** (per theme: current state · market bias · confidence ·
  horizon · key transmission). Rendered at the very top so the report reads
  **environment → sectors → drivers → evidence → names**. Scores are PRIOR until calibrated.

## Per-sector factor library (institutional sector scores)

* `sector_factors.py` — fixes the "same drivers for every sector" problem. Each of **12
  sectors** has its OWN weighted factor model (from the macro-strategist review), not a shared
  macro set:
  Banks → RBI/rate-stress, yield-curve/MTM, credit growth, earnings (VIX only a 5% secondary);
  IT → AI regime, US enterprise IT spend, USDINR, earnings (SOX only secondary);
  Upstream → Brent **level** + move + windfall policy; OMC → GRM/pricing/Brent-inverse;
  Auto → consumer demand, rural, rates, steel/fuel (no US10Y); Pharma → USFDA, USDINR, generic
  pricing, API (no VIX); Metals → China PMI/stimulus, copper, steel, USD (no Kospi); plus the
  previously-missing **FMCG, Realty, Capital Goods, Telecom, Power & Utilities** (the last two
  as the AI-infrastructure beneficiaries).
  Each factor is tagged macro / regime / catalyst / fundamental. The daily score uses
  **effective weight = base × activation × regime multiplier** (the coefficient review's
  strongest ask): base weights are the stable priors, *activation* is how live the factor is
  today (a macro move's magnitude, a catalyst hit, a parsed fundamental's impact), and the
  *regime multiplier* amplifies factors that matter more in the active regime (AI-Substitution
  ×1.8 on AI/SOX, risk-off ×1.5 on VIX/FII, inflation ×1.3 on rate/oil — so oil + CPI
  reinforce). Relationships stay fixed; influence adapts. The report shows the
  base × activation × regime = effective decomposition for the amplified factors. Renders as
  the "Sector scores — per-sector factor models" table. Weights are PRIOR until fitted.

## Phase 2 (done)

* `semis_regime.py` — conditional SOX/KOSPI → Indian IT cause engine (supersedes the naive rule).
* `subsectors.py` — deep sub-sector factor models for Auto / Banking / Pharma / FMCG.

Both are wired into `enrich.py` (steps 8–9) and rendered in the report. Fundamental factors
that need data (CASA, NIM, deposit growth, provisions) are named with their exposure sign so
the desk knows the driver even before a data feed is attached — the natural next step is
wiring those fundamentals from filings/earnings into `subsectors.build`.
