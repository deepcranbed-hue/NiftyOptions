# Implementation Plan — Panel Reorganization: Four Workspaces, Left Tab Rails

**Scope: FRONTEND ONLY. Re-home the existing ~13 panels into 4 workspaces with
left-side tabs. NO component rewrites, NO backend changes, NO new features.**
Every panel keeps its props, state handling, API calls, and behavior exactly as
today — only routing and layout change. If a step seems to require modifying a
component's internals or an endpoint, STOP — it doesn't; re-read this scope.

**Read first:** frontend routing setup, the panel components list, README
panel overview.

---

## The four workspaces (top-level nav, in this order)

Arranged by the trader's decision funnel: WHY is the market moving → WHERE is it
positioned → WHAT do I trade → plumbing.

### 1 · MARKET INTELLIGENCE  (the "why" — the news consolidation)
Left tabs, top to bottom:
| Tab | Panel(s) re-homed |
|---|---|
| **Global** | GlobalCuesPanel + Metals Barometer + bond/rupee read (stacked sections in one tab) |
| **Sector** | SectorNewsPanel (sentiment + drill-down) with SectorEarningsPanel below it |
| **Flows** | FlowsPanel (FII/DII/SIP + disambiguation verdict) |
| **Events** | EventCalendarPanel |
| **Daily Report** | the composed report view + .docx export button |

Persistent header strip across all tabs of this workspace: dominant-driver chip
· index bias (±1) · coverage % · news as-of time. (These values already exist in
state — the strip only displays them.)

### 2 · MARKET STRUCTURE  (the "where")
| Tab | Panel(s) |
|---|---|
| **OI Positioning** | OIPositioningPanel (walls, bursts, writing-vs-buying) |
| **Vol & RND** | ComplacencyPanel + RND card (move/skew/provenance badge) + vol-surface section |
| **Price Chart** | PriceChartPanel (when built; tab hidden until then) |
| **Compare** | CaptureComparePanel (its four internal sections unchanged) |

### 3 · TRADE  (the "what")
| Tab | Panel(s) |
|---|---|
| **Suggester** | StrategySuggesterPanel (family ranking + rationale) |
| **Strikes & Build** | optimizer pick · recommended structures (Leg Execution Matrix) · manual entry · payoff preview → Trade button |
| **Portfolio** | PortfolioPanel (positions, lineage, P&L attribution, payoff diagrams, Net P&L) |

### 4 · DATA & OPS  (plumbing)
| Tab | Panel(s) |
|---|---|
| **Ingestion** | NSESyncPanel + BreezeSyncPanel + CSV upload form (spot / expiry-date / VIX fields unchanged) |
| **Captures** | capture list / dropdown management |
| **Health** | the existing pipeline/provenance indicators gathered on one page (display-only aggregation of statuses already emitted — RND provenance, LLM provider status, data as-of times) |
| **Config** | LLM provider switch (llm_config), weights refresh note |

---

## Layout & interaction rules
1. **Top-level nav:** 4 workspace entries (Intelligence · Structure · Trade ·
   Data). Persistent across the app; current workspace highlighted.
2. **Left tab rail** inside each workspace: fixed ~200px, tab = icon + label;
   active tab highlighted; unbuilt tabs (Price Chart) hidden, not greyed.
3. **Deep links:** each tab gets a route (`/intel/sector`, `/trade/portfolio`)
   so refresh/bookmark lands on the same tab. Default tab = first in each rail.
4. **State preservation:** switching tabs must NOT unmount-and-lose panel state
   (keep components mounted with display toggling, or lift the few pieces of
   local state that matter — e.g. selected capture, selected expiry). Test: pick
   capture in Compare → visit Trade → return → selection intact.
5. **Cross-workspace context:** the globally-selected capture + expiry (the
   dropdown) lives in shared state ABOVE the workspaces (navbar), not inside any
   panel — all panels keep reading it exactly as they do now.
6. **The Formula Overlay** stays globally available in every tab (unchanged).
7. **Update buttons keep their homes:** "Update News" etc. stay with their
   panels; the global "Run Pipeline" action stays in the navbar.
8. **Mobile/narrow (<900px):** left rail collapses to a horizontal scrollable
   tab bar or dropdown; workspaces become a hamburger nav.
9. **No visual redesign:** existing panel styling untouched; only the shell
   (nav + rail + routes) is new.

## Migration steps (each shippable, ~an evening each)
1. Build the shell: workspace nav + tab rail + routes, all tabs pointing at
   placeholder slots.
2. Re-home Workspace 3 (Trade) first — it's the daily-use path; verify the
   suggester → strikes → Trade → portfolio flow end-to-end.
3. Re-home Workspace 2 (Structure), verify capture selection + compare flow.
4. Re-home Workspace 1 (Intelligence) + the header strip.
5. Re-home Workspace 4 (Data & Ops); retire the old flat layout.

## Acceptance criteria
1. Every existing panel reachable in ≤2 clicks (workspace → tab); none removed,
   none rewritten; all API calls and behavior byte-identical.
2. Deep links work; refresh preserves workspace+tab; default tabs sensible.
3. Capture/expiry selection is global and survives tab and workspace switches.
4. The full trade flow (suggester → strikes → Trade → portfolio → compare)
   works across its new homes with no regression.
5. Update buttons and Run Pipeline function exactly as before.
6. Narrow-screen layout usable (rail collapses).

## Rules
- Re-home, never rewrite. Shell-only change; component internals untouched.
- No backend edits, no new endpoints, no new features smuggled in.
- Panel state survives tab switching; global capture/expiry selection lives in
  the navbar shell.
- Unbuilt tabs hidden; nothing rendered as "coming soon".
- Ship in the migration order; the app must be fully usable after every step.
