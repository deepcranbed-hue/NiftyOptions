import React, { useCallback, useMemo, useState } from 'react';
import { PieChart, RefreshCw, ArrowUpDown, ChevronDown, ChevronRight, HelpCircle } from 'lucide-react';

/**
 * Nifty50Panel — index-constituent scan: 1D/1W/6M/1Y returns + a categorical
 * "priced rich / in-line / cheap vs sector peers" read.
 *
 * Data: /api/nifty50-view (computed server-side from yfinance ON REQUEST,
 * cached 30 min; force=true bypasses). Convention (MarketStateView /
 * AIInfraThemePanel / SectorViewPanel): NO auto-run — nothing fetches or
 * computes until the user presses Run. The verdict is a cross-sectional
 * heuristic (vs sector-median P/E, P/B fallback), NOT a fair-value model.
 */

type Verdict = {
  label: 'rich' | 'in-line' | 'cheap'; metric: string; value?: number;
  vs_median_pct: number; sector_median: number; basis: string; reversion_pct?: number;
};
type Drivers = {
  position: string; tailwinds: string[]; headwinds: string[];
  latest_quarter?: { period: string; as_of: string; points: string[] };
  recent_change?: { as_of: string; points: string[]; verdict: string };
};
// MEASURED, not judged: how the stock actually traded the session after results,
// from earnings_reactions.json (announcement days picked by VOLUME only, so the
// finding can't be circular). rel = versus NIFTY; sect_rel = versus its sector index.
type Bias = 'positive' | 'neutral' | 'negative';
type Reaction = {
  n_events: number;
  full_mean_r1d_pct: number; full_mean_rel1d_pct: number;
  full_positive_share: number; full_bias: Bias;
  recent_n: number; recent_mean_r1d_pct: number; recent_mean_rel1d_pct: number;
  recent_mean_sect_rel1d_pct?: number | null;
  recent_positive_share: number; recent_bias: Bias;
  diverges?: boolean;
};

const BIAS_STYLE: Record<Bias, string> = {
  positive: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  neutral: 'bg-slate-50 text-slate-500 border-slate-200',
  negative: 'bg-rose-50 text-rose-700 border-rose-200',
};

type Row = {
  symbol: string; name: string; sector: string; weight: number | null;
  last: number | null; d1_pct: number | null; w1_pct: number | null; m6_pct: number | null; y1_pct: number | null;
  pos_52w: number | null; hi_52w?: number; lo_52w?: number;
  up_to_high_pct?: number | null; down_to_low_pct?: number | null; as_of: string | null;
  pe: number | null; fwd_pe: number | null; pb: number | null; div_yield?: number | null;
  verdict: Verdict | null; drivers?: Drivers | null;
  yahoo_symbol?: string | null; symbol_note?: string | null;
  rel_1w?: number | null; rel_6m?: number | null; rel_1y?: number | null;
  reaction?: Reaction | null;
};
type View = {
  fetched_at: number;
  index: { last: number; d1_pct: number | null; w1_pct: number | null; m6_pct: number | null; y1_pct: number | null; as_of: string } | null;
  rows: Row[]; note: string; mechanism?: string[];
  drivers_meta?: { as_of: string; note: string } | null;
  reactions_meta?: { as_of: string; events: number; names: number; diverging: number } | null;
  index_read?: {
    weighted_pe: number | null; pe_coverage_pct: number; pe_band: number[];
    val_label: 'cheap' | 'fair' | 'mildly rich' | 'rich' | null;
    breadth: Record<string, number>;
    dma50: number | null; dma200: number | null; above50: boolean; above200: boolean;
    off_high_pct: number; trend_label: 'uptrend' | 'downtrend' | 'mixed';
    lean: string; why: string; note: string;
  } | null;
};

type Scenario = { trigger: string; nifty_pct: number[]; horizon: string; anchor: string };
type Factor = {
  id: string; name: string; direction: 'tailwind' | 'headwind' | 'mixed' | 'neutral';
  fragile?: boolean; status: string; transmission: string;
  facts: { date: string; note: string; source: string }[];
  watch: string[]; scenarios: Scenario[];
};
type FactorsDoc = {
  as_of: string; note: string;
  expectation: { as_of: string; today: string; week: string; derived_from: string };
  factors: Factor[];
};

type TodayDriver = { rank: number; title: string; role: string; detail: string; source: string };
type TodayBrief = {
  as_of: string; headline: string; structure: string;
  drivers: TodayDriver[];
  sector_math: { weights: { sector: string; weight_pct: string }[]; rule: string };
  monitor: { rank: number; item: string }[];
  note: string;
};

const ROLE_TONE: Record<string, string> = {
  'primary drag': 'bg-rose-50 text-rose-600 border-rose-200',
  'background risk': 'bg-amber-50 text-amber-700 border-amber-200',
  arithmetic: 'bg-slate-50 text-slate-600 border-slate-200',
  cushion: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  support: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

const DIR_TONE: Record<Factor['direction'], string> = {
  tailwind: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  headwind: 'bg-rose-50 text-rose-600 border-rose-200',
  mixed: 'bg-amber-50 text-amber-700 border-amber-200',
  neutral: 'bg-slate-50 text-slate-500 border-slate-200',
};

const VAL_TONE: Record<string, string> = {
  cheap: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  fair: 'bg-blue-50 text-blue-700 border-blue-200',
  'mildly rich': 'bg-amber-50 text-amber-700 border-amber-200',
  rich: 'bg-rose-50 text-rose-600 border-rose-200',
};
const LEAN_TONE = (lean: string) =>
  lean.startsWith('constructive but') ? 'bg-amber-50 text-amber-700 border-amber-200'
    : lean.startsWith('constructive') ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : lean.startsWith('cautious') ? 'bg-rose-50 text-rose-600 border-rose-200'
    : 'bg-slate-50 text-slate-600 border-slate-200';

const card = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';
const VERDICT_STYLE: Record<Verdict['label'], string> = {
  rich: 'bg-rose-50 text-rose-600 border-rose-200',
  'in-line': 'bg-slate-50 text-slate-500 border-slate-200',
  cheap: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

type SortKey = 'weight' | 'd1_pct' | 'w1_pct' | 'm6_pct' | 'y1_pct' | 'pe' | 'vs_median' | 'rel_6m';

const fmtPct = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`;

function Pct({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <span className={`font-mono ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
      {v >= 0 ? '+' : ''}{v}%
    </span>
  );
}

function RangeBar({ v }: { v: number | null }) {
  // Position within the 52-week range: 0 = at low, 1 = at high.
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <div className="relative h-1.5 w-16 rounded-full bg-slate-100" title={`${Math.round(v * 100)}% of 52-week range`}>
      <div className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-indigo-500" style={{ left: `calc(${v * 100}% - 4px)` }} />
    </div>
  );
}

export function Nifty50Panel() {
  const [view, setView] = useState<View | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cached, setCached] = useState(false);
  const [sectorFilter, setSectorFilter] = useState<string>('all');
  const [verdictFilter, setVerdictFilter] = useState<string>('all');
  const [sortKey, setSortKey] = useState<SortKey>('weight');
  const [sortAsc, setSortAsc] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showMechanism, setShowMechanism] = useState(false);
  const [tab, setTab] = useState<'today' | 'factors' | 'stocks'>('today');
  const [today, setToday] = useState<TodayBrief | null>(null);
  const [todayLoading, setTodayLoading] = useState(false);
  const [factors, setFactors] = useState<FactorsDoc | null>(null);
  const [factorsLoading, setFactorsLoading] = useState(false);
  const [expandedFactors, setExpandedFactors] = useState<Set<string>>(new Set());
  const [scenarioKey, setScenarioKey] = useState<string>('');

  // Factors are a plain file read (curated nifty_factors.json) — still button-gated.
  const loadFactors = useCallback(async () => {
    setFactorsLoading(true);
    try {
      const r = await fetch('/api/nifty-factors');
      const j = await r.json();
      if (j.success) setFactors(j.factors);
      else setErr(j.detail || 'Failed to load factors');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setFactorsLoading(false); }
  }, []);

  // Today's brief: curated nifty_today.json — a file read, still button-gated.
  const loadToday = useCallback(async () => {
    setTodayLoading(true);
    try {
      const r = await fetch('/api/nifty-today');
      const j = await r.json();
      if (j.success) setToday(j.today);
      else setErr(j.detail || 'Failed to load today brief');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setTodayLoading(false); }
  }, []);

  const toggleFactor = (id: string) =>
    setExpandedFactors((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggle = (sym: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });

  // NO auto-run — the scan (bars for 50 names + valuation fetch) runs on button press only.
  const run = useCallback(async (force = false) => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/nifty50-view${force ? '?force=true' : ''}`);
      const j = await r.json();
      if (j.success) { setView(j.view); setCached(!!j.cached); }
      else setErr(j.detail || 'Scan failed');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  const sectors = useMemo(() => {
    const s = new Set<string>();
    view?.rows.forEach((r) => s.add(r.sector));
    return Array.from(s).sort();
  }, [view]);

  const sortVal = useCallback((r: Row): number => {
    if (sortKey === 'vs_median') return r.verdict?.vs_median_pct ?? -Infinity;
    const v = r[sortKey];
    return typeof v === 'number' ? v : -Infinity;
  }, [sortKey]);

  const rows = useMemo(() => {
    if (!view) return [];
    return view.rows
      .filter((r) => sectorFilter === 'all' || r.sector === sectorFilter)
      .filter((r) => verdictFilter === 'all' || r.verdict?.label === verdictFilter)
      .sort((a, b) => (sortAsc ? sortVal(a) - sortVal(b) : sortVal(b) - sortVal(a)));
  }, [view, sectorFilter, verdictFilter, sortKey, sortAsc, sortVal]);

  const counts = useMemo(() => {
    const n = { rich: 0, 'in-line': 0, cheap: 0 } as Record<string, number>;
    view?.rows.forEach((r) => { if (r.verdict) n[r.verdict.label] += 1; });
    return n;
  }, [view]);

  // --- Today's Market section — the daily reasoning: why the index moved ---
  const todaySection = (
    <div className={card}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
          Today's market — why the index moved {today ? `(${today.as_of})` : ''}
        </span>
        <button onClick={loadToday} disabled={todayLoading}
          className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
          title="Reads curated nifty_today.json — no market data fetched. Decays in one day.">
          {todayLoading ? 'Loading…' : today ? 'Reload brief' : "Load today's brief"}
        </button>
      </div>

      {today && (
        <div className="mt-3 space-y-3">
          <p className="text-xs font-bold text-slate-800 leading-relaxed">{today.headline}</p>
          <p className="text-[11px] text-slate-600 leading-relaxed">
            <b className="text-[10px] uppercase text-slate-400 mr-1">Structure</b>{today.structure}
          </p>

          {/* Ranked drivers */}
          <div className="space-y-2">
            {today.drivers.map((dr) => (
              <div key={dr.rank} className="flex items-start gap-2.5">
                <span className="text-sm font-black text-slate-300 shrink-0 w-4">{dr.rank}</span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[11px] font-bold text-slate-700">{dr.title}</span>
                    <span className={`text-[9px] font-black px-1.5 py-0.5 rounded-full border ${ROLE_TONE[dr.role] ?? 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                      {dr.role}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-600 leading-snug mt-0.5">
                    {dr.detail} <span className="text-slate-400">· {dr.source}</span>
                  </p>
                </div>
              </div>
            ))}
          </div>

          {/* Sector-weight arithmetic */}
          <div className="rounded-xl border border-slate-200 px-3 py-2">
            <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Sector-weight math</div>
            <div className="flex flex-wrap gap-2 mb-1">
              {today.sector_math.weights.map((w, i) => (
                <span key={i} className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                  {w.sector}: {w.weight_pct}%
                </span>
              ))}
            </div>
            <p className="text-[11px] text-slate-600 leading-snug">{today.sector_math.rule}</p>
          </div>

          {/* What to monitor */}
          <div>
            <div className="text-[10px] font-black uppercase text-amber-600 mb-1">What to monitor next</div>
            <div className="space-y-1">
              {today.monitor.map((m) => (
                <div key={m.rank} className="text-[11px] text-slate-600 flex items-start gap-2">
                  <span className="font-black text-amber-500 shrink-0">{m.rank}.</span>
                  <span>{m.item}</span>
                </div>
              ))}
            </div>
          </div>
          <p className="text-[9px] text-slate-400 leading-snug">{today.note}</p>
        </div>
      )}
    </div>
  );

  // --- Macro Factors section (shared: renders before or after the scan is run) ---
  const selectedScenario = useMemo(() => {
    if (!factors || !scenarioKey) return null;
    const [fid, idxStr] = scenarioKey.split('::');
    const f = factors.factors.find((x) => x.id === fid);
    const s = f?.scenarios[Number(idxStr)];
    return f && s ? { f, s } : null;
  }, [factors, scenarioKey]);

  const factorTally = useMemo(() => {
    const t: Record<string, number> = { tailwind: 0, headwind: 0, mixed: 0, neutral: 0 };
    factors?.factors.forEach((f) => { t[f.direction] += 1; });
    return t;
  }, [factors]);

  const factorsSection = (
    <div className={card}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
          Macro factors — what can move the index {factors ? `(as of ${factors.as_of})` : ''}
        </span>
        <button onClick={loadFactors} disabled={factorsLoading}
          className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
          title="Reads curated nifty_factors.json — no market data fetched">
          {factorsLoading ? 'Loading…' : factors ? 'Reload factors' : 'Load factors'}
        </button>
      </div>

      {factors && (
        <div className="mt-3 space-y-3">
          {/* Expectation — today / this week */}
          <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2 mb-1.5">
              <span className="text-[10px] font-black uppercase text-indigo-700">Expectation</span>
              <span className="text-[9px] text-indigo-400">as of {factors.expectation.as_of} — decays in days</span>
              <span className="text-[10px] text-slate-500 ml-auto">
                {factorTally.tailwind} tailwind · {factorTally.headwind} headwind · {factorTally.mixed} mixed · {factorTally.neutral} neutral
              </span>
            </div>
            <p className="text-[11px] text-slate-700 leading-relaxed"><b>Today:</b> {factors.expectation.today}</p>
            <p className="text-[11px] text-slate-700 leading-relaxed mt-1"><b>This week:</b> {factors.expectation.week}</p>
            <p className="text-[9px] text-slate-400 mt-1">{factors.expectation.derived_from}</p>
          </div>

          {/* Scenario picker — what % move to expect from a given change */}
          <div className="rounded-xl border border-slate-200 px-3 py-2.5">
            <div className="text-[10px] font-black uppercase text-slate-400 mb-1.5">
              Scenario — if this happens, what does Nifty do?
            </div>
            <select value={scenarioKey} onChange={(e) => setScenarioKey(e.target.value)}
              className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-700">
              <option value="">Pick a scenario…</option>
              {factors.factors.map((f) =>
                f.scenarios.map((s, i) => (
                  <option key={`${f.id}::${i}`} value={`${f.id}::${i}`}>
                    [{f.name}] {s.trigger}
                  </option>
                )))}
            </select>
            {selectedScenario && (() => {
              const { f, s } = selectedScenario;
              const [lo, hi] = [Math.min(...s.nifty_pct), Math.max(...s.nifty_pct)];
              const base = view?.index?.last ?? null;
              return (
                <div className="mt-2 text-[11px] text-slate-600 space-y-1">
                  <div>
                    Expected Nifty move:{' '}
                    <b className={`font-mono ${hi <= 0 ? 'text-rose-600' : lo >= 0 ? 'text-emerald-700' : 'text-amber-700'}`}>
                      {lo >= 0 ? '+' : ''}{lo}% to {hi >= 0 ? '+' : ''}{hi}%
                    </b>{' '}
                    over <b>{s.horizon}</b>
                    {base != null && (
                      <span className="text-slate-500">
                        {' '}(≈ {Math.round(base * (1 + lo / 100)).toLocaleString('en-IN')} – {Math.round(base * (1 + hi / 100)).toLocaleString('en-IN')} from {base.toLocaleString('en-IN')})
                      </span>
                    )}
                    {base == null && <span className="text-slate-400"> — run the scan to see implied index levels</span>}
                  </div>
                  <div><span className="text-slate-400 font-bold">Historical anchor:</span> {s.anchor}</div>
                  <div><span className="text-slate-400 font-bold">Factor:</span> {f.name} — {f.transmission}</div>
                  <p className="text-[9px] text-amber-700 pt-0.5">
                    Episode-based analogy, not a model output — this repo's own macro→index regression was retired at R²=0.036.
                  </p>
                </div>
              );
            })()}
          </div>

          {/* Factor list — expandable like the company rows */}
          <div className="divide-y divide-slate-100 border border-slate-100 rounded-xl overflow-hidden">
            {factors.factors.map((f) => {
              const open = expandedFactors.has(f.id);
              return (
                <div key={f.id}>
                  <button onClick={() => toggleFactor(f.id)}
                    className="w-full flex items-start gap-2 px-3 py-2 text-left hover:bg-slate-50/70">
                    {open ? <ChevronDown className="w-3.5 h-3.5 mt-0.5 text-slate-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 mt-0.5 text-slate-400 shrink-0" />}
                    <span className="text-xs font-black text-slate-800 whitespace-nowrap">{f.name}</span>
                    <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border shrink-0 ${DIR_TONE[f.direction]}`}>
                      {f.direction}{f.fragile ? ' · fragile' : ''}
                    </span>
                    <span className="text-[11px] text-slate-500 leading-snug hidden md:block">{f.status}</span>
                  </button>
                  {open && (
                    <div className="px-9 pb-3 space-y-2">
                      <p className="text-[11px] text-slate-600 md:hidden">{f.status}</p>
                      <p className="text-[11px] text-slate-600">
                        <b className="text-[10px] uppercase text-slate-400 mr-1">Transmission</b>{f.transmission}
                      </p>
                      <div>
                        <div className="text-[10px] font-black uppercase text-slate-400 mb-0.5">Evidence (dated — decays)</div>
                        {f.facts.map((e, i) => (
                          <div key={i} className="text-[11px] text-slate-600 flex items-start gap-2">
                            <span className="font-mono text-slate-400 shrink-0">{e.date}</span>
                            <span>{e.note} <span className="text-slate-400">· {e.source}</span></span>
                          </div>
                        ))}
                      </div>
                      <div>
                        <div className="text-[10px] font-black uppercase text-slate-400 mb-0.5">Scenarios</div>
                        {f.scenarios.map((s, i) => {
                          const [lo, hi] = [Math.min(...s.nifty_pct), Math.max(...s.nifty_pct)];
                          return (
                            <div key={i} className="text-[11px] text-slate-600 flex items-start gap-2">
                              <b className={`font-mono shrink-0 ${hi <= 0 ? 'text-rose-600' : lo >= 0 ? 'text-emerald-700' : 'text-amber-700'}`}>
                                {lo >= 0 ? '+' : ''}{lo}%…{hi >= 0 ? '+' : ''}{hi}%
                              </b>
                              <span>{s.trigger} <span className="text-slate-400">({s.horizon} · anchor: {s.anchor})</span></span>
                            </div>
                          );
                        })}
                      </div>
                      <div>
                        <div className="text-[10px] font-black uppercase text-amber-600 mb-0.5">What to watch</div>
                        {f.watch.map((w, i) => (
                          <div key={i} className="text-[11px] text-amber-700 flex items-start gap-1.5">
                            <span className="font-black shrink-0">→</span><span>{w}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[9px] text-slate-400 leading-snug">{factors.note}</p>
        </div>
      )}
    </div>
  );

  const SortTh = ({ k, children, align = 'right' }: { k: SortKey; children: React.ReactNode; align?: string }) => (
    <th className={`px-2 py-2.5 text-${align} cursor-pointer select-none hover:text-slate-600`}
      onClick={() => { sortKey === k ? setSortAsc(!sortAsc) : (setSortKey(k), setSortAsc(false)); }}>
      <span className="inline-flex items-center gap-0.5">{children}
        <ArrowUpDown className={`w-3 h-3 ${sortKey === k ? 'text-indigo-500' : 'text-slate-300'}`} />
      </span>
    </th>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <PieChart className="w-5 h-5 text-indigo-600" /> Nifty 50 — returns & pricing scan
          </h2>
          <p className="text-xs text-slate-500">
            1D · 1W · 6M · 1Y returns and rich/cheap vs sector peers
            {view?.index ? ` — index ${view.index.last.toLocaleString('en-IN')} (as of ${view.index.as_of})` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => run(false)} disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-600 text-white disabled:opacity-50"
            title="Fetches 1Y of bars for all 50 names + valuation data (yfinance), computes returns and sector-relative verdicts. Cached 30 min server-side.">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            {loading ? 'Computing…' : view ? 'Re-run scan' : 'Run scan'}
          </button>
          {view && (
            <button onClick={() => run(true)} disabled={loading}
              className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-100 text-slate-600 disabled:opacity-50"
              title="Bypass the 30-min server-side cache">
              Force
            </button>
          )}
        </div>
      </div>

      {err && <div className="text-xs text-rose-600">{err}. Is the backend running?</div>}

      {/* Index strip + cheap/rich read sit ABOVE the tabs — shared context for all three panels */}
      {!view && !loading && !err && (
        <p className="text-[11px] text-slate-400">
          Press <b className="text-indigo-600">Run scan</b> to show the index returns and the cheap/rich
          read here (fetches a year of prices for all 50 constituents; ~15-30s cold).
        </p>
      )}
      {view && (
        <>
          {/* Index strip */}
          {view.index && (
            <div className={`${card} flex flex-wrap items-center gap-6 text-xs font-bold`}>
              <span className="text-slate-400 uppercase text-[10px]">Nifty 50 index</span>
              <span className="text-base font-black text-slate-900" title={`last close ${view.index.as_of} — Yahoo, ~15-min delayed intraday`}>
                {view.index.last.toLocaleString('en-IN')}
              </span>
              <span>1D <Pct v={view.index.d1_pct} /></span>
              <span>1W <Pct v={view.index.w1_pct} /></span>
              <span>6M <Pct v={view.index.m6_pct} /></span>
              <span>1Y <Pct v={view.index.y1_pct} /></span>
              <span className="text-[10px] text-slate-400 font-normal ml-auto">
                {cached ? 'served from cache (≤30 min old)' : 'freshly computed'}
              </span>
            </div>
          )}

          {/* Index read — is Nifty cheap or rich today, and the short-term lean */}
          {view.index_read && (
            <div className={card}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                  Is Nifty cheap or rich today?
                </span>
                {view.index_read.val_label && (
                  <span className={`text-[11px] font-black px-2.5 py-0.5 rounded-full border ${VAL_TONE[view.index_read.val_label]}`}>
                    {view.index_read.val_label}
                    {view.index_read.weighted_pe != null && (
                      <span className="font-normal opacity-80"> · P/E {view.index_read.weighted_pe}</span>
                    )}
                  </span>
                )}
                <span className={`text-[11px] font-black px-2.5 py-0.5 rounded-full border ${
                  view.index_read.trend_label === 'uptrend' ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : view.index_read.trend_label === 'downtrend' ? 'bg-rose-50 text-rose-600 border-rose-200'
                    : 'bg-slate-50 text-slate-600 border-slate-200'}`}>
                  {view.index_read.trend_label}
                </span>
                <span className="text-[11px] text-slate-500">
                  {view.index_read.above50 ? '▲' : '▼'} 50-DMA {view.index_read.dma50?.toLocaleString('en-IN') ?? '—'} ·{' '}
                  {view.index_read.above200 ? '▲' : '▼'} 200-DMA {view.index_read.dma200?.toLocaleString('en-IN') ?? '—'} ·{' '}
                  {view.index_read.off_high_pct}% off 52W high
                </span>
              </div>
              <div className="mt-2.5 flex flex-wrap items-center gap-3">
                <span className="text-[10px] font-black uppercase text-slate-400">Short-term lean</span>
                <span className={`text-xs font-black px-3 py-1 rounded-full border ${LEAN_TONE(view.index_read.lean)}`}>
                  {view.index_read.lean}
                </span>
                <span className="text-[11px] text-slate-600">{view.index_read.why}</span>
              </div>
              <div className="mt-1.5 text-[11px] text-slate-500">
                Breadth: {view.index_read.breadth['cheap'] ?? 0} cheap · {view.index_read.breadth['in-line'] ?? 0} in-line ·{' '}
                {view.index_read.breadth['rich'] ?? 0} rich vs their own sector peers.
              </div>
              <p className="text-[9px] text-slate-400 mt-2 leading-snug">{view.index_read.note}</p>
            </div>
          )}
        </>
      )}

      {/* Sub-panel tabs — Today (daily reasoning) · Factors (macro library) · Stocks (the 50) */}
      <div className="flex gap-1 bg-slate-100 p-1 rounded-xl w-fit">
        {([['today', "Today's Market"], ['factors', 'Macro Factors'], ['stocks', 'Nifty Stocks']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition ${tab === id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'today' && todaySection}

      {tab === 'factors' && factorsSection}

      {tab === 'stocks' && !view && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-indigo-600">Run scan</span> to compute the Nifty 50 stock table.
          </p>
        </div>
      )}

      {view && tab === 'stocks' && (
        <>
          {/* Decision mechanism — how every number and verdict on this screen is produced */}
          {view.mechanism && (
            <div className={card}>
              <button onClick={() => setShowMechanism(!showMechanism)}
                className="flex items-center gap-2 text-[11px] font-black uppercase tracking-wide text-slate-500 hover:text-slate-700 w-full text-left">
                <HelpCircle className="w-4 h-4 text-indigo-500" />
                Decision mechanism — how the verdicts &amp; scenarios are computed
                {showMechanism ? <ChevronDown className="w-3.5 h-3.5 ml-auto" /> : <ChevronRight className="w-3.5 h-3.5 ml-auto" />}
              </button>
              {showMechanism && (
                <div className="mt-2 space-y-1.5">
                  {view.mechanism.map((s, i) => (
                    <p key={i} className="text-[11px] text-slate-600 leading-relaxed">{s}</p>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Filters */}
          <div className={card}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">Sector</span>
              <button onClick={() => setSectorFilter('all')}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${sectorFilter === 'all' ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                All ({view.rows.length})
              </button>
              {sectors.map((s) => (
                <button key={s} onClick={() => setSectorFilter(sectorFilter === s ? 'all' : s)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${sectorFilter === s ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                  {s} ({view.rows.filter((r) => r.sector === s).length})
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">Pricing</span>
              {(['all', 'cheap', 'in-line', 'rich'] as const).map((v) => (
                <button key={v} onClick={() => setVerdictFilter(verdictFilter === v && v !== 'all' ? 'all' : v)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${verdictFilter === v ? 'bg-slate-900 text-white border-slate-900' : v === 'all' ? 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50' : `${VERDICT_STYLE[v]} hover:opacity-80`}`}>
                  {v}{v !== 'all' ? ` (${counts[v]})` : ''}
                </button>
              ))}
            </div>
          </div>

          {/* Table */}
          <div className={`${card} overflow-x-auto p-0`}>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase font-black text-slate-400 border-b border-slate-100">
                  <th className="px-3 py-2.5 w-6" />
                  <th className="px-2 py-2.5">Company</th>
                  <th className="px-2 py-2.5">Sector</th>
                  <SortTh k="weight">Wt%</SortTh>
                  <th className="px-2 py-2.5 text-right">Price ₹</th>
                  <SortTh k="d1_pct">1D</SortTh>
                  <SortTh k="w1_pct">1W</SortTh>
                  <SortTh k="m6_pct">6M</SortTh>
                  <SortTh k="y1_pct">1Y</SortTh>
                  <SortTh k="rel_6m">vs Nifty 6M</SortTh>
                  <th className="px-2 py-2.5">52W</th>
                  <SortTh k="pe">P/E</SortTh>
                  <th className="px-2 py-2.5 text-right">Fwd</th>
                  <th className="px-2 py-2.5 text-right">P/B</th>
                  <SortTh k="vs_median" align="left">Pricing</SortTh>
                  <th className="px-2 py-2.5"
                    title="Measured: how this stock traded the session after its last 8 results announcements, vs Nifty. An arrow marks names whose recent behaviour differs from their full-sample habit.">Results</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const open = expanded.has(r.symbol);
                  return (
                  <React.Fragment key={r.symbol}>
                  <tr onClick={() => toggle(r.symbol)} className="border-b border-slate-50 hover:bg-slate-50/60 align-top cursor-pointer">
                    <td className="px-3 py-2 text-slate-400">
                      {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <span className="font-black text-slate-800">{r.symbol}</span>
                      {r.symbol_note && (
                        <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                          title={r.symbol_note}>→{r.yahoo_symbol}</span>
                      )}
                      {r.last == null && (
                        <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-rose-50 text-rose-600 border border-rose-200"
                          title="No price returned by the data source for any known ticker for this name">no data</span>
                      )}
                      <div className="text-[10px] text-slate-400">{r.name}</div>
                    </td>
                    <td className="px-2 py-2 text-slate-500 whitespace-nowrap">{r.sector}</td>
                    <td className="px-2 py-2 text-right font-mono text-slate-500">{r.weight ?? '—'}</td>
                    <td className="px-2 py-2 text-right font-mono font-bold text-slate-900"
                      title={r.as_of ? `latest daily close (${r.as_of}) — Yahoo, ~15-min delayed intraday` : undefined}>
                      {r.last != null ? r.last.toLocaleString('en-IN') : '—'}
                    </td>
                    <td className="px-2 py-2 text-right"><Pct v={r.d1_pct} /></td>
                    <td className="px-2 py-2 text-right"><Pct v={r.w1_pct} /></td>
                    <td className="px-2 py-2 text-right"><Pct v={r.m6_pct} /></td>
                    <td className="px-2 py-2 text-right"><Pct v={r.y1_pct} /></td>
                    <td className="px-2 py-2 text-right"
                      title="6-month return minus the index's 6-month return — negative means it lagged Nifty">
                      <Pct v={r.rel_6m} />
                    </td>
                    <td className="px-2 py-2"><RangeBar v={r.pos_52w} /></td>
                    <td className="px-2 py-2 text-right font-mono text-slate-600">{r.pe ?? '—'}</td>
                    <td className="px-2 py-2 text-right font-mono text-slate-400">{r.fwd_pe ?? '—'}</td>
                    <td className="px-2 py-2 text-right font-mono text-slate-400">{r.pb ?? '—'}</td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      {r.verdict ? (
                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${VERDICT_STYLE[r.verdict.label]}`}
                          title={`${r.verdict.metric.toUpperCase()} ${r.verdict.vs_median_pct >= 0 ? '+' : ''}${r.verdict.vs_median_pct}% vs ${r.verdict.basis} median ${r.verdict.sector_median}`}>
                          {r.verdict.label} {r.verdict.vs_median_pct >= 0 ? '+' : ''}{r.verdict.vs_median_pct}%
                        </span>
                      ) : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      {r.reaction ? (
                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${BIAS_STYLE[r.reaction.recent_bias]}`}
                          title={`Last ${r.reaction.recent_n} results: ${fmtPct(r.reaction.recent_mean_rel1d_pct)} vs Nifty on average the next session, ${Math.round(r.reaction.recent_positive_share * 100)}% positive.\nFull sample (${r.reaction.n_events} events): ${fmtPct(r.reaction.full_mean_rel1d_pct)} vs Nifty, ${r.reaction.full_bias}.${r.reaction.diverges ? '\n\nRecent behaviour DIFFERS from the full-sample habit — the market has changed how it reads this name.' : ''}`}>
                          {r.reaction.recent_bias}
                          {r.reaction.diverges && <span className="ml-1" title="diverges from full-sample habit">⇄</span>}
                        </span>
                      ) : <span className="text-slate-300">—</span>}
                    </td>
                  </tr>
                  {open && (
                    <tr className="border-b border-slate-100 bg-slate-50/50">
                      <td />
                      <td colSpan={14} className="px-2 py-3">
                        {/* Price header — current (delayed) price front and centre */}
                        <div className="flex flex-wrap items-center gap-3 mb-3 text-xs">
                          <span className="text-lg font-black text-slate-900 font-mono">
                            {r.last != null ? `₹${r.last.toLocaleString('en-IN')}` : '—'}
                          </span>
                          <Pct v={r.d1_pct} />
                          <span className="text-[10px] text-slate-400">
                            {r.as_of ? `as of ${r.as_of} · Yahoo daily close, ~15-min delayed intraday` : ''}
                          </span>
                          {r.hi_52w != null && r.lo_52w != null && (
                            <span className="text-[10px] text-slate-400 ml-auto">
                              52W range ₹{r.lo_52w.toLocaleString('en-IN')} – ₹{r.hi_52w.toLocaleString('en-IN')}
                            </span>
                          )}
                        </div>
                        {r.symbol_note && (
                          <p className="text-[10px] text-amber-700 mb-3">{r.symbol_note}</p>
                        )}
                        {r.last == null && (
                          <p className="text-[10px] text-rose-600 mb-3">
                            No data — the source returned nothing for any known ticker of this name
                            (tried <b>{r.symbol}.NS</b>). Usually an NSE rename or demerger: add the
                            current ticker to <b>_TICKER_ALTS</b> in backend/nifty50_routes.py, or refresh
                            nifty-50-stock-list.csv.
                          </p>
                        )}
                        <div className="grid md:grid-cols-2 gap-4">
                          {/* WHY this verdict */}
                          <div>
                            <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Why this verdict</div>
                            {r.verdict ? (
                              <p className="text-[11px] text-slate-600 leading-relaxed">
                                {r.verdict.metric === 'pe' ? 'Trailing P/E' : 'P/B (P/E unavailable or negative — book-value fallback)'}{' '}
                                <b>{r.verdict.value ?? (r.verdict.metric === 'pe' ? r.pe : r.pb)}</b> is{' '}
                                <b className={r.verdict.vs_median_pct >= 0 ? 'text-rose-600' : 'text-emerald-700'}>
                                  {r.verdict.vs_median_pct >= 0 ? '+' : ''}{r.verdict.vs_median_pct}%
                                </b>{' '}
                                vs the {r.verdict.basis === 'sector' ? `${r.sector} peer` : 'whole-index (small sector)'} median of{' '}
                                <b>{r.verdict.sector_median}</b> → <b>{r.verdict.label}</b> (thresholds: ≥+25% rich, ≤−25% cheap).
                                A premium can be earned (growth/quality) and a discount deserved (weak fundamentals) — this flags the
                                question, it doesn't answer it.
                              </p>
                            ) : (
                              <p className="text-[11px] text-slate-400">No verdict — P/E and P/B unavailable from the data source.</p>
                            )}
                          </div>
                          {/* Upside / downside scenarios */}
                          <div>
                            <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Scenarios — reference points, not forecasts</div>
                            <div className="space-y-1 text-[11px] leading-relaxed">
                              <div>
                                <span className="text-slate-500">Upside to its 52-week high{r.hi_52w ? ` (₹${r.hi_52w.toLocaleString('en-IN')})` : ''}: </span>
                                <b className="text-emerald-700 font-mono">{r.up_to_high_pct != null ? `+${r.up_to_high_pct}%` : '—'}</b>
                              </div>
                              <div>
                                <span className="text-slate-500">Downside to its 52-week low{r.lo_52w ? ` (₹${r.lo_52w.toLocaleString('en-IN')})` : ''}: </span>
                                <b className="text-rose-600 font-mono">{r.down_to_low_pct != null ? `${r.down_to_low_pct}%` : '—'}</b>
                              </div>
                              <div>
                                <span className="text-slate-500">If its {r.verdict?.metric === 'pb' ? 'P/B' : 'P/E'} reverted to the peer median (earnings unchanged): </span>
                                {r.verdict?.reversion_pct != null ? (
                                  <b className={`font-mono ${r.verdict.reversion_pct >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                                    {r.verdict.reversion_pct >= 0 ? '+' : ''}{r.verdict.reversion_pct}%
                                  </b>
                                ) : <span className="text-slate-300">—</span>}
                              </div>
                              <p className="text-[10px] text-slate-400 leading-snug pt-1">
                                These say where price HAS traded in the past year and where valuation WOULD sit at peer parity —
                                they carry no probability and are not price targets.
                              </p>
                            </div>
                          </div>
                        </div>

                        {/* Qualitative drivers — the WHY the numbers can't see */}
                        {r.drivers ? (
                          <div className="mt-3 pt-3 border-t border-slate-200/70">
                            <p className="text-[11px] text-slate-600 leading-snug mb-2">
                              <span className="text-[10px] font-black uppercase text-slate-400 mr-1.5">Position</span>
                              {r.drivers.position}
                              {r.div_yield != null && r.div_yield > 0 && (
                                <span className="ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 border border-indigo-200">
                                  div yield ~{r.div_yield}%
                                </span>
                              )}
                            </p>
                            {r.reaction && (
                              <div className="rounded-xl border border-amber-200 bg-amber-50/50 px-3 py-2 mb-3">
                                <div className="text-[10px] font-black uppercase text-amber-700 mb-1">
                                  How it trades results — measured
                                  <span className="font-normal text-amber-500 ml-1.5">
                                    {r.reaction.n_events} announcements since 2018 · next-session move vs Nifty
                                  </span>
                                </div>
                                <div className="flex flex-wrap gap-4 text-[11px] text-slate-600">
                                  <span>
                                    <span className="text-slate-400">Usually</span>{' '}
                                    <span className="font-mono font-bold">{fmtPct(r.reaction.full_mean_rel1d_pct)}</span>{' '}
                                    <span className={`font-black ${r.reaction.full_bias === 'positive' ? 'text-emerald-600' : r.reaction.full_bias === 'negative' ? 'text-rose-600' : 'text-slate-500'}`}>
                                      {r.reaction.full_bias}
                                    </span>
                                    <span className="text-slate-400"> · {Math.round(r.reaction.full_positive_share * 100)}% positive</span>
                                  </span>
                                  <span>
                                    <span className="text-slate-400">Last {r.reaction.recent_n}</span>{' '}
                                    <span className="font-mono font-bold">{fmtPct(r.reaction.recent_mean_rel1d_pct)}</span>{' '}
                                    <span className={`font-black ${r.reaction.recent_bias === 'positive' ? 'text-emerald-600' : r.reaction.recent_bias === 'negative' ? 'text-rose-600' : 'text-slate-500'}`}>
                                      {r.reaction.recent_bias}
                                    </span>
                                    <span className="text-slate-400"> · {Math.round(r.reaction.recent_positive_share * 100)}% positive</span>
                                  </span>
                                  {r.reaction.recent_mean_sect_rel1d_pct != null && (
                                    <span>
                                      <span className="text-slate-400">vs own sector</span>{' '}
                                      <span className="font-mono font-bold">{fmtPct(r.reaction.recent_mean_sect_rel1d_pct)}</span>
                                    </span>
                                  )}
                                </div>
                                {r.reaction.diverges && (
                                  <p className="text-[11px] font-bold text-amber-800 mt-1.5">
                                    ⇄ Recent results are being read differently from this stock's own history
                                    ({r.reaction.full_bias} → {r.reaction.recent_bias}) — the change is in the reaction, not necessarily the fundamentals.
                                  </p>
                                )}
                                <p className="text-[10px] text-slate-400 mt-1.5">
                                  Announcement days are detected by trading VOLUME only, never by the size of the move —
                                  selecting on the move would guarantee the result. Measured against Nifty, so this is
                                  out-performance on results day, not merely a rise.
                                </p>
                              </div>
                            )}
                            {r.drivers.recent_change && (
                              <div className="rounded-xl border border-violet-200 bg-violet-50/50 px-3 py-2 mb-3">
                                <div className="text-[10px] font-black uppercase text-violet-700 mb-1">
                                  What changed
                                  <span className="font-normal text-violet-400 ml-1.5">reviewed {r.drivers.recent_change.as_of}</span>
                                </div>
                                <div className="space-y-0.5">
                                  {r.drivers.recent_change.points.map((p, i) => (
                                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1.5">
                                      <span className="text-violet-400 shrink-0">·</span><span>{p}</span>
                                    </div>
                                  ))}
                                </div>
                                <p className="text-[11px] font-bold text-violet-800 mt-1.5">{r.drivers.recent_change.verdict}</p>
                              </div>
                            )}
                            {r.drivers && !r.drivers.recent_change && (
                              <p className="text-[10px] text-slate-400 mb-2">
                                Re-checked in the latest review — nothing material changed for this name.
                              </p>
                            )}
                            {r.drivers.latest_quarter && (
                              <div className="rounded-xl border border-blue-200 bg-blue-50/50 px-3 py-2 mb-3">
                                <div className="text-[10px] font-black uppercase text-blue-700 mb-1">
                                  Latest quarter — {r.drivers.latest_quarter.period}
                                  <span className="font-normal text-blue-400 ml-1.5">as of {r.drivers.latest_quarter.as_of} · decays in ~a quarter</span>
                                </div>
                                <div className="space-y-0.5">
                                  {r.drivers.latest_quarter.points.map((p, i) => (
                                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1.5">
                                      <span className="text-blue-400 shrink-0">·</span><span>{p}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                            <div className="grid md:grid-cols-2 gap-4">
                              <div>
                                <div className="text-[10px] font-black uppercase text-emerald-700 mb-1">Tailwinds — why investors hold</div>
                                <div className="space-y-1">
                                  {r.drivers.tailwinds.map((t, i) => (
                                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1.5">
                                      <span className="font-black text-emerald-600 shrink-0">✓</span><span>{t}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                              <div>
                                <div className="text-[10px] font-black uppercase text-rose-600 mb-1">Headwinds — what can hurt</div>
                                <div className="space-y-1">
                                  {r.drivers.headwinds.map((h, i) => (
                                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1.5">
                                      <span className="font-black text-rose-500 shrink-0">✗</span><span>{h}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </div>
                          </div>
                        ) : (
                          <p className="mt-3 pt-3 border-t border-slate-200/70 text-[11px] text-slate-400">
                            No curated drivers for this name yet — add it to nifty50_drivers.json.
                          </p>
                        )}
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && (
              <div className="px-4 py-8 text-center text-xs text-slate-400">No constituents match the current filters.</div>
            )}
          </div>

          <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3 space-y-1.5">
            {view.drivers_meta && (
              <p className="text-[10px] text-amber-700 leading-relaxed">
                <b>Drivers note (as of {view.drivers_meta.as_of}):</b> {view.drivers_meta.note}
              </p>
            )}
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <b className="text-slate-600">Honesty note:</b> {view.note}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
