# DATA_SOURCES — source taxonomy & reliability weighting

The Collector Agent ingests from five source families. Each item carries a **source tier**
that flows through the whole pipeline and weights an event's reliability and an override's
evidentiary strength. News is *one* family, not the system.

## 1. Source families

### Global Macro
Central banks and multilaterals — the highest-authority sources for policy and macro regime.

`Federal Reserve · ECB · BoE · BoJ · RBI · PBOC · IMF · World Bank · BIS`

### Market News
Wires and financial press — fastest, broadest, but noisiest; must be normalized and
source-weighted.

`Reuters · Bloomberg · CNBC · Financial Times · Wall Street Journal · Nikkei · Economic Times · Moneycontrol`

### Corporate
Primary disclosure — the ground truth for single-name events.

`Earnings · SEC/NSE/BSE filings · Investor presentations · Conference calls · Guidance · M&A announcements`

### Market Data
The tape — the reality every relationship is validated against.

`Oil · Gold · Copper · Natural Gas · USD Index · Treasury Yields · VIX · FX · Global Indices · Option Markets`

### Alternative Data
Leading/orthogonal signals — early evidence a textbook chain is or isn't transmitting.

`Shipping · Satellite · Weather · Social Media · Supply Chain · AI Trends`

## 2. Source-tier reliability weighting

Every ingested item is stamped a tier. Tiers weight (a) how much an event's reliability is
trusted before calibration and (b) how much an item counts as override evidence in the
Validation Agent.

| Tier | Definition | Examples | Weight role |
|---|---|---|---|
| **1** | Primary / official | Central-bank statements, exchange filings, company IR, the tape itself | Highest; can confirm/override on its own |
| **2** | Tier-1 financial wires/press | Reuters, Bloomberg, FT, WSJ, Nikkei | High; multiple tier-2 corroborate a tier-1-equivalent |
| **3** | Reputable market press | CNBC, Economic Times, Moneycontrol | Medium; needs corroboration for a lone claim |
| **4** | Curated alt-data providers | Shipping/satellite/supply-chain vendors | Signal, not proof; strong when it *leads* the tape |
| **5** | Social / unverified | Social media, forums | Lowest; a lead to verify, never a standalone basis |

This mirrors the `source_weight()` logic already present in `market_scan.py`, generalized to
five families.

## 3. Ingestion characteristics

| Family | Cadence | Structure | Primary use |
|---|---|---|---|
| Global Macro | event-driven (scheduled releases) | semi-structured text + data | regime, policy edges |
| Market News | continuous | unstructured text | event detection, normalization |
| Corporate | event-driven | filings (structured) + text | company events, exposure |
| Market Data | streaming/polled | structured numeric | validation, driver-dominance, tape |
| Alternative Data | varied (daily–weekly) | mixed | leading evidence, override reasons |

## 4. Provenance requirements

Every `RawItem` the Collector emits **must** carry:

```
source          : the outlet/provider
source_tier     : 1–5 (above)
url / doc_ref   : locatable original
ts_event        : when the event occurred (not when ingested)
ts_ingest       : when we saw it
lang            : source language
ts_estimated    : true if ts_event was inferred, not stated
```

`ts_event` is what the no-lookahead invariant is enforced against. If it can only be estimated,
the item is tagged `ts_estimated` and treated conservatively (never allowed to justify a
same-minute reaction it may not have preceded).

## 5. Reachability note

Some of the most valuable sources — paywalled wires, JS-heavy pages, exchange filing portals,
forums — are exactly the ones standard fetchers can't read. The Collector Agent is specified
to use robust web-data tooling (rendered fetches, authenticated feeds, filing APIs) so
coverage is not silently biased toward the easy-to-scrape end of the source distribution. A
gap in coverage is logged, never hidden.

## 6. Mapping to the existing project

| Family | Existing `newsindex/` touchpoint |
|---|---|
| Market News | `fetch_news()`, RSS ingestion, `fetch_article.py`, Playwright fallback |
| Corporate | `fetch_earnings()`, `enrich_earnings()`, `build_catalysts()` |
| Market Data | `fetch_quotes()`, cross-asset symbols (oil/gold/copper/USDINR/yields/VIX) |
| Global Macro | policy-headline detectors (`rbi_dovish`, `india_cpi_hot`, `us_cpi_cool`, `pmi_strong`) |
| Alternative Data | *new* — the least-developed family today; the biggest expansion opportunity |
| Source weighting | `source_weight()` → generalized to the 5-tier table above |

The clean-room design keeps these as adapters: the Collector Agent's tools wrap the existing
fetchers where they exist, and add the alt-data family that the current pipeline lacks.
