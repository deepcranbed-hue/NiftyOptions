import React, { useCallback, useMemo, useState } from 'react';
import { PieChart, RefreshCw, ArrowUpDown, ChevronDown, ChevronRight, HelpCircle, X } from 'lucide-react';

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

// The data contract and the shared tone tables live in nifty50Shared so this panel and
// the full-page stock view can't drift apart about what the payload means.
import {
  type Row, type View, type Verdict,
  BIAS_STYLE, VERDICT_STYLE, VAL_TONE, LEAN_TONE, card,
  fmtPct, stockHref, Pct, RangeBar, BAND_STYLE, QUADRANT_STYLE,
} from './nifty50Shared';
import { Nifty50StockDetail } from './Nifty50StockPage';
import { Nifty50QualityGrowth } from './Nifty50QualityGrowth';

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
  // A move with no news behind it is a different claim from an explained one. It gets its
  // own chip so the brief can say "largest drag, cause unknown" without that honesty being
  // flattened into the same grey fallback as any role the table simply forgot.
  unattributed: 'bg-violet-50 text-violet-600 border-violet-200',
  flow: 'bg-sky-50 text-sky-700 border-sky-200',
  macro: 'bg-indigo-50 text-indigo-600 border-indigo-200',
  positioning: 'bg-fuchsia-50 text-fuchsia-600 border-fuchsia-200',
};

const DIR_TONE: Record<Factor['direction'], string> = {
  tailwind: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  headwind: 'bg-rose-50 text-rose-600 border-rose-200',
  mixed: 'bg-amber-50 text-amber-700 border-amber-200',
  neutral: 'bg-slate-50 text-slate-500 border-slate-200',
};

type SortKey = 'weight' | 'd1_pct' | 'w1_pct' | 'm6_pct' | 'y1_pct' | 'pe' | 'vs_median' | 'rel_6m' | 'implied';

// Tab ids: the three fixed panels, plus one `stock:<SYMBOL>` per opened constituent.
const FIXED_TABS = [['today', "Today's Market"], ['factors', 'Macro Factors'],
                    ['stocks', 'Nifty Stocks'], ['gap', 'Expectation Gap'],
                    ['quality', 'Quality Growth']] as const;
const STOCK_TAB = 'stock:';
// Past this the strip wraps into a second row and stops reading as a strip, so the
// oldest tab closes — same instinct as a browser dropping to a scroll, minus the scroll.
const MAX_STOCK_TABS = 6;

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
  const [tab, setTab] = useState<string>('today');
  // Symbols with an open tab, oldest first.
  const [openStocks, setOpenStocks] = useState<string[]>([]);
  const [today, setToday] = useState<TodayBrief | null>(null);
  const [todayLoading, setTodayLoading] = useState(false);
  const [factors, setFactors] = useState<FactorsDoc | null>(null);
  const [factorsLoading, setFactorsLoading] = useState(false);
  const [expandedFactors, setExpandedFactors] = useState<Set<string>>(new Set());
  const [scenarioKey, setScenarioKey] = useState<string>('');

  // Factors are a plain file read (curated nifty_factors.json) — still button-gated.
  const loadFactors = useCallback(async () => {
    setFactorsLoading(true); setErr(null);  // else a previous failure's banner outlives it
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
    setTodayLoading(true); setErr(null);
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

  // --- per-stock tabs ---------------------------------------------------------
  // A left-click on a symbol opens it as a tab in this panel's own strip, beside
  // Today's Market / Macro Factors / Nifty Stocks. The row click still expands inline
  // for a quick peek, and cmd/ctrl/middle-click still opens the standalone page — the
  // symbol stays a real <a href>, so the browser's own affordances keep working.
  const openStock = useCallback((sym: string) => {
    setOpenStocks((prev) => {
      if (prev.includes(sym)) return prev;
      const next = [...prev, sym];
      return next.length > MAX_STOCK_TABS ? next.slice(next.length - MAX_STOCK_TABS) : next;
    });
    setTab(`${STOCK_TAB}${sym}`);
  }, []);

  const closeStock = useCallback((sym: string) => {
    setOpenStocks((prev) => prev.filter((s) => s !== sym));
    // Closing the tab you're looking at drops you back to the table, not to a blank pane.
    setTab((t) => (t === `${STOCK_TAB}${sym}` ? 'stocks' : t));
  }, []);

  // Let the browser handle any click that asks for a new tab/window itself.
  const onSymbolClick = useCallback((e: React.MouseEvent, sym: string) => {
    e.stopPropagation();  // don't also toggle the row's inline expand
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    openStock(sym);
  }, [openStock]);

  const activeStock = tab.startsWith(STOCK_TAB) ? tab.slice(STOCK_TAB.length) : null;
  const activeRow = useMemo(
    () => (activeStock && view ? view.rows.find((r) => r.symbol === activeStock) ?? null : null),
    [activeStock, view],
  );

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
    if (sortKey === 'implied') return r.expectation?.implied_eps_growth_pct ?? -Infinity;
    const v = r[sortKey as keyof Row];
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

      {/* An incomplete scan is not cached server-side, but it IS rendered — say so, or a
          screen full of dashes reads as a market observation. */}
      {view?.degraded && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] font-bold text-amber-800">
          Incomplete data — {view.degraded}
        </div>
      )}

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
                  {/* off_high_pct is negative by construction — printing it raw read as a double negative */}
                  {Math.abs(view.index_read.off_high_pct)}% below 52W high
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
                {/* A breadth split is a read on the index only if most names were valued —
                    otherwise it is a read on Yahoo's uptime, and that has to be visible. */}
                {!!view.index_read.unvalued && (
                  <span className="text-amber-700"> {view.index_read.unvalued} not valued
                    {!!view.index_read.fundamentals_failed &&
                      ` (${view.index_read.fundamentals_failed} because the fundamentals fetch failed)`}.
                  </span>
                )}
              </div>
              <p className="text-[9px] text-slate-400 mt-2 leading-snug">{view.index_read.note}</p>
            </div>
          )}

          {/* Earnings growth vs the multiple — what the index is priced for, and the
              three levers that can break it. Sits under the index read because it is
              the same question one layer down: not "is it rich" but "on what". */}
          {view.earnings_vs_valuation && (() => {
            const e = view.earnings_vs_valuation;
            return (
              <div className={card}>
                <div className="flex flex-wrap items-baseline gap-2 mb-2.5">
                  <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                    Earnings growth vs the multiple
                  </span>
                  <span className="text-[10px] text-slate-400">
                    {e.names} names · {e.weight_covered_pct}% of weight
                    {e.excluded.length > 0 && ` · ${e.excluded.join(' & ')} excluded (no trailing P/E)`}
                  </span>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  <div className="rounded-xl border border-slate-200 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-slate-400">Priced for</div>
                    <div className="text-lg font-black font-mono text-indigo-700 mt-0.5">
                      +{Math.round(e.implied_growth_pct)}%
                    </div>
                    <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                      EPS growth · {e.trailing_pe}× → {e.forward_pe}×
                    </div>
                  </div>
                  <div className="rounded-xl border border-slate-200 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-slate-400">Earnings yield</div>
                    <div className="text-lg font-black font-mono text-slate-800 mt-0.5">{e.earnings_yield_pct}%</div>
                    <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                      forward {e.forward_earnings_yield_pct}%
                    </div>
                  </div>
                  <div className="rounded-xl border border-slate-200 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-slate-400">vs 10-yr G-sec</div>
                    <div className={`text-lg font-black font-mono mt-0.5 ${e.yield_gap_pp < 0 ? 'text-rose-600' : 'text-emerald-700'}`}>
                      {e.yield_gap_pp > 0 ? '+' : ''}{e.yield_gap_pp}pp
                    </div>
                    <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                      G-sec {e.gsec_10y_pct}% ({e.gsec_yoy_pp > 0 ? '+' : ''}{e.gsec_yoy_pp}pp y/y)
                    </div>
                  </div>
                  <div className="rounded-xl border border-slate-200 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-slate-400">1 turn of P/E</div>
                    <div className="text-lg font-black font-mono text-slate-800 mt-0.5">{e.pct_per_pe_turn}%</div>
                    <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                      of index value · to 18× is {e.to_band_low_pct}%
                    </div>
                  </div>
                </div>

                {/* The three levers — the taxonomy that separates a de-rating from a dip */}
                <div className="grid md:grid-cols-3 gap-2 mt-2.5">
                  <div className="rounded-xl border border-rose-200 bg-rose-50/40 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-rose-700">Discount rate — fast, index-wide</div>
                    <p className="text-[11px] text-slate-600 leading-snug mt-1">
                      The 10-year is {e.gsec_10y_pct}% and up {e.gsec_yoy_pp}pp in a year, while the index
                      yields {e.earnings_yield_pct}% on trailing earnings — a gap of {e.yield_gap_pp}pp.
                      At outright parity the multiple would be {e.parity_pe}× against {e.trailing_pe}× today.
                      A negative gap is normal for India, but it means the multiple rests on growth, not on
                      current earnings — and rates move every multiple at once, within days.
                    </p>
                  </div>
                  <div className="rounded-xl border border-amber-200 bg-amber-50/40 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-amber-700">Growth — slow, sector by sector</div>
                    <p className="text-[11px] text-slate-600 leading-snug mt-1">
                      Competition and demand show up first as a falling FORWARD estimate, not a falling
                      price. Watch the spread below: the sectors priced for the most growth have the most
                      to lose from a downgrade, and {e.priced_to_shrink.length > 0
                        ? `${e.priced_to_shrink.map((c) => c.symbol).join(', ')} ${e.priced_to_shrink.length === 1 ? 'is' : 'are'} already priced to shrink`
                        : 'nothing is currently priced to shrink'}.
                    </p>
                  </div>
                  <div className="rounded-xl border border-slate-300 bg-slate-50 px-3 py-2">
                    <div className="text-[10px] font-black uppercase text-slate-600">Earnings level — the denominator</div>
                    <p className="text-[11px] text-slate-600 leading-snug mt-1">
                      Crude, input costs and supply shocks cut E rather than the multiple — so the P/E
                      RISES as the price falls, which is why a cyclical looks dear at the bottom.
                      Profit-booking and flow are a fourth thing entirely: they move price without touching
                      rate, growth or earnings, which is why they mean-revert and these three do not.
                    </p>
                  </div>
                </div>

                {/* Reality check against the economy underneath — and the horizon calibration */}
                {e.macro_check && (() => {
                  const mc = e.macro_check;
                  const best = mc.best_fit_horizon.replace('.', '_').replace('_5y', '_5y');
                  const key = mc.best_fit_horizon === '1y' ? '1y' : mc.best_fit_horizon === '2y' ? '2y' : '1_5y';
                  return (
                    <div className="rounded-xl border border-blue-200 bg-blue-50/40 px-3 py-2.5 mt-2.5">
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="text-[10px] font-black uppercase text-blue-700">
                          Can the economy carry it?
                        </span>
                        <span className="text-[10px] text-slate-400">macro as of {mc.as_of}</span>
                      </div>
                      <p className="text-[11px] text-slate-600 leading-snug mt-1">
                        The <b>+{Math.round(e.implied_growth_pct)}%</b> embedded is a TOTAL change, not an
                        annual rate. Annualised it reconciles with the published Nifty earnings forecast of{' '}
                        <b>~{mc.nifty_eps_forecast_fy27_pct}%</b> at a <b>{mc.best_fit_horizon}</b> horizon —
                        which is the best evidence available that the forward multiple is not a one-year number.
                      </p>
                      <div className="grid grid-cols-3 gap-2 mt-2">
                        {(['1y', '1_5y', '2y'] as const).map((k) => (
                          <div key={k} className={`rounded-lg border px-2 py-1.5 ${k === key ? 'border-blue-400 bg-white' : 'border-slate-200'}`}>
                            <div className="text-[9px] font-black uppercase text-slate-400">
                              over {k.replace('_5y', '.5 yr').replace('1y', '1 yr').replace('2y', '2 yr')}
                              {k === key && <span className="text-blue-600"> · best fit</span>}
                            </div>
                            <div className="text-sm font-black font-mono text-slate-800">
                              {mc.implied_annualised[k]}%<span className="text-[10px] font-normal text-slate-400">/yr</span>
                            </div>
                            <div className="text-[9px] text-slate-400">
                              {mc.excess_over_nominal_gdp_pp[k] >= 0 ? '+' : ''}{mc.excess_over_nominal_gdp_pp[k]}pp vs GDP
                            </div>
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-slate-500 leading-snug mt-2">
                        Nominal GDP: <b>{mc.nominal_gdp_fy26_pct}%</b> in FY26, forecast{' '}
                        <b>~{mc.nominal_gdp_fy27_pct}%</b> FY27. Earnings outgrowing GDP is India&apos;s
                        actual regime — Nifty-500 profit-to-GDP has run from{' '}
                        <b>{mc.profit_to_gdp_fy20_pct}%</b> (FY20) to a record{' '}
                        <b>{mc.profit_to_gdp_pct}%</b> (FY26) — so it is not implausible. But it compounds
                        from a record, and that is where mean reversion would bite.
                      </p>
                    </div>
                  );
                })()}

                {/* Where the growth expectation actually sits */}
                <div className="mt-2.5 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-left text-[9px] uppercase font-black text-slate-400 border-b border-slate-100">
                        <th className="py-1.5 pr-2">Sector</th>
                        <th className="py-1.5 px-2 text-right">Wt%</th>
                        <th className="py-1.5 px-2 text-right">Trailing</th>
                        <th className="py-1.5 px-2 text-right">Forward</th>
                        <th className="py-1.5 pl-2 text-right">Priced for</th>
                      </tr>
                    </thead>
                    <tbody>
                      {e.sectors.map((s) => (
                        <tr key={s.sector} className="border-b border-slate-50">
                          <td className="py-1 pr-2 text-slate-600">{s.sector}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{s.weight}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{s.trailing_pe}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{s.forward_pe}</td>
                          <td className={`py-1 pl-2 text-right font-mono font-bold ${
                            s.implied_growth_pct < 0 ? 'text-rose-600'
                              : s.implied_growth_pct >= 50 ? 'text-amber-700' : 'text-slate-700'}`}>
                            {s.implied_growth_pct >= 0 ? '+' : ''}{s.implied_growth_pct}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <p className="text-[9px] text-slate-400 mt-2 leading-snug">{e.note}</p>
              </div>
            );
          })()}
        </>
      )}

      {/* Sub-panel tabs — Today (daily reasoning) · Factors (macro library) · Stocks (the
          50) · then one tab per constituent the user has opened from the table. */}
      <div className="flex flex-wrap gap-1 bg-slate-100 p-1 rounded-xl w-fit max-w-full">
        {FIXED_TABS.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition ${tab === id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
            {label}
          </button>
        ))}
        {openStocks.map((sym) => {
          const on = tab === `${STOCK_TAB}${sym}`;
          return (
            <span key={sym}
              className={`flex items-center rounded-lg transition ${on ? 'bg-white shadow-sm' : 'hover:bg-slate-200/60'}`}>
              <button onClick={() => setTab(`${STOCK_TAB}${sym}`)}
                className={`pl-3 pr-1.5 py-1.5 text-xs font-bold ${on ? 'text-indigo-700' : 'text-slate-500 hover:text-slate-700'}`}>
                {sym}
              </button>
              <button onClick={() => closeStock(sym)} title={`Close ${sym}`} aria-label={`Close ${sym}`}
                className="pr-2 pl-0.5 py-1.5 text-slate-300 hover:text-rose-500">
                <X className="w-3 h-3" />
              </button>
            </span>
          );
        })}
        {openStocks.length > 1 && (
          <button onClick={() => { setOpenStocks([]); setTab('stocks'); }}
            className="px-2.5 py-1.5 text-[10px] font-bold text-slate-400 hover:text-slate-700"
            title="Close every open stock tab">
            close all
          </button>
        )}
      </div>

      {tab === 'today' && todaySection}

      {tab === 'factors' && factorsSection}

      {/* Quality Growth reads its own artifact (quality_growth.json) and does not need
          the yfinance scan, so it works whether or not Run scan has been pressed. A
          symbol click opens the same in-panel stock tab as the table above. */}
      {tab === 'quality' && <Nifty50QualityGrowth onSymbolClick={openStock} />}

      {tab === 'gap' && !view && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-indigo-600">Run scan</span> to compute the expectation gap.
          </p>
        </div>
      )}

      {tab === 'gap' && view && (view.expectation_gap ? (() => {
        const g = view.expectation_gap;
        const [loT, hiT] = g.thresholds_pp;
        return (
          <div className="space-y-4">
            <div className={card}>
              <div className="flex flex-wrap items-baseline gap-2 mb-1">
                <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
                  Expectation gap — the bar against the track record
                </span>
                <span className="text-[10px] text-slate-400">{g.names} names</span>
                {/* Stated at the top, not buried in a footnote: this ranks research
                    questions. The self-test strip below is why. */}
                <span className="text-[10px] font-black uppercase tracking-wide px-2 py-0.5 rounded-full border border-slate-300 bg-slate-50 text-slate-500">
                  guideline · not a signal
                </span>
              </div>
              <p className="text-[12px] text-slate-700 leading-relaxed">
                Growth <b>priced in</b> minus growth <b>recently delivered</b>, in percentage points.
                This measures the <b>size of the bet, not its direction</b>. A forward P/E below trailing
                just means earnings are expected to grow — if consensus is met and the multiple holds,
                the holder earns roughly that growth. A <b className="text-rose-600">high bar</b> widens
                the range of outcomes <i>both ways</i>; a <b className="text-emerald-700">low bar</b> is
                not safety, since it can mean earnings are forecast to fall. What settles it is whether
                growth persists past the forecast year. Implied growth alone does not discriminate at
                all: its median is <b>{g.median_implied_pct}%</b> and almost nothing is priced for a
                decline, so reading a high number as upside marks nearly every name attractive.
              </p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
                <div className="rounded-xl border border-slate-200 px-3 py-2">
                  <div className="text-[10px] font-black uppercase text-slate-400">Median gap</div>
                  <div className="text-lg font-black font-mono text-slate-800 mt-0.5">
                    {g.median_gap_pp >= 0 ? '+' : ''}{g.median_gap_pp}pp
                  </div>
                  <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                    priced {g.median_implied_pct}% · delivered {g.median_delivered_pct}%
                  </div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50/40 px-3 py-2">
                  <div className="text-[10px] font-black uppercase text-rose-700">High bar</div>
                  <div className="text-lg font-black font-mono text-rose-600 mt-0.5">{g.high_bar}</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">gap ≥ +{hiT}pp</div>
                </div>
                <div className="rounded-xl border border-slate-200 px-3 py-2">
                  <div className="text-[10px] font-black uppercase text-slate-400">In line</div>
                  <div className="text-lg font-black font-mono text-slate-700 mt-0.5">{g.in_line}</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">between {loT} and +{hiT}pp</div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 px-3 py-2">
                  <div className="text-[10px] font-black uppercase text-emerald-700">Low bar</div>
                  <div className="text-lg font-black font-mono text-emerald-700 mt-0.5">{g.low_bar}</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">gap ≤ {loT}pp</div>
                </div>
              </div>
              {/* SELF-TEST. The tab measures its own predictive power against the one
                  non-judgmental dataset in the repo and prints the answer whether or not
                  it flatters the idea. A ranking that stays silent about its hit rate
                  invites a confidence it never earned. */}
              {g.calibration && (() => {
                const c = g.calibration!;
                const r = c.r_gap_vs_full_reaction;
                const bm = c.band_mean_rel_pct || {};
                return (
                  <div className={`rounded-xl border px-3 py-2.5 mt-3 ${c.informative
                    ? 'border-blue-200 bg-blue-50/40' : 'border-slate-200 bg-slate-50/60'}`}>
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="text-[10px] font-black uppercase tracking-wide text-slate-500">
                        Does this predict anything?
                      </span>
                      <span className="text-[11px] font-mono font-black text-slate-800">
                        r = {r == null ? '—' : r}
                      </span>
                      <span className="text-[10px] text-slate-400">
                        gap vs measured results-day move (rel. NIFTY) · {c.names} names ·{' '}
                        {c.events} events
                      </span>
                      <span className={`text-[10px] font-black uppercase tracking-wide px-2 py-0.5 rounded-full border ${
                        c.informative
                          ? 'border-blue-200 bg-blue-50 text-blue-700'
                          : 'border-slate-300 bg-white text-slate-500'}`}>
                        {c.informative ? 'some signal' : 'no measurable edge'}
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-600 leading-snug mt-1.5">
                      <b>{c.verdict}</b> Band averages —{' '}
                      <b className="text-rose-600">high bar {bm['high bar'] == null ? '—' : `${bm['high bar']}%`}</b>,{' '}
                      in line {bm['in line'] == null ? '—' : `${bm['in line']}%`},{' '}
                      <b className="text-emerald-700">low bar {bm['low bar'] == null ? '—' : `${bm['low bar']}%`}</b>.
                      {' '}The theory needs high-bar to be the worst of the three; it currently{' '}
                      {c.bands_ordered_as_theory_predicts ? 'is' : <b>is not</b>}.
                      {' '}|r| under {c.noise_threshold_r} is indistinguishable from zero here.
                    </p>
                    <p className="text-[10px] text-slate-400 leading-snug mt-1">
                      Not a backtest and cannot be one: the gap is measured today, the reactions
                      run back to 2018, so nothing is aligned in time. It can only weaken the
                      "gap is a signal" reading, never confirm it.
                    </p>
                  </div>
                );
              })()}
              {/* The normalised comparison is the better one; say so loudly while it's absent
                  rather than letting a single noisy quarter quietly drive the ranking. */}
              {!g.normalized_available && (
                <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 mt-2 leading-snug">
                  <b>Ranking on one quarter.</b> The Normalised columns are empty — run{' '}
                  <b>data_agent/fundamentals/eps_cagr_backfill.py</b> to build eps_history.json.
                  Until then a single year-on-year print drives the table, and one bad quarter
                  (Dr Reddy's −69%) or one huge one (JSW Steel +113%) can dominate it.
                </p>
              )}
              {!!g.normalized_available && (
                <p className="text-[10px] text-slate-500 mt-2 leading-snug">
                  Normalised growth available for <b>{g.normalized_available}</b> of {g.names} names.
                  Mind the units: embedded growth is a TOTAL change while a CAGR is per year, so the
                  Gap vs CAGR column shows both a 1-year and a 2-year annualisation — at the longer
                  horizon the gap is roughly halved.
                </p>
              )}
            </div>

            <div className={`${card} overflow-x-auto p-0`}>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase font-black text-slate-400 border-b border-slate-100">
                    <th className="px-3 py-2.5">Company</th>
                    <th className="px-2 py-2.5">Sector</th>
                    <th className="px-2 py-2.5 text-right">Wt%</th>
                    <th className="px-2 py-2.5 text-right">P/E</th>
                    <th className="px-2 py-2.5 text-right">Fwd</th>
                    <th className="px-2 py-2.5 text-right" title="Growth the price already assumes">Priced for</th>
                    <th className="px-2 py-2.5 text-right" title="Most recent year-on-year earnings growth — one reading, not a trend">Delivered</th>
                    <th className="px-2 py-2.5 text-right" title="Priced for minus delivered (one quarter). Positive = a promise still to be kept.">Gap 1Q</th>
                    <th className="px-2 py-2.5 text-right" title="Return on equity, derived as P/B divided by P/E. Backward-looking average over ALL capital — not the return on the next rupee invested — and inflated by leverage and by a thin equity base.">ROE</th>
                    <th className="px-2 py-2.5 text-right" title="Multi-year EPS CAGR — how fast earnings have actually compounded. Needs eps_history.json.">Normalised</th>
                    <th className="px-2 py-2.5 text-right" title="Embedded growth annualised MINUS the multi-year CAGR. Two figures: assuming the forward estimate is 1 year out, and 2 years out. A longer horizon halves the gap — the units matter.">Gap vs CAGR</th>
                    <th className="px-2 py-2.5">Bar · growth / return</th>
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((r) => (
                    <tr key={r.symbol} className="border-b border-slate-50 hover:bg-slate-50/60">
                      <td className="px-3 py-2 whitespace-nowrap">
                        <a href={stockHref(r.symbol)} onClick={(e) => onSymbolClick(e, r.symbol)}
                          className="font-black text-slate-800 hover:text-indigo-600 hover:underline decoration-dotted underline-offset-2">
                          {r.symbol}
                        </a>
                        {r.cyclical_caution && (
                          <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                            title="Delivered growth is negative while implied growth is high — the signature of a cyclical at a trough. Depressed trailing earnings inflate the implied figure mechanically. This gap is the cycle, not necessarily a mispricing.">
                            cyclical
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2 text-slate-500 whitespace-nowrap">{r.sector}</td>
                      <td className="px-2 py-2 text-right font-mono text-slate-500">{r.weight ?? '—'}</td>
                      <td className="px-2 py-2 text-right font-mono text-slate-500">{r.trailing_pe}</td>
                      <td className="px-2 py-2 text-right font-mono text-slate-400">{r.forward_pe}</td>
                      <td className="px-2 py-2 text-right font-mono text-slate-700">
                        {r.implied_growth_pct >= 0 ? '+' : ''}{Math.round(r.implied_growth_pct)}%
                      </td>
                      <td className={`px-2 py-2 text-right font-mono ${r.delivered_growth_pct < 0 ? 'text-rose-600' : 'text-slate-700'}`}>
                        {r.delivered_growth_pct >= 0 ? '+' : ''}{Math.round(r.delivered_growth_pct)}%
                      </td>
                      <td className={`px-2 py-2 text-right font-mono font-black ${
                        r.gap_pp >= hiT ? 'text-rose-600' : r.gap_pp <= loT ? 'text-emerald-700' : 'text-slate-600'}`}>
                        {r.gap_pp >= 0 ? '+' : ''}{Math.round(r.gap_pp)}pp
                      </td>
                      <td className="px-2 py-2 text-right font-mono whitespace-nowrap">
                        {r.roe_pct != null ? (
                          <span className={r.roe_pct >= 15 ? 'text-slate-800 font-bold' : 'text-slate-500'}
                            title={r.quality_quadrant ?? undefined}>
                            {r.roe_pct}%
                            {r.roe_thin_equity && (
                              <span className="text-amber-600" title="Very high ROE usually signals a thin equity base — high payout, buybacks, negative working capital — rather than superior operating returns."> †</span>
                            )}
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-slate-600">
                        {r.normalized_growth_pct != null ? (
                          <span title={`${r.normalized_basis ?? ''}${r.normalized_sign_change ? ' · series contains a loss year — read with caution' : ''}`}>
                            {r.normalized_growth_pct >= 0 ? '+' : ''}{Math.round(r.normalized_growth_pct)}%
                            {r.normalized_sign_change && <span className="text-amber-600"> *</span>}
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2 text-right font-mono whitespace-nowrap">
                        {r.gap_vs_normalized_pp != null ? (
                          <span title={`Embedded ${Math.round(r.implied_growth_pct)}% total. Annualised: ${Math.round(r.implied_annualised_1y_pct ?? 0)}% over 1yr, ${Math.round(r.implied_annualised_2y_pct ?? 0)}% over 2yr.`}>
                            <b className={r.gap_vs_normalized_pp >= hiT ? 'text-rose-600'
                              : r.gap_vs_normalized_pp <= loT ? 'text-emerald-700' : 'text-slate-600'}>
                              {r.gap_vs_normalized_pp >= 0 ? '+' : ''}{Math.round(r.gap_vs_normalized_pp)}
                            </b>
                            <span className="text-slate-400 text-[10px]">
                              {' / '}{r.gap_vs_normalized_2y_pp != null
                                ? `${r.gap_vs_normalized_2y_pp >= 0 ? '+' : ''}${Math.round(r.gap_vs_normalized_2y_pp)}` : '—'}pp
                            </span>
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${BAND_STYLE[r.band]}`}>
                          {r.band}
                        </span>
                        {r.quality_quadrant && (
                          <span className={`ml-1 text-[9px] font-bold px-1.5 py-0.5 rounded-full border ${QUADRANT_STYLE[r.quality_quadrant] ?? ''}`}
                            title="Embedded growth crossed with return on equity. Descriptive, not a verdict.">
                            {r.quality_quadrant.replace('priced for ', '').replace(' · ', ' / ')}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3">
              <p className="text-[10px] text-slate-500 leading-relaxed">
                <b className="text-slate-600">How to read this:</b> {g.note}
              </p>
            </div>
          </div>
        );
      })() : (
        <div className={card}>
          <p className="text-[11px] text-slate-400">
            Expectation gap unavailable — it needs both a forward multiple and a delivered-growth
            figure. Re-run the scan; if it stays empty, the fundamentals fetch is not returning
            earningsGrowth.
          </p>
        </div>
      ))}

      {/* A stock tab can only be opened from the table, so `view` is always loaded here —
          but a re-run that came back thin could still drop the row, hence the fallback. */}
      {activeStock && (
        activeRow
          ? <Nifty50StockDetail row={activeRow} view={view} embedded />
          : (
            <div className={card}>
              <p className="text-sm font-bold text-slate-700">{activeStock} isn't in the current scan.</p>
              <p className="text-[11px] text-slate-500 mt-1">
                Re-run the scan, or close this tab.
              </p>
            </div>
          )
      )}

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
                  <SortTh k="implied">Priced for</SortTh>
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
                      {/* Left-click opens a tab in this panel's strip; cmd/ctrl/middle-click
                          falls through to the href and opens the standalone page. The rest
                          of the row still expands inline for a quick peek. */}
                      <a href={stockHref(r.symbol)}
                        onClick={(e) => onSymbolClick(e, r.symbol)}
                        className="font-black text-slate-800 hover:text-indigo-600 hover:underline decoration-dotted underline-offset-2"
                        title={`Open ${r.symbol} as a tab — full detail, room to read (⌘/Ctrl-click for its own window)`}>
                        {r.symbol}
                      </a>
                      {r.symbol_note && (
                        <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                          title={r.symbol_note}>→{r.yahoo_symbol}</span>
                      )}
                      {r.last == null && (
                        <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-rose-50 text-rose-600 border border-rose-200"
                          title="No price returned by the data source for any known ticker for this name">no data</span>
                      )}
                      {r.last != null && r.partial_history && (
                        <span className="ml-1 text-[9px] font-black px-1 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-300"
                          title={r.history_note ?? `Only ${r.bars ?? 0} sessions of history — windows longer than that are left blank rather than shortened`}>
                          {r.bars ?? 0}d
                        </span>
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
                        /* A dashed border marks an index-median verdict. Zomato at 670x P/E
                           against the whole-index median of 30x rendered identically to a
                           real peer comparison; the distinction lived only in the tooltip. */
                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${VERDICT_STYLE[r.verdict.label]} ${r.verdict.basis === 'index' ? 'border-dashed opacity-80' : ''}`}
                          title={r.verdict.basis === 'index'
                            ? `${r.verdict.metric.toUpperCase()} ${r.verdict.vs_median_pct >= 0 ? '+' : ''}${r.verdict.vs_median_pct}% vs the WHOLE-INDEX median ${r.verdict.sector_median} — ${r.sector} has fewer than 3 valued peers, so this is not a peer comparison. Treat it as weak evidence.`
                            : `${r.verdict.metric.toUpperCase()} ${r.verdict.vs_median_pct >= 0 ? '+' : ''}${r.verdict.vs_median_pct}% vs ${r.sector} peer median ${r.verdict.sector_median}${r.verdict.peers ? ` (${r.verdict.peers} peers)` : ''}`}>
                          {r.verdict.label} {r.verdict.vs_median_pct >= 0 ? '+' : ''}{r.verdict.vs_median_pct}%
                          {r.verdict.basis === 'index' && <span className="ml-1 font-normal opacity-70">idx</span>}
                        </span>
                      ) : (
                        <span className="text-slate-300"
                          title={r.fundamentals_ok === false
                            ? 'No verdict — the fundamentals fetch failed for this name (not the same as having no P/E)'
                            : 'No verdict — P/E and P/B unavailable'}>
                          {r.fundamentals_ok === false ? '⚠' : '—'}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-right whitespace-nowrap">
                      {r.expectation?.implied_eps_growth_pct != null ? (
                        <span className={`font-mono font-bold ${r.expectation.implied_eps_growth_pct >= 60 ? 'text-amber-700' : r.expectation.implied_eps_growth_pct < 0 ? 'text-slate-400' : 'text-slate-700'}`}
                          title={`Consensus is priced for ${r.expectation.implied_eps_growth_pct}% EPS growth (trailing P/E ${r.pe ?? '—'} / forward P/E ${r.fwd_pe ?? '—'} − 1). This is the bar a result has to clear — a company can report strong absolute growth and still fall short of it.`}>
                          {r.expectation.implied_eps_growth_pct >= 0 ? '+' : ''}{Math.round(r.expectation.implied_eps_growth_pct)}%
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
                      {/* 17 header columns; the empty chevron cell above covers one, so 16 */}
                      <td colSpan={16} className="px-2 py-3">
                        <a href={stockHref(r.symbol)}
                          onClick={(e) => onSymbolClick(e, r.symbol)}
                          className="inline-flex items-center gap-1 text-[10px] font-black uppercase tracking-wide text-indigo-600 hover:underline mb-2">
                          Open full view as a tab →
                        </a>
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
                            <span className="text-[10px] text-slate-400 ml-auto"
                              title="Dividend-adjusted closes, so these sit slightly below NSE's published 52-week extremes — the gap widens with the yield.">
                              52W range ₹{r.lo_52w.toLocaleString('en-IN')} – ₹{r.hi_52w.toLocaleString('en-IN')}
                              {r.partial_history ? ` (${r.bars ?? 0} sessions only)` : ''}
                            </span>
                          )}
                        </div>
                        {r.symbol_note && (
                          <p className="text-[10px] text-amber-700 mb-3">{r.symbol_note}</p>
                        )}
                        {r.history_note && (
                          <p className="text-[10px] text-amber-700 mb-3">{r.history_note}</p>
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
                                vs the {r.verdict.basis === 'sector'
                                  ? `${r.sector} peer${r.verdict.peers ? ` (${r.verdict.peers} names)` : ''}`
                                  : 'whole-index (small sector)'} median of{' '}
                                <b>{r.verdict.sector_median}</b> → <b>{r.verdict.label}</b> (thresholds: ≥+25% rich, ≤−25% cheap).
                                A premium can be earned (growth/quality) and a discount deserved (weak fundamentals) — this flags the
                                question, it doesn't answer it.
                                {r.verdict.basis === 'index' && (
                                  <b className="text-amber-700"> {r.sector} has fewer than 3 valued peers, so this compares
                                  the stock to the index as a whole rather than to anything like it — weak evidence, not a peer verdict.</b>
                                )}
                              </p>
                            ) : (
                              <p className="text-[11px] text-slate-400">
                                {r.fundamentals_ok === false
                                  ? 'No verdict — the fundamentals fetch failed for this name. That is a data gap, not a statement about the company; re-run the scan.'
                                  : 'No verdict — P/E and P/B unavailable from the data source.'}
                              </p>
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
                                ) : r.verdict?.reversion_note ? (
                                  <span className="text-slate-400 italic">{r.verdict.reversion_note}</span>
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
                            {r.expectation?.implied_eps_growth_pct != null && (
                              <div className="rounded-xl border border-purple-200 bg-purple-50/50 px-3 py-2 mb-3">
                                <div className="text-[10px] font-black uppercase text-purple-700 mb-1">
                                  Priced for — the growth already in the price
                                  {r.expectation.as_of && (
                                    <span className="font-normal text-purple-400 ml-1.5">
                                      analyst data as of {r.expectation.as_of.slice(0, 10)}
                                    </span>
                                  )}
                                </div>
                                <div className="flex flex-wrap gap-4 text-[11px] text-slate-600">
                                  <span>
                                    <span className="text-slate-400">Implied EPS growth</span>{' '}
                                    <span className="font-mono font-bold text-purple-800">
                                      {r.expectation.implied_eps_growth_pct >= 0 ? '+' : ''}
                                      {Math.round(r.expectation.implied_eps_growth_pct)}%
                                    </span>
                                    <span className="text-slate-400"> ({r.pe ?? '—'}× → {r.fwd_pe ?? '—'}×)</span>
                                  </span>
                                  {r.expectation.target_mean != null && (
                                    <span>
                                      <span className="text-slate-400">Target</span>{' '}
                                      <span className="font-mono font-bold">₹{r.expectation.target_mean.toLocaleString('en-IN')}</span>
                                      {r.expectation.target_upside_pct != null && (
                                        <span className="text-slate-400"> ({fmtPct(r.expectation.target_upside_pct)})</span>
                                      )}
                                    </span>
                                  )}
                                  {r.expectation.dispersion_pct != null && (
                                    <span title="Spread between the highest and lowest analyst target, as a share of the mean. Wide = low agreement. Only meaningful once corporate actions are normalised — a stale pre-bonus target beside a post-bonus price manufactures disagreement nobody has.">
                                      <span className="text-slate-400">Spread</span>{' '}
                                      <span className="font-mono font-bold">{r.expectation.dispersion_pct}%</span>
                                      {r.expectation.target_low != null && r.expectation.target_high != null && (
                                        <span className="text-slate-400"> (₹{r.expectation.target_low.toLocaleString('en-IN')}–₹{r.expectation.target_high.toLocaleString('en-IN')})</span>
                                      )}
                                    </span>
                                  )}
                                  {r.expectation.analysts != null && (
                                    <span className="text-slate-400">{r.expectation.analysts} analysts</span>
                                  )}
                                  {r.expectation.next_earnings && (
                                    <span className="text-slate-400">reports {r.expectation.next_earnings}</span>
                                  )}
                                </div>
                                <p className="text-[10px] text-slate-400 mt-1.5">
                                  Trailing P/E ÷ forward P/E − 1. No weights are set by hand — it is what the two
                                  multiples already imply. Yahoo's forward P/E is a next-fiscal-year consensus against a
                                  trailing-twelve-month denominator, so read this as growth embedded over the next one to
                                  two years, not as a hurdle for the next quarter. It is why a company can report strong
                                  absolute growth and still fall: good in absolute terms, short of what was priced in.
                                </p>
                              </div>
                            )}
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
            {/* Provenance for the two measured layers. Both were computed server-side and
                thrown away here — including the divergence count, which is the whole point
                of the Results column. */}
            {(view.reactions_meta || view.expectation_meta) && (
              <p className="text-[10px] text-slate-500 leading-relaxed">
                {view.reactions_meta && (
                  <>
                    <b className="text-slate-600">Results reactions:</b>{' '}
                    {view.reactions_meta.events} announcements across {view.reactions_meta.names} names
                    (measured to {view.reactions_meta.as_of}) —{' '}
                    <b>{view.reactions_meta.diverging}</b> currently trade results differently from their
                    own full-sample habit.
                  </>
                )}
                {view.expectation_meta && (
                  <>
                    {view.reactions_meta ? ' · ' : ''}
                    <b className="text-slate-600">Expectation:</b>{' '}
                    {view.expectation_meta.snapshots} snapshot{view.expectation_meta.snapshots === 1 ? '' : 's'} from{' '}
                    {view.expectation_meta.source}, captured {String(view.expectation_meta.captured_at).slice(0, 10)}
                    {view.expectation_meta.median_implied_eps_growth_pct != null && (
                      <> — index median priced for {fmtPct(view.expectation_meta.median_implied_eps_growth_pct)} EPS growth</>
                    )}
                    {view.expectation_meta.snapshots === 1 &&
                      ' (single capture — targets and dispersion will drift until the pre-print snapshot job runs regularly)'}
                    .
                  </>
                )}
              </p>
            )}
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
