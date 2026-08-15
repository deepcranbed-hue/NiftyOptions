# Market Scan — self-run weekly/daily market scanner

A zero-cost, **standalone** scanner that pulls **news, Nifty/stock prices, an earnings
calendar, and macro/geopolitics cues** and writes a dated Markdown report + CSV to
`./reports/`. No paid APIs, no per-run cost. Runs entirely on your machine.

This folder is self-contained:
```
newsindex/
  market_scan.py       <- the scanner
  requirements.txt     <- dependencies
  README.md            <- this file
  reports/             <- generated reports land here
```

## What it pulls (India-focused)
1. **Verdict banner** — one-line risk-on/off read from the numbers (rule-based, free).
2. **Indices** — Nifty, Sensex, Bank Nifty, Nifty IT, India VIX.
3. **Macro / global cues** — Brent, WTI, Gold, USD/INR, **Dollar Index (DXY)**, US 10Y, Dow, Nasdaq.
4. **Domestic flows — FII / DII** — daily cash-market provisional net from NSE. FII-out /
   DII-in tells you if domestic (SIP) money is absorbing foreign selling. (Monthly SIP/AMFI
   data is noted with a link — it's a monthly release, not daily.)
5. **Indian IT / AI-fear watch** — Nifty IT move, **Kospi** + **SOX** (global AI/chip bellwethers),
   USDINR export tailwind/headwind, the IT pack (TCS, Infosys, Wipro, HCL, Tech M, LTIM,
   Persistent, Coforge). Includes an **AI stance for Indian IT** read: the AI signal is
   two-sided, so it classifies headlines as *opportunity* (AI deal wins, GenAI contracts —
   e.g. "TCS wins AI-architecture deal") vs *threat* (job cuts, automation, pricing pressure)
   and reports the net lean. A single coefficient can't sign this — the news framing decides.
5b. **Cross-asset → sector impact map** — auto-translates the day's moves into Indian sector
   winners/losers with reasoning: oil↑ → ONGC good / paints, OMCs, aviation, FMCG bad;
   weak rupee → IT & pharma exporters up; Kospi↓ → AI-fear spillover to IT; rising US10Y/DXY
   → FII outflow risk & pressure on financials/realty/autos; inflation → rate-sensitive
   financials. Includes live proxy prices (ONGC vs BPCL vs Asian Paints) to show the divergence.
   Now also includes:
   - **Oil price-LEVEL regimes** (non-linear): <$80 benign, $80–90 watch, $90–100 stress,
     >$100 crisis — each with escalating macro damage and intra-sector winners/losers, plus a
     Strait-of-Hormuz tail-risk warning when Middle-East headlines coincide with high oil.
   - **Metals — haven vs growth**: Gold/Silver (safe-haven + inflation hedge → gold financiers,
     jewellers, miners; silver also solar/EV) vs Copper ('Dr Copper' growth barometer →
     Hindalco/Vedanta/Polycab). Flags the gold↑ + copper↓ risk-off divergence.
   - **Geopolitics weighting**: Middle-East/Iran (oil-dominant) is weighted heavily; Russia-Ukraine
     is downweighted as currently muted for India; defence names (HAL, BEL, BDL, Mazagon) flagged.
5c. **Thematic & structural plays** — detects thematic stories in the news and attaches the
   *reasoning*: 💍 Jewellery & Gold (why they round-tripped — duty cut → inventory loss then
   demand surge; gold financiers gain), 🔋 EV vs ICE (high oil helps EVs, hurts ICE), 🖥️
   Semiconductors / India chip push (PLI fabs — Kaynes, CG Power, Micron), 🇨🇳 China chip /
   US-China tech war (China+1 boosts Dixon/Kaynes; rare-earth curbs a risk). Shows a live
   theme-basket (Titan, Kalyan, Muthoot, Tata Motors, Ola Electric, Kaynes, CG Power, Dixon).
5d. **Policy / Government push** — flags PLI, budget, duties, capex, defence/railway/semiconductor
   headlines that create sector-specific optimism.

5e. **Macro plumbing** (surfaced in the thematic section + the impact map):
   - **RBI FX toolkit** — forward book (~$110bn), FX reserves, gold buying → how the rupee is
     defended and how it feeds **G-sec yields** via forward premia / liquidity.
   - **Corporate dollar bonds / ECBs** — foreign capital in (rupee-supportive) vs external-debt
     & hedging demand pushing up forward premia; effect on the local bond market.
   - **Retail sales & consumer sentiment (India + US)** — US data → Fed path → US yields/dollar
     → FII & G-sec pressure here; India festive/rural/FMCG demand → RBI call + consumer earnings.
   - **G-sec / bond linkage** — rising US10Y & DXY narrow the India–US spread, firm Indian yields,
     and risk FPI debt outflows.

You can edit the `THEMES` list near the top of `market_scan.py` to add your own stories
(keywords + a "why" paragraph) — the report auto-surfaces any theme with a matching headline.
6. **News headlines** — RSS from Economic Times, Moneycontrol, Business Standard, Reuters,
   CNBC; auto-tagged for oil/Iran/war/Fed/AI/FII/rupee etc.
7. **Earnings** — NSE corporate-events calendar, with **Nifty 50 heavyweights flagged first**
   and a fundamentals snapshot (mkt cap, P/E, trailing Rev/Profit YoY) per results-reporter.
   Strong-growth Nifty 50 names are called out as "optimism drivers".

## One-time setup
```bash
cd "/Users/deepak/antigravity/NiftyOptions/newsindex"
pip3 install -r requirements.txt
```

## Run it
```bash
python3 market_scan.py
```
Output lands in `./reports/market_scan_YYYY-MM-DD_HHMM.md` and a matching `headlines_*.csv`.

## Schedule it (free, on your Mac — no Claude cost)
Run every weekday at 8:00 AM using cron:
```bash
crontab -e
```
Add this line (edit the path to this folder):
```
0 8 * * 1-5 cd "/Users/deepak/antigravity/NiftyOptions/newsindex" && /usr/bin/python3 market_scan.py >> cron.log 2>&1
```
Save and exit. That's it — a fresh report every weekday morning, no API bills.

(To confirm cron ran: check `cron.log` and the `reports/` folder.)

## Optional: Playwright for JS-heavy / blocked sites
Lightweight RSS + yfinance covers almost everything. Only if a specific site
(e.g. a paywalled or fully JS-rendered page) refuses plain requests, flip
`USE_PLAYWRIGHT = True` at the top of `market_scan.py` and install:
```bash
pip3 install playwright
playwright install chromium
```
Then call `fetch_with_playwright(url)` for that source. Note: Playwright respects
paywalls/logins — it renders the page, it doesn't bypass subscriptions.

## Two summaries: Market vs Company
The top of the report is split for two kinds of reader:
- **Market summary (index drivers)** — the verdict banner, the LLM desk note, and the
  🔗 causal engine (oil, VIX, FII/DII, rates, currency, global). For a Nifty-futures view.
- **🏢 Company summary (stock drivers)** — company-specific stories classified 🟢 Positive /
  🔴 Negative / ⚪ Neutral, each tagged with **sector** and **index impact** (High / Medium /
  Low / Negligible, read from the stock's Nifty weight). So "High company / Negligible Nifty"
  instantly tells a stock-picker it's an opportunity that won't move the index. Company
  detection uses `COMPANY_GAZETTEER`; edit it to add names you follow.
- **📅 Upcoming catalysts** — results due **tomorrow** and in the **next ~3 days**, with
  ⭐ marking Nifty 50 heavyweights.

_Limitation: sentiment is read from the headline only, so a nuanced line like "HCL: despite
strong deal wins, risk-reward unfavourable" can misread as positive. Full-article extraction
(the Playwright/trafilatura step) would fix that — see the roadmap note below._

## Full-article extraction (`fetch_article.py`)
By default the scanner only reads **RSS headlines + snippets** — it never opens the article,
which is why headline-only sentiment can misread nuance. `fetch_article.py` fixes that.

**Backend order (benchmarked via `compare_backends.py`):**
- **trafilatura first** (plain HTTP, no browser) — clean article body, fast, cool, and crucially
  **not browser-fingerprinted**, so it sails through Akamai anti-bot on open pages (Moneycontrol)
  where headless browsers get blocked.
- **crawl4ai fallback** (browser, stealth) — only when trafilatura comes up thin. Beat Playwright
  on bot-blocked ET pages (more figures, ~2× faster). Note: it grabs the whole page incl. nav,
  so `first_paragraph()` skips link/nav lines to keep the lead clean.
- **Playwright** — off by default (`ALLOW_PLAYWRIGHT = False`); crawl4ai replaced it. Flip on as a
  last resort. Paywalled pages (Reuters/ET Prime) still return truncated text — logins not bypassed.

Compare them yourself anytime: `python3 compare_backends.py` (or pass a URL).
- **SQLite cache** (`articles.db`) — never re-fetches the same URL; polite delays between fetches.
- Extracts the **body, a lead snippet, and key figures** (₹ crore, %, $bn, bps).

Enable it in `market_scan.py`: set `USE_FULLTEXT = True` (top of file). It enriches the top
`FULLTEXT_MAX` company-summary stories with their key figures + a lead sentence — so instead of
just "HCL Tech — [headline]", you get the capex number and a real snippet. Install:
```bash
pip install trafilatura                      # required for extraction
pip install playwright && playwright install chromium   # optional JS fallback
```
Test one URL: `python3 fetch_article.py "https://www.moneycontrol.com/news/....html"`

_It's OFF by default because fetching adds network time; turn it on when you want deeper company
notes. Once on, the LLM desk note also benefits (richer input)._

## 🎯 "What actually mattered today" (synthesis)
The dashboard now includes the sell-side summary table — a single view of which drivers *controlled
the tape* today and why, pulling together contribution, dominance and the override logic:

| Driver | Expected | Actual influence | Why |
|---|---|---|---|
| FII | 🔴 Bearish | 🔥 High (32%) | FII selling but DII buying absorbed it (net liquidity OK) |
| Kospi | 🟢 Bullish | Medium (13%) | capped — weak/indirect transmission |
| Oil | 🔴 Bearish | Low (5%) | level-amplified inflation, but small move |

- **Theme-based read** (themes are more stable than single variables): instead of "Dominant driver:
  Kospi", it says **"Dominant theme: Flows (−0.37) · Supporting: FII, Oil, CPI · Counter: VIX, Kospi,
  SOX"** — grouping related drivers and showing which pushed with vs against the tape.
- **Interaction effects:** two drivers pushing the same way reinforce each other — "🔗 Oil↑ +
  India-CPI-hot → inflation/RBI-hawkish amplified", "🔗 US yields↑ + dollar↑ → EM/FII headwind
  amplified" — added as an explicit, transparent term in the expected move.

## 📟 Executive dashboard (trading-desk layer 1)
The report now opens with a **20-second, scannable dashboard** in institutional style, before any
detail:
- **Header:** Market regime · Trading bias (Bullish/Neutral/Bearish + score) · Conviction % · AI regime.
- **Fair-value gap:** observed vs driver-implied, with "market above/below/at model".
- **⚖️ Market conflict matrix** — the standout: each layer (Price action / Macro / News / Flow / Quant)
  with its direction + conviction, then a **consensus** ("2 bull / 2 bear → Split"), so you see *where*
  the layers disagree, not just a single label.
- **🔝 Top drivers** (biggest contributors to Nifty), **🎯 sector bias heat-map** (Banks 🟢 / IT 🔴
  AI-Substitution / Defence 🟢 …), **📌 top catalysts**, and **💡 highest-conviction themes**.

Everything else (the causal chains, scorecard, company analysis, historical reliability, tables)
follows as **supporting evidence**. Biases are directional context (Bullish/Bearish/Neutral) — not
buy/sell calls with targets — and the report says so.

## 📊 At-a-glance dashboard (three timelines, one view)
The report opens with a dashboard that separates the signals that used to be conflated under one
label — because "what the market did" and "what the drivers imply" are different questions:

| Layer | What it is |
|---|---|
| **Current market** — observed | the tape (fact): Nifty/Bank Nifty/VIX actual moves → Risk-on/off |
| **Macro bias** — driver model | fair-value drift the drivers imply + conviction |
| **News bias** — company news | source-weighted sentiment score |
| **Quant bias** — option-chain/stat | from `signals.json` (or "not fed") |
| **➡️ Final trading bias** | weighted combination |

Each layer has its **own label** (Market Regime is observed; Macro/News/Quant Bias are forward),
so the market can be *down* while the model leans *up* without contradiction.

**Baseline is explicit.** The observed index move is labelled **"vs prev close"** (the standard
today's-change convention) and also shows the **intraday move from today's open** — so a day that's
down vs yesterday but up from the open reads correctly instead of looking contradictory.

**Live index weights (no drift).** Nifty 50 weights and constituents load at runtime from the
`strategy_framework` master (`nifty-50-stock-list.csv`, updated weekly) via `constituents.py`, so
index-impact labels, ranks, dashboard weighting and heavyweight-earnings flags never go stale on a
rebalance. The built-in dict is a fallback only (used if `newsindex/` runs without the framework).

**Fresh + validated prices (three layers).** (1) Quotes come from yfinance `fast_info` (updates
through the live session) not daily bars; (2) every price is **range-checked against today's
high–low** — outside the range = 🛑 SUSPECT; (3) the indices are **cross-checked against NSE's own
`allIndices` API** (the authoritative exchange source) — if yfinance disagrees with NSE by >0.3%,
it's flagged with both values shown ("yfinance 24,018 vs NSE 24,185, 0.69% off"). Any suspect print
raises a top-level data-quality banner and **fails `--selftest`** so it can't be published. The NSE
cross-check catches wrong-but-in-range values the range check alone can't; it fails soft if NSE is
unreachable, in which case the range check + as-of flag still apply.

**The Gap.** "Expected move" was renamed **fair-value drift** (the same-day move the drivers imply,
from prev close — not a next-move forecast). The dashboard then shows the **gap**: e.g. "Nifty
−0.08% observed vs +0.17% driver-implied → market ~0.25% **below** macro fair value (lagging the
drivers)." That's a genuine relative-value read the old report couldn't give.

## Transparency & data-quality (show the numbers behind the labels)
- **Current tape vs forward model** are now separated: the verdict banner is labelled "current
  tape" (today's actual index move); the causal section is the "forward-driver model" (what the
  drivers *imply*). If they disagree, a note explains it — no more "Risk-off" over "Mildly bullish".
- **Contribution derivation** — the expected move shows a `move × coefficient = contribution` table,
  so no number looks arbitrary.
- **Conviction breakdown** — the agreement % is shown with the counts that produce it
  ("3 bullish drivers +0.65 vs 4 bearish −0.12").
- **Scoreboard** shows `score × weight = contribution` per row and sums to the combined.
- **Outlier caps (#8)** — a single stale/extreme print (e.g. Kospi +9%) is clamped to a sanity band
  for the expected-move and flagged, so it can't dominate.
- **News dedup** — identical stories across ET Markets/ET Stocks are collapsed, so "5 AI headlines"
  means 5 unique stories.
- **Dynamic wording** — e.g. gold falling no longer reads as a "safe-haven rally"; the text reflects
  today's actual direction.
- **Company importance** is quantitative (weight %, ~rank, impact) and the company summary is ordered
  by index importance × news quality. **Historical reliability is colour-coded** (🟢≥70 / 🟩≥60 /
  🟠≥50 / 🔴<50).

## 🧮 Signal scoreboard + conviction
- **Conviction** (in the causal engine) = how *aligned* today's drivers are (signal agreement),
  and which drivers dissent — e.g. "Signal agreement: 94% (High) — pulling the other way: Kospi,
  DXY." It is **not** a probability of the outcome (a heuristic model can't calibrate that).
- **Signal scoreboard** combines a **Macro** score (from the causal engine) and a **News** score
  (source-weighted company sentiment) into a **Combined** lean (−1 bearish … +1 bullish). It also
  reads any external quant signals from **`signals.json`** — drop in your NiftyOptions option-chain,
  momentum, VRP, IC/hit-ratio, RND, VWAP scores and they fold into the combined view. The file
  ships as an inert template; add a `score` to a key to activate it.
- **News quality**: sources are weighted (`SOURCE_WEIGHTS`) — reputable wires high, opinion/tip
  pieces ("5 stocks to buy…") down-weighted. The **News score = Importance × Sentiment ×
  Index-weight**, so a negative HUL story (Medium index weight, reputable source) counts far more
  than a positive smallcap tip.

## ⏳ Signals by time horizon
Separates today's drivers so an intraday catalyst isn't conflated with a structural story:
Next-30-min (option chain/VWAP/OI — from your quant feed), Intraday (VIX/flows/news — with a
same-day lean), 1–3 days (oil/results/RBI), 1–4 weeks (inflation/Fed/DXY), 3–12 months (AI/semis,
policy themes). Directional lean is modeled **only** for intraday same-day drivers; longer
horizons list what to watch — the tool does not fake a 1-month prediction.

## 📈 Standout movers (weight-adjusted)
Under the causal engine, the report ranks the biggest gainers/losers across all fetched
stocks and tags each with its **approximate Nifty weight** (`NIFTY50_WEIGHTS`). The point:
a +3.5% Kalyan Jewellers (not in Nifty) or −4% HCL Tech (~1.6%) barely moves the index, while
a −1% HDFC Bank (~13%) does. The desk-note LLM is fed these movers and told to name a couple
and flag small weights. Edit `NIFTY50_WEIGHTS` as the index rebalances.

> Data honesty note: RBI's forward-book / reserves / gold figures are **not** fetched live —
> the report no longer prints a specific number (previously a hardcoded "~$110bn"). For the
> real figures, see the RBI Weekly Statistical Supplement (Fridays).

## 🔗 Causal / sentiment engine (expected % move — intuition, not a forecast)
Right under the verdict, the report now shows a **cause → effect** block that:
- scores **sentiment** (Bearish → Bullish) from oil, VIX, FII flows and geopolitics;
- estimates an **expected % move** for Nifty & Bank Nifty (with a range);
- prints the **causal chains** firing, e.g. `Oil ↑ → inflation ↑ → RBI hawkish → yields ↑
  → bank treasury pressure → Bank Nifty ↓`, each tagged with its Nifty contribution;
- shows the **contribution breakdown** (oil −0.12, FII −0.31, geo −0.10 … → net −0.46%).

The magnitude comes from the `SENSITIVITY` dict near the top of `market_scan.py` — rough,
hand-set "% index move per unit of driver" coefficients. **Edit them** to tune your intuition.
On a live back-test the engine estimated Nifty −0.46% vs an actual −0.53% — close, but treat it
as *directional intuition only*.

> ⚠️ This is NOT a prediction, trade signal, or investment advice. The coefficients are not
> yet calibrated on history. For real probabilities, back-test event→next-day-return over past
> data (fits the NiftyOptions backtest framework) and replace the hand-set numbers.

## Prompt distillation + eval harness (`desk_note_examples.py`, `eval_notes.py`)
The local model's *narrative* is improved by **prompt distillation** — no fine-tuning, no GPU.
A teacher model wrote gold-standard desk notes in `desk_note_examples.py`; `market_scan.py`
injects two as **few-shot examples**, so the small model imitates the house style (India-first,
a named mover with its index weight, real driver numbers, conditional-on-RBI, no advice).

`eval_notes.py` scores your local models against an **8-point rubric** (incl. a prompt-leakage
check — small models sometimes echo the input scaffolding) so you can pick the best one objectively:
```bash
python3 eval_notes.py                              # llama3.2:3b vs qwen2.5:7b
python3 eval_notes.py --models qwen2.5:7b qwen2.5:14b
python3 eval_notes.py --no-fewshot                 # measure the few-shot lift
```
It runs held-out fixtures through each model and checks: leads with India, names a mover, gives
weight context, cites a driver number, **no unverified RBI figure**, right length, no investment
advice. Higher total = better. Use it to decide 3B-vs-7B and whether few-shot actually helps.
Add your own gold notes / rubric checks to steer the style.

## Pre-flight self-test (publish gate)
Before publishing, run the built-in check:
```bash
python3 market_scan.py --selftest   # exits non-zero if the report is unsafe to publish
```
It scans the generated report for the failure modes we've actually hit: **NaN** values (bad feed),
**echoed prompt scaffolding** in the desk note, **empty critical fields** (no Nifty value / verdict
/ expected-move), and flags **outlier drivers**. Without `--selftest` it still prints a PASS/FAIL
line; with the flag it returns a non-zero exit code so a cron/publish pipeline can gate on it:
```bash
python3 market_scan.py --selftest && ./publish.sh   # only publishes on PASS
```

## Sector coverage
Beyond banks/IT/oil/metals, the engine now covers **Telecom, Cement, Power, Auto (split into
ICE-2W/PV vs EV/CV), Pharma, Defence, Realty, Consumer Durables, Chemicals and Capital Goods** —
via an expanded company gazetteer (news → sector tagging), a proxy universe (Bharti, UltraTech,
NTPC, Maruti/Hero/Bajaj, Sun Pharma, HAL, DLF, SRF, L&T…), and new cause→effect linkages: oil↑ →
ICE autos down / EV up, weak rupee → pharma exporters, geopolitics → defence, oil → chemicals &
cement cost.

**India-macro news-flags.** Since RBI repo / PMI / GST / monsoon aren't free live series, the engine
detects them in the **news** and routes them to sectors: repo-cut/dovish → banks, NBFCs, autos,
realty; strong PMI → capital goods/industrials; good monsoon → rural FMCG/2W/tractors; strong GST
→ consumption. (Edit the detectors to tune.)

## 🕸️ Transmission map — driver → channel → sector (causal network)
The engine no longer models `driver → sector`. It models **`driver → transmission channel →
sector`**, where one driver fans out through several economic channels — encoded in an extensible
`TRANSMISSION` data structure (add tariffs / monsoon / fiscal by defining channels, not sector rules):

- **Oil** → 4 channels: *Energy prices* (upstream 🟢 / OMCs, aviation 🔴), *Inflation→RBI→rates*
  (banks 🟢 short-term NIM, but realty/NBFCs 🔴 — with the horizon note "banks +ve days–weeks,
  −ve later if growth slows"), *Currency/CAD* (IT & pharma exporters 🟢), *Input costs* (paints,
  chemicals, tyres, logistics 🔴). So banks aren't hit by oil *directly* — only via inflation→rates.
- **AI/semis** → 3 channels: *Infrastructure capex* (EMS, power NTPC/PowerGrid, telecom Bharti 🟢),
  *Productivity* (banks, insurance, pharma, manufacturing 🟢 — long-term ROE, not via chips),
  *Substitution* (IT services 🔴 — marked **⚡ ACTIVE** only under the Substitution regime, dormant
  under Complement).

This is how macro strategists reason: effects differ by **channel, horizon and regime**, and the
same driver produces winners *and* losers across the economy.

## Transmission mechanisms & driver dominance (macro rigor)
The causal chains now show the **transmission node**, not a direct driver→sector jump:
- **FII** → *large-caps with high foreign ownership* (banks, IT, Reliance), not "banks only".
- **US10Y** → *rate differential → dollar → FII flow → India G-sec/yields* → banks (indirect, via
  currency & capital-flow channel — India G-secs don't follow US10Y one-for-one).
- **SOX** → *AI-infra capex → enterprise budget allocation → consulting/deal-wins* → Indian IT
  (Substitution vs Complement).
- **Geopolitics (Iran)** → *1° oil (+shipping/insurance) → 2° inflation → 3° RBI/rates → banks*;
  defence is one branch, not the whole story.
- **Copper** → India's power/EV/capital-goods/electricals chain (Polycab, KEI, ABB, Siemens, CG Power).

**Oil is non-linear in its LEVEL, not just its % move.** A +5% move at $92 bites much more than
+5% at $70 (CAD/inflation stress, near the psychological barriers). The oil contribution is now
scaled by a **level multiplier** (×0.6 below $75 → ×1.0 normal → ×1.4 near $90 → ×2.2 in a shock),
shown transparently in the dominance table ("× coef × lvl 1.4, Brent $92"), and the oil-regime
block flags proximity to the round-number **$80 / $90 / $100 barriers**.

**Weak-transmission dominance cap.** Kospi/SOX are *indirect* transmissions (global risk → Asia tech
→ India), so their coefficients were cut and their **dominance is capped at 15%** — a Korean index
can't explain a third of the Nifty move. **Net institutional flow** (FII+DII) is now shown alongside
foreign positioning, so a −₹740cr FII / +₹2,928cr DII day reads as *net-liquidity positive, foreign
positioning negative* — two different signals. The **conflict matrix shows the raw scores** (not just
Bull/Bear). The **AI regime requires corroboration** — one headline/price signal → "Possible
Substitution", multiple (IBM + Accenture + Indian IT) → confirmed. The **driver-override stack is
now stock-specific** (no oil in a bank's stack; rates added for rate-sensitives).

**Driver-dominance table** — the forward model now shows each driver's contribution AND its
**dominance %** (share of total force), with the **dominant driver** called out ("FII 56% of the
move"), so a trader sees what's actually moving the market.

**Driver-override analysis (overridden ≠ broken).** A stock moving against a rule is no longer
called a "failed rule". The engine decomposes its move into competing drivers — Oil/rule, Market
risk, FII flow, Company news — scores each, and names the **dominant** one. So on a day with a small
oil rise + risk-on tape, it reads: *"IndiGo +2.2% · oil rule expected ↓ → **overridden by Market
risk-on** (oil influence weak); ONGC −0.1% → **overridden by FII flow**; Hero +0.5% → **overridden
by Company news** (Ather investment)."* The economic link still holds — a stronger driver just
controlled price discovery that session. Matches how a trading desk reasons.

**Oil-shock classification.** Oil moves are typed — **Supply** (Iran/Hormuz/OPEC → bearish for India,
no growth offset), **Demand** (global growth → supportive for cyclicals), **Policy** (OPEC cut), or
**Inventory** (temporary) — because the same +% transmits very differently by shock type.

**Economic rationale vs statistical support** — the scorecard reliability column now separates
**Econ ★** (how economically sound the relationship is, independent of today) from the historical
hit-rate. High-★ + low-% = "sound rule, noisy session"; low-★ + low-% = "questionable rule".

## 🔁 Cause → effect scorecard — regime-aware & weighted
The scorecard now reflects that markets aren't fixed rules:
- **AI regime detector (data-driven, decided once up front)** — classifies whether AI news is
  **Complement** (cloud demand, deal wins → SOX/Kospi↑ ⇒ Indian IT ↑) or **Substitution** (IBM/peer
  warnings, consulting slowdown, budgets to infrastructure → SOX/Kospi↑ ⇒ Indian IT **↓**). The
  decision uses **both news AND the price tape** — the strongest signal is chips UP but Indian IT
  DOWN, which *is* substitution playing out live (fires even with no keywords). The regime is
  detected once and **propagated consistently** to the causal chains, the sector map, and the
  scorecard — so the narrative and the scoring never contradict each other. Under Substitution the
  SOX→IT and Kospi→IT rules **flip their expected sign**, so IT falling on a chip-rally day is scored
  **✔ confirmed under the AI-Substitution regime**, not a failed rule.
- **Weighted agreement** — "held" is now weighted by **index weight**: TCS/Infosys (~5% each) count
  far more than a 0%-weight name, so a rule isn't called broken because a tiny stock diverged.
- **Driver strength** (Strong/Medium/Weak) and **explicit expected direction** (e.g. "US10Y −0.5% →
  Banks ↑") are shown, so signs aren't left to the reader to infer.
- **Honest reliability** — bands recalibrated (50% = coin-flip, so 57% is **Weak** not Moderate) with
  a **95% confidence band** (e.g. "57% ±5%, n=408"), so weak/insignificant edges are obvious.
- **Bucking the trend is merged per stock** — one line lists all rules a stock contradicted (no more
  TCS/Infosys printed twice), with the reason headline if one exists.

## 🔁 Cause → effect scorecard (rule vs tape) — every sector
The report now verifies **every** cross-asset rule against the actual tape, not just oil. For each
active linkage (oil→producers/users, weak-rupee→IT, gold→jewellers, copper→metals, SOX/Kospi→IT,
yields→banks, FII→financials) it checks whether the proxy stocks actually moved as predicted and
shows a per-proxy ✓/✗ with a "held" tally. A low held count means broad-market direction or a
stock-specific shock overrode the rule that day — e.g. "Kospi +9% → Indian IT: 0/2, TCS/Infosys
fell anyway (IBM warning dominated)." Edit `RELATIONSHIPS` to add linkages/proxies.

The oil channel also gets a dedicated reconciliation line, and the desk-note LLM is fed the
**actual** proxy moves and told never to assert a textbook link ("oil helps ONGC") when the stock
actually fell.

**Regime-dependent AI read.** Global chips up is NOT automatically good for Indian IT. The IT/AI
watch now interprets the *combination*: Kospi/SOX up **and** IT up = risk-on together; Kospi/SOX
up **but IT down** = 🔴 **AI-fear** — the market pricing AI as a threat to the services model
(clients funding AI infrastructure over contract renewals; the conversion to deals may not show
until results — the IBM warning). The oil chain is also spelled out fully: producers up, refiners
hurt (crude = input cost), downstream hurt via transport + crude-derivative raw materials (paints,
tyres, plastics).

**Bucking the trend.** Any proxy that moves against its rule is listed by name with a reason — if a
headline mentions the stock it's cited (e.g. "BPCL rises on strong marketing margins despite crude
uptick"), otherwise a one-line "bucking the trend (broad-market/stock-specific; no headline)".

## Inflation is two-sided (India hot vs US cool)
Inflation is no longer a single bearish tag. **Hot India CPI** (above forecast) → RBI hawkish →
bearish for rate-sensitive financials. **Cooling US CPI** → Fed-easing hopes → risk-on tailwind
(semis, EM equities, FII flows) that partly offsets India's headwinds. Both feed the causal
engine (with opposite signs) and the sector map, so a day like "India CPI hot + US CPI cooling"
reads as *mixed / low-conviction* instead of falsely stacking as risk-off.

## 📚 Event memory / historical analogues (`build_events.py`)
Turns the causal *rules* into empirical *evidence*. It backfills price history and computes,
for each kind of event, what the index did the **next day** — count, average, and how often it
fell:
```bash
python3 build_events.py            # ~4 years -> events.db
python3 build_events.py --years 6
```
Then every report gains a **📚 Historical analogues** section: for today's driver setup it cites,
e.g. *"Brent up >3% (N=34): next-day Bank Nifty avg −0.6%, fell 62% of the time."* So the reader
gets history, not just intuition.

Honest limits (stated in the report too): conditions are **price-based only** — free data has no
historical FII or geopolitics tags, so "oil rose because of Iran" can't be isolated. Low N = weak
evidence. Past ≠ future — it's context, not a forecast. Re-run monthly to refresh.

**Linkage confidence (self-evaluating rules).** `build_events.py` also computes a **historical
hit-rate for each cross-asset rule** — how often the proxy stocks actually moved as the rule
predicts, over years of data. The 🔁 cause→effect scorecard then shows a "Hist. reliability"
column: e.g. "oil→OMC 83% (High)" vs "SOX→Indian IT 42% (Low)". So the report can say a linkage is
historically reliable *or* flag that today's inference rests on a weak rule. Company importance is
also shown as a ★ rating (from Nifty weight) instead of just High/Medium/Low.

## Calibrating the engine from history (`calibrate.py`)
The `SENSITIVITY` numbers start as hand-set guesses. To ground them in real data:
```bash
python3 calibrate.py            # ~3 years
python3 calibrate.py --years 5  # more history
```
It downloads Nifty / Bank Nifty / Nifty IT and a **comprehensive** driver set —
Brent, India VIX, USD/INR, **Kospi**, **Dollar Index (DXY)**, **Philadelphia Semiconductor
Index (SOX)**, **US 10Y & 5Y yields (rate hike/cut fear)**, Nasdaq — with **time-zone lag
alignment** (US markets close after India, so India reacts to the *previous* US session).

It runs two regressions:
1. **Contemporaneous** (today's move vs today's drivers) — this calibrates the live
   "expected move" engine; it writes `suggested_sensitivity.py` ready to paste in.
2. **Predictive** (tomorrow vs today) — an honest reality check. Expect a *tiny* R²:
   markets are near-efficient, so next-day moves are mostly unpredictable. That low number
   is a real finding, not a bug — it's why the tool is framed as intuition, not a crystal ball.

FII/DII flows aren't in the regression (no free historical series via yfinance), so those
coefficients stay hand-set unless you feed your own FII CSV.

## Verdict banner + optional local LLM
Every report now opens with a **rule-based verdict** — e.g.
`Verdict: 🔴 Risk-off. Nifty -0.41%, banks -1.03%, VIX +1.66%, oil +2.06%; 3 geopolitics headline(s), 4 AI headline(s).`
This is computed from the numbers (no LLM, instant, free, never hallucinates).

If you also want a short **narrative paragraph** ("what's driving it"), point it at a
**local LLM via Ollama** — free, private, offline, no per-run cost:
```bash
# 1. install Ollama from https://ollama.com
ollama pull llama3.1:8b      # ~4.7GB; or qwen2.5:7b, mistral, etc.
```
Then set `USE_LOCAL_LLM = True` near the top of `market_scan.py`. If Ollama isn't
running, the script just skips the paragraph — the rule-based verdict still shows.

**Rule of thumb:** use rules for the numeric read (direction, VIX, oil), use the local
LLM only to summarise headlines into prose. Don't use an LLM for the numbers.

## Customize
Open `market_scan.py` and edit the CONFIG block near the top:
- `INDICES`, `MACRO`, `STOCKS` — add/remove tickers (yfinance symbols).
- `RSS_FEEDS` — add your preferred news sources.
- `MACRO_KEYWORDS` — tune what counts as a "market-moving" headline.

## Notes
- Everything is wrapped in try/except: one dead source never kills the run.
- NSE sometimes blocks server requests; if the earnings table is empty, the report
  tells you to check nseindia.com / bseindia.com directly.
- Not investment advice.
