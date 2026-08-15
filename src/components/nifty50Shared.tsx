/**
 * nifty50Shared — the /api/nifty50-view data contract and the small presentational
 * pieces that both consumers of it need.
 *
 * There are two views onto the same payload: Nifty50Panel (the 50-row table, with a
 * compact inline expand for a quick peek) and Nifty50StockPage (one stock, full
 * browser tab, room to breathe). They are deliberately different layouts — that is the
 * point of having both — but they must never disagree about what a Verdict or a
 * Reaction IS. So the types and the tone tables live here and nowhere else; only the
 * arrangement is duplicated.
 */
import React from 'react';

export type Verdict = {
  label: 'rich' | 'in-line' | 'cheap'; metric: string; value?: number;
  vs_median_pct: number; sector_median: number; basis: string;
  peers?: number;
  // Null once the multiple sits beyond 3x / below 1/3 of the peer median — see
  // reversion_note. A -95% "reversion" off a 670x P/E is arithmetic, not a scenario.
  reversion_pct?: number | null; reversion_note?: string | null;
};

export type Drivers = {
  position: string; tailwinds: string[]; headwinds: string[];
  latest_quarter?: { period: string; as_of: string; points: string[] };
  recent_change?: { as_of: string; points: string[]; verdict: string };
};

// MEASURED, not judged: how the stock actually traded the session after results,
// from earnings_reactions.json (announcement days picked by VOLUME only, so the
// finding can't be circular). rel = versus NIFTY; sect_rel = versus its sector index.
export type Bias = 'positive' | 'neutral' | 'negative';

export type Reaction = {
  n_events: number;
  full_mean_r1d_pct: number; full_mean_rel1d_pct: number;
  full_positive_share: number; full_bias: Bias;
  recent_n: number; recent_mean_r1d_pct: number; recent_mean_rel1d_pct: number;
  recent_mean_sect_rel1d_pct?: number | null;
  recent_positive_share: number; recent_bias: Bias;
  diverges?: boolean;
};

// EXPECTATION — what the market is priced FOR, not what was delivered.
// implied_eps_growth_pct is trailing P/E / forward P/E - 1. Yahoo's forward multiple is
// a next-fiscal-year consensus against a trailing-twelve-month denominator, so read it
// as growth embedded over one to two years, not as a next-quarter hurdle. Target /
// dispersion / coverage come from expectation_snapshots.json, captured BEFORE a print
// because consensus is revised continuously and the pre-announcement value cannot be
// recovered afterwards.
export type Expectation = {
  implied_eps_growth_pct: number | null;
  target_mean?: number | null; target_low?: number | null; target_high?: number | null;
  target_upside_pct?: number | null; dispersion_pct?: number | null;
  analysts?: number | null; next_earnings?: string | null; as_of?: string | null;
};

// Where one number sits vs its sector peers and vs the 50. Computed server-side from
// the scan that already ran — rank + median, never a score: a rank says where a name
// sits without implying the gap between ranks means anything.
export type Ctx = {
  value: number;
  // false for a trailing P/E above 100x — a near-zero earnings denominator, not an
  // expensive stock. Those get no rank and are kept out of everyone's medians.
  comparable: boolean;
  sector: string;
  sector_rank: number | null; sector_n: number; sector_median: number | null;
  index_rank: number | null; index_n: number; index_median: number | null;
  superlative: string; label: string;
};

// The stock's own past, from pe_history.json (optional — absent until the backfill runs).
export type PeHistory = {
  median: number | null; p25: number | null; p75: number | null;
  current: number; percentile_now: number;
  n_quarters: number; years: number; first: string; last: string;
  lag_assumed?: boolean; source?: string;
};

// Quarterly FII/FPI shareholding — a STOCK, not a flow. change_pp is percentage POINTS.
export type FiiHolding = {
  latest_pct: number; period: string;
  prev_pct: number; change_pp: number; change_4q_pp: number;
  quarters_span: number; direction: 'adding' | 'trimming' | 'flat' | 'unknown';
  trend: { period: string; pct: number }[];
};

// Researched, DATED context from valuation_notes.json — the layer neither a peer rank
// nor an own-history median can supply: WHY the number is what it is. Hand-written with
// sources, so it decays like nifty50_drivers.json and carries its own as_of.
export type ValuationNote = {
  as_of?: string; applies_to?: string;
  headline: string; summary?: string;
  why_the_multiple?: string[];
  current_scenario?: string[];
  better_lens?: { note?: string; metrics: { label: string; value: string; detail?: string }[] };
  what_would_change_it?: string[];
  caveats?: string;
  sources?: { title: string; url: string }[];
};

// What growth the index is priced for, and the three levers that can break the multiple.
// Computed on a MATCHED sample — only names carrying both a trailing and a forward P/E,
// because loss-makers appear in the forward pool alone and inflate implied growth.
export type EarningsVsValuation = {
  trailing_pe: number; forward_pe: number;
  weight_covered_pct: number; names: number; excluded: string[];
  implied_growth_pct: number;
  earnings_yield_pct: number; forward_earnings_yield_pct: number;
  gsec_10y_pct: number; gsec_as_of: string; gsec_yoy_pp: number;
  yield_gap_pp: number; forward_yield_gap_pp: number;
  pct_per_pe_turn: number; to_band_low_pct: number; to_band_mid_pct: number;
  parity_pe: number;
  sectors: { sector: string; weight: number; trailing_pe: number;
             forward_pe: number; implied_growth_pct: number }[];
  top_contributors: { symbol: string; weight: number; implied_growth_pct: number }[];
  priced_to_shrink: { symbol: string; weight: number; implied_growth_pct: number }[];
  // Is the embedded growth reachable for the economy underneath it — and what horizon
  // is the forward multiple actually using? Calibrated against the published forecast.
  macro_check?: {
    as_of: string;
    nominal_gdp_fy26_pct: number; nominal_gdp_fy27_pct: number;
    nifty_eps_forecast_fy27_pct: number;
    profit_to_gdp_pct: number; profit_to_gdp_fy20_pct: number;
    implied_annualised: Record<string, number | null>;
    excess_over_nominal_gdp_pp: Record<string, number>;
    best_fit_horizon: string; note: string;
  } | null;
  note: string;
};

// Growth PRICED IN minus growth RECENTLY DELIVERED, in percentage points. A high bar is
// a bigger promise to keep, not a bigger opportunity — the direction most people invert.
export type GapRow = {
  symbol: string; name: string; sector: string; weight: number | null;
  trailing_pe: number; forward_pe: number;
  implied_growth_pct: number; delivered_growth_pct: number; gap_pp: number;
  revenue_growth_pct?: number | null;
  band: 'high bar' | 'in line' | 'low bar';
  cyclical_caution: boolean;
  // Normalised comparison — present only where eps_history.json has been built.
  // NOTE the units: implied_growth_pct is a TOTAL change, a CAGR is per year, so the
  // embedded figure is annualised at both a 1-year and a 2-year horizon before differencing.
  // Growth QUALITY. roe_pct = P/B / P/E. Backward-looking, leverage-sensitive, and
  // inflated by a thin equity base — roe_thin_equity flags the last of those.
  roe_pct?: number | null;
  roe_thin_equity?: boolean;
  quality_quadrant?: string | null;
  normalized_growth_pct?: number | null;
  normalized_basis?: string | null;
  normalized_sign_change?: boolean | null;
  implied_annualised_1y_pct?: number | null;
  implied_annualised_2y_pct?: number | null;
  gap_vs_normalized_pp?: number | null;
  gap_vs_normalized_2y_pp?: number | null;
};

// The tab's SELF-TEST. Correlation between each name's current gap and how it has
// actually traded the session after results, from earnings_reactions.json. Explicitly
// NOT a backtest — the gap is measured today, the reactions run back to 2018 — so it
// can only refute the "gap is a signal" reading, never confirm it. Shipped regardless
// of whether it flatters the idea; `informative` is false when |r| sits inside the
// noise band or the band means are not ordered the way the theory requires.
export type GapCalibration = {
  names: number; events: number;
  r_gap_vs_full_reaction: number | null;
  r_gap_vs_recent_reaction: number | null;
  band_mean_rel_pct: Record<string, number | null>;
  band_n: Record<string, number>;
  bands_ordered_as_theory_predicts: boolean;
  informative: boolean;
  noise_threshold_r: number;
  verdict: string; note: string;
};

export type ExpectationGap = {
  calibration?: GapCalibration | null;
  rows: GapRow[]; names: number;
  median_gap_pp: number; median_implied_pct: number; median_delivered_pct: number;
  high_bar: number; in_line: number; low_bar: number;
  thresholds_pp: number[]; normalized_available?: number;
  roe_strong_pct?: number; growth_high_pct?: number;
  quadrant_counts?: Record<string, number>;
  note: string;
};

export const QUADRANT_STYLE: Record<string, string> = {
  'priced for growth · high return': 'bg-emerald-50 text-emerald-700 border-emerald-200',
  'priced for growth · low return': 'bg-amber-50 text-amber-700 border-amber-200',
  'priced for little · high return': 'bg-blue-50 text-blue-700 border-blue-200',
  'priced for little · low return': 'bg-slate-50 text-slate-500 border-slate-200',
};

export const BAND_STYLE: Record<GapRow['band'], string> = {
  'high bar': 'bg-rose-50 text-rose-600 border-rose-200',
  'in line': 'bg-slate-50 text-slate-500 border-slate-200',
  'low bar': 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

export type Flows = {
  available: boolean; as_of?: string | null; days?: number;
  recent?: { date: string; fii_net: number | null; dii_net: number | null;
             fii_idx_fut_net?: number | null; fii_idx_opt_net?: number | null }[];
  fii_5d_cr?: number | null; dii_5d_cr?: number | null;
  fii_20d_cr?: number | null; dii_20d_cr?: number | null;
  fii_streak_days?: number; regime?: string;
  cash_note?: string;
  sector_fpi?: Record<string, number> | null;
  sector_fpi_as_of?: string | null; sector_fpi_note?: string;
};

export type Row = {
  symbol: string; name: string; sector: string; weight: number | null;
  last: number | null; d1_pct: number | null; w1_pct: number | null; m6_pct: number | null; y1_pct: number | null;
  pos_52w: number | null; hi_52w?: number; lo_52w?: number;
  up_to_high_pct?: number | null; down_to_low_pct?: number | null; as_of: string | null;
  pe: number | null; fwd_pe: number | null; pb: number | null; div_yield?: number | null;
  // false = Yahoo threw and we have NO fundamentals, as distinct from a company that
  // simply has no P/E because it loses money.
  fundamentals_ok?: boolean;
  bars?: number; partial_history?: boolean; history_note?: string | null;
  // A 1-day move beyond ±20% in a Nifty 50 name is almost always an unadjusted
  // corporate action (bonus, split, demerger going ex), not performance.
  suspect_corporate_action?: boolean;
  verdict: Verdict | null; drivers?: Drivers | null;
  yahoo_symbol?: string | null; symbol_note?: string | null;
  rel_1w?: number | null; rel_6m?: number | null; rel_1y?: number | null;
  reaction?: Reaction | null;
  expectation?: Expectation | null;
  context?: Record<string, Ctx>;
  pe_history?: PeHistory | null;
  fii_holding?: FiiHolding | null;
  valuation_note?: ValuationNote | null;
};

export type IndexBlock = {
  last: number; d1_pct: number | null; w1_pct: number | null;
  m6_pct: number | null; y1_pct: number | null; as_of: string;
};

export type View = {
  fetched_at: number;
  index: IndexBlock | null;
  rows: Row[]; note: string; mechanism?: string[];
  flows?: Flows | null;
  earnings_vs_valuation?: EarningsVsValuation | null;
  expectation_gap?: ExpectationGap | null;
  pe_history_meta?: { as_of: string; source: string; names: number; note?: string } | null;
  fii_holding_meta?: { as_of: string; source: string; names: number; note?: string } | null;
  valuation_notes_meta?: { as_of: string; source: string; names: number; note?: string } | null;
  // Set when the scan came back too thin to cache — the numbers on screen are an
  // upstream failure, not a picture of the market.
  degraded?: string | null;
  drivers_meta?: { as_of: string; note: string } | null;
  reactions_meta?: { as_of: string; events: number; names: number; diverging: number } | null;
  expectation_meta?: { captured_at: string; source: string; snapshots: number;
                       median_implied_eps_growth_pct?: number } | null;
  index_read?: {
    weighted_pe: number | null; pe_coverage_pct: number; pe_band: number[];
    val_label: 'cheap' | 'fair' | 'mildly rich' | 'rich' | null;
    breadth: Record<string, number>;
    dma50: number | null; dma200: number | null; above50: boolean; above200: boolean;
    off_high_pct: number; trend_label: 'uptrend' | 'downtrend' | 'mixed';
    unvalued?: number; fundamentals_failed?: number;
    lean: string; why: string; note: string;
  } | null;
};

export const BIAS_STYLE: Record<Bias, string> = {
  positive: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  neutral: 'bg-slate-50 text-slate-500 border-slate-200',
  negative: 'bg-rose-50 text-rose-700 border-rose-200',
};

export const VERDICT_STYLE: Record<Verdict['label'], string> = {
  rich: 'bg-rose-50 text-rose-600 border-rose-200',
  'in-line': 'bg-slate-50 text-slate-500 border-slate-200',
  cheap: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

export const VAL_TONE: Record<string, string> = {
  cheap: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  fair: 'bg-blue-50 text-blue-700 border-blue-200',
  'mildly rich': 'bg-amber-50 text-amber-700 border-amber-200',
  rich: 'bg-rose-50 text-rose-600 border-rose-200',
};

export const LEAN_TONE = (lean: string) =>
  lean.startsWith('constructive but') ? 'bg-amber-50 text-amber-700 border-amber-200'
    : lean.startsWith('constructive') ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : lean.startsWith('cautious') ? 'bg-rose-50 text-rose-600 border-rose-200'
    : 'bg-slate-50 text-slate-600 border-slate-200';

export const card = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';

export const fmtPct = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`;

export const rupee = (v: number | null | undefined) =>
  v == null ? '—' : `₹${v.toLocaleString('en-IN')}`;

/** The deep link for a constituent's own page. One definition, so the table link and
 *  any future cross-reference can never point at different URLs. */
export const stockHref = (symbol: string) =>
  `/intel/nifty50/${encodeURIComponent(symbol)}`;

const ord = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], m = n % 100;
  return `${n}${s[(m - 20) % 10] ?? s[m] ?? s[0]}`;
};

/**
 * The one-line read under a number: where it sits among peers, and against the median.
 *
 * Phrasing rules that matter:
 *  - Rank 1 gets the metric's own superlative ("cheapest of the 11 financials"), which
 *    travels in the payload so a low P/E reads "cheapest" and a high yield reads
 *    "highest-yielding" without the UI hardcoding either.
 *  - A sector of one or two names is not a peer group, so we say "only N in sector" and
 *    lean on the index comparison instead of implying a ranking nobody can use.
 *  - A non-comparable multiple says why rather than showing a rank it doesn't have.
 */
export function readLine(c: Ctx | undefined): string | null {
  if (!c) return null;
  if (!c.comparable) {
    return `Not comparable — above 100×, which measures a near-zero earnings base rather than an expensive stock. Excluded from the medians.`;
  }
  const parts: string[] = [];
  if (c.sector_rank && c.sector_n >= 3) {
    parts.push(c.sector_rank === 1
      ? `${c.superlative} of the ${c.sector_n} ${c.sector} names`
      : `${ord(c.sector_rank)} ${c.superlative} of ${c.sector_n} in ${c.sector}`);
  } else if (c.sector_n > 0 && c.sector_n < 3) {
    parts.push(`only ${c.sector_n} ${c.sector} name${c.sector_n === 1 ? '' : 's'} in the index — no peer group`);
  }
  if (c.index_rank) {
    parts.push(`${ord(c.index_rank)} of ${c.index_n} in the Nifty 50`);
  }
  if (c.index_median != null) parts.push(`index median ${c.index_median}`);
  return parts.length ? `${parts.join(' · ')}` : null;
}

/**
 * "cheap against its own past" — only when pe_history.json has been built, and only as
 * far as its depth actually supports.
 *
 * The generated file turned out to be far shallower than the field name suggests:
 * `n_quarters` is 4 for most names but spans 3 years, i.e. one observation a year, not
 * a quarterly series. Two guards follow from that, because the failure mode here is a
 * sentence that sounds authoritative and isn't:
 *
 *   under 4 points  → nothing at all. A "median" of two numbers is their midpoint.
 *   under 8 points  → the median, with its own sample size attached, and NO percentile.
 *                     percentile_now off 4 observations can only be 0/25/50/75/100%, so
 *                     "this cheap only 25% of the time" is "1 of 4 readings" wearing a
 *                     statistic's clothing.
 */
export function historyLine(h: PeHistory | null | undefined): string | null {
  if (!h || h.median == null) return null;
  const n = h.n_quarters;
  if (n < 4) return null;
  const side = h.current < h.median ? 'below' : h.current > h.median ? 'above' : 'at';
  const span = h.years >= 1 ? ` over ${h.years} yr${h.years === 1 ? '' : 's'}` : '';
  if (n < 8) {
    return `${side} its own median of ${h.median} — but that median is only ${n} readings${span}`;
  }
  const pct = Math.round(h.percentile_now * 100);
  const rarity = pct <= 25 ? ` — this cheap only ${pct}% of the time`
    : pct >= 75 ? ` — this expensive only ${100 - pct}% of the time`
    : '';
  const label = h.years >= 2.5 ? `${Math.round(h.years)}-yr` : `${n}-reading`;
  return `${side} its own ${label} median of ${h.median}${rarity}`;
}

const _MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

/** "Jun 2026" -> a comparable month index, or null if the label isn't a month-year. */
function periodIndex(p: string): number | null {
  const m = /^([A-Za-z]{3})[a-z]*\s+(\d{4})$/.exec((p || '').trim());
  if (!m) return null;
  const mi = _MONTHS.indexOf(m[1].toLowerCase());
  return mi < 0 ? null : Number(m[2]) * 12 + mi;
}

/**
 * What the shareholding deltas actually measure, read off the period labels.
 *
 * The backfill's `change_pp` is "latest minus previous filing" and `change_4q_pp` is
 * "latest minus four back" — but the filings are not evenly spaced. In the current file
 * 45 of 48 names step a clean 3 months while 3 step 1-2, and change_4q_pp spans 9
 * months for every name (4 observations = 3 intervals), not the 12 the name implies.
 * So the UI quotes the real gap and the real end label instead of saying "last quarter"
 * and "four quarters" over data that means something else.
 */
export function holdingSpans(h: FiiHolding) {
  const t = h.trend ?? [];
  const last = t[t.length - 1], prev = t[t.length - 2], first = t[0];
  const between = (a?: { period: string }, b?: { period: string }) => {
    if (!a || !b) return null;
    const ia = periodIndex(a.period), ib = periodIndex(b.period);
    return ia == null || ib == null ? null : ib - ia;
  };
  return {
    prevLabel: prev?.period ?? null,
    gapMonths: between(prev, last),
    firstLabel: first?.period ?? null,
    longMonths: between(first, last),
    longChangePp: last && first ? Math.round((last.pct - first.pct) * 100) / 100 : null,
  };
}

export const months = (n: number | null) =>
  n == null ? '' : n === 1 ? '1 month' : `${n} months`;

export function Pct({ v, className = '' }: { v: number | null | undefined; className?: string }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <span className={`font-mono ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'} ${className}`}>
      {v >= 0 ? '+' : ''}{v}%
    </span>
  );
}

export function RangeBar({ v, width = 'w-16' }: { v: number | null; width?: string }) {
  // Position within the 52-week range: 0 = at low, 1 = at high.
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <div className={`relative h-1.5 ${width} rounded-full bg-slate-100`} title={`${Math.round(v * 100)}% of 52-week range`}>
      <div className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-indigo-500" style={{ left: `calc(${v * 100}% - 4px)` }} />
    </div>
  );
}
