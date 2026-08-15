# Sector Intelligence Framework

> **Central principle: every sector deserves its own causal ontology.**
> Reuse the *reasoning framework*, not the *factors*. The four-layer pipeline is
> constant; the causal variables and transmission mechanisms change by sector.

> **Temporal principle: evidence is not permanent.** Every fact has a validity
> period and a decay profile. The platform revises expectations not only when new
> evidence arrives, but also as old evidence loses relevance. This is what keeps
> the system *dynamic* instead of a collection of stale scores and static
> assumptions.

> **Validation principle: evidence before optimization.** Every new signal must
> first demonstrate causal, incremental information through a standalone validation
> battery *before* it is permitted to enter any portfolio simulation or trading
> strategy. Research order is fixed: **Hypothesis → Signal Construction → Signal
> Validation → Economic Interpretation → Portfolio Simulation → Deployment.**
> Portfolio simulation is step 5, not step 3 — a backtest before the diagnostic is
> the cart before the horse, and it is how every false start in this project
> happened (the macro regression, the over-read R², the inflated Sharpe).
> **The order is strict and one-directional: no backtest may *precede or motivate*
> validation.** An attractive backtest anchors human judgment — people defend a
> seductive Sharpe — so a signal that arrives already wearing a 3.3 Sharpe must still
> enter validation as if it had none. Proven empirically here: the IT signal showed a
> 3.3 Sharpe / +50% *four times* and validation blocked it anyway. Validation gates the
> strategy; the strategy never gates validation.

This document is the platform architecture. It generalizes the (deliberately
abandoned) Nifty IT attribution engine into a framework that extends to banking,
energy, metals, autos, pharma, and any other sector — without forcing them into
one explanatory mold.

---

## 0. Why this exists — earned, not assumed

Every layer and rule below exists because a simpler version failed in a specific,
diagnosed way. This is the audit trail of the design:

| We tried | It failed because | The lesson it forced |
|---|---|---|
| Daily macro→IT regression | Nasdaq is a **co-effect** of the Fed/AI environment, not a cause of IT — a fork, not a link. R²≈0.04, betas wrong-signed. | Don't regress across a mis-specified causal graph. Causation ≠ correlation of siblings. |
| "AI Threat Score = 73" | No ground truth; false precision. | Store **explicit categorical states + evidence**, not invented scores. |
| Decompose implied `r` into "AI = 2.1%" | Implied required return is **one aggregate** (AI + recession + macro + liquidity + FX + geopolitics). | Report **Market-Implied Required Return** as a single number; narrate the *why*, never the split. |
| Point fair value ("= 44,000") | Ignores that markets trade the **distribution**, and that price moves on *changes in expectations*, not levels. | Fair value is a **scenario distribution**; the driver is `Expectation − Price`. |
| One regression for all sectors | Energy/Banks have short transmission chains; IT has six steps. | **Each sector needs its own ontology**, and chain length picks the method. |
| Rotation-signal backtest showed +50% "alpha" | In-sample, single ~11-mo regime, unaudited for look-ahead & cross-market calendar leakage; a 52% hit-rate can't honestly yield +50% at 40% exposure. | A surprising backtest **raises** skepticism. Establish IC / decay / regime **causally** (`signal_validation.py`, `merge_asof` not `shift`) BEFORE any PnL; **freeze the strategy** until the signal passes. Diagnostic and strategy stay separate scripts. |

The platform is the accumulated correction of these mistakes.

---

## 1. The four-layer pipeline (constant across sectors)

```
  Layer 1  EVIDENCE            observable facts
             │                 oil, Fed, guidance, broker reports, deal wins, FII, OI, macro prints
             ▼
  Layer 2  MARKET EXPECTATIONS partially observable
             │                 enterprise spending, AI adoption, margins, revenue, risk appetite
             ▼                 (consensus estimates, analyst revisions, price-implied growth/return)
  Layer 3  INTRINSIC VALUE     model output (a DISTRIBUTION, not a point)
             │                 revenue CAGR → EPS → required return → fair-value scenarios
             ▼
  Layer 4  MARKET PRICE        observable
                               discount / premium vs intrinsic value, flows, positioning, momentum
```

**Evidence updates Expectations; Expectations drive Intrinsic Value; the gap
between Intrinsic Value and Price — and its closing — is the return.** Macro
variables never explain price directly; they enter at Layer 1 as evidence,
update Layer 2, which propagates to Layer 3. You propagate belief updates *along*
the chain — you never regress *across* it. That is why this is stable where the
regression was not.

### Observability gradient (where the uncertainty lives)

- **L1 Evidence** — fully observable.
- **L2 Market Expectations** — *partially* observable. This is why we say
  "expectations," not "beliefs": consensus, revisions, and price-implied
  growth/return are real data that **anchor** this layer. (Belief is unobservable
  — "whose belief?"; expectation is the market's aggregate, partly measurable.)
- **L3 Intrinsic Value** — model output; subjective assumptions; a distribution.
- **L4 Market Price** — fully observable.

The framework is **anchored at both ends** (evidence and price); modeling
uncertainty concentrates in the middle (L2→L3). The **reverse mode** — backing
growth/required-return out of price — lets the observable L4 cross-check the
subjective L3.

---

## 2. Two engines, mapped onto the layers

| Engine | Layers | Explains | Horizon | Character |
|---|---|---|---|---|
| **Value engine** | L1 → L3 | *value* — why the fair-value thesis changed | medium-term (quarters–years) | stable, economic, the core product |
| **Market engine** | L4 | *price* — why it moved today/this week | daily–weekly | descriptive, flows/positioning/company-news; **not** a macro model |

The Transmission Engine is an **assembler**, not a regression: it narrates the daily
move from FII/DII flows, OI positioning, momentum, and company news it already
has. It does not use macro proxies. Daily sector price is dominated by flows and
idiosyncratic events; don't force a model where signal doesn't exist.

**"Is the sector cheap?"** is not a valuation question — it is `Price vs Intrinsic
Value` (L4 vs L3). Same engine, read as a gap.

---

### 2.1 Two enrichment modules — kept *inside* the four layers, not new layers

Adding top-level layers you cannot populate with reliable data is the failure
mode to avoid. Two mechanisms enrich the pipeline as **modules**:

- **Interpretation module (inside L1→L2).** Evidence is not expectation — it is
  *interpreted*. A Jefferies note is neither a fact nor an expectation; it is a
  credible *interpretation* that participants adopt. Different participants
  (long-only, macro funds, retail, shorts, quants) interpret the same evidence
  differently, and market expectation is their aggregate. So the NewsAgent's job
  is **interpretation extraction**, not sentiment or scores:
  `evidence → interpretations (each signed + evidence-logged) → expectation revision`.

- **Capital-allocation module (alongside L4).** Sector indices are dominated by
  **rotation**, not only valuation. Money leaves a fully-valued sector and enters
  a cheap laggard; that flow moves price *independent of intrinsic value*. This is
  the mechanism behind every "oil down but ONGC down / Nasdaq up but Infosys flat"
  — capital allocation overriding fundamental value. It sits at L4 as one reason
  Price deviates from Intrinsic Value (with flows, positioning, momentum), and it
  is the module that answers "where is money rotating, and is momentum left?"

## 3. Honest guardrails (anti-false-precision — non-negotiable)

1. **Required return is one aggregate.** Report *Market-Implied Required Return*;
   never decompose into named risk premia. The NewsAgent narrates the likely why.
2. **Expectations are tracked as directional deltas with an evidence audit trail.**
   A "confidence 72%" level is a soft anchor with no ground truth; the defensible
   product is "**▲9% this week, because ①…②…③…**" with each belief-update logged
   to its evidence. Integrity is in the trail, not the number.
3. **Intrinsic value is a distribution:** `E[value] = Σ P(scenarioᵢ)·value(scenarioᵢ)`.
   News usually moves the **weights**, not the scenario values — which is how price
   rises with earnings flat.
4. **The price-vs-value gap is measured against the distribution** (e.g. "price
   below the 25th percentile of fair value"), never against a point estimate.
5. **Explanation engine, not prediction engine.** Judged by *coherence* — does it
   correctly flag "this rally is belief-revision + flows, not earnings"? — not by
   forecast accuracy. Daily accuracy for long-chain sectors is unachievable; don't
   pretend otherwise.
6. **Validation before monetization — the five mandatory gates.** No signal enters a
   portfolio simulation until it clears, in order, a standalone validation battery
   (`signal_validation.py`). A surprising backtest *raises* skepticism; the gates,
   not the Sharpe, decide. Judged on **shape, relative value, and dependence-corrected
   out-of-sample significance — never on the IC's magnitude vs a prior.**

   | Gate | Question | PASS condition |
   |---|---|---|
   | 1 Causality | only pre-open info? | `merge_asof` (never `shift`), ADR-only, predictor is a *return innovation* not an RRG *level* |
   | 2 Decay + significance | genuine overnight transfer? | **economic decay** — L1 ≫ all later lags AND later lags \|IC\|<0.10 (not machine-precision monotonicity) — **and** block-bootstrap p<0.05 (dependence-corrected) |
   | 3 Incremental information | better than the simple alternatives? | **economic threshold** — IC edge over baseline ≥0.02 AND walk-forward Δ ≥0.02 (a statistical Δ>0 is NOT an architectural pass; a +0.007 Δ is noise) |
   | 4 Out-of-sample | survives walk-forward? | expanding-window OOS hit > chance |
   | 5 Information Capture | real AND reachable at your horizon? | gap vs intraday split — signal in the untradeable overnight gap ⇒ efficient-but-uncapturable; **human sign-off** |

   A non-decaying IC (Gate 2) is the signature of an autocorrelated *level* or
   calendar leakage, not transmission; that single check would have caught the
   inflated backtest. Sharpe/Sortino/drawdown are *evaluation* metrics for step 5,
   never *validation* metrics — they stay frozen until all four auto-gates pass.

---

## 4. Sector classification: transmission-chain length picks the method

The IT failure gave the platform its **diagnostic**: how many steps between the
primary driver and price?

```
Energy   Oil ─► crack spreads / refining ─► OMC / upstream / airlines        (1–2 steps)
Banks    Yield curve / liquidity ─► NIM / credit growth / NPA ─► EPS          (2–3 steps)
Pharma   USFDA / pricing / pipeline ─► approvals ─► revenue                    (2–3 steps)
Autos    Demand / financing / commodity costs ─► volumes / margins            (2–3 steps)
IT       Oil ─► inflation ─► Fed ─► budgets ─► guidance ─► expectations ─► P/E (5–6 steps)
```

| Sector | Primary ontology | Chain | Recommended method |
|---|---|---|---|
| Energy | commodity-driven | short | direct attribution / regression works |
| Banks | balance-sheet / rate-driven | short–med | direct attribution + credit cycle model |
| Pharma | pipeline / regulatory | medium | event/approval model + regulatory flags |
| Autos | demand / financing | medium | demand-cycle model |
| **IT** | **expectation- & valuation-driven** | **long** | **belief-network + thesis/valuation engine; regression is hopeless** |

**Rule:** short-chain commodity/rate sectors → macro attribution is legitimate;
long-chain expectation/valuation sectors → the Evidence→Expectations→Value
belief-network. Chain length is the test for which model to apply.

### 4.1 REFINEMENT (post IT + Bank) — chain length and overnight proxy are ORTHOGONAL

The IT/Bank results forced a correction: "chain length → signal strength" was *one* axis
doing two jobs. They are **two orthogonal dimensions**, and they select **different engines**:

- **Fundamental-chain length** → the **Fundamental/Thesis Engine** (short chain = tractable
  medium-term valuation model).
- **Availability of a clean overnight-traded proxy** → the **Transmission Engine** (a driver
  that trades before the India open and leads the sector).

|  | overnight proxy: **good** | overnight proxy: **poor** |
|---|---|---|
| **short chain** | both engines viable — Energy? (crude proxy + oil→earnings) | Fundamental engine (no proxy) |
| **long chain** | Transmission engine only — but often gap-locked (**IT**: software peers) | hardest — neither (**Banks**: only generic risk → route to rate-cycle Fundamental) |

IT proved a *long* chain can still have a *strong* overnight signal (great proxy), and Bank
proved a shorter chain can have a *weak* one (no proxy — its "signal" was generic global risk,
XLF < Nasdaq). So overnight strength ∝ **proxy existence**, not chain length. And per §6.8,
even where a proxy exists the transmission has been **gap-locked (efficient)** — so the
Transmission Engine's product is largely a cross-market **efficiency map**, tradeable output rare.

---

## 5. The common interface each sector implements

The framework is an interface; each sector plugs in its own graph:

1. **Evidence sources** — the observable facts that matter for this sector.
2. **Expectation variables** — what that evidence updates (and how each maps to a
   growth/margin/risk input). Categorical states, evidence-logged.
3. **Intrinsic-value model** — the sector's valuation (growth bridge → EPS →
   required return → fair-value distribution; reverse mode for what's priced in).
4. **Price / positioning** — flows, OI, momentum, and the gap vs intrinsic value.

The **NewsAgent is the shared transducer at L1→L2 for every sector**: it reads
evidence and estimates *which expectations moved and in which direction*, with an
evidence trail — not sentiment, not a score.

---

## 6. Reference implementation — Indian IT (the sector that taught us the framework)

| Layer | IT implementation | Status |
|---|---|---|
| L1 Evidence | Upstox market data, `macro.factor_series` (FRED: US10Y, Nasdaq, crude), FII/DII, OI | built |
| L1→L2 | NewsAgent (macro-transmission detector) → expectation states | **to wire** |
| L2/L3 | `sector_scorecard.py` (valuation, quality, momentum, ownership) | built |
| L3 | `investment_thesis.py` (causal growth bridge → EPS → forward + reverse; risk-premium multiple) | built |
| L4 | Transmission Engine (flows/OI/news assembler + global software-services peer factor) | to build (mostly assembles existing panels) |
| — | `factor_regression.py` (daily macro→IT) | **retired** — kept as the documented lesson |

IT's causal graph (the long chain that broke regression):
```
enterprise IT spending · AI adoption · AI automation · deal pipeline · pricing · margins
        └────────────► revenue CAGR ► EPS ► required return ► fair value ► (gap vs price)
```

### 6.1 IT has two horizons, two engines, two *models* (not two parameter sets)

The single sentence that captures the split:

> **The Indian IT Transmission Engine uses a global software-services peer factor for
> short-term price transmission, while the Value Engine uses enterprise-spending
> fundamentals for medium-term valuation.**

Why they must be *different models*, not one model at two speeds:

| Horizon | Engine | Primary driver | Transmission chain | Character |
|---|---|---|---|---|
| Daily–weekly | **Market** (L4) | global software-services peers (ACN, CTSH, CRM, Infosys-ADR) rotating up/down | **short & strong** — Indian IT ≈ the same asset class, priced overnight | positioning/rotation |
| Quarters–years | **Value** (L1→L3) | enterprise IT / AI spending → revenue → EPS → fair value | **long & weak daily, strong cumulatively** | fundamental |

This is why `factor_regression.py` failed and why it stays retired: it aimed a
macro proxy (Nasdaq/crude/US10Y) at the *daily* horizon, where the real short
chain is **peer transmission**, not macro. Nasdaq is now ~25% software-services /
~75% AI-hardware — mostly noise for Indian IT. The right daily latent factor is a
**Global Software-Services basket** (indicative weights ACN ~40%, CTSH ~25%,
CRM ~20%, Infosys-ADR ~15%, or the first PC of the basket). Two distinct rotations
to separate: *global peer* rotation (software vs AI-hardware — the 31-Jul move) and
*domestic sector* rotation (IT vs Banks/Energy — the L4 capital-allocation module).

**Reference event — the 31-Jul-2026 rotation (mechanism + catalyst confirmed).**
The cleanest live illustration of the global-peer rotation, and of why it is a
*rotation*, not a *peer cause*:

1. **Catalyst (L1 evidence).** Late-July hyperscaler earnings confirmed AI capex
   keeps climbing (~$400B combined MSFT+AMZN, ~$700B+ industry 2026).
2. **Destination re-rates.** Memory / AI-infra names — which had crashed ~30% days
   earlier on AI-capex-*slowdown* fear — roared back double-digits on that
   confirmation (Micron, SK Hynix, Sandisk; Sandisk +24% intraday). A −30%→+24%
   round-trip inside a week, hinged on the capex print.
3. **Funding source sold.** Capital rotated OUT of software services to fund the
   AI-infra leg: US software peers (ACN, CTSH, CRM, Infosys ADR) fell overnight;
   Indian IT followed intraday (TCS −4.35%, Persistent −4.56%, Infosys −4.15%).

The modeling reading: **Indian IT and its US software peers fell together as
co-victims of one rotation — ACN did not "cause" TCS.** The peer basket is a valid
*leading observable* (same asset class, US prints overnight first); the *mechanism*
is the software⇄AI-infra rotation, catalysed by hyperscaler capex guidance and
confirmable in FII flow. This is the fork insight one level up: as Nasdaq was a
co-effect of the Fed/AI environment, ACN and Indian IT are co-effects of this
rotation — fine for a predictor that prints first, provided it is modelled as
rotation, not as one-name causation.

Two refinements the same event surfaces:
- **It is software *services*, not all software, that transmits.** Oracle Financial
  Services Software (a product/platform name) *gained* while the services names
  sold off — so the peer basket should be pure services exposure, and
  product/platform names are a separate bucket inside the index.
- **Positioning amplifies the peer beta.** Indian IT had run +11% (Infosys) / +8%
  (TCS) over the prior seven sessions; the rotation therefore landed as a sharp
  −4% profit-booking. The same peer move on an un-extended sector transmits less —
  peer beta scales with how over-bought the local sector already is, linking the
  peer factor to the relative-momentum input.

### 6.2 To validate BEFORE hard-coding the peer factor (v1.0 stays frozen)

The peer factor is entered here as a **hypothesis with strong single-session
support (31-Jul-2026), not a proven principle.** Before it becomes the Market
Engine's primary daily signal, run the horse-race — candidate overnight predictor
vs **next-day Nifty IT return**, across multiple regimes (2022 rate shock, 2023
recovery, 2024–25 AI capex, 2026 rotation), scored three ways:

| Candidate overnight predictor | Correlation | Hit-rate (sign) | Information Coefficient |
|---|---|---|---|
| Nasdaq Composite | ? | ? | ? |
| S&P 500 IT sector | ? | ? | ? |
| Accenture (ACN) | ? | ? | ? |
| Cognizant (CTSH) | ? | ? | ? |
| Infosys ADR (INFY) | ? | ? | ? |
| **Software-Peer basket (PCA)** | ? | ? | ? |

Decision rule: **if the peer basket consistently dominates Nasdaq/S&P-IT on
hit-rate and IC across regimes, the daily transmission mechanism is confirmed** and
the Transmission Engine hard-codes it. If it only wins in the 2026 rotation regime, keep
it regime-conditional. This is a *measurement*, not a redesign — v1.0's architecture
is unchanged; only the IT Transmission Engine's L4 input is being calibrated.

**Why the mixed indices lose — mechanically, not incidentally.** A
software⇄AI-infra rotation *splits Nasdaq and XLK internally*: their
semiconductor/hardware weight rises while their software weight falls, so on a
rotation day the index nets flat-to-green even as Indian IT drops (31-Jul: index
buoyed by memory, Indian IT −4%). A *pure software-services* basket carries only
the transmitting side, so it reads the rotation cleanly. The index proxies are not
merely weaker — they are *structurally conflicted by the very rotation the study is
trying to detect*. The sharper test therefore adds an **AI-infra proxy**
(Micron / SK Hynix / SMH) and checks whether the **software-minus-AI-infra spread**
predicts next-day Indian IT better than software peers alone; it should also
**split hit-rate by up-day vs down-day**, since global risk-off transmits through
peers while the domestic bull narrative (the Jefferies-led re-rating) is generated
locally and peers cannot see it.

Interim evidence (single-name, single-regime, **not** the gate): a rolling OLS of
local INFY on its peers returned R²≈0.39 (vs ≈0.04 for the retired macro model),
ACN the strongest univariate signal (corr 0.54, *above* Infosys' own ADR at 0.43),
USDINR ≈0. Supportive of the peer mechanism; still owes the index-level,
proxy-inclusive, multi-regime horse-race above.

### 6.3 The sharper hypothesis — daily driver is *rotation within tech*, not software direction

31-Jul was not "software ↓"; it was "software ↓ **because** AI-infra ↑." Those are
different days: `software −3% / semis +8%` (active rotation) transmits to Indian IT
differently from `software −3% / semis −3%` (broad risk-off). So the Transmission Engine's
primary daily signal is not a peer *level* but a **rotation factor** — the relative
allocation *within* global technology. Candidates, in ascending sophistication:

| # | Candidate | What it is |
|---|---|---|
| 1 | `NASDAQ` / `XLK` | index baselines; **internally split** by the rotation → expected to lose |
| 2 | `SW_SERVICES` | **Global Software Services** basket — the *business model* (enterprise IT spend, consulting, outsourcing, implementation): ACN, CTSH, INFY_ADR (+ EPAM, WIT, IBM) |
| 3 | `SW_PRODUCTS` | product SaaS (CRM, ADBE, MSFT) — different customer dynamics, kept **separate** |
| 4 | `SW_ALL` | services + products — contrast: does adding products *dilute* the signal? |
| 5 | `AI_INFRA` | AI-infrastructure basket (NVDA, MU, SMH), PCA/weights |
| 6 | **`GSS_ROTATION`** | **z(SW_SERVICES) − z(AI_INFRA)** — the *Global Software Services Rotation Factor*, the hypothesis under test |

**Name it for the business model, not the sector label.** The factor is *Global
Software Services* — enterprise IT spend / consulting / outsourcing / implementation
(ACN, CTSH, INFY_ADR, EPAM…). Salesforce, Adobe and Microsoft are *software* but
their customer dynamics differ, so they sit in `SW_PRODUCTS`. This is not asserted —
it is **tested**: if `SW_SERVICES` beats `SW_ALL`, the business-model distinction is
confirmed empirically (the OFSS-bucked-the-selloff observation, generalized).

Baskets are defined by **economic exposure, not ticker** (today's memory leader is
MU; next year Broadcom) — edit the group's constituent list, the PCA re-fits. The
market-neutral form regresses each basket on a market factor (SPY) and takes
residuals, so the spread is *pure rotation* with broad beta removed.

Note (avoid double-counting): since `spread = services − ai_infra`, once SW_SERVICES
is in a model, adding the SPREAD contributes essentially the same variance as adding
AI_INFRA alone. The spread's value is as a *single signed axis* (and for the quadrant
logic), not as independent incremental R² beyond AI_INFRA.

**Evaluation battery (simple correlation is retired).** Each candidate is scored on:
IC + hit-rate (predictive power/direction), **rolling IC mean±std** (stability),
**regime IC** per year (2018 / covid / AI-capex / rotation), **IC decay lag 1→5**
(the measured *signal half-life* — feeds the Transmission Engine decay math, §9),
**incremental R² over SW_SERVICES** (added variance beyond peers), **walk-forward
incremental hit-rate** (added *direction* beyond peers — out-of-sample, un-gameable:
the strong form of "does the rotation dimension add value?"), **Granger F-test**
(does it *lead* beyond its own autocorrelation?), **quadrant asymmetry** (soft↑infra↑
· soft↑infra↓ · soft↓infra↑ · soft↓infra↓ — the rotation quadrant should carry the
signal), and **up-/down-day split** (peers should call selloffs better than rallies,
since the domestic bull narrative is generated locally). Implemented in
`it_rotation_signal.py`.

**Pre-registered expectations (recorded 2026-08-01, before the run — not to be edited
post hoc):** NASDAQ *weak*; XLK *> Nasdaq*; SW_SERVICES *strong*; GSS_ROTATION
*strongest on selloffs*; AI_INFRA *weak alone, useful only combined with services*.
The last is the sharp one: if AI-infra is weak standalone but the walk-forward hit
jumps when it's *combined* with services, that is the rotation mechanism confirmed —
and the pre-registration stops us rationalizing whatever we see after the fact.

**Upgraded gate:** hard-code `GSS_ROTATION` only if it beats NASDAQ, adds
out-of-sample hit-rate over SW_SERVICES, shows the `soft↓/infra↑` quadrant worst,
holds IC across regimes, *and* leads on Granger. If confirmed, the finding is stronger
than "a better peer proxy" — **the daily transmission mechanism for Indian IT is
relative capital rotation within global technology**, and the primary external signal
of the Transmission Engine is the **Global Software Services Rotation Factor**.

**EMPIRICAL RESULT — first clean validation (2025-2026 data, `gate_validation.py`).**
The battery ran causally at last, and it *refuted the rotation elaboration* while
confirming a simpler signal — exactly the discipline the gates exist to enforce:

- **The methodology vindicated itself.** IC now **decays sharply** (SW_SERVICES ≈0.48
  at lag-1, below 0.06 by lag-2). The earlier non-decaying 0.305→0.310 *was* the
  autocorrelated-RRG-level + calendar leakage we diagnosed; `merge_asof` + return
  innovations removed it. Gate 2 passes for a real reason.
- **A real software-services overnight signal exists:** SW_SERVICES IC ≈0.48, hit
  ≈69%, block-boot p≈0, stable across 2025 (0.46) and 2026 (0.51), beats NASDAQ
  (0.14) and XLK (0.11).
- **The ROTATION hypothesis is NOT supported.** AI_INFRA IC ≈ **−0.05 (insignificant)**;
  `GSS_ROTATION` (0.30) is *weaker* than services alone and adds **Δ≈0** out-of-sample.
  Pre-registration held: AI-infra weak alone ✓ — but the "useful when combined" half
  is **falsified**. Verdict: **BLOCKED on Gate 3.** The simpler peer *level* wins; the
  elegant "software − AI-infra" spread is decoration. §6.1/§6.4's "rotation factor"
  framing is **demoted to a hypothesis the data currently rejects.**
- **Peer decomposition — external transmission SURVIVED.** `PURE_PEERS`
  (ACN/CTSH/EPAM/IBM, no index members) IC = **0.444** — it did *not* collapse toward
  NASDAQ's 0.14. Curiously it is *higher* than `CONSTITUENT_ADR` (INFY_ADR/WIT_ADR,
  0.392). All software baskets cluster ~0.40-0.48 while indices sit ~0.11-0.14 and
  AI-infra ~0 — consistent with a genuine **global software-services common factor**
  leading Indian IT overnight, not merely own-ADR arbitrage. All four auto-gates PASS.
- **Everything above was a single-regime (2025-2026) read. The 9-year sample
  (2018-2026, 2,114 sessions) OVERTURNS it — final verdict: BLOCKED.** This is the
  regime test doing exactly its job: the spectacular AI-boom result was largely an
  artifact of that window.
  - **CORRECTION (do not read the pooled IC as "collapse").** The pooled 9-yr numbers
    (SW 0.30 vs NASDAQ 0.255) *average two regimes and describe neither*. Year-by-year,
    `SW_SERVICES − NASDAQ` IC is **≈0/negative 2018-2023** (+0.01, −0.03, −0.03, −0.02,
    −0.03, −0.02) then **breaks positive and widens: 2024 +0.11, 2025 +0.15, 2026 +0.35.**
    The peer-over-index edge is **regime-conditional and structurally coherent** — it
    appears exactly when the post-genAI tech split appears — not a mirage and not a
    constant. Mechanism: driven **more by NASDAQ *decoupling*** (its IC fell 0.43→0.16,
    2022→2026, as it filled with AI-hardware Indian IT doesn't track) than by software
    strengthening (held 0.40→0.51). This is §6.2's "Nasdaq is internally split" thesis,
    confirmed longitudinally — but only in the AI-era regime.
  - **The tradeable intraday edge mostly vanishes and is NOT stable.** Full-sample
    `PURE_PEERS` intraday IC = **0.07** (was 0.215 in 2025-26); most IC is still the
    untradeable gap (0.475). Per-year intraday IC is **negative in 2018 (−0.04) and 2021
    (−0.01)**, marginal (~0.06-0.09) mid-cycle, and only material in 2025 (0.11) / 2026
    (0.21). Regime-*conditional*, not structural. `tradeable_intraday = False`.
  - **Live hypothesis, not a trade:** the intraday edge has *risen* recently
    (2024:0.09 → 2025:0.11 → 2026:0.21), plausibly a structural intensification of the
    Indian-IT ↔ global-software coupling in the AI era. Worth *monitoring* (does 2027 hold
    ~0.15-0.20?), but a rising recent IC is not a deployment basis — betting it persists is
    the recency-overfit trap the framework exists to prevent.
  - **Meta-result — the framework worked.** A single-regime run screamed "3.3 Sharpe,
    deploy"; causal validation + a 9-year regime test revealed a real-but-regime-conditional,
    gap-dominated (largely-uncapturable-at-open) signal and **blocked deployment before any
    capital** — while still correctly recording the relationship as statistically real. Validation-before-
    monetization earned its place: it turned a seductive artifact into a documented,
    correct "no." No strategy backtest is authorised.

**The "IT peer hypothesis" was not one hypothesis but three — decompose, don't binary-judge:**

| # | Hypothesis | Status |
|---|---|---|
| **H1** | *Structural coupling* — Indian IT **always** follows US software-services (all regimes) | **REJECTED** — no edge over Nasdaq 2018-2023; the relationship is not a constant |
| **H2** | *AI-era coupling* — the link tightened, and the peer basket separated from Nasdaq, **during** the post-2023 AI transition | **SUPPORTED, UNDER OBSERVATION** — clean structural break: SW−Nasdaq edge +0.11/+0.15/+0.35 across 2024-2026, with a coherent mechanism (Nasdaq → AI-hardware index). But it is *one* regime observed 3 years; revisit if 2027 sustains it. Promising, not yet confirmed. |
| **H3** | *Rotation interaction* — software↓ *while* AI-infra↑ → Indian IT underperforms next session | **NOT TESTED** — the *linear* spread failed (IC −0.057, inverted), but the *conditional quadrant* claim was never evaluated; a linear factor can be flat while an interaction is real |

Two questions, deliberately separated (the earlier error was fusing them):

- **Research question — do US software-services peers lead Indian IT?** **Yes** — the
  relationship is statistically real (IC decays sharply, monotone quintiles Q0 −0.72% →
  Q4 +0.56%, block-boot p≈0), and in the AI-era regime the peer basket is a *materially*
  better lead than Nasdaq.
- **Trading question — can you capture it after the Indian open?** **Almost surely not.**
  The IC is dominated by the **overnight gap** (0.475) with only 0.07 intraday: the open
  already prices the US move. That is a **market-efficiency** finding, not a broken signal.

**Gate 5 renamed `Information Capture`** (was "Tradeability"): a real signal can be
unreachable at a given execution horizon. For an at-open trader → blocked. For pre-open
auction / cross-listed instruments → possibly capturable — same signal, different horizon.
**Gate 2 recalibrated** to the *economic* decay test (L1 ≫ all later lags AND later lags
insignificant) rather than machine-precision monotonicity — the sharp 0.29→0.02 drop is
the point; a 0.02↔0.05 wiggle in the noise floor is not a failure.

**Descriptive ≠ predictive — the RRG is positioning, the horse-race is the signal.**
A Relative-Rotation-Graph (RS-Ratio / RS-Momentum vs a benchmark) shows *where* each
basket sits in the rotation cycle; it does **not** measure whether the factor
*forecasts* next-day Nifty IT. Both belong — in §6.4 the RRG is the positioning /
"Domestic Rotation" context, the horse-race (this section) is the overnight *signal*
that feeds Opening Bias — but a rotation can be vivid in RRG space and still carry no
tradable overnight edge (already priced by the India open). Only IC / hit-rate /
incremental R² / Granger settle that; the horse-race stays the measurement of record.

*Descriptive confirmation logged (31-Jul-2026, US universe vs NASDAQ):*
software-services (ACN, CTSH, CRM, INFY_ADR) all **WEAKENING** (RS>100, momentum<100 —
the rally rolling over); AI-infra (NVDA, MU, SMH) all **IMPROVING** (RS<100,
momentum>100 — the crushed names turning up; MU 60d +28.6% vs 20d −15.6%). The
software→infra rotation is visible as two adjacent opposite quadrants, mirroring
domestic Nifty IT = WEAKENING; NASDAQ sits on the 100-line *between* the halves —
concretely why it is a muted predictor. Strong support for the mechanism; **not yet
the predictive gate.**

### 6.4 The Transmission Engine, target shape (external signals only — no macro)

```
External Signals
  Global Software Services factor · AI-Infra factor · GSS-Rotation factor (primary) ·
  Domestic Rotation (RRG) · Momentum · Positioning
        └──► Opening Bias ──► Intraday Probability
```

Deliberately **no oil, no Fed, no USD-INR** here — those are long-chain macro
variables and belong to the **Value Engine** as L1 evidence, not the daily engine.
Putting them in the Transmission Engine is exactly the mis-specification `factor_regression.py`
died of; the chain-length principle (§4) enforces the separation.

### 6.5 The peer signal's real role — a HYPOTHESIS to validate, not a module to wire in

The 9-year result *reassigns* rather than discards the software-services signal: not a
daily alpha (efficiently priced into the open — Gate 5), but a candidate **confidence
modifier / regime filter for the medium-term Value Engine** — updating scenario *weights*,
not fair value (§3 guardrail 3), as a *module inside* L1→L3, not a new engine (§2.1).
**This is a new, untested hypothesis.** The project's core lesson is that a compelling
story is the anchoring trap in a new costume; it goes through the same gates.

Why it's plausible (and why it is a *different variable*):
- **Horizon-specific efficiency.** The overnight gap (~0.5-1%) that makes the signal
  untradeable *daily* is *noise* at a 1-3 month horizon (±10% moves). A signal predicting
  *medium-term* Nifty IT wouldn't fight the open-gap efficiency — it could be real **and**
  capturable, unlike the overnight.
- **Different encoding.** The daily *return innovation* decays to ~0 by lag 2 (shown). The
  role wants the *slow regime/trend state* (weeks of relative strength). Persistence — the
  property that disqualified the **RRG level** as a daily signal — is a *feature* for a
  regime state. The demoted RRG may be the right form here.

Gates it must clear before it becomes a module:
1. **Medium-horizon predictive test** — does the software-regime state predict forward
   20-/60-day Nifty IT at all? (Escapes the gap trap; cheapest first test.)
2. **Incremental over what we already have** — value only if it predicts *beyond* valuation
   + momentum + the fundamental view; else "software strong" just double-counts "enterprise
   spending strong." Walk-forward, OOS.
3. **Role ranking by testability** — *confidence modifier* (months of thesis-vs-realized →
   testable) ≫ *regime detector* (only ONE regime transition in the data, ~2023 → n=1,
   essentially unvalidatable now; promising narrative, not established).

Measurement hurdle: "improves the fundamental outlook" needs a *historical* thesis-state
series (the engine emits only a current estimate). Tractable proxy first: valuation
percentile + trailing (reported) earnings trend as "the thesis," then test whether the
software-regime state adds forward-return accuracy on top.

### 6.6 Fundamentals-primary medium-term engine — right direction, most dangerous backtest

Reframe (correct, and the destination): fundamentals are the **base** forward-return model;
the software regime enters only as an **incremental confidence modifier** (§6.5), Bayesian
on the scenario *weights*, not fair value. This is the four layers made concrete at 1-12
months. But fundamental forward-return backtesting is the easiest self-deception in finance
— it needs *more* discipline than the daily work, not less. Four traps, specific here:

1. **Point-in-time data — confront FIRST.** Use only what was *known* at each date (as-reported
   financials + real reporting lag, not today's restated snapshot). Our `fundamentals` DB is
   *current* snapshots. Reconstructable look-ahead-free: trailing valuation percentile
   (price × lagged EPS), revenue/EPS growth (+~45d lag), FII-holding trend, margins/ROE.
   **NOT available: EPS *revisions* and guidance** — need an analyst-*estimate* history we
   don't have. Prune the factor list to the feasible subset before modelling.
2. **Effective N collapses with horizon (inverse of intuition).** Forward 12-mo on 9 yr ≈
   **~9 independent obs** → unfalsifiable; 3-mo ≈ ~36 overlapping → weak. The long horizon you
   most want is the least testable. Don't build the 12-mo claim on this sample.
3. **Overfitting.** 5-7 betas on tiny N is noise. Start with **1-2** factors (valuation +
   growth); add only what earns incremental OOS value.
4. **Cross-sectional, not time-series.** Index-fundamentals → index-return is the tiny-N trap.
   Rank the **~10 constituents** by fundamentals → predict *relative* forward returns: ~10×
   the observations, the standard validatable form.

**Classify factors before modelling** — test tactical only after structural sets a baseline
(prevents double-counting; software added LAST because it is easiest to over-credit):

| Structural (slow) | Tactical (fast) |
|---|---|
| valuation percentile, revenue growth, ROE, margins, earnings quality | **Global Software Services regime**, FII/DII flow, relative momentum, breadth, news |

Build order (each gated before the next):
1. **Fundamental data audit** (`fundamental_data_audit.py`, the `gate_validation.py` of data)
   — history depth, point-in-time integrity, **survivorship (historical constituent
   membership, not just today's 10)**, coverage, PIT-safe factor availability, cross-sectional
   N → PASS/FAIL. If it returns "DATA ACQUISITION REQUIRED", the bottleneck is *data*, not
   modelling — source deeper/dated fundamentals first and stop here.
2. **Cross-sectional fundamentals-only base** — rank ~10 constituents on **1-2** PIT-safe
   structural factors, reporting-lagged, forward **60/120-day** (skip 12-mo — untestable);
   **ranking buckets before regression** (cheap/mid/expensive → does cheap outperform?);
   rank-IC + **per-regime** split (no regime pass — value traps are regime-dependent).
3. **Add structural factors one at a time** (valuation → +growth → +ROE …), each kept only
   if it lifts OOS rank-IC.
4. **Add the software regime LAST** — Model N vs Model N+regime, walk-forward OOS; keep only
   if it adds meaningful accuracy (0.21→0.29 yes; 0.21→0.22 no). Confidence, not fair value.

**"Just use recent-regime data" does NOT lower the bar — it confirms it.** The regime
change we found was in the *software-peer daily transmission* (post-2023), **not** in the
*fundamental value→return* relationship (never tested; no evidence cheap Indian IT started
or stopped outperforming in 2023). Restricting to the recent regime leaves ~7 quarters —
too few to validate a fundamental factor, which needs *multiple earnings cycles*; and
fitting to the window where a signal already looks strong is the textbook overfit the whole
project guards against. Structural factors want long history; the tactical software regime
is inherently recent-only and therefore stays a *monitored* signal, not a validated one.
Two layers, two data regimes.

**AUDIT RUN — verdict: DATA ACQUISITION REQUIRED (step 1 blocks).** Median **7 quarters**
per company (2023-2026), **N=71** company-quarters — both fail (need ≥20 / ≥200). Coverage
10/10; PIT and survivorship FLAG. So the fundamental engine is **data-blocked, not
model-blocked** — no backtest is authorised until depth exists. And the caveat is
structural: retail sources (screener.in) add *depth* but give *restated* values without
announcement dates, so PIT stays a FLAG; institutional feeds (Capitaline/CMIE) are the clean
but costly route, and survivorship still needs a historical NIFTY IT membership series. The
fundamental medium-term engine is therefore a real *data project*, parked pending acquisition.
Immediately-actionable alternative with data already 9-yr deep: the **short-chain Energy
positive control** (§7) — price/macro only, no PIT/survivorship problem.

**UPDATE — screener.in data collected (~10-yr quarterly). Go/no-go by sector:**
- **IT → BUILD.** Available fields (Sales, PAT, EPS, Book value, Operating Profit→OPM%) *are*
  the core IT factors: valuation (P/E, P/B, EV/EBITDA), revenue/EPS growth, margin trend.
  USD-revenue & buyback are refinements. Cross-sectional base model is buildable now.
- **Bank → DO NOT build on valuation + PAT.** Screener's *Excel* export normalizes banks into
  a manufacturing template and **drops NPA/NIM/PCR/provisions** — and the fields that remain are
  *actively misleading*: a bank's P/B is low precisely when the market fears its book, so
  "cheap P/B" **systematically buys the value traps** (PSU banks 2015-20), and PAT is dominated
  by provisioning swings. A valuation+PAT bank model would likely have *negative* real value.
  ROA is a weak, provisioning-noisy quality proxy — not enough. **Two honest paths:** (a) source
  asset quality (GNPA/NNPA/NIM/PCR/credit-cost) from screener's *HTML* page / moneycontrol /
  results PDFs — the Excel export isn't enough; or (b) the **rate-cycle sector thesis** (India
  10Y/repo/curve → NIM/earnings), which needs *no* per-bank NPA. Announce date absent → 45-day
  lag (both). NII ≈ Sales−Interest is rough (screener "Sales" includes other income).

### 6.7 Two products, two validation standards (correcting a category error)

The audit's verdict blocks a **statistical factor backtest**, not a **decision-support
thesis**. These are different products; conflating their standards was an error (I stamped
"not worth building" — a Product-A verdict — onto Product B).

| | **Product A — Signal Engine** | **Product B — Investment Thesis Engine** |
|---|---|---|
| Question | can we *trade* this? | is the sector cheap/expensive; what's priced in? |
| Standard | IC, hit-rate, walk-forward, regime — strict | economic coherence, explicit assumptions, scenario analysis, audit trail |
| Tool | `signal_validation.py` (five gates) | `investment_thesis.py` (**already built**: bear/base/bull → fair value + reverse mode) |
| Data need | deep point-in-time, N≥200 | *current* fundamentals + valuation context (light) |
| Failure mode | fake precision (inflated IC) | **unfalsifiable narrative** (assumptions chosen to fit the answer) |
| Guard | the gates | **audit trail** — assumptions sourced; evidence→weight mapping *pre-specified & symmetric* (§3 g5, §9) |

So the medium-term IT work is **not** blocked — it changes objective: not "fundamentals
predict returns statistically" (Product A, data-blocked) but "a transparent thesis engine
with scenario analysis + a Bayesian, evidence-logged confidence updater." Most of it exists;
what's missing is the **scenario-weight updater** (P(scenario) moved by evidence, each move
logged) and its NewsAgent feed.

**The software regime's home is here** — a *scenario-weight updater*, not a return predictor.
Its "real but not tradeable at the open" result stops being disqualifying: as one
evidence-logged input adjusting *confidence* (not producing a trade), it clears a different,
lower bar (§3 g3, §6.5). But the mapping ("software strong → +bull weight") is itself an
assumption that must be pre-specified and applied symmetrically to good and bad news, or it
is rationalization.

**Two complementary tracks, not competing:** (a) **Energy → Product A** — the five-gate
validator on a short-chain sector, the framework's positive control; (b) **IT → Product B** —
reposition `investment_thesis.py` as the thesis engine and add the evidence-logged Bayesian
weight updater. Different questions, different standards, one platform.

**Product B design — the Evidence Policy (precommitted, symmetric) + audit log.** Scenario
probabilities move only by a *predefined* table of log-odds shifts, written before use and
symmetric (good and bad news equal-and-opposite unless empirics say otherwise) — this is the
guard against the narrative-rationalization failure mode. Example:

| Evidence | Δ bull log-odds |
|---|---|
| Guidance raised / cut | +0.8 / −0.8 |
| Accenture outlook up / down | +0.4 / −0.4 |
| Software regime positive / negative | +0.3 / −0.3 |
| FII buying / selling > threshold | +0.2 / −0.2 |

Every update is logged with its reasons (`2026-08-01: bull 40%→46% — +0.4 ACN guidance,
+0.2 software regime, −0.1 valuation`) so it can be audited months later. **Evidence-quality
module (missing today):** each evidence item carries not just direction but
`effective_weight = source_credibility × strength × recency`, where recency is the §9
half-life decay — a Microsoft print enters at full weight and fades over a quarter; a rumor
enters at a fraction and fades in days. The NewsAgent emits `{driver, direction, strength,
credibility, evidence[]}`; the Thesis Engine applies the policy. NewsAgent **emits structured
evidence, never predicts price** — the scenario *values* stay fixed; only the *weights* move.

Roadmap: **Phase 1** — Energy through the validator (framework positive control, target =
upstream oil-beta per §7). **Phase 2** — extend `investment_thesis.py` with explicit scenario
probabilities + the Evidence Policy + audit log + confidence output (no forecasting change).
**Phase 3** — NewsAgent emits the structured-evidence schema above.

**Product B's core mechanic — VALUATION IS AN ENCODED EXPECTATION; the engine decodes it.**
The spine of the Thesis Engine (for banks; the P/E analog for IT): a price is not "cheap" or
"expensive" — it is a *bet the market has already placed* about future returns on equity. So the
engine's job is to **decode the expectation a multiple embeds, then form a view on whether reality
beats or misses it.** The alpha lives entirely in the *gap between implied and delivered*, and it
is **regime-independent** — a different axis from the §6.8 regime-conditional P/B factor.
- **Decode (quantitative, not narrative):** fit the cross-sectional line `P/B = a + b·ROE` across
  the peer set; a bank's **implied ROE = (P/B − a) / b** is the return the price demands. Compare
  to *delivered* ROE (= PAT ÷ standalone net worth). **Residual > 0** (P/B above the line ⟺ implied
  ROE > delivered) = market **priced for improvement / durability** — the "priced for delivery"
  names (ICICI, Federal): upside only if they *beat* the embedded bar, downside if ROE merely holds.
  **Residual < 0** = market **priced for skepticism** — the "better-than-feared" names (HDFC at its
  own P/B floor, Bandhan): re-rate if delivered ROE simply stops disappointing the low bar.
- **The discipline that makes it a thesis, not a scorecard:** the reasons a name trades rich/cheap
  ("✓ low NPAs ✓ high ROE ✓ management credibility") must be **derived from the decode, never
  asserted as bullets** — asserting them is the ⭐-scorecard failure in prose. The engine earns its
  keep ONLY where P/B and fundamentals **disagree** (premium multiple on a *falling* ROE; trough
  multiple on a *stabilizing* ROE); agreement is noise dressed as insight. This is the concrete,
  always-available form of the §7.2 **expectation snapshotter** — P/B *is* the standing expectation
  snapshot, so "better-than-feared" becomes measurable without waiting for an earnings date.
- **Honest gate:** current-ROE-vs-implied is buildable now (data in hand) and already sorts who is
  priced above/below their own delivery. The **real edge needs a *forward* ROE estimate** vs the
  implied bar — not yet built. Until then this is a coherence-judged *thesis lens* (Product B), not
  a validated signal. UI note: the decode is **computed on demand (button), never on load.**

### 6.8 Bank result — moderate, but *generic risk* not bank-specific; gap-locked (2nd sector)

Nifty Bank through the same five gates (2018-2026): the taxonomy prediction *held* — moderate
overnight IC (~0.19), below IT's peer-driven peak. But the honest read is what that IC **is**:
**generic global-risk beta, not banking transmission.**
- **`XLF` (US financials) UNDERPERFORMS broad `NASDAQ`** (0.164 < 0.169) — US banks do *not*
  lead Indian banks. `RISK_ON` (0.196) beats NASDAQ (0.169) by a trivial 0.027; walk-forward
  Δ = **+0.007** (noise). Gate 3 "passed" on a technicality — **no bank-specific incremental
  information** over generic risk. What moves Bank overnight is risk-on/off → FII flows →
  India's largest FII holding gaps; it would move any heavily-foreign-owned sector.
- **Gap-locked, same as IT** (gap 0.47 vs intraday **−0.06** — the open slightly *over-reacts*,
  mild intraday reversal). Not capturable at the open.
- **`US10Y` & `USDINR` insignificant** (p 0.35 / 0.73) — because they are *US* rates / a 24h FX,
  not India rates. Bank's differentiated driver (domestic rate cycle) is untouched here.

**Bank intelligence = decompose into components, each VALIDATED (not a hand-set scorecard).**
The bank direction signal splits into: FII inflow, NIM surprise, credit-growth surprise,
asset-quality trend, leadership breadth, and management-guidance sentiment (NewsAgent) — this
*is* the §6.7 evidence decomposition, and it is the right destination. But a ⭐-rating momentum
scorecard and a gut "60-65% continues" are the **false precision the platform exists to
replace** (cf. the retired "AI Threat = 73" and the four-times-blocked gut Sharpe). Each
component earns its weight by **incremental rank-IC / OOS validation** — "it captures the
catalyst therefore it outperforms" is the assumption that failed for IT (gap-locked) and Bank
overnight (generic risk). Guidance-sentiment (LLM) is the most powerful and the most prone to
rationalization → it MUST use the pre-committed **symmetric** evidence policy (§6.7) + an OOS
test, or it is "AI Threat = 73" in a new costume. The momentum "score" is an **output** of
validated, evidence-logged components — never hand-set stars, never a gut probability.

**Financials ≠ one node — split Consumer-Credit (NBFC) from Core-Banking (carve at the joint,
again).** Two universes, drivers, valuations: **Core Banking** (HDFC/ICICI/SBI/Axis — deposits,
NIM, CASA, GNPA, corporate credit, RBI; valued on **P/B**) vs **Consumer Credit / NBFC** (Bajaj
Finance, Shriram, Cholamandalam — AUM growth, funding-cost *spread*, credit cost, consumption
cycle; valued on **P/E**). **STRUCTURAL FACT: NBFCs are NOT in Nifty Bank** — they sit in Nifty
Financial Services (FINNIFTY). So the bank factor model on the 12 BANKNIFTY names captures **Core
Banking only**; the NBFC leg is an *unmodeled separate universe* — and the *early-cycle* half most
likely to lead. NBFCs lead on improving consumption / falling funding costs; banks are later-cycle.
The **NBFC-vs-Bank relative-strength rotation is itself an early/late-cycle leadership signal** —
parallel to software⇄AI-infra for IT; testable, not assumed. "Leadership" (who attracts the
incremental buyer: relative strength, volume, earnings surprise) is a valid concept distinct from
index weight — but a hand-set Leadership Score (25% RS + 15% volume …) is the same false precision;
each component earns its weight by validation vs forward *relative* return.

**Bank stock-selection result (Product B, cross-sectional, `bank_factor_model.py`) — P/B is a
CONDITIONAL factor, not standalone. CONFIRMED on clean standalone data.** 12 BANKNIFTY names,
2017-2026, annual point-in-time (period_end+45d), P/B = adj_price × split-adjusted shares ÷
**standalone** net worth (a **net-worth-flat discriminator** stops bank mergers/recaps — PNB
amalgamation, BoB, IDFC-First — being mistaken for splits). The finding is a **sign flip in
P/B's forward-relative rank-IC across regimes**: **+0.12** (2017-20) → **−0.52** (2021-23) →
**~0/+0.11 noisy** (2024-26). Cheapness is *punished* in the deteriorating leg (the value trap)
and *paid* in the recovery. **Robust** to horizon (recovery −0.515 @365d, −0.576 @182d) and to
dropping late-listed banks (core-9 **−0.622**) — not a sampling artifact.
- **DATA-INTEGRITY RE-VALIDATION (passed).** First run used **consolidated** net worth, which
  bloats subsidiary-heavy banks and *collapsed* their P/B (Kotak 2.8x→1.06x, HDFC→0.97x) —
  corrupting the cross-sectional rank the IC depends on. Re-run on **standalone** net worth
  (P/B now street-accurate across the whole cross-section: HDFC 2.06, ICICI 2.64, SBI 1.63,
  Kotak 2.84 — not just one anchored name) **preserved the sign pattern**; recovery IC eased
  −0.59→−0.52 (the old number was mildly inflated by the Kotak artifact). *Lesson carried to IT:
  anchor multiple names, filter `basis` per sector (banks=standalone, IT=consolidated).*
- **HARD CAVEATS (why this is a confirmed THESIS, still not a signal):** (a) the asset-quality
  *interpretation* is **not verified in-sample** — loaded `asset_quality` starts 2023, covering
  neither the deteriorating nor recovery regime; "regimes = NPA phases" is *external market
  history* and the boundaries were drawn knowing it (**hindsight in the partition**). (b) **n = 3
  years per regime — one instance of each cycle; clean data adds integrity, not regime-instances.**
  (c) Bank returns are **gap-locked** (above) — not a validated *tradeable* signal. (d) **Not
  operational:** "rank on P/B only while system asset quality is improving" needs a **live
  regime-detector**; the 2023-26 GNPA (no cycle in it) cannot produce one. Cheapest unlock = ONE
  RBI system-GNPA / system-credit-cost series back to ~2015, not the deep per-bank scrape.
- **The cross-sectional value-trap 2×2 was the wrong test** — starved to N=2,3 per cell *and*
  entirely inside the normalization regime (2023-26) where P/B has no edge. The **regime-conditioned
  P/B IC on 9 years is the powered version of the same question** — deep-axis, not shallow-axis.
- **Quality-factor signs (GNPA-level +0.31, NIM −0.22, ROA −0.15; all n=4, 2023-26) are a
  recovery/normalization-window artifact** ("beaten-down PSUs rallied"), NOT durable quality
  factors — do not hard-code "buy high-GNPA banks."

**Two meta-findings:**
1. **Both sectors tested (IT, Bank) are gap-locked.** Strengthens the hypothesis that **the
   Indian open prices overnight information efficiently in general.** **Energy is now the
   decisive test** — a slow physical chain (crude→upstream) is the best remaining candidate
   for a signal that survives *into intraday*. If Energy is also gap-locked, overnight→open is
   simply not a tradeable edge in this market.
2. **Taxonomy refinement:** overnight signal *strength* tracks **whether a clean
   overnight-traded proxy exists** (IT has software peers; Bank has none, only generic risk),
   **not chain length**. Chain length predicts *fundamental/valuation* tractability; the
   overnight signal is a separate axis. Bank's real edge, if any, is the **rate cycle** — a
   thesis-engine question needing India rate data, not this overnight signal.

---

## 7. Open / next (prioritized)

1. **NewsAgent interpretation module (L1→L2)** — evidence → interpretations →
   expectation revision, each with an evidence log (Accenture guide cut →
   `enterprise_spending: weak`; hyperscaler capex up → `ai_implementation: strong`).
2. **Expectation snapshotter** — capture the *perishable* pre-event consensus
   (before each earnings date) so "better-than-feared" is measurable later.
3. **Capital-allocation tracker (L4 module)** — sector rotation, FII/DII, relative
   valuation, relative momentum. Answers "where is money going, and is there
   momentum left?" — the class of question rotation, not fundamentals, governs.
4. **Scenario-weighted valuation** — `E[value] = Σ P·value` + a belief-revision log
   on `investment_thesis.py` (news moves the weights, not the scenarios).
5. **Peer-factor horse-race (IT Transmission Engine, §6.2)** — the correlation / hit-rate /
   Information-Coefficient study of candidate overnight predictors (Nasdaq, S&P-IT,
   ACN, CTSH, Infosys-ADR, Software-Peer PCA) vs next-day Nifty IT across regimes.
   *Gate:* the peer factor is hard-coded as the daily signal **only if** it beats the
   macro/index proxies on hit-rate and IC out-of-sample. Until then it is a logged
   hypothesis, not a principle.

Then: a **second sector, short-chain first** (Energy or Banks) to prove the
interface generalizes — easier by design (direct transmission, observable ends),
and a useful contrast that shows the platform picking *different methods for
different chain lengths*.

**This is now the priority — and it is a test of the FRAMEWORK, not of a trade.**
Having shown the *long-chain* sector (IT) has no stable daily edge across nine years,
the sharpest possible validation is whether a *short-chain* sector does — because that
is where the chain-length taxonomy (§4) predicts a signal *should* exist. The elegant
falsification test: the very tool that **failed** for IT — daily macro→sector regression
(`factor_regression.py`, retired) — should **succeed** for Energy (oil → OMC/upstream,
1-2 steps). If Energy yields stronger IC, cleaner decay, and better regime stability than
IT did, the framework hasn't merely rejected one hypothesis — it has **correctly predicted
where signals should and should not exist**, which is a far stronger result than any
single trading rule. If Energy *also* yields nothing tradeable, that too is informative
(daily sector prediction may simply be hard). Run Energy through the *same* five-gate
`signal_validation.py` (same gates, sector-parameterized target/driver) for a clean
cross-sector comparison.

**CRITICAL target-design caveat — do NOT run raw `NIFTYENERGY`.** Nifty Energy is a
*heterogeneous* basket (Reliance = refining margins/petrochem not crude level; OMCs
IOC/BPCL/HPCL where crude↑ often = margin *squeeze*, **inverse sign**; upstream ONGC/Oil-India
crude↑ = good; plus power utilities, Coal India, GAIL). Feeding crude → NIFTYENERGY risks
the **internal-cancellation** failure that made Nasdaq useless for IT — opposing oil
sensitivities netting to noise — and would yield a **false negative that wrongly indicts the
framework**. The clean short-chain positive control is **crude → a pure upstream oil-beta
target (ONGC + Oil India)**: 1-2 steps, unambiguous sign. Second experiment: crude → OMC
basket with an *expected negative* sign (oil↑ → OMCs↓ = the chain working in reverse, even
stronger validation). Carving the right target IS the discipline; the raw index betrays it.

**Idiosyncratic (company-specific) risk — noise, not bias.** Single-name idiosyncrasy is
uncorrelated with crude, so it only *weakens* IC (a false-*negative* risk), never fabricates
one. Mitigation: **homogeneous-sign basket, MEDIAN-aggregated** (ONGC+OIL) — same oil sign
(no cancellation) and the median cancels part of each name's idiosyncratic move (the IBM
robustness); optionally **market-neutralize** (residual vs NIFTY) to strip broad-market beta
and isolate the oil-specific component (validator's orthogonalization, unchanged). Honest
limit: India's pure-upstream universe is ~2 names, so diversification is partial — read a weak
result as *underpowered/noisy*, not "framework failed." The OMC basket (IOC/BPCL/HPCL, 3
homogeneous names, expected negative sign) diversifies better and is the reverse-chain test.
Still fundamentals-free — prices only.

**Pre-registered as a positive-control / efficiency experiment (before running):**
- **H₀ (efficient):** the India open prices overnight info → Energy is **gap-locked** too
  (intraday IC ≈ 0, like IT 0.07 and Bank −0.06). Third confirmation ⇒ conclude *overnight→open
  is not a tradeable edge in this market for the sectors tested* — a real microstructure result.
- **H₁ (slow commodity):** oil propagates slower → Energy retains a **capturable intraday** edge.
Decision metric: **intraday IC on the UPSTREAM basket** (not close-to-close, which is
gap-dominated; not NIFTYENERGY, whose heterogeneity confounds H₀ vs a cancellation artifact).

**Honest caveat on H₁ — the daily test may be the wrong horizon.** Crude is a *hyper-liquid,
globally-watched 24h* instrument, so ONGC likely opens as efficiently repriced to overnight
crude as IT does to software → the *daily* Energy signal is probably **also gap-locked (H₀)**.
The "commodity propagates slowly" intuition is a **medium-term fundamental** effect (a sustained
oil *trend* → realizations → energy earnings over weeks), **not** a daily overnight one. So
Energy's real opportunity is likely the **Fundamental/Thesis engine at the 1-3 month horizon** —
and, uniquely, that is **data-ready with no acquisition wall**: oil *is* the fundamental driver
*and* it's a deep price series. Energy is therefore the first sector where **Product B can be
tested on clean data** (oil-trend → forward energy returns), where IT and Bank both needed
fundamentals we don't have. Run the daily signal for the efficiency verdict; expect the *value*
to be in the medium-term oil-trend test.

---

## 8. The one-line thesis of the whole platform

The unsuccessful Nifty IT attribution engine produced the design principle for the
entire system: **don't reuse factors — reuse the reasoning framework.** The
four layers stay constant; the causal variables and transmission mechanisms change
by sector. That is a stronger foundation than any single IT-specific model, and it
extends naturally to every sector on the exchange.

---

## 9. v1.0 — temporal evidence, the hypothesis manager, and the freeze

### It is a Hypothesis Management engine (more precise than "explanation")

The platform does not predict and does not merely explain — it **manages competing
hypotheses**, the way an institutional research desk does. Each hypothesis carries
its supporting and contradicting evidence, a confidence that decays over time, and
a last-updated stamp:

```
Hypothesis: "Indian IT benefits from enterprise AI"
  supporting     ✓ Microsoft AI capex   ✓ TCS AI deal wins   ✓ Accenture demand
  contradicting  ✗ weak discretionary   ✗ pricing pressure
  status: still supported   confidence: <decaying>   last_updated: <date>
```

### Evidence is temporal — decay is one line, so it ships in v1.0

Evidence is stored as `fact + interpretation + impact + validity + half_life`.
Its live weight decays:

```
remaining_influence(t) = initial_impact × 0.5 ^ (age / half_life)

confidence(Hypothesis) = Σ  sign(e) · impact(e) · remaining_influence(e)     over its evidence
```

A hypothesis's confidence therefore fades on its own unless refreshed — that is
what makes the platform dynamic. Indicative half-lives (**priors, not measurements**
— calibrate them from real price reaction over earnings seasons):

| Evidence | Half-life |
|---|---|
| FII buying / positioning | 1–5 days |
| Broker upgrade / interpretation | 1–3 weeks |
| Quarterly earnings | ~1 quarter |
| Management guidance | 1–2 quarters |
| AI / strategy shift | 1–3 years |
| Structural cost advantage | 5–10 years |

The same decay makes sector **rotation self-expiring** in the capital-allocation
module, and lets the engine answer "is the market still reacting to that news?"

### The platform structure (everything designed fits here — no new complexity)

```
Sector Intelligence Platform (v1.0)
├── Evidence Store        L1  — facts + interpretation + validity + decay
├── Hypothesis Manager    L2  — competing hypotheses; evidence for/against; decaying confidence
├── Valuation Engine      L3  — scenario-distribution fair value (built: IT)
├── Capital Allocation    L4  — rotation, flows, positioning, gap-to-value
├── Market Dashboard      surface — what changed, why, and is it already priced in
└── NewsAgent             transducer — evidence → interpretation → hypothesis/expectation updates
```

### Frozen at v1.0 — stop designing, start validating

The design is frozen because the remaining work is *implementation*, not concepts.
Over this design process the major architectural pitfalls were each eliminated:

- correlation mistaken for causation,
- one-size-fits-all factor models,
- false precision in qualitative scores,
- direct decomposition of aggregate risk premia,
- point estimates instead of scenario distributions,
- static evidence with no decay.

From here the fastest path to a better system is **not more concepts** — it is to
implement the NewsAgent interpretation module, the valuation engine (built for IT),
and the capital-allocation tracker, then **validate over several earnings seasons.**
Real usage will calibrate the decay half-lives and reveal the next genuine
architectural change far better than further design iteration ever could.
