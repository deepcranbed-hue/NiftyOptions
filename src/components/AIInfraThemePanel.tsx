import React, { useCallback, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Server, X } from 'lucide-react';
import { AIInfraCompanyPage } from './AIInfraCompanyPage';

/**
 * AIInfraThemePanel — thematic view: India-listed beneficiaries of the AI
 * infrastructure buildout (data centres, power, cooling, fibre, networking,
 * compute).
 *
 * Data: /api/ai-infra-theme (curated ai_infra_theme.json at repo root);
 * `?quotes=true` enriches with yfinance NSE quotes (cached 15 min server-side).
 * Follows SECTOR_INTELLIGENCE_FRAMEWORK.md: this is the L1 evidence layer for
 * an explicit hypothesis set — dated evidence with decay, categorical exposure
 * states, no invented scores.
 */

type Evidence = { date: string; note: string; source: string };
type Quote = {
  last: number; d1_pct: number | null; m3_pct: number | null; y1_pct: number | null;
  since_pct?: number | null; hi_52w?: number; lo_52w?: number; as_of: string;
};
type Stance = 'up' | 'sideways' | 'down';
type Outlook = {
  stance: Stance; confidence: 'low' | 'medium' | 'high';
  rationale: string; catalysts: { when: string; note: string }[]; watch: string;
  as_of: string; valid_till: string;
};
type Grade = 'buy' | 'hold' | 'sell';
/**
 * A 12-month call. Per SECTOR_INTELLIGENCE_FRAMEWORK.md the three inputs are stored
 * SEPARATELY and never combined into a number — the framework's own rule is
 * "explicit categorical states + evidence, not invented scores". Read the inputs and
 * disagree with the weighting; the grade alone is not the product.
 */
type Grade12m = {
  grade: Grade; conviction: 'low' | 'medium' | 'high';
  evidence_strength: 'disclosed-revenue' | 'disclosed-orders' | 'announced-only' | 'none';
  exposure: Company['exposure'];
  priced_in: 'little' | 'partly' | 'fully' | 'beyond-delivery';
  valuation: {
    pe_ttm: number | null; pe_note: string; last?: number | null;
    from_52w_hi_pct?: number | null; y1_pct?: number | null; m3_pct?: number | null;
    source: string; as_of: string;
  };
  rationale: string; changes_if: string; as_of: string; valid_till: string;
};
type Company = {
  symbol: string; name: string; segment: string; mcap_bucket: string;
  exposure: 'pure-play' | 'significant' | 'partial';
  fno: boolean | null; thesis: string; evidence: Evidence[]; risk: string;
  quote?: Quote | null; outlook_3m?: Outlook; grade_12m?: Grade12m;
};
type Hypothesis = { id: string; text: string; supporting: string[]; contradicting: string[]; status: string };
type Segment = { id: string; label: string };
type Theme = {
  theme: string; as_of: string; thesis: string; method_note: string;
  hypotheses: Hypothesis[]; segments: Segment[]; companies: Company[];
  excluded_watch: { name: string; why: string }[]; disclaimer: string;
  outlook_note?: string; grading_note?: string; quotes_error?: string;
};

const card = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';
const hdr = 'text-[11px] font-black uppercase tracking-wide text-slate-400';

const EXPOSURE_STYLE: Record<Company['exposure'], string> = {
  'pure-play': 'bg-emerald-50 text-emerald-700 border-emerald-200',
  significant: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  partial: 'bg-slate-50 text-slate-500 border-slate-200',
};
const EXPOSURE_ORDER: Record<Company['exposure'], number> = { 'pure-play': 0, significant: 1, partial: 2 };

const STANCE_META: Record<Stance, { glyph: string; cls: string; label: string }> = {
  up: { glyph: '▲', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', label: 'up' },
  sideways: { glyph: '→', cls: 'bg-amber-50 text-amber-700 border-amber-200', label: 'sideways' },
  down: { glyph: '▼', cls: 'bg-rose-50 text-rose-600 border-rose-200', label: 'down' },
};

const GRADE_META: Record<Grade, { cls: string; solid: string }> = {
  buy: { cls: 'bg-emerald-50 text-emerald-700 border-emerald-300', solid: 'bg-emerald-600' },
  hold: { cls: 'bg-slate-50 text-slate-600 border-slate-300', solid: 'bg-slate-500' },
  sell: { cls: 'bg-rose-50 text-rose-700 border-rose-300', solid: 'bg-rose-600' },
};
const EVIDENCE_LABEL: Record<Grade12m['evidence_strength'], string> = {
  'disclosed-revenue': 'company quantifies DC revenue',
  'disclosed-orders': 'named DC contracts with values',
  'announced-only': 'capacity or intent, no contracted revenue',
  none: 'thematic association only',
};
const PRICED_LABEL: Record<Grade12m['priced_in'], string> = {
  little: 'little priced in',
  partly: 'partly priced in',
  fully: 'fully priced in',
  'beyond-delivery': 'priced beyond what has been delivered',
};

function GradeBadge({ g }: { g: Grade12m | undefined }) {
  if (!g) return <span className="text-slate-300">—</span>;
  const m = GRADE_META[g.grade];
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-black uppercase px-2 py-0.5 rounded-full border ${m.cls}`}
      title={`12-month grade · conviction ${g.conviction} · ${EVIDENCE_LABEL[g.evidence_strength]} · ${PRICED_LABEL[g.priced_in]} · valid to ${g.valid_till}`}>
      {g.grade}
      <span className="font-normal opacity-70">· {g.conviction[0]}</span>
    </span>
  );
}

/**
 * Price-vs-lean check — pure DISPLAY logic, computed only when the user has
 * pressed the quotes button. ±4% thresholds are a rendering convention, not a
 * validated signal: it answers "is price so far agreeing with the lean?".
 */
function leanCheck(o: Outlook | undefined, q: Quote | null | undefined):
  { label: string; cls: string } | null {
  if (!o || !q || q.since_pct == null) return null;
  const m = q.since_pct;
  if (Math.abs(m) < 4) return { label: `early (${m >= 0 ? '+' : ''}${m}%)`, cls: 'bg-slate-50 text-slate-500 border-slate-200' };
  const agrees = (o.stance === 'up' && m > 0) || (o.stance === 'down' && m < 0);
  const sideways = o.stance === 'sideways';
  if (sideways) return { label: `broke range (${m >= 0 ? '+' : ''}${m}%)`, cls: 'bg-amber-50 text-amber-700 border-amber-200' };
  return agrees
    ? { label: `tracking (${m >= 0 ? '+' : ''}${m}%)`, cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
    : { label: `against (${m >= 0 ? '+' : ''}${m}%)`, cls: 'bg-rose-50 text-rose-600 border-rose-200' };
}

function StanceBadge({ o }: { o: Outlook | undefined }) {
  if (!o) return <span className="text-slate-300">—</span>;
  const m = STANCE_META[o.stance];
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-black px-2 py-0.5 rounded-full border ${m.cls}`}
      title={`3-month lean · confidence ${o.confidence} · valid to ${o.valid_till}`}>
      {m.glyph} {m.label}
      <span className="font-normal opacity-70">· {o.confidence[0]}</span>
    </span>
  );
}

function Pct({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <span className={`font-mono ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
      {v >= 0 ? '+' : ''}{v}%
    </span>
  );
}

const COMPANY_TAB = 'co:';
// Past this the strip wraps to a second row and stops reading as a strip — same
// ceiling as the Nifty 50 panel, so the two tables behave identically.
const MAX_COMPANY_TABS = 6;

export function AIInfraThemePanel() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [loading, setLoading] = useState(false);
  const [quotesLoading, setQuotesLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hasQuotes, setHasQuotes] = useState(false);
  // Companies open as tabs in THIS panel, not as browser windows. Previously the
  // symbol was a bare <a target="_blank">, so every click threw the user into a new
  // browser tab and lost the table's filters and scroll position — while the Nifty 50
  // table right next to it opened an in-panel tab. Same gesture, two behaviours.
  const [tab, setTab] = useState<string>('table');
  const [openCompanies, setOpenCompanies] = useState<string[]>([]);

  const openCompany = useCallback((sym: string) => {
    setOpenCompanies((prev) => {
      if (prev.includes(sym)) return prev;
      const next = [...prev, sym];
      return next.length > MAX_COMPANY_TABS ? next.slice(next.length - MAX_COMPANY_TABS) : next;
    });
    setTab(`${COMPANY_TAB}${sym}`);
  }, []);

  const closeCompany = useCallback((sym: string) => {
    setOpenCompanies((prev) => prev.filter((s) => s !== sym));
    setTab((t) => (t === `${COMPANY_TAB}${sym}` ? 'table' : t));
  }, []);

  // Modified clicks still belong to the browser: cmd/ctrl/shift/middle-click follows the
  // href to the standalone route. Only a plain left-click is intercepted.
  const onCompanyClick = useCallback((e: React.MouseEvent, sym: string) => {
    e.stopPropagation();          // the row itself toggles the inline peek
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    openCompany(sym);
  }, [openCompany]);

  const activeCompany = tab.startsWith(COMPANY_TAB) ? tab.slice(COMPANY_TAB.length) : null;

  const [segFilter, setSegFilter] = useState<string>('all');
  const [expFilter, setExpFilter] = useState<string>('all');
  const [stanceFilter, setStanceFilter] = useState<string>('all');
  const [gradeFilter, setGradeFilter] = useState<string>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(async (withQuotes: boolean, force = false) => {
    withQuotes ? setQuotesLoading(true) : setLoading(true);
    setErr(null);
    try {
      const qs = withQuotes ? `?quotes=true${force ? '&force_quotes=true' : ''}` : '';
      const r = await fetch(`/api/ai-infra-theme${qs}`);
      const j = await r.json();
      if (j.success) {
        setTheme(j.theme);
        if (withQuotes) setHasQuotes(true);
        if (j.theme.quotes_error) setErr(`Quotes unavailable — ${j.theme.quotes_error}`);
      } else setErr(j.detail || 'Failed to load theme');
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setLoading(false); setQuotesLoading(false);
    }
  }, []);

  // NO auto-run — same rule as MarketStateView: everything loads/computes only
  // when the user presses a button. The dataset button reads the curated JSON;
  // the quotes button runs the yfinance fetch + since-call lean check.

  const toggle = (sym: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });

  const segLabel = useMemo(() => {
    const m: Record<string, string> = {};
    theme?.segments.forEach((s) => { m[s.id] = s.label; });
    return m;
  }, [theme]);

  const filtered = useMemo(() => {
    if (!theme) return [];
    return theme.companies
      .filter((c) => segFilter === 'all' || c.segment === segFilter)
      .filter((c) => expFilter === 'all' || c.exposure === expFilter)
      .filter((c) => stanceFilter === 'all' || c.outlook_3m?.stance === stanceFilter)
      .filter((c) => gradeFilter === 'all' || c.grade_12m?.grade === gradeFilter)
      .sort((a, b) =>
        a.segment === b.segment
          ? EXPOSURE_ORDER[a.exposure] - EXPOSURE_ORDER[b.exposure]
          : a.segment.localeCompare(b.segment));
  }, [theme, segFilter, expFilter, stanceFilter, gradeFilter]);

  const stanceCounts = useMemo(() => {
    const n: Record<Stance, number> = { up: 0, sideways: 0, down: 0 };
    theme?.companies.forEach((c) => { if (c.outlook_3m) n[c.outlook_3m.stance] += 1; });
    return n;
  }, [theme]);

  const gradeCounts = useMemo(() => {
    const n: Record<Grade, number> = { buy: 0, hold: 0, sell: 0 };
    theme?.companies.forEach((c) => { if (c.grade_12m) n[c.grade_12m.grade] += 1; });
    return n;
  }, [theme]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <Server className="w-5 h-5 text-indigo-600" /> AI Infrastructure — India beneficiaries
          </h2>
          <p className="text-xs text-slate-500">
            Data centres · power · cooling · fibre · networking · compute — evidence as of {theme?.as_of ?? '—'}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => load(false)} disabled={loading}
            className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
            title="Reads the curated ai_infra_theme.json — no market data fetched">
            {loading ? 'Loading…' : theme ? 'Reload dataset' : 'Load dataset'}
          </button>
          {theme && (
            <button onClick={() => load(true)} disabled={quotesLoading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-600 text-white disabled:opacity-50"
              title="Fetches NSE quotes (yfinance) and computes 1D/3M/1Y moves + the price-vs-lean check">
              <RefreshCw className={`w-3.5 h-3.5 ${quotesLoading ? 'animate-spin' : ''}`} />
              {quotesLoading ? 'Computing…' : hasQuotes ? 'Re-run quotes + lean check' : 'Run quotes + lean check'}
            </button>
          )}
          {hasQuotes && (
            <button onClick={() => load(true, true)} disabled={quotesLoading}
              className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-100 text-slate-600 disabled:opacity-50"
              title="Bypass the 15-min server-side quote cache">
              Force
            </button>
          )}
        </div>
      </div>

      {err && <div className="text-xs text-rose-600">{err}. Is the backend running?</div>}

      {!theme && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-slate-900 font-black">Load dataset</span> to open the AI-infra view.
          </p>
          <p className="text-[11px] text-slate-400 mt-1">
            Nothing runs automatically — the dataset is a file read; quotes and the price-vs-lean
            check only compute when you press their button.
          </p>
        </div>
      )}

      {theme && openCompanies.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 bg-slate-100 rounded-xl p-1">
          <button onClick={() => setTab('table')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition ${tab === 'table' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
            Theme table
          </button>
          {openCompanies.map((sym) => {
            const on = tab === `${COMPANY_TAB}${sym}`;
            return (
              <span key={sym}
                className={`flex items-center rounded-lg transition ${on ? 'bg-white shadow-sm' : 'hover:bg-slate-200/60'}`}>
                <button onClick={() => setTab(`${COMPANY_TAB}${sym}`)}
                  className={`pl-3 pr-1.5 py-1.5 text-xs font-bold ${on ? 'text-indigo-700' : 'text-slate-500 hover:text-slate-700'}`}>
                  {sym}
                </button>
                <button onClick={() => closeCompany(sym)} title={`Close ${sym}`}
                  className="pr-2 pl-0.5 py-1.5 text-slate-300 hover:text-slate-600">
                  <X className="w-3 h-3" />
                </button>
              </span>
            );
          })}
        </div>
      )}

      {theme && activeCompany && (
        <AIInfraCompanyPage symbol={activeCompany} embedded />
      )}

      {theme && !activeCompany && (
        <>
          {/* Thesis */}
          <div className={card}>
            <span className={hdr}>Theme thesis</span>
            <p className="text-xs text-slate-600 mt-2 leading-relaxed">{theme.thesis}</p>
            <p className="text-[9px] text-slate-400 mt-2 leading-snug">{theme.method_note}</p>
          </div>

          {/* Hypothesis manager */}
          <div className="grid lg:grid-cols-2 gap-4">
            {theme.hypotheses.map((h) => (
              <div key={h.id} className={card}>
                <div className="flex items-center justify-between gap-2">
                  <span className={hdr}>{h.id} — hypothesis</span>
                  <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${
                    h.status === 'supported' ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
                    {h.status}
                  </span>
                </div>
                <p className="text-xs font-bold text-slate-700 mt-1.5">{h.text}</p>
                <div className="mt-2 space-y-1">
                  {h.supporting.map((s, i) => (
                    <div key={i} className="text-[11px] text-emerald-700 flex items-start gap-1.5">
                      <span className="font-black shrink-0">✓</span><span>{s}</span>
                    </div>
                  ))}
                  {h.contradicting.map((s, i) => (
                    <div key={i} className="text-[11px] text-rose-600 flex items-start gap-1.5">
                      <span className="font-black shrink-0">✗</span><span>{s}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Filters */}
          <div className={card}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">Segment</span>
              <button onClick={() => setSegFilter('all')}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${segFilter === 'all' ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                All ({theme.companies.length})
              </button>
              {theme.segments.map((s) => {
                const n = theme.companies.filter((c) => c.segment === s.id).length;
                if (!n) return null;
                return (
                  <button key={s.id} onClick={() => setSegFilter(segFilter === s.id ? 'all' : s.id)}
                    className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${segFilter === s.id ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                    {s.label} ({n})
                  </button>
                );
              })}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">Exposure</span>
              {(['all', 'pure-play', 'significant', 'partial'] as const).map((e) => (
                <button key={e} onClick={() => setExpFilter(expFilter === e && e !== 'all' ? 'all' : e)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${expFilter === e ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                  {e}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">3M lean</span>
              <button onClick={() => setStanceFilter('all')}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${stanceFilter === 'all' ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                all
              </button>
              {(['up', 'sideways', 'down'] as const).map((s) => (
                <button key={s} onClick={() => setStanceFilter(stanceFilter === s ? 'all' : s)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${stanceFilter === s ? 'bg-slate-900 text-white border-slate-900' : `${STANCE_META[s].cls} hover:opacity-80`}`}>
                  {STANCE_META[s].glyph} {s} ({stanceCounts[s]})
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <span className="text-[10px] font-black uppercase text-slate-400 mr-1">12M grade</span>
              <button onClick={() => setGradeFilter('all')}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition ${gradeFilter === 'all' ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                all
              </button>
              {(['buy', 'hold', 'sell'] as const).map((g) => (
                <button key={g} onClick={() => setGradeFilter(gradeFilter === g ? 'all' : g)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-bold uppercase border transition ${gradeFilter === g ? 'bg-slate-900 text-white border-slate-900' : `${GRADE_META[g].cls} hover:opacity-80`}`}>
                  {g} ({gradeCounts[g]})
                </button>
              ))}
            </div>
          </div>

          {/* Company table */}
          {!hasQuotes && (
            <div className="rounded-xl border border-indigo-200 bg-indigo-50/50 px-3 py-2 text-[11px] text-indigo-700">
              Prices aren't loaded yet — press <b>Run quotes + lean check</b> above to fetch live NSE prices
              (Price ₹, 1D/3M/1Y, since-call check) for all {theme.companies.length} names. Nothing fetches automatically.
            </div>
          )}
          <div className={`${card} overflow-x-auto p-0`}>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase font-black text-slate-400 border-b border-slate-100">
                  <th className="px-3 py-2.5 w-6" />
                  <th className="px-2 py-2.5">Company</th>
                  <th className="px-2 py-2.5">Segment</th>
                  <th className="px-2 py-2.5">Exposure</th>
                  <th className="px-2 py-2.5">3M lean</th>
                  <th className="px-2 py-2.5">12M grade</th>
                  <th className="px-2 py-2.5">Cap</th>
                  {hasQuotes && (<>
                    <th className="px-2 py-2.5 text-right">Price ₹</th>
                    <th className="px-2 py-2.5 text-right">1D</th>
                    <th className="px-2 py-2.5 text-right">3M</th>
                    <th className="px-2 py-2.5 text-right">1Y</th>
                    <th className="px-2 py-2.5">Lean check</th>
                  </>)}
                  <th className="px-2 py-2.5">Thesis</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => {
                  const open = expanded.has(c.symbol);
                  return (
                    <React.Fragment key={c.symbol}>
                      <tr onClick={() => toggle(c.symbol)}
                        className="border-b border-slate-50 hover:bg-slate-50/60 cursor-pointer align-top">
                        <td className="px-3 py-2 text-slate-400">
                          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                        </td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          {/* The symbol opens the full company page in a new tab; the rest
                              of the row still toggles the inline peek. stopPropagation so
                              one click does not do both. */}
                          <a href={`/intel/ai-infra/${encodeURIComponent(c.symbol)}`}
                            onClick={(e) => onCompanyClick(e, c.symbol)}
                            title={`${c.name} — opens as a tab here (⌘/Ctrl-click for its own window)`}
                            className="font-black text-slate-800 hover:text-indigo-600 hover:underline">
                            {c.symbol}
                          </a>
                          {c.fno && <span className="ml-1.5 text-[9px] font-black px-1 py-0.5 rounded bg-violet-50 text-violet-600 border border-violet-200">F&O</span>}
                          <div className="text-[10px] text-slate-400 font-normal">{c.name}</div>
                        </td>
                        <td className="px-2 py-2 text-slate-500 whitespace-nowrap">{segLabel[c.segment] ?? c.segment}</td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${EXPOSURE_STYLE[c.exposure]}`}>{c.exposure}</span>
                        </td>
                        <td className="px-2 py-2 whitespace-nowrap"><StanceBadge o={c.outlook_3m} /></td>
                        <td className="px-2 py-2 whitespace-nowrap"><GradeBadge g={c.grade_12m} /></td>
                        <td className="px-2 py-2 text-slate-500">{c.mcap_bucket}</td>
                        {hasQuotes && (<>
                          <td className="px-2 py-2 text-right font-mono font-bold text-slate-900"
                            title={c.quote ? `latest daily close (${c.quote.as_of}) — Yahoo, ~15-min delayed intraday` : 'no quote from source'}>
                            {c.quote ? c.quote.last.toLocaleString('en-IN') : '—'}
                          </td>
                          <td className="px-2 py-2 text-right"><Pct v={c.quote?.d1_pct} /></td>
                          <td className="px-2 py-2 text-right"><Pct v={c.quote?.m3_pct} /></td>
                          <td className="px-2 py-2 text-right"><Pct v={c.quote?.y1_pct} /></td>
                          <td className="px-2 py-2 whitespace-nowrap">
                            {(() => {
                              const lc = leanCheck(c.outlook_3m, c.quote);
                              return lc
                                ? <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${lc.cls}`}
                                    title={`price move since the ${c.outlook_3m?.as_of} call vs the '${c.outlook_3m?.stance}' lean`}>{lc.label}</span>
                                : <span className="text-slate-300">—</span>;
                            })()}
                          </td>
                        </>)}
                        <td className="px-2 py-2 text-slate-600 leading-snug min-w-[240px]">{c.thesis}</td>
                      </tr>
                      {open && (
                        <tr className="border-b border-slate-100 bg-slate-50/50">
                          <td />
                          <td colSpan={hasQuotes ? 12 : 7} className="px-2 py-3">
                            {/* Price header — current (delayed) price front and centre */}
                            {c.quote ? (
                              <div className="flex flex-wrap items-center gap-3 mb-3 text-xs">
                                <span className="text-lg font-black text-slate-900 font-mono">
                                  ₹{c.quote.last.toLocaleString('en-IN')}
                                </span>
                                <Pct v={c.quote.d1_pct} />
                                <span className="text-[10px] text-slate-400">
                                  as of {c.quote.as_of} · Yahoo daily close, ~15-min delayed intraday
                                </span>
                                {c.quote.hi_52w != null && c.quote.lo_52w != null && (
                                  <span className="text-[10px] text-slate-400 ml-auto">
                                    52W range ₹{c.quote.lo_52w.toLocaleString('en-IN')} – ₹{c.quote.hi_52w.toLocaleString('en-IN')}
                                  </span>
                                )}
                              </div>
                            ) : (
                              <p className="text-[10px] text-slate-400 mb-3">
                                No price loaded{hasQuotes ? ' for this symbol (source returned nothing)' : ' — run quotes above to fetch it'}.
                              </p>
                            )}
                            <div className="grid md:grid-cols-3 gap-4">
                              <div>
                                <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Evidence (dated — decays)</div>
                                <div className="space-y-1">
                                  {c.evidence.map((e, i) => (
                                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-2">
                                      <span className="font-mono text-slate-400 shrink-0">{e.date}</span>
                                      <span>{e.note} <span className="text-slate-400">· {e.source}</span></span>
                                    </div>
                                  ))}
                                </div>
                                <div className="text-[10px] font-black uppercase text-slate-400 mt-3 mb-1">Key risk</div>
                                <p className="text-[11px] text-rose-600 leading-snug">{c.risk}</p>
                              </div>
                              {c.outlook_3m ? (
                                <div className="md:col-span-2">
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className="text-[10px] font-black uppercase text-slate-400">3-month outlook</span>
                                    <StanceBadge o={c.outlook_3m} />
                                    <span className="text-[9px] text-slate-400">valid to {c.outlook_3m.valid_till}</span>
                                  </div>
                                  <p className="text-[11px] text-slate-600 leading-snug">{c.outlook_3m.rationale}</p>
                                  <div className="grid sm:grid-cols-2 gap-3 mt-2">
                                    <div>
                                      <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Catalysts (Aug–Oct 2026)</div>
                                      <div className="space-y-1">
                                        {c.outlook_3m.catalysts.map((k, i) => (
                                          <div key={i} className="text-[11px] text-slate-600 flex items-start gap-2">
                                            <span className="font-mono text-slate-400 shrink-0">{k.when}</span>
                                            <span>{k.note}</span>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                    <div>
                                      <div className="text-[10px] font-black uppercase text-slate-400 mb-1">What changes the call</div>
                                      <p className="text-[11px] text-amber-700 leading-snug">{c.outlook_3m.watch}</p>
                                    </div>
                                  </div>
                                </div>
                              ) : (
                                <div className="md:col-span-2 text-[11px] text-slate-400">No 3-month outlook recorded for this name.</div>
                              )}
                              {c.grade_12m && (
                                <div className="md:col-span-2 border-t border-slate-100 pt-3">
                                  <div className="flex flex-wrap items-center gap-2 mb-1">
                                    <span className="text-[10px] font-black uppercase text-slate-400">12-month grade</span>
                                    <GradeBadge g={c.grade_12m} />
                                    <span className="text-[9px] text-slate-400">valid to {c.grade_12m.valid_till}</span>
                                  </div>
                                  {/* The three inputs, shown separately and never summed. */}
                                  <div className="flex flex-wrap gap-1.5 mb-2">
                                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border bg-white text-slate-600 border-slate-200"
                                      title="Evidence quality: does the company quantify data-centre revenue, name contracts, or only announce intent?">
                                      evidence · {EVIDENCE_LABEL[c.grade_12m.evidence_strength]}
                                    </span>
                                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${EXPOSURE_STYLE[c.grade_12m.exposure]}`}
                                      title="Exposure purity — how much of the P&L is actually this theme">
                                      exposure · {c.grade_12m.exposure}
                                    </span>
                                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border bg-white text-slate-600 border-slate-200"
                                      title="From trailing P/E, distance from the 52-week high and the one-year move">
                                      price · {PRICED_LABEL[c.grade_12m.priced_in]}
                                    </span>
                                  </div>
                                  <p className="text-[11px] text-slate-600 leading-snug">{c.grade_12m.rationale}</p>
                                  <div className="grid sm:grid-cols-2 gap-3 mt-2">
                                    <div>
                                      <div className="text-[10px] font-black uppercase text-slate-400 mb-1">Valuation used</div>
                                      <div className="text-[11px] text-slate-600 flex flex-wrap gap-x-4 gap-y-0.5 font-mono">
                                        <span>P/E {c.grade_12m.valuation.pe_ttm ?? 'n/m'}</span>
                                        {c.grade_12m.valuation.y1_pct != null && <span>1Y <Pct v={c.grade_12m.valuation.y1_pct} /></span>}
                                        {c.grade_12m.valuation.from_52w_hi_pct != null && <span>vs 52w hi <Pct v={c.grade_12m.valuation.from_52w_hi_pct} /></span>}
                                      </div>
                                      <p className="text-[10px] text-slate-400 leading-snug mt-1">{c.grade_12m.valuation.pe_note}</p>
                                    </div>
                                    <div>
                                      <div className="text-[10px] font-black uppercase text-slate-400 mb-1">What changes the grade</div>
                                      <p className="text-[11px] text-amber-700 leading-snug">{c.grade_12m.changes_if}</p>
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <div className="px-4 py-8 text-center text-xs text-slate-400">No companies match the current filters.</div>
            )}
          </div>

          {/* Not investible / watch */}
          <div className={card}>
            <span className={hdr}>Not investible on NSE/BSE (context only)</span>
            <div className="mt-2 space-y-1">
              {theme.excluded_watch.map((x, i) => (
                <div key={i} className="text-[11px] text-slate-500">
                  <b className="text-slate-600">{x.name}</b> — {x.why}
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3 space-y-1.5">
            {theme.outlook_note && (
              <p className="text-[10px] text-amber-700 leading-relaxed">
                <b>Outlook note:</b> {theme.outlook_note}
              </p>
            )}
            {theme.grading_note && (
              <p className="text-[10px] text-amber-700 leading-relaxed">
                <b>Grade note:</b> {theme.grading_note}
              </p>
            )}
            {hasQuotes && (
              <p className="text-[10px] text-slate-500 leading-relaxed">
                <b className="text-slate-600">Lean check:</b> price move since each call date vs its lean —
                'early' inside ±4%, 'tracking'/'against' beyond it, 'broke range' when a sideways call moved ±4%.
                A rendering convention for eyeballing calls, not a validated signal.
              </p>
            )}
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <b className="text-slate-600">Honesty note:</b> {theme.disclaimer}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
