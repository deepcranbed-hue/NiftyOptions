import React, { useCallback, useMemo, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import { card } from './nifty50Shared';

/**
 * Nifty50Outlook — 6M / 1Y / 2Y scenario arithmetic, sitting on top of what the index
 * has actually done.
 *
 * THIS TAB DOES NOT FORECAST, AND THE DESIGN ENFORCES THAT RATHER THAN DISCLAIMING IT.
 *
 *   · No probability is shown until the reader types one. The scenario table ships with
 *     every weight at zero and no expected value. The moment a weight is entered the
 *     expected level appears and is labelled with whose assumption it is.
 *
 *   · Rows the data cannot support are HIDDEN, not footnoted. `sufficient: false` means
 *     fewer than five independent windows — at 2018-onward history that is the whole 2Y
 *     row. A median computed from three independent draws printed beside a warning still
 *     reads as a distribution, so it is behind an explicit "show anyway" toggle.
 *
 *   · Every scenario's inputs carry per-field provenance. A level quoted from a
 *     brokerage and a growth rate invented by the script are visually different things.
 *
 * Convention: NO auto-run — nothing fetches until the button is pressed.
 */

type Horizon = '6M' | '1Y' | '2Y';

type HistRow = {
  label: Horizon; sessions: number;
  n_windows: number; n_independent: number; sufficient: boolean;
  median_pct: number; p10_pct: number; p25_pct: number; p75_pct: number; p90_pct: number;
  min_pct: number; max_pct: number; pct_positive: number; all_returns: number[];
  conditioned: { label: string; n_windows: number; n_independent: number; sufficient: boolean; median_pct: number; pct_positive: number } | null;
};

type ScenLevel = { level: number; ret_pct: number; eps: number; annualised_pct: number; history_percentile: number | null };

type Dist = { n: number; min: number; p10: number; p25: number; median: number; p75: number; p90: number; max: number; mean: number };

type Earnings = {
  available: boolean; panel_symbols: number; first_fy: number; last_fy: number;
  growth: { fy: number; aggregate_profit_cr: number; yoy_pct: number }[];
  growth_dist: Dist; growth_dist_ex_covid: Dist;
  recent_3y_pct: number[]; decelerating: boolean; note: string;
};

type PeDoc = {
  available: boolean; first: string; last: string; n: number; today: number;
  dist: Dist; dist_ex_covid: Dist | null;
  today_percentile: number; today_percentile_ex_covid: number | null;
  series: { d: string; pe: number }[]; anchor_note: string;
};

type Conditional = {
  tercile_cuts: number[]; today_bucket: string; verdict: string;
  horizons: { label: Horizon; any_sufficient: boolean; buckets: {
    bucket: string; n_windows: number; n_independent: number; sufficient: boolean;
    median_pct: number; pct_positive: number; min_pct: number; max_pct: number }[] }[];
};

type Scenario = {
  id: string; name: string; kind: 'mechanical' | 'published' | 'curated' | 'measured' | 'reference' | 'conditional'; source: string;
  measured?: { g_source: string; exit_pe_source: string; exit_pe_percentile: number; g_percentile?: number };
  narrative: string; invalidated_by: string;
  eps_growth_pct: number; exit_pe_used: number; exit_pe_label: string;
  quoted: string[]; assumed: string[];
  levels: Record<Horizon, ScenLevel>;
  published_vs_model?: {
    published_level: number; published_for: string; ret_from_spot_pct: number;
    implied_exit_pe: Record<Horizon, number>; note: string;
  };
};

type QRow = { period: string; yoy_pct: number; names: number; weight_pct: number; aggregate_pat_cr: number };

export type MarginCohort = {
  available: boolean;
  measure: string;
  ratio: string;
  panel: number;
  weight_pct: number | null;
  ttm_pct: number | null;
  ttm_period: string | null;
  ttm_yoy_pp: number | null;
  quarter_pct: number | null;
  quarter_yoy_pp: number | null;
  qoq_volatility_pp: number | null;
  ttm_volatility_pp: number | null;
  trend_readable_quarterly: boolean;
};

type AccelDoc = {
  as_of: string;
  hypothesis: { id: string; claim: string; status: string; why_it_matters: string;
                gap_pp: number | null; settles_on: string; next_observation: string };
  L1_evidence: {
    annual: { panel: number; growth: { fy: number; yoy_pct: number }[]; latest_fy_pct: number };
    quarterly: { available: boolean; balanced_panel_names: number; balanced: QRow[];
                 partial_latest: (QRow & { consistent_panel_path: QRow[] }) | null;
                 exit_rate_pct: number | null; decelerating: boolean;
                 turned_up?: boolean; path?: number[]; source?: string; note: string };
    // Two cohorts, never summed. Operating margin and financing margin are ratios over
    // different denominators, so a single blended index margin would have no referent.
    margin: {
      available: boolean;
      blended?: false;
      reason?: string;
      operating?: MarginCohort;
      financing?: MarginCohort;
      coverage?: { measured_pct: number; excluded_pct: number; reason: string };
      note?: string;
    };
  };
  L2_expectations: { sell_side_band_pct: number[]; sell_side_source: string;
                     market_implied_pct: number | null; market_implied_note: string;
                     snapshots_held: number; revisions_measurable: boolean; revisions_note: string };
  L4_price: { spot: number };
  channels: { id: string; direction: string; watch: string; observable: boolean;
              why: string; source_needed: string | null }[];
  channels_observable: number; note: string; caveats: string[];
};

type BtMethod = { n: number; mae_pp: number; median_err_pp: number; bias_pp: number;
                  direction_hit_pct: number | null; vs_null_pp: number | null; beats_null: boolean };
export type BtDoc = {
  as_of: string; asof_dates: number; first_asof: string | null; publication_lag_days: number;
  methods: Record<string, string>;
  summary: Record<string, {
    n_asof: number; n_independent: number; sufficient: boolean;
    methods: Record<string, BtMethod>; best_by_mae: string | null;
    rerating_check: { n: number; n_predicted_rerating: number;
                      reverted_as_predicted_pct: number | null;
                      median_predicted: number; median_realised: number; note: string } | null;
  }>;
  caveats: string[];
};

export type OutlookDoc = {
  as_of: string; model: string; note: string; caveats: string[];
  anchor: {
    spot: number; spot_date: string; trailing_pe: number; forward_pe: number;
    pe_coverage_pct: number; index_eps: number; implied_growth_pct: number;
    pe_label: string; expectation_captured_at: string | null; note: string;
  };
  history: {
    source: string; extended: boolean; first: string; last: string;
    sessions: number; years: number; horizons: HistRow[]; warning: string;
  };
  scenarios: Scenario[];
  earnings: Earnings; pe: PeDoc; conditional: Conditional;
};

const KIND_TONE: Record<Scenario['kind'], string> = {
  published: 'bg-sky-50 text-sky-700 border-sky-200',
  // A reference point is not a forecast and must not be coloured like one.
  reference: 'bg-slate-100 text-slate-600 border-slate-300',
  conditional: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  measured: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  curated: 'bg-violet-50 text-violet-600 border-violet-200',
  mechanical: 'bg-slate-50 text-slate-500 border-slate-200',
};

const fmt = (n: number) => Math.round(n).toLocaleString('en-IN');
const sign = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;

/** Where a scenario's return falls inside the realised distribution. Drawn only when
 *  the row has enough independent windows to be worth drawing. */
function DistStrip({ row, marks }: { row: HistRow; marks: { id: string; ret: number; tone: string }[] }) {
  const lo = Math.min(row.min_pct, ...marks.map((m) => m.ret));
  const hi = Math.max(row.max_pct, ...marks.map((m) => m.ret));
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * 100;
  return (
    <div className="relative h-11 mt-1">
      {/* p10-p90 body, p25-p75 core */}
      <div className="absolute top-3 h-2 rounded-full bg-slate-200"
        style={{ left: `${x(row.p10_pct)}%`, width: `${x(row.p90_pct) - x(row.p10_pct)}%` }} />
      <div className="absolute top-2.5 h-3 rounded-full bg-slate-300"
        style={{ left: `${x(row.p25_pct)}%`, width: `${x(row.p75_pct) - x(row.p25_pct)}%` }} />
      <div className="absolute top-1.5 w-0.5 h-5 bg-slate-600" style={{ left: `${x(row.median_pct)}%` }}
        title={`median ${sign(row.median_pct)}%`} />
      <div className="absolute top-0.5 w-px h-3 bg-slate-300" style={{ left: `${x(0)}%` }} />
      {marks.map((m) => (
        <div key={m.id} className={`absolute top-0 w-1 h-8 rounded-full ${m.tone}`}
          style={{ left: `${x(m.ret)}%` }} title={`${m.id}: ${sign(m.ret)}%`} />
      ))}
      <div className="absolute bottom-0 left-0 text-[8px] text-slate-400">{sign(lo)}%</div>
      <div className="absolute bottom-0 right-0 text-[8px] text-slate-400">{sign(hi)}%</div>
    </div>
  );
}

export function Nifty50Outlook() {
  const [doc, setDoc] = useState<OutlookDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [h, setH] = useState<Horizon>('1Y');
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [showThin, setShowThin] = useState(false);
  const [showCaveats, setShowCaveats] = useState(false);
  const [accel, setAccel] = useState<AccelDoc | null>(null);
  const [showChannels, setShowChannels] = useState(false);
  const [bt, setBt] = useState<BtDoc | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch('/api/nifty-outlook');
      const j = await r.json();
      if (j.success) setDoc(j.outlook);
      else setErr(j.detail || 'Failed to load the outlook');
      // The hypothesis tracker is a separate artifact and a separate failure: the
      // outlook must still render if it is missing.
      try {
        const ra = await fetch('/api/nifty-earnings-acceleration');
        const ja = await ra.json();
        if (ja.success) setAccel(ja.acceleration);
      } catch { /* absent tracker is not an error for this tab */ }
      try {
        const rb = await fetch('/api/nifty-outlook-backtest');
        const jb = await rb.json();
        if (jb.success) setBt(jb.backtest);
      } catch { /* absent backtest is not an error for this tab */ }
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  const row = doc?.history.horizons.find((x) => x.label === h) || null;
  const usable = !!row && (row.sufficient || showThin);

  // Weights are the READER'S. Nothing is pre-filled, and the expected value only
  // appears once they sum to something. An unweighted scenario table is a set of
  // conditional statements; a weighted one is a forecast, and whose it is matters.
  const totalW = useMemo(() => {
    let t = 0;
    Object.keys(weights).forEach((k) => {
      const v = Number(weights[k]);
      if (Number.isFinite(v)) t += v;
    });
    return t;
  }, [weights]);

  const expected = useMemo(() => {
    if (!doc || totalW <= 0) return null;
    let acc = 0;
    doc.scenarios.forEach((s) => { acc += (weights[s.id] || 0) * s.levels[h].level; });
    return acc / totalW;
  }, [doc, weights, totalW, h]);

  const marks = useMemo(() => (doc ? doc.scenarios.map((s) => ({
    id: s.name.split('—')[0].trim(),
    ret: s.levels[h].ret_pct,
    tone: s.kind === 'published' ? 'bg-sky-500' : s.kind === 'conditional' ? 'bg-emerald-500' : 'bg-slate-400',
  })) : []), [doc, h]);

  const toggle = (id: string) => setOpen((p) => {
    const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  return (
    <div className="space-y-4">
      <div className={card}>
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
            Outlook — scenarios, not a forecast {doc ? `(${doc.as_of})` : ''}
          </span>
          <button onClick={load} disabled={loading}
            className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
            title="Reads nifty_outlook.json — written by backend/quant/nifty_outlook.py. Nothing is computed on request.">
            {loading ? 'Loading…' : doc ? 'Reload' : 'Load outlook'}
          </button>
        </div>

        {err && (
          <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">{err}</div>
        )}

        {doc && (
          <div className="mt-3 space-y-3">
            <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50/60 px-3 py-2">
              <AlertTriangle className="w-3.5 h-3.5 text-rose-500 shrink-0 mt-0.5" />
              <p className="text-[10px] text-rose-800 leading-snug">
                <b>This is not a prediction of the Nifty.</b> This repo tested the inputs people forecast
                with and retired them: the daily macro→index regression died at R² = 0.036, high-and-rising
                crude fails to predict next-day weakness at p = 0.979, and crude beta flips sign by regime
                (+0.006 full sample vs −0.075 in 2026). What follows is arithmetic conditional on inputs
                nobody here can predict, shown against what the index has actually done.
              </p>
            </div>

            {/* anchor */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {[
                ['Spot', fmt(doc.anchor.spot), doc.anchor.spot_date],
                ['Trailing P/E', doc.anchor.trailing_pe.toFixed(1), `${doc.anchor.pe_label} · ${doc.anchor.pe_coverage_pct}% covered`],
                ['Index EPS', fmt(doc.anchor.index_eps), 'implied, not published'],
                ['Forward P/E', doc.anchor.forward_pe.toFixed(1), `prices +${doc.anchor.implied_growth_pct}% growth`],
                ['Horizon', h, h === '6M' ? '126 sessions' : h === '1Y' ? '252 sessions' : '504 sessions'],
              ].map(([k, v, sub]) => (
                <div key={k as string} className="rounded-xl border border-slate-200 px-3 py-2">
                  <div className="text-[9px] font-black uppercase text-slate-400">{k}</div>
                  <div className="text-lg font-black font-mono text-slate-800 leading-tight">{v}</div>
                  <div className="text-[9px] text-slate-400 leading-snug">{sub}</div>
                </div>
              ))}
            </div>

            <div className="flex gap-1 bg-slate-100 p-1 rounded-xl w-fit">
              {(['6M', '1Y', '2Y'] as Horizon[]).map((x) => (
                <button key={x} onClick={() => setH(x)}
                  className={`px-4 py-1.5 rounded-lg text-xs font-bold transition ${h === x ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                  {x}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ---- the open hypothesis: L1 evidence vs L2 expectation ---- */}
      {accel && (() => {
        const H = accel.hypothesis;
        const q = accel.L1_evidence.quarterly;
        const path = q.partial_latest?.consistent_panel_path?.length
          ? q.partial_latest.consistent_panel_path : q.balanced;
        const band = accel.L2_expectations.sell_side_band_pct;
        const m = accel.L1_evidence.margin;
        const op = m.operating;
        const fin = m.financing;
        return (
          <div className={card}>
            <div className="flex flex-wrap items-baseline gap-2 mb-1.5">
              <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                The open question — {H.id}
              </span>
              <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full border border-amber-300 bg-amber-50 text-amber-700">
                {H.status}
              </span>
              <span className="text-[9px] text-slate-400">next observation · {H.next_observation}</span>
            </div>
            <p className="text-[12px] font-bold text-slate-800 leading-snug">{H.claim}</p>
            <p className="text-[10px] text-slate-500 leading-snug mt-1">{H.why_it_matters}</p>

            {/* the four layers, as one line each */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-3 py-2">
                <div className="text-[9px] font-black uppercase text-emerald-700">L1 · Evidence</div>
                <div className="text-xl font-black font-mono text-emerald-800 leading-tight">
                  {sign(q.exit_rate_pct ?? 0)}%
                </div>
                <div className="text-[9px] text-slate-500 leading-snug">
                  latest quarter YoY, aggregate PAT
                </div>
              </div>
              <div className="rounded-xl border border-sky-200 bg-sky-50/50 px-3 py-2">
                <div className="text-[9px] font-black uppercase text-sky-700">L2 · Expectation</div>
                <div className="text-xl font-black font-mono text-sky-800 leading-tight">
                  {band[0]}–{band[1]}%
                </div>
                <div className="text-[9px] text-slate-500 leading-snug">assumed for FY27</div>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="text-[9px] font-black uppercase text-slate-400">L3 · Valuation</div>
                <div className="text-xl font-black font-mono text-slate-700 leading-tight">
                  {doc ? `${doc.pe.today}×` : '—'}
                </div>
                <div className="text-[9px] text-slate-500 leading-snug">
                  {doc ? `${doc.pe.today_percentile}th pctile of its own history` : ''}
                </div>
              </div>
              <div className="rounded-xl border border-rose-200 bg-rose-50/50 px-3 py-2">
                <div className="text-[9px] font-black uppercase text-rose-700">The gap</div>
                <div className="text-xl font-black font-mono text-rose-700 leading-tight">
                  {H.gap_pp}pp
                </div>
                <div className="text-[9px] text-slate-500 leading-snug">
                  evidence to expectation — this is the research question
                </div>
              </div>
            </div>

            {/* the quarterly path — the thing the annual number hides */}
            <div className="mt-3">
              <div className="text-[9px] font-black uppercase text-slate-400 mb-1">
                Aggregate quarterly PAT growth, YoY — the shape the annual figure hides
                {q.partial_latest && (
                  <span className="text-slate-300 normal-case font-normal">
                    {' '}· consistent {q.partial_latest.names}-name panel, {q.partial_latest.weight_pct}% of weight
                  </span>
                )}
              </div>
              <div className="flex items-end gap-2 h-16">
                {path.map((r) => {
                  const mx = Math.max(...path.map((x) => Math.abs(x.yoy_pct)), band[1]);
                  return (
                    <div key={r.period} className="flex-1 flex flex-col items-center justify-end h-full">
                      <span className="text-[9px] font-mono font-bold text-slate-600 leading-none mb-0.5">
                        {sign(r.yoy_pct)}%
                      </span>
                      <div className="w-full rounded-sm bg-emerald-400"
                        style={{ height: `${Math.max(3, (Math.abs(r.yoy_pct) / mx) * 100)}%` }}
                        title={`${r.period}: ₹${r.aggregate_pat_cr?.toLocaleString('en-IN')} cr`} />
                      <span className="text-[8px] text-slate-400 leading-none mt-0.5">
                        {r.period.slice(2, 7)}
                      </span>
                    </div>
                  );
                })}
                <div className="flex-1 flex flex-col items-center justify-end h-full opacity-60">
                  <span className="text-[9px] font-mono font-bold text-sky-700 leading-none mb-0.5">
                    +{band[0]}–{band[1]}%
                  </span>
                  <div className="w-full rounded-sm bg-sky-300 border-2 border-dashed border-sky-500"
                    style={{ height: `${(band[1] / Math.max(...path.map((x) => Math.abs(x.yoy_pct)), band[1])) * 100}%` }}
                    title="what FY27 is assumed to deliver" />
                  <span className="text-[8px] text-sky-600 leading-none mt-0.5">FY27e</span>
                </div>
              </div>
              <p className="text-[10px] text-slate-600 mt-1.5 leading-snug">
                Annual FY{String(accel.L1_evidence.annual.growth.slice(-1)[0].fy).slice(2)} growth was{' '}
                <b className="font-mono">{sign(accel.L1_evidence.annual.latest_fy_pct)}%</b> — but that is the
                average of a year that decelerated through itself. The exit rate is{' '}
                <b className="font-mono">{sign(q.exit_rate_pct ?? 0)}%</b> — the rate the
                index is actually running at, not the year's average.
                {q.turned_up && !q.decelerating ? (
                  <> That path <b>troughed and turned up</b>{q.path?.length ? (
                    <> ({q.path.slice(-4).map((x: number) => `${x > 0 ? '+' : ''}${x}%`).join(' → ')})</>
                  ) : null}, so the question is whether the turn holds, not whether a
                  collapse reverses.</>
                ) : (
                  <> The question is whether that becomes the 12-14% assumed for FY27.</>
                )}
                {m.available && op?.available && op.ttm_yoy_pp !== null && (
                  <> Operating margin (non-financials, {op.weight_pct}% of weight) is{' '}
                  <b className="font-mono">{op.ttm_pct}%</b> TTM,{' '}
                  <b className={op.ttm_yoy_pp < 0 ? 'text-rose-600' : 'text-emerald-700'}>
                    {sign(op.ttm_yoy_pp)}pp YoY</b>
                  {fin?.available && fin.ttm_yoy_pp !== null && (
                    <>; financing margin (lenders, {fin.weight_pct}%) is{' '}
                    <b className="font-mono">{fin.ttm_pct}%</b> TTM,{' '}
                    <b className={fin.ttm_yoy_pp < 0 ? 'text-rose-600' : 'text-emerald-700'}>
                      {sign(fin.ttm_yoy_pp)}pp YoY</b></>
                  )}. Reported separately, never summed — the two are ratios over different
                  denominators.</>
                )}
                {m.available === false && m.reason && (
                  <> Margin channel unavailable: {m.reason}</>
                )}
              </p>
              <p className="text-[9px] text-slate-400 mt-1 leading-snug">{q.note}</p>
              {m.coverage && (
                <p className="text-[9px] text-slate-400 mt-1 leading-snug">
                  Margin coverage {m.coverage.measured_pct}% of index weight;{' '}
                  {m.coverage.excluded_pct}% excluded — {m.coverage.reason}.
                </p>
              )}
            </div>

            {/* what would settle it */}
            <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2">
              <p className="text-[10px] text-slate-700 leading-snug">
                <b className="text-[9px] uppercase text-slate-400 mr-1">Settles on</b>{H.settles_on}
              </p>
              {!accel.L2_expectations.revisions_measurable && (
                <p className="text-[10px] text-amber-800 leading-snug mt-1">
                  <b className="text-[9px] uppercase text-amber-600 mr-1">Blocked</b>
                  {accel.L2_expectations.revisions_note}
                </p>
              )}
            </div>

            <button onClick={() => setShowChannels((v) => !v)}
              className="flex items-center gap-1.5 mt-2.5 text-[10px] font-black uppercase tracking-wide text-slate-400 hover:text-slate-600">
              {showChannels ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              Evidence channels — {accel.channels_observable} of {accel.channels.length} observable today
            </button>
            {showChannels && (
              <div className="mt-2 space-y-1">
                {accel.channels.map((c) => (
                  <div key={c.id} className="flex items-start gap-2">
                    <span className={`text-[9px] font-black px-1.5 py-0.5 rounded-full border shrink-0 ${
                      c.observable ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                   : 'bg-slate-50 text-slate-400 border-slate-200'}`}>
                      {c.observable ? 'wired' : 'gap'}
                    </span>
                    <span className={`text-[9px] font-black px-1.5 py-0.5 rounded-full border shrink-0 ${
                      c.direction === 'for' ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                      : c.direction === 'against' ? 'bg-rose-50 text-rose-600 border-rose-200'
                      : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                      {c.direction}
                    </span>
                    <div className="min-w-0">
                      <div className="text-[10px] text-slate-700 leading-snug">{c.watch}</div>
                      <div className="text-[9px] text-slate-400 leading-snug">
                        {c.why}{c.source_needed ? ` · needs: ${c.source_needed}` : ''}
                      </div>
                    </div>
                  </div>
                ))}
                <p className="text-[9px] text-slate-400 leading-snug pt-1">{accel.note}</p>
              </div>
            )}
          </div>
        );
      })()}

      {doc && doc.earnings?.available && doc.pe?.available && (
        <div className={card}>
          <div className="flex flex-wrap items-baseline gap-2 mb-2">
            <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
              Measured inputs — where the scenario numbers come from
            </span>
            <span className="text-[9px] text-slate-400">
              {doc.earnings.panel_symbols} names with a complete FY{doc.earnings.first_fy}–FY{doc.earnings.last_fy} series
            </span>
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            {/* aggregate earnings */}
            <div>
              <div className="text-[9px] font-black uppercase text-slate-400 mb-1">
                Aggregate Nifty-50 net profit, year on year
              </div>
              <div className="flex items-end gap-1 h-16">
                {doc.earnings.growth.map((g) => {
                  const mx = Math.max(...doc.earnings.growth.map((x) => Math.abs(x.yoy_pct)));
                  return (
                    <div key={g.fy} className="flex-1 flex flex-col items-center justify-end h-full">
                      <span className={`text-[8px] font-mono leading-none mb-0.5 ${g.yoy_pct >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                        {sign(g.yoy_pct)}
                      </span>
                      <div className={`w-full rounded-sm ${g.yoy_pct >= 0 ? 'bg-emerald-400' : 'bg-rose-400'}`}
                        style={{ height: `${Math.max(2, (Math.abs(g.yoy_pct) / mx) * 100)}%` }}
                        title={`FY${g.fy}: ₹${g.aggregate_profit_cr.toLocaleString('en-IN')} cr`} />
                      <span className="text-[8px] text-slate-400 leading-none mt-0.5">{String(g.fy).slice(2)}</span>
                    </div>
                  );
                })}
              </div>
              <p className="text-[10px] text-slate-600 mt-1.5 leading-snug">
                median <b className="font-mono">{doc.earnings.growth_dist.median}%</b> ·
                ex-COVID <b className="font-mono">{doc.earnings.growth_dist_ex_covid.median}%</b>{' '}
                <span className="text-slate-400">
                  (n={doc.earnings.growth_dist.n} and {doc.earnings.growth_dist_ex_covid.n})
                </span>
              </p>
              {/* This flag is computed on ANNUAL growth (index_valuation), which is a
                  different series from the quarterly path above. Annual can still be
                  monotonically decelerating while the latest QUARTER has turned — FY26
                  is the average of a year that fell through itself. Labelled, because
                  two unlabelled "decelerating" claims from different series next to a
                  turned-up path is how a reader concludes the app contradicts itself. */}
              {doc.earnings.decelerating && (
                <p className="text-[10px] text-amber-700 leading-snug">
                  Monotonically decelerating on ANNUAL growth:{' '}
                  {doc.earnings.recent_3y_pct.map((x) => `${x}%`).join(' → ')}. The quarterly
                  path above is the fresher read and is measured separately.
                </p>
              )}
            </div>

            {/* reconstructed P/E */}
            <div>
              <div className="text-[9px] font-black uppercase text-slate-400 mb-1">
                Index trailing P/E, reconstructed — {doc.pe.first} → {doc.pe.last}
              </div>
              <div className="relative h-14">
                {(() => {
                  const ps = doc.pe.series;
                  const lo = Math.min(...ps.map((x) => x.pe), doc.pe.today);
                  const hi = Math.max(...ps.map((x) => x.pe));
                  const pts = ps.map((x, i) => `${(i / (ps.length - 1)) * 100},${100 - ((x.pe - lo) / (hi - lo)) * 100}`).join(' ');
                  return (
                    <>
                      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
                        <polyline points={pts} fill="none" stroke="#64748b" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
                        <line x1="0" y1={100 - ((doc.pe.dist.median - lo) / (hi - lo)) * 100}
                          x2="100" y2={100 - ((doc.pe.dist.median - lo) / (hi - lo)) * 100}
                          stroke="#cbd5e1" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
                      </svg>
                      <span className="absolute top-0 right-0 text-[8px] text-slate-400">{hi.toFixed(0)}×</span>
                      <span className="absolute bottom-0 right-0 text-[8px] text-slate-400">{lo.toFixed(0)}×</span>
                    </>
                  );
                })()}
              </div>
              <p className="text-[10px] text-slate-600 mt-1 leading-snug">
                today <b className="font-mono">{doc.pe.today}×</b> — the{' '}
                <b className="text-amber-700">{doc.pe.today_percentile}th percentile</b> of its own history
                {doc.pe.today_percentile_ex_covid !== null && <> ({doc.pe.today_percentile_ex_covid}th ex-COVID)</>}.
                Median {doc.pe.dist.median}× · range {doc.pe.dist.min}–{doc.pe.dist.max}×.
              </p>
            </div>
          </div>

          <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-[10px] text-amber-900 leading-snug">
              <b>Read the percentile carefully.</b> Today's multiple looks cheap against a sample that
              began expensive and de-rated, so <b>every scenario built on a P/E percentile implies a
              re-rating</b>. Three biases push the same way: constant constituents (today's winners
              back-cast to 2018 overstate past earnings, understating past P/E), the COVID denominator,
              and mean reversion — which this method assumes and cannot test.{' '}
              {doc.conditional && <>The test is the tercile table below and it does not run: the cheap
              bucket has {doc.conditional.horizons.find((x) => x.label === '1Y')?.buckets.find((b) => b.bucket === 'cheap')?.n_independent} independent
              1Y window and none at 2Y.</>}
            </p>
          </div>
          <p className="text-[9px] text-slate-400 mt-1.5 leading-snug">{doc.pe.anchor_note}</p>
        </div>
      )}

      {doc && row && (
        <>
          {/* ---- layer 1: what has actually happened ---- */}
          <div className={card}>
            <div className="flex flex-wrap items-baseline gap-2 mb-1">
              <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                What the Nifty has actually done over {h}
              </span>
              <span className="text-[9px] text-slate-400">
                {doc.history.source} · {doc.history.first} → {doc.history.last} · {doc.history.years}y
              </span>
            </div>

            {!row.sufficient && !showThin ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3">
                <p className="text-[11px] text-amber-900 leading-snug">
                  <b>Hidden — not enough independent history.</b> {h} windows overlap almost completely
                  at this sample depth: {row.n_windows.toLocaleString()} windows but only{' '}
                  <b className="font-mono">{row.n_independent}</b> independent ones. A median from{' '}
                  {row.n_independent} draws describes one path through 2018–2026, and printing it with a
                  caveat underneath does not stop it being read as a distribution.
                </p>
                <button onClick={() => setShowThin(true)}
                  className="mt-2 text-[10px] font-bold text-amber-800 underline">
                  Show it anyway — I understand it is one path, not a distribution
                </button>
              </div>
            ) : (
              <>
                {!row.sufficient && (
                  <p className="text-[10px] font-bold text-amber-700 mb-1">
                    Shown on request · only {row.n_independent} independent windows · one path, not a distribution
                  </p>
                )}
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
                  {[
                    ['Median', sign(row.median_pct) + '%'],
                    ['p10', sign(row.p10_pct) + '%'],
                    ['p90', sign(row.p90_pct) + '%'],
                    ['Worst', sign(row.min_pct) + '%'],
                    ['Best', sign(row.max_pct) + '%'],
                    ['Positive', row.pct_positive + '%'],
                  ].map(([k, v]) => (
                    <div key={k} className="rounded-lg border border-slate-200 px-2 py-1.5">
                      <div className="text-[9px] font-black uppercase text-slate-400">{k}</div>
                      <div className="text-sm font-black font-mono text-slate-700">{v}</div>
                    </div>
                  ))}
                </div>
                <DistStrip row={row} marks={marks} />
                <p className="text-[9px] text-slate-400 leading-snug">
                  Grey band p10–p90, darker core p25–p75, dark line the median; coloured pins are the
                  scenarios below. <b>{row.n_windows.toLocaleString()} overlapping windows,{' '}
                  {row.n_independent} independent.</b> Price return only.
                </p>
                {row.conditioned && (
                  row.conditioned.sufficient || showThin ? (
                    <p className="text-[10px] text-slate-600 mt-1.5 leading-snug">
                      <b className="text-[9px] uppercase text-slate-400 mr-1">Conditioned</b>
                      {row.conditioned.label}: median <b>{sign(row.conditioned.median_pct)}%</b>,{' '}
                      {row.conditioned.pct_positive}% positive — but only{' '}
                      <b className="font-mono">{row.conditioned.n_independent}</b> independent window
                      {row.conditioned.n_independent === 1 ? '' : 's'}
                      {!row.conditioned.sufficient && <b className="text-amber-700"> · below the evidence floor</b>}.
                    </p>
                  ) : (
                    <p className="text-[10px] text-slate-400 mt-1.5">
                      Drawdown-conditioned row hidden — {row.conditioned.n_independent} independent window
                      {row.conditioned.n_independent === 1 ? '' : 's'}.
                    </p>
                  )
                )}
              </>
            )}
            <p className="text-[9px] text-slate-400 mt-2 leading-snug">{doc.history.warning}</p>
          </div>

          {/* ---- layer 2: scenarios ---- */}
          <div className={card}>
            <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
              <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                Scenarios at {h} — level = EPS × (1+g)<sup>T</sup> × exit P/E
              </span>
              {expected === null ? (
                <span className="text-[10px] text-slate-400">
                  weights are yours — enter any and an expected level appears
                </span>
              ) : (
                <span className="text-[10px] text-slate-600">
                  Your weighted expectation:{' '}
                  <b className="font-mono text-slate-900">{fmt(expected)}</b>{' '}
                  <span className="text-slate-400">
                    ({sign((expected / doc.anchor.spot - 1) * 100)}% · weights sum {totalW}
                    {totalW !== 100 ? ', normalised' : ''})
                  </span>
                </span>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-[9px] uppercase font-black text-slate-400 border-b border-slate-200">
                    <th className="py-1.5 pr-2 text-left">Scenario</th>
                    <th className="py-1.5 px-2 text-right">EPS g</th>
                    <th className="py-1.5 px-2 text-right">Exit P/E</th>
                    <th className="py-1.5 px-2 text-right">Level</th>
                    <th className="py-1.5 px-2 text-right">vs spot</th>
                    <th className="py-1.5 px-2 text-right">p.a.</th>
                    <th className="py-1.5 px-2 text-right">In history</th>
                    <th className="py-1.5 pl-2 text-right">Your weight</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.scenarios.map((s) => {
                    const L = s.levels[h];
                    const isOpen = open.has(s.id);
                    return (
                      <React.Fragment key={s.id}>
                        <tr className="border-b border-slate-50 hover:bg-slate-50/60">
                          <td className="py-1.5 pr-2 cursor-pointer" onClick={() => toggle(s.id)}>
                            <div className="flex items-center gap-1">
                              {isOpen ? <ChevronDown className="w-3 h-3 text-slate-400" /> : <ChevronRight className="w-3 h-3 text-slate-300" />}
                              <span className="font-bold text-slate-800">{s.name}</span>
                              <span className={`text-[8px] font-black px-1.5 py-0.5 rounded-full border ${KIND_TONE[s.kind]}`}>
                                {s.kind}
                              </span>
                            </div>
                            <div className="text-[9px] text-slate-400 leading-none ml-4">{s.source}</div>
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">{s.eps_growth_pct}%</td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                            {s.exit_pe_used.toFixed(1)}
                            <span className="text-slate-300 text-[9px]"> {s.exit_pe_label}</span>
                            {s.measured && (
                              <div className="text-[8px] text-slate-400 leading-none"
                                title="percentile of the index's own reconstructed P/E, 2018-2026">
                                {s.measured.exit_pe_percentile}th pctile
                              </div>
                            )}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono font-black text-slate-800">{fmt(L.level)}</td>
                          <td className={`py-1.5 px-2 text-right font-mono font-bold ${L.ret_pct >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                            {sign(L.ret_pct)}%
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">{sign(L.annualised_pct)}%</td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                            {L.history_percentile === null || !usable ? '—' : `${L.history_percentile}th`}
                          </td>
                          <td className="py-1.5 pl-2 text-right">
                            <input type="number" min={0} max={100} step={5}
                              value={weights[s.id] ?? ''} placeholder="0"
                              onChange={(e) => setWeights((w) => ({ ...w, [s.id]: Number(e.target.value) }))}
                              className="w-14 rounded-lg border border-slate-300 px-1.5 py-0.5 text-[11px] font-mono text-right" />
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-slate-100 bg-slate-50/40">
                            <td colSpan={8} className="py-2.5 px-3 space-y-1.5">
                              <p className="text-[11px] text-slate-700 leading-snug">{s.narrative}</p>
                              <p className="text-[10px] text-slate-600 leading-snug">
                                <b className="text-[9px] uppercase text-slate-400 mr-1">Invalidated by</b>
                                {s.invalidated_by}
                              </p>
                              {s.measured && (s.kind === 'reference' || s.kind === 'conditional') && (
                                <p className="text-[10px] text-emerald-800 leading-snug">
                                  <b className="text-[9px] uppercase text-emerald-600 mr-1">Measured</b>
                                  growth from {s.measured.g_source}; exit multiple from{' '}
                                  {s.measured.exit_pe_source} ({s.measured.exit_pe_percentile}th percentile).
                                </p>
                              )}
                              {s.measured && s.kind === 'published' && s.measured.g_percentile !== undefined && (
                                <p className="text-[10px] text-sky-800 leading-snug">
                                  <b className="text-[9px] uppercase text-sky-600 mr-1">In our units</b>
                                  their exit multiple is the <b>{s.measured.exit_pe_percentile}th percentile</b>{' '}
                                  of the index's own 2018–2026 P/E, and their growth rate the{' '}
                                  <b>{s.measured.g_percentile}th percentile</b> of eight observed fiscal years.
                                </p>
                              )}
                              <div className="flex flex-wrap gap-x-4 gap-y-1">
                                {!!s.quoted?.length && (
                                  <p className="text-[10px] text-sky-800 leading-snug">
                                    <b className="text-[9px] uppercase text-sky-600 mr-1">Quoted</b>
                                    {s.quoted.join(' · ')}
                                  </p>
                                )}
                                {!!s.assumed?.length && (
                                  <p className="text-[10px] text-amber-800 leading-snug">
                                    <b className="text-[9px] uppercase text-amber-600 mr-1">Assumed here</b>
                                    {s.assumed.join(' · ')}
                                  </p>
                                )}
                              </div>
                              {s.published_vs_model && (
                                <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-2">
                                  <p className="text-[10px] text-slate-700 leading-snug">
                                    Published <b className="font-mono">{fmt(s.published_vs_model.published_level)}</b>{' '}
                                    for {s.published_vs_model.published_for} ({sign(s.published_vs_model.ret_from_spot_pct)}%).
                                    Under this model's convention that level needs an exit P/E of{' '}
                                    <b className="font-mono">{s.published_vs_model.implied_exit_pe['6M']}</b> at 6M,{' '}
                                    <b className="font-mono">{s.published_vs_model.implied_exit_pe['1Y']}</b> at 1Y,{' '}
                                    <b className="font-mono">{s.published_vs_model.implied_exit_pe['2Y']}</b> at 2Y.
                                  </p>
                                  <p className="text-[9px] text-slate-400 mt-1 leading-snug">{s.published_vs_model.note}</p>
                                </div>
                              )}
                              <p className="text-[9px] text-slate-400">
                                EPS at {h}: {fmt(L.eps)} (today {fmt(doc.anchor.index_eps)})
                              </p>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {expected !== null && (
              <p className="text-[10px] text-slate-500 mt-2 leading-snug">
                That number is <b>your</b> forecast, not this repo's — it is your weights applied to
                arithmetic. Nothing here estimated a probability, and the weights above started empty
                for that reason.
              </p>
            )}
            <p className="text-[9px] text-slate-400 mt-1.5 leading-snug">{doc.model}</p>
          </div>

          {/* ---- has any of this ever worked? ---- */}
          {bt && bt.summary[h] && (() => {
            const S = bt.summary[h];
            const rc = S.rerating_check;
            // Explicit cast: this project ships no @types/react and Object.entries
            // degrades to unknown[] under its tsconfig.
            const order = (Object.entries(S.methods) as [string, BtMethod][])
              .sort((a, b) => a[1].mae_pp - b[1].mae_pp);
            const fundamentalsBeat = order.some(([k, v]) =>
              (k.startsWith('M1') || k.startsWith('M2')) ? v.beats_null : false);
            return (
              <div className={card}>
                <div className="flex flex-wrap items-baseline gap-2 mb-1.5">
                  <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                    Has this method ever worked? — walk-forward at {h}
                  </span>
                  <span className="text-[9px] text-slate-400">
                    {S.n_asof} quarterly as-of dates from {bt.first_asof}, {S.n_independent} independent
                  </span>
                  {!S.sufficient && (
                    <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full border border-amber-300 bg-amber-50 text-amber-700">
                      below evidence floor
                    </span>
                  )}
                </div>
                <p className={`text-[11px] leading-snug ${fundamentalsBeat ? 'text-slate-700' : 'text-rose-800 font-bold'}`}>
                  {fundamentalsBeat
                    ? 'At least one fundamental method beat the no-change null at this horizon.'
                    : 'Neither fundamental method beats "assume no change" at this horizon. The scenario table above is arithmetic, and this is what that arithmetic has been worth.'}
                </p>
                <div className="overflow-x-auto mt-2">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-[9px] uppercase font-black text-slate-400 border-b border-slate-200">
                        <th className="py-1 pr-2 text-left">Method</th>
                        <th className="py-1 px-2 text-right">MAE</th>
                        <th className="py-1 px-2 text-right">Bias</th>
                        <th className="py-1 px-2 text-right">Direction</th>
                        <th className="py-1 pl-2 text-right">vs null</th>
                      </tr>
                    </thead>
                    <tbody>
                      {order.map(([k, v]) => (
                        <tr key={k} className="border-b border-slate-50">
                          <td className="py-1 pr-2">
                            <span className="font-bold text-slate-700">{k.split('_')[0]}</span>{' '}
                            <span className="text-slate-500">{bt.methods[k]}</span>
                          </td>
                          <td className="py-1 px-2 text-right font-mono text-slate-700">{v.mae_pp}pp</td>
                          <td className={`py-1 px-2 text-right font-mono ${v.bias_pp >= 0 ? 'text-amber-700' : 'text-slate-500'}`}>
                            {sign(v.bias_pp)}
                          </td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">
                            {v.direction_hit_pct === null ? 'n/a' : `${v.direction_hit_pct}%`}
                          </td>
                          <td className={`py-1 pl-2 text-right font-mono font-bold ${v.vs_null_pp !== null && v.vs_null_pp > 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                            {v.vs_null_pp === null ? '—' : `${sign(v.vs_null_pp)}pp`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {rc && (
                  <div className="mt-2.5 rounded-xl border border-rose-200 bg-rose-50/50 px-3 py-2">
                    <p className="text-[11px] text-rose-900 leading-snug">
                      <b>The multiple did not revert.</b> The reference rows above assume it moves toward its
                      median — <b className="font-mono">{rc.median_predicted}×</b>. Across these as-of dates it
                      actually moved <b className="font-mono">{rc.median_realised}×</b>. Of{' '}
                      <b>{rc.n_predicted_rerating}</b> dates with the multiple <i>below</i> its own median, it rose
                      over the following {h} in only <b>{rc.reverted_as_predicted_pct}%</b>.
                    </p>
                    <p className="text-[10px] text-rose-800 leading-snug mt-1">
                      This reconciles with the cheap-tercile forward returns rather than contradicting them:
                      those windows made money through <b>earnings outrunning a still-compressing multiple</b>,
                      not through re-rating. Which is the case for treating the earnings question above as the
                      whole question.
                    </p>
                  </div>
                )}
                <p className="text-[9px] text-slate-400 mt-2 leading-snug">
                  MAE is mean absolute error in percentage points of the realised return; bias &gt; 0 means the
                  method projected more than the index delivered. The null itself is biased{' '}
                  {sign(S.methods['M3_no_change']?.bias_pp ?? 0)}pp in this sample, so beating it here partly
                  means being bullish enough — the whole window is one regime.
                </p>
              </div>
            );
          })()}

          <div className={card}>
            <button onClick={() => setShowCaveats((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-wide text-slate-400 hover:text-slate-600">
              {showCaveats ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              What would make every number on this tab wrong ({doc.caveats.length})
            </button>
            {showCaveats && (
              <ul className="mt-2 space-y-1">
                {doc.caveats.map((c, i) => (
                  <li key={i} className="text-[10px] text-slate-600 leading-snug flex gap-1.5">
                    <span className="text-slate-300">·</span><span>{c}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[10px] text-slate-500 mt-2 leading-snug">{doc.note}</p>
          </div>
        </>
      )}

      {!doc && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-indigo-600">Load outlook</span> to see the scenario grid.
          </p>
        </div>
      )}
    </div>
  );
}
