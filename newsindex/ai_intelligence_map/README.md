# AI Industry Intelligence Map

A self-run, **zero-cost** tracker for the *entire* AI value chain — semiconductors →
frontier models → cloud → networking → power → applications. It merges **curated
intelligence** (moats, risks, dependencies, KPIs, bull/bear, investment "phase") with
**live market data** (yfinance) and **tagged news** (public RSS), and renders an
interactive dashboard you can sort, filter, and search — plus CSV and Markdown you can
diff over time.

Think of it as a *factor screen for the AI ecosystem* rather than one-stock-at-a-time
analysis. Same spirit as `market_scan.py` next door: no paid APIs, runs entirely on your
machine, dated outputs land in `./reports/`.

```
ai_intelligence_map/
  registry.py        <- the file you EDIT (all the curated intelligence)
  ai_map.py          <- the engine (live data + news + renders outputs)
  requirements.txt
  README.md          <- this file
  reports/           <- dated ai_map_<date>.{html,csv,md} land here
```

## What it tracks

34 companies across 6 layers (add your own in `registry.py`):

| Layer | Examples |
|---|---|
| L1 Semiconductors & silicon | NVIDIA, AMD, Broadcom, Marvell, TSMC, Micron, SK Hynix, Qualcomm, Intel, Arm, ASML |
| L2 Frontier / model labs | OpenAI, Anthropic, Google DeepMind, Meta/Llama, xAI, Mistral, Cohere |
| L3 Cloud & compute | Microsoft, Amazon, Oracle, CoreWeave |
| L4 Networking & interconnect | Arista (+ Broadcom, Marvell) |
| L5 Power, cooling & energy | Vertiv, Eaton, Schneider, GE Vernova |
| L6 Applications & enterprise SW | Apple, Adobe, Salesforce, ServiceNow, Intuit, SAP, Autodesk |

For each: **role, moat, biggest risk, dependencies, key customers, KPIs to watch,
one-line bull/bear, investment phase, and authoritative source URLs.** Public names get
live price / market cap / P/E / revenue growth / margins / 52-week move overlaid each run;
private labs (OpenAI, Anthropic, xAI, Mistral, Cohere) carry a curated last-known
valuation with the source to verify against.

## One-time setup

```bash
cd "ai_intelligence_map"
pip3 install -r requirements.txt
```

## Run it

```bash
python3 ai_map.py                 # full run: live market data + tagged news
python3 ai_map.py --no-live       # curated map + news only (skip yfinance)
python3 ai_map.py --no-news       # curated map + live data only (skip RSS)
python3 ai_map.py --fetch-sources # also pull a readable snippet from each source URL
```

Outputs (dated) land in `./reports/`:
- `ai_map_<date>.html` — **interactive dashboard** (open this): search box, filter by
  layer or phase, sort by market cap / name / phase, per-company news + source links.
- `ai_map_<date>.csv` — one row per company (curated fields + live metrics) for your own
  screens / pivots.
- `ai_map_<date>.md` — a Markdown snapshot; commit these to `git` and you get a running
  diff of how the map changes week to week.

If the machine is offline or Yahoo is unreachable, live numbers show `—` and the curated
map still renders — nothing crashes.

## How the "consolidate from sources" part works

- **Live numbers** come from Yahoo Finance via `yfinance` (no key needed).
- **News** is pulled from AI-ecosystem RSS feeds (Tom's Hardware, The Verge, Ars Technica,
  CNBC Tech, SemiAnalysis, VentureBeat AI, Reuters Tech — edit `NEWS_FEEDS` in `ai_map.py`)
  and each headline is keyword-tagged to the companies it mentions.
- **Source pages** — each company has authoritative URLs in `registry.py` (investor-relations
  pages, the Tom's Hardware custom-ASIC tracker, etc.). `--fetch-sources` fetches a readable
  snippet from each using `trafilatura`, with an **optional Playwright fallback** for
  bot-blocked / JavaScript pages (same pattern `market_scan.py` uses). Install Playwright
  only if you want that fallback.

## Make it a habit

Run it weekly and keep the dated files, or schedule it (macOS `launchd`/`cron`) the same
way you run `market_scan.py`. Because every run is dated, the `.md`/`.csv` history becomes
a longitudinal record of how leadership rotates across the phases.

## Extending

Everything lives in `registry.py`:
- **Add a company** → append a dict to `COMPANIES` (copy an existing one as a template).
- **Add a source** → drop a URL into that company's `sources` list, or into `SECTOR_SOURCES`.
- **Add news coverage** → add keywords under `EXTRA_KEYWORDS` in `ai_map.py`.
- **Re-bucket** → change a company's `layer` or `phase`.

Sanity-check the registry any time with `python3 registry.py`.

---
*A research map, not investment advice. Private-company valuations are curated estimates —
verify against the linked sources, which change with every funding round.*

Curated source anchors: the [Tom's Hardware custom-AI-ASIC tracker](https://www.tomshardware.com/tech-industry/semiconductors/custom-ai-asics-examined-from-broadcom-to-mtia)
and [Investopedia's AI-trade coverage](https://www.investopedia.com/).
