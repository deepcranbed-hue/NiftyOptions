import React, { useCallback, useMemo, useState } from 'react';
import { Landmark, RefreshCw, Cpu, TrendingUp, ChevronDown, ChevronRight, HelpCircle } from 'lucide-react';

/**
 * SectorViewPanel — Sector Intelligence container. Nifty Bank + Nifty IT; Financials later.
 *
 * Data: /api/sector-view/{sector} (bank_view.json / it_view.json; ?quotes=true refreshes live
 * NSE prices + returns + current P/B or P/E, cached 15 min server-side).
 * Convention (AIInfraThemePanel / MarketStateView): NO auto-run — nothing loads or computes
 * until the user presses a button. The valuation→ROE decode runs on the client, on button press.
 *
 * Per SECTOR_INTELLIGENCE_FRAMEWORK.md §6.7/§6.8: the regime-conditional P/B factor is validated
 * for BANKS only (credit cycle). IT has no such factor — its lens is the embedded-expectation
 * decode (P/E→implied ROE) + valuation-vs-own-history + momentum. Stances are positioning tilts.
 */

type Row = {
  bank?: string; stock?: string;
  pb?: number; bvps?: number; pb_pct?: number; pb_min?: number; pb_max?: number; gnpa?: number; gnpa_chg1y?: number | null; pcr?: number;
  pe?: number; eps?: number; pe_pct?: number; pe_min?: number; pe_max?: number; opm?: number | null; rev_growth?: number | null;
  roe?: number | null;
  ret_1w?: number | null; ret_1m?: number | null; ret_6m?: number | null; rel_6m?: number | null; last_px?: number;
};
type View = {
  as_of?: string; index?: { bank?: string; name?: string; ret_1w?: number | null; ret_1m?: number | null; ret_6m?: number | null };
  banks?: Row[]; stocks?: Row[]; quotes_as_of?: number; quotes_error?: string;
};
type Regime = 'deteriorating' | 'normalized' | 'improving';

const NAME: Record<string, string> = {
  HDFCBANK: 'HDFC Bank', ICICIBANK: 'ICICI Bank', SBIN: 'SBI', KOTAKBANK: 'Kotak Mahindra', AXISBANK: 'Axis Bank',
  INDUSINDBK: 'IndusInd', BANKBARODA: 'Bank of Baroda', PNB: 'PNB', AUBANK: 'AU SFB', IDFCFIRSTB: 'IDFC First',
  FEDERALBNK: 'Federal Bank', BANDHANBNK: 'Bandhan Bank',
  TCS: 'TCS', INFY: 'Infosys', HCLTECH: 'HCL Tech', WIPRO: 'Wipro', TECHM: 'Tech Mahindra', LTIM: 'LTIMindtree',
  PERSISTENT: 'Persistent', COFORGE: 'Coforge', MPHASIS: 'Mphasis', LTTS: 'L&T Tech',
};
const PSU = ['SBIN', 'BANKBARODA', 'PNB'], SFB = ['AUBANK'], TURN = ['INDUSINDBK', 'IDFCFIRSTB', 'BANDHANBNK'];

const TONE: Record<string, string> = {
  con: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  hold: 'bg-blue-50 text-blue-700 border-blue-200',
  neu: 'bg-slate-50 text-slate-500 border-slate-200',
  cau: 'bg-amber-50 text-amber-700 border-amber-200',
  avoid: 'bg-rose-50 text-rose-600 border-rose-200',
  spec: 'bg-violet-50 text-violet-700 border-violet-200',
};

const REGIME_META: Record<Regime, { label: string; ic: string; note: string }> = {
  deteriorating: { label: 'Deteriorating', ic: 'P/B IC +0.12', note: 'Cheapness is PUNISHED — cheap = value trap; quality/defensive favoured. (Hypothetical — not today.)' },
  normalized: { label: 'Normalized — current', ic: 'P/B IC ≈ 0', note: 'The factor is DORMANT — cheapness is inert, so stances lean on execution & momentum, not valuation. This is where we are.' },
  improving: { label: 'Improving / recovery', ic: 'P/B IC −0.52', note: 'Cheap banks RE-RATE hard — cheap + improving = the buy. (Hypothetical — the recovery already happened.)' },
};

// --- BANK stance (regime-conditional, validated) ---
function bankStance(b: Row, regime: Regime): { label: string; tone: string; why: string } {
  const cheap = (b.pb_pct ?? 0.5) <= 0.33, rich = (b.pb_pct ?? 0.5) >= 0.67;
  const improving = (b.gnpa_chg1y ?? 0) < -0.05, deteriorating = (b.gnpa_chg1y ?? 0) > 0.05;
  const stressed = (b.gnpa ?? 0) >= 2.5, leading = (b.rel_6m ?? 0) > 5, lagging = (b.rel_6m ?? 0) < -5;
  if (regime === 'improving') {
    if (cheap && improving && stressed) return { label: 'Speculative buy', tone: 'spec', why: 'cheap + improving but elevated GNPA — high-beta re-rating' };
    if (cheap && improving) return { label: 'Buy — re-rating', tone: 'con', why: "cheap + quality improving: the factor's sweet spot in a recovery" };
    if (cheap && deteriorating) return { label: 'Avoid — trap', tone: 'avoid', why: 'cheap but quality worsening — a trap even in recovery' };
    if (rich) return { label: 'Underweight', tone: 'cau', why: 'richly valued — the factor headwind when cheap names re-rate' };
    return { label: 'Neutral', tone: 'neu', why: 'mid valuation — modest in a cheap-wins regime' };
  }
  if (regime === 'deteriorating') {
    if (cheap) return { label: 'Avoid — value trap', tone: 'avoid', why: 'cheap is punished when quality deteriorates — the classic trap' };
    if (rich && improving) return { label: 'Defensive hold', tone: 'hold', why: 'quality/expensive names are the shelter when the cycle turns down' };
    if (stressed) return { label: 'Avoid', tone: 'avoid', why: 'elevated GNPA into a deteriorating cycle — highest risk' };
    return { label: 'Neutral / defensive', tone: 'neu', why: 'no cheapness edge; bias to quality as the cycle worsens' };
  }
  if (leading && stressed) return { label: 'Speculative', tone: 'spec', why: 'strong bounce off idiosyncratic stress (high GNPA) — recovery trade, high-beta, not a quality holding' };
  if (leading && rich) return { label: 'Hold — leader', tone: 'hold', why: 'momentum leader but richly valued in its own range — priced for delivery, don’t chase' };
  if (leading) return { label: 'Constructive', tone: 'con', why: 'quality + positive momentum align — the executing names the mild quality-tilt favours now' };
  if (cheap && lagging) return { label: 'Neutral — don’t chase', tone: 'neu', why: 'cheap vs own history but de-rating (structural, not a spring); factor edge ≈ 0 so the low %ile is not a buy' };
  if (lagging) return { label: 'Cautious', tone: 'cau', why: 'lagging on weak execution; no factor tailwind to offset it — wait for delivery' };
  return { label: 'Neutral', tone: 'neu', why: 'execution-driven; no cross-sectional factor edge in this regime' };
}

// --- IT stance (descriptive: valuation-vs-own-history + momentum; NOT a validated factor) ---
function itStance(s: Row): { label: string; tone: string; why: string } {
  const cheap = (s.pe_pct ?? 0.5) <= 0.33, rich = (s.pe_pct ?? 0.5) >= 0.67;
  const leading = (s.rel_6m ?? 0) > 5, lagging = (s.rel_6m ?? 0) < -5;
  if (rich && leading) return { label: 'Premium — delivering', tone: 'hold', why: 'richly valued but momentum confirms — priced for continued growth; must keep delivering to hold the multiple' };
  if (rich && lagging) return { label: 'Priced for growth, fading', tone: 'cau', why: 'premium multiple but losing momentum — vulnerable if growth disappoints' };
  if (cheap && leading) return { label: 'Re-rating', tone: 'con', why: 'cheap vs own history + positive momentum — the market re-warming to it' };
  if (cheap && lagging) return { label: 'Out of favour', tone: 'neu', why: 'cheap vs own history but still lagging — no catalyst yet' };
  if (leading) return { label: 'In favour', tone: 'con', why: 'positive momentum, mid valuation' };
  if (lagging) return { label: 'Lagging', tone: 'cau', why: 'underperforming with no valuation cushion' };
  return { label: 'Neutral', tone: 'neu', why: 'mid valuation, no clear momentum tilt' };
}

function bucketOf(t: string) { return PSU.includes(t) ? 'PSU' : SFB.includes(t) ? 'SFB' : TURN.includes(t) ? 'Turnaround' : 'Private'; }

function ols(pts: { x: number; y: number }[]) {
  const n = pts.length; const mx = pts.reduce((s, p) => s + p.x, 0) / n; const my = pts.reduce((s, p) => s + p.y, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  pts.forEach(p => { sxy += (p.x - mx) * (p.y - my); sxx += (p.x - mx) ** 2; syy += (p.y - my) ** 2; });
  const b = sxy / sxx, a = my - b * mx;
  let ssr = 0; pts.forEach(p => { const f = a + b * p.x; ssr += (f - my) ** 2; });
  return { a, b, r2: syy ? ssr / syy : 0 };
}

const fnum = (v: number | null | undefined, d = 2) => (v == null || Number.isNaN(v) ? '—' : v.toFixed(d));
function Pct({ v }: { v: number | null | undefined }) {
  if (v == null || Number.isNaN(v)) return <span className="text-slate-300">—</span>;
  return <span className={`font-mono ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</span>;
}

const SECTORS = [
  { id: 'bank', label: 'Nifty Bank', icon: Landmark, ready: true },
  { id: 'it', label: 'Nifty IT', icon: Cpu, ready: true },
  { id: 'financials', label: 'Nifty Financials', icon: TrendingUp, ready: false },
];

// stance glossary shown in a collapsible — meanings, not just colours
const GLOSSARY_BANK = [
  ['Constructive', 'con', 'Fundamentals + momentum align — an executing name the current (no-edge) regime mildly favours. A positive tilt, not a strong buy.'],
  ['Hold — leader', 'hold', 'A momentum leader now richly valued in its own range — priced for delivery. Own it, but don’t chase; upside needs it to beat an already-high bar.'],
  ['Neutral / don’t chase', 'neu', 'No cross-sectional edge, or cheap-but-de-rating for structural reasons. In a no-edge regime the low percentile is NOT a buy signal.'],
  ['Cautious', 'cau', 'Lagging on weak execution with no factor tailwind to offset it — wait for delivery before adding.'],
  ['Speculative', 'spec', 'A sharp bounce off idiosyncratic stress (high GNPA) — a high-beta recovery trade, not a quality holding. Higher upside AND higher downside.'],
  ['Underweight', 'cau', '(Deteriorating / improving regimes) Richly valued — a headwind when cheap names re-rate; the factor works against it.'],
  ['Avoid — value trap', 'avoid', '(Deteriorating regime) Cheap, but cheapness is punished when asset quality worsens — a trap, not a bargain.'],
  ['Buy — re-rating', 'con', '(Improving regime) Cheap + quality improving — the factor’s sweet spot; the historical big winner.'],
];
const GLOSSARY_IT = [
  ['Premium — delivering', 'hold', 'Richly valued but momentum confirms — priced for continued growth; must keep delivering to hold the multiple.'],
  ['Priced for growth, fading', 'cau', 'Premium multiple losing momentum — vulnerable if growth disappoints.'],
  ['Re-rating / In favour', 'con', 'Cheap (or mid) vs own history + positive momentum — the market re-warming to it.'],
  ['Out of favour / Lagging', 'neu', 'Cheap or mid but still underperforming — no catalyst yet.'],
];
const GLOSSARY_DECODE = [
  ['Durability premium', 'hold', 'Paying up for a proven high ROE — must keep delivering to hold it.'],
  ['Growth / Recovery premium', 'spec', 'Priced for an ROE well above what it earns today (growth or turnaround optionality).'],
  ['Structural PSU discount', 'cau', 'Delivers ROE the price ignores — a governance/dilution discount, NOT a forecast that ROE will fall.'],
  ['Priced for skepticism', 'neu', 'Market doubts the ROE is durable — a better-than-feared candidate if it holds.'],
  ['Fair for its ROE', 'neu', 'P/B (or P/E) ≈ what current ROE justifies vs peers — no thesis.'],
];

export const SectorViewPanel: React.FC = () => {
  const [sector, setSector] = useState<string>('bank');
  const [view, setView] = useState<View | null>(null);
  const [loading, setLoading] = useState(false);
  const [quotesLoading, setQuotesLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [regime, setRegime] = useState<Regime>('normalized');
  const [showDecode, setShowDecode] = useState(false);
  const [showGloss, setShowGloss] = useState(false);

  const isBank = sector === 'bank';

  const load = useCallback(async (withQuotes: boolean, force = false) => {
    withQuotes ? setQuotesLoading(true) : setLoading(true);
    setErr(null);
    try {
      const qs = withQuotes ? `?quotes=true${force ? '&force_quotes=true' : ''}` : '';
      const r = await fetch(`/api/sector-view/${sector}${qs}`);
      const j = await r.json();
      if (j.success) {
        setView(j.view);
        if (j.view.quotes_error) setErr(`Live quotes unavailable — ${j.view.quotes_error} (showing stored prices)`);
      } else setErr(j.detail || 'Failed to load sector view');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); setQuotesLoading(false); }
  }, [sector]);

  const rows = useMemo(() => {
    const list = (isBank ? view?.banks : view?.stocks) || [];
    return [...list].sort((a, b) => (b.rel_6m ?? -999) - (a.rel_6m ?? -999));
  }, [view, isBank]);

  const idOf = (r: Row) => (isBank ? r.bank : r.stock) as string;
  const valOf = (r: Row) => (isBank ? r.pb : r.pe) as number | undefined;
  const pctOf = (r: Row) => (isBank ? r.pb_pct : r.pe_pct) ?? 0.5;

  // BANK: P/B decodes on ROE (higher ROE → higher P/B). IT: P/E decodes on GROWTH
  // (higher growth → higher P/E) — ROE is the wrong axis for IT (TCS: top ROE, bottom P/E).
  const decode = useMemo(() => {
    const xOf = (r: Row) => (isBank ? r.roe : r.rev_growth);
    const list = rows.filter(r => xOf(r) != null && valOf(r) != null);
    if (list.length < 3) return null;
    const { a, b, r2 } = ols(list.map(r => ({ x: xOf(r) as number, y: valOf(r) as number })));
    const posThr = isBank ? 0.3 : 4, negThr = isBank ? -0.3 : -4;
    const out = list.map(r => {
      const xv = xOf(r) as number; const v = valOf(r) as number; const fair = a + b * xv; const resid = v - fair;
      const t = idOf(r); const bk = bucketOf(t);
      let tag = isBank ? 'Fair for its ROE' : 'Fair for its growth', tone = 'neu';
      if (resid > posThr) {
        if (isBank && bk === 'SFB') { tag = 'Growth premium'; tone = 'spec'; }
        else if (isBank && bk === 'Turnaround') { tag = 'Recovery premium'; tone = 'spec'; }
        else if (isBank && (r.roe ?? 0) >= 15) { tag = 'Durability premium'; tone = 'hold'; }
        else if (isBank) { tag = 'Growth premium'; tone = 'spec'; }
        else { tag = 'Priced for growth'; tone = 'spec'; }
      } else if (resid < negThr) {
        if (isBank && bk === 'PSU') { tag = 'Structural PSU discount'; tone = 'cau'; }
        else if (isBank) { tag = 'Priced for skepticism'; tone = 'neu'; }
        else { tag = 'Cheap for its growth'; tone = 'con'; }
      }
      return { id: t, x: xv, v, fair, resid, tag, tone };
    }).sort((p, q) => q.resid - p.resid);
    return { a, b, r2, list: out };
  }, [rows, isBank]);
  const xMetric = isBank ? 'ROE' : 'growth';

  const idx = view?.index;
  const valLabel = isBank ? 'P/B' : 'P/E';
  const gloss = isBank ? GLOSSARY_BANK : GLOSSARY_IT;

  return (
    <div className="bg-white rounded-2xl p-6 sm:p-8 border border-slate-200 shadow-sm mt-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div>
          <h3 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <Landmark className="w-5 h-5 text-indigo-500" /> Sector Intelligence
          </h3>
          <p className="text-sm text-slate-500 mt-1">
            Fundamentals × market × stance. Valuation is a <b>thesis</b>, not a signal —
            stances are positioning tilts, not predictions (framework §6.7/§6.8).
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => load(false)} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 transition flex items-center gap-2 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> {view ? 'Reload' : 'Load view'}
          </button>
          <button onClick={() => load(true, true)} disabled={quotesLoading || !view}
            className="px-4 py-2 rounded-lg text-sm font-bold text-emerald-700 bg-emerald-100 hover:bg-emerald-200 transition flex items-center gap-2 disabled:opacity-40">
            <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} /> Refresh live prices
          </button>
        </div>
      </div>

      {/* Sector tabs */}
      <div className="flex flex-wrap gap-2 mb-5">
        {SECTORS.map(s => {
          const Icon = s.icon; const active = s.id === sector;
          return (
            <button key={s.id} disabled={!s.ready}
              onClick={() => { if (s.ready) { setSector(s.id); setView(null); setShowDecode(false); } }}
              className={`px-3.5 py-2 rounded-xl text-xs font-bold flex items-center gap-2 border transition ${active ? 'bg-indigo-600 text-white border-indigo-600 shadow' : s.ready ? 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50' : 'bg-slate-50 text-slate-300 border-slate-100 cursor-not-allowed'}`}>
              <Icon className="w-4 h-4" /> {s.label}{!s.ready && <span className="text-[9px] font-semibold opacity-70">soon</span>}
            </button>
          );
        })}
      </div>

      {err && <div className="bg-rose-50 text-rose-700 p-3 rounded-xl text-sm font-medium border border-rose-100 mb-4">{err}</div>}

      {!view && !loading && (
        <div className="text-sm text-slate-400 py-10 text-center border border-dashed border-slate-200 rounded-xl">
          Press <b className="text-slate-600">Load view</b> to fetch the {SECTORS.find(s => s.id === sector)?.label} view. Nothing computes on app start.
        </div>
      )}

      {view && (
        <>
          {/* Conclusion + index strip */}
          <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 mb-4 text-sm text-slate-600 leading-relaxed">
            {isBank ? (
              <><b className="text-slate-800">Bottom line:</b> banks are ballast, not the engine — benign, catalyst-free regime.
                P/B has <b>~zero cross-sectional edge in this (normalization) regime</b>, so cheapness is <b>not</b> a buy signal — the cheapest premium names (HDFC, Kotak) are the laggards.</>
            ) : (
              <><b className="text-slate-800">Nifty IT:</b> valued on P/E + growth, not P/B. No validated regime factor (that's bank-specific) — the lens here is
                <b> what growth is priced in</b> (the P/E→growth decode) plus valuation-vs-own-history and momentum. Stances are descriptive, not a factor call.</>
            )}
            {idx && (idx.ret_6m != null) && (
              <span className="ml-1">{isBank ? 'Bank Nifty' : 'Nifty IT'}: 1W <Pct v={idx.ret_1w} /> · 1M <Pct v={idx.ret_1m} /> · 6M <Pct v={idx.ret_6m} /></span>
            )}
            {view.as_of && <span className="text-slate-400"> · fundamentals as of {view.as_of}{view.quotes_as_of ? ' · prices live' : ''}</span>}
          </div>

          {/* Regime toggle (BANK only) */}
          {isBank && (
            <div className="mb-4">
              <div className="text-[10px] uppercase font-black text-slate-400 mb-2 tracking-wider">Credit-cycle regime — recomputes every stance (the validated conditional factor)</div>
              <div className="flex flex-wrap gap-2">
                {(Object.keys(REGIME_META) as Regime[]).map(rk => (
                  <button key={rk} onClick={() => setRegime(rk)}
                    className={`px-3 py-2 rounded-lg text-xs font-bold border transition ${regime === rk ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}>
                    {REGIME_META[rk].label}<span className={`ml-1.5 font-normal ${regime === rk ? 'text-indigo-200' : 'text-slate-400'}`}>{REGIME_META[rk].ic}</span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-500 mt-2">{REGIME_META[regime].note}</p>
            </div>
          )}

          {/* Table */}
          <div className="border border-slate-200 rounded-xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500 border-b border-slate-200">
                <tr className="text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2.5 text-left font-semibold">{isBank ? 'Bank' : 'Company'}</th>
                  <th className="px-3 py-2.5 text-right font-semibold">{valLabel}</th>
                  {isBank ? <th className="px-3 py-2.5 text-right font-semibold">Book/sh</th> : <th className="px-3 py-2.5 text-right font-semibold">EPS</th>}
                  <th className="px-3 py-2.5 text-right font-semibold">ROE</th>
                  <th className="px-3 py-2.5 text-right font-semibold">%ile</th>
                  {isBank ? <th className="px-3 py-2.5 text-right font-semibold">GNPA</th> : <th className="px-3 py-2.5 text-right font-semibold">Op.Mgn</th>}
                  {isBank ? <th className="px-3 py-2.5 text-right font-semibold">PCR</th> : <th className="px-3 py-2.5 text-right font-semibold">Rev gr</th>}
                  <th className="px-3 py-2.5 text-right font-semibold">1W</th>
                  <th className="px-3 py-2.5 text-right font-semibold">1M</th>
                  <th className="px-3 py-2.5 text-right font-semibold">6M</th>
                  <th className="px-3 py-2.5 text-right font-semibold">6M vs idx</th>
                  <th className="px-3 py-2.5 text-left font-semibold">Stance</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map(r => {
                  const st = isBank ? bankStance(r, regime) : itStance(r);
                  const id = idOf(r); const val = valOf(r); const pct = pctOf(r);
                  const improving = (r.gnpa_chg1y ?? 0) < -0.05, worse = (r.gnpa_chg1y ?? 0) > 0.05;
                  const vmin = isBank ? r.pb_min : r.pe_min, vmax = isBank ? r.pb_max : r.pe_max;
                  return (
                    <tr key={id} className="hover:bg-slate-50">
                      <td className="px-3 py-2.5 whitespace-nowrap"><span className="font-bold text-slate-800">{NAME[id] || id}</span><span className="block text-[10px] text-slate-400">{id}</span></td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-800">{fnum(val, isBank ? 2 : 1)}<span className="block text-[9px] text-slate-400 font-normal">{fnum(vmin, 1)}–{fnum(vmax, 1)}</span></td>
                      {isBank
                        ? <td className="px-3 py-2.5 text-right text-slate-500">₹{fnum(r.bvps, 0)}</td>
                        : <td className="px-3 py-2.5 text-right text-slate-500">₹{fnum(r.eps, 1)}</td>}
                      <td className="px-3 py-2.5 text-right text-slate-700 font-semibold">{r.roe == null ? '—' : `${r.roe.toFixed(1)}%`}</td>
                      <td className="px-3 py-2.5 text-right text-slate-500">{Math.round(pct * 100)}</td>
                      {isBank
                        ? <td className="px-3 py-2.5 text-right text-slate-700">{fnum(r.gnpa)}<span className={`ml-1 text-[10px] ${improving ? 'text-emerald-600' : worse ? 'text-rose-600' : 'text-slate-400'}`}>{improving ? '▼' : worse ? '▲' : '▬'}</span></td>
                        : <td className="px-3 py-2.5 text-right text-slate-700">{r.opm == null ? '—' : `${r.opm.toFixed(1)}%`}</td>}
                      {isBank
                        ? <td className="px-3 py-2.5 text-right text-slate-400">{fnum(r.pcr, 0)}</td>
                        : <td className="px-3 py-2.5 text-right"><Pct v={r.rev_growth} /></td>}
                      <td className="px-3 py-2.5 text-right"><Pct v={r.ret_1w} /></td>
                      <td className="px-3 py-2.5 text-right"><Pct v={r.ret_1m} /></td>
                      <td className="px-3 py-2.5 text-right"><Pct v={r.ret_6m} /></td>
                      <td className="px-3 py-2.5 text-right"><Pct v={r.rel_6m} /></td>
                      <td className="px-3 py-2.5"><span className={`inline-block px-2 py-1 rounded-md text-[11px] font-bold border ${TONE[st.tone]}`} title={st.why}>{st.label}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Stance glossary (button-gated) */}
          <div className="mt-4">
            <button onClick={() => setShowGloss(v => !v)}
              className="w-full text-left px-4 py-3 rounded-xl text-sm font-bold bg-slate-50 text-slate-700 border border-slate-200 hover:bg-slate-100 transition flex items-center gap-2">
              {showGloss ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              <HelpCircle className="w-4 h-4 text-slate-400" /> What the stances mean
            </button>
            {showGloss && (
              <div className="mt-3 border border-slate-200 rounded-xl p-4 bg-white space-y-3">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2.5">
                  {gloss.map(([label, tone, meaning]) => (
                    <div key={label} className="flex items-start gap-2.5">
                      <span className={`shrink-0 mt-0.5 inline-block px-2 py-0.5 rounded-md text-[10px] font-bold border ${TONE[tone as string]}`}>{label}</span>
                      <span className="text-xs text-slate-600 leading-snug">{meaning}</span>
                    </div>
                  ))}
                </div>
                <div className="pt-2 border-t border-slate-100">
                  <div className="text-[10px] uppercase font-black text-slate-400 mb-2 tracking-wider">Decode tags (embedded-expectation panel)</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2.5">
                    {GLOSSARY_DECODE.map(([label, tone, meaning]) => (
                      <div key={label} className="flex items-start gap-2.5">
                        <span className={`shrink-0 mt-0.5 inline-block px-2 py-0.5 rounded-md text-[10px] font-bold border ${TONE[tone as string]}`}>{label}</span>
                        <span className="text-xs text-slate-600 leading-snug">{meaning}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Decode (button-gated) */}
          <div className="mt-3">
            <button onClick={() => setShowDecode(v => !v)}
              className="w-full text-left px-4 py-3 rounded-xl text-sm font-bold bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 transition flex items-center gap-2">
              {showDecode ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              Decode embedded expectations — fit {valLabel} vs ROE (runs only on click)
            </button>
            {showDecode && decode && (
              <div className="mt-3 border border-slate-200 rounded-xl p-4 bg-white">
                <p className="text-sm text-slate-600 leading-relaxed mb-4">
                  Fitting <b>{valLabel} = {decode.a.toFixed(2)} + {decode.b.toFixed(3)}·{isBank ? 'ROE' : 'Growth'}</b> across the {isBank ? 'banks' : 'IT names'}, current {xMetric} explains only
                  <b> R² = {Math.round(decode.r2 * 100)}%</b> of the {valLabel} spread. The other <b>{100 - Math.round(decode.r2 * 100)}%</b> is the
                  <b> embedded expectation</b> — {isBank ? 'future ROE and structural premium/discount' : 'durability of that growth, margins, and franchise quality'}. The residual ({valLabel} minus the line) is what the market prices that today's numbers don't show.
                </p>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                  <svg viewBox="0 0 460 300" className="w-full h-auto bg-slate-50 border border-slate-200 rounded-lg">
                    {(() => {
                      const W = 460, H = 300, pad = 40;
                      const xmx = Math.max(...decode.list.map(r => r.x), isBank ? 16 : 38) + (isBank ? 2 : 4);
                      const ymx = Math.max(...decode.list.map(r => r.v), isBank ? 4.4 : 40) * 1.08;
                      const X = (v: number) => pad + (v / xmx) * (W - pad - 12);
                      const Y = (v: number) => H - 30 - (v / ymx) * (H - 30 - 14);
                      return (
                        <>
                          <line x1={pad} y1={Y(0)} x2={W - 12} y2={Y(0)} stroke="#e2e8f0" />
                          <line x1={pad} y1={12} x2={pad} y2={Y(0)} stroke="#e2e8f0" />
                          <line x1={X(0)} y1={Y(decode.a)} x2={X(xmx)} y2={Y(decode.a + decode.b * xmx)} stroke="#6366f1" strokeWidth={1.5} strokeDasharray="5 3" />
                          {decode.list.map(r => (
                            <g key={r.id}>
                              <circle cx={X(r.x)} cy={Y(r.v)} r={4.5} fill={r.resid > (isBank ? 0.3 : 4) ? '#d97706' : r.resid < (isBank ? -0.3 : -4) ? '#2563eb' : '#94a3b8'} />
                              <text x={X(r.x) + 6} y={Y(r.v) + 3} fontSize={9} fill="#64748b">{r.id.replace('BANK', '').replace('BK', '')}</text>
                            </g>
                          ))}
                          <text x={(pad + W) / 2} y={H - 6} fontSize={10} fill="#94a3b8" textAnchor="middle">{isBank ? 'ROE' : 'Rev growth'} %  →</text>
                          <text x={13} y={H / 2} fontSize={10} fill="#94a3b8" textAnchor="middle" transform={`rotate(-90 13 ${H / 2})`}>{valLabel}  →</text>
                        </>
                      );
                    })()}
                  </svg>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="text-slate-400 border-b border-slate-200 text-[10px] uppercase">
                        <tr><th className="text-left py-1.5 px-2">Name</th><th className="text-right px-2">{isBank ? 'ROE' : 'Grw'}</th><th className="text-right px-2">{valLabel}</th><th className="text-right px-2">Fair</th><th className="text-right px-2">Resid</th><th className="text-left px-2">Read</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {decode.list.map(r => (
                          <tr key={r.id}>
                            <td className="py-1.5 px-2 text-slate-700 font-semibold">{r.id}</td>
                            <td className="px-2 text-right text-slate-600">{r.x.toFixed(1)}</td>
                            <td className="px-2 text-right text-slate-800 font-semibold">{r.v.toFixed(isBank ? 2 : 1)}</td>
                            <td className="px-2 text-right text-slate-400">{r.fair.toFixed(isBank ? 2 : 1)}</td>
                            <td className={`px-2 text-right font-mono ${r.resid > 0 ? 'text-amber-600' : 'text-blue-600'}`}>{r.resid > 0 ? '+' : ''}{r.resid.toFixed(isBank ? 2 : 1)}</td>
                            <td className="px-2"><span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold border ${TONE[r.tone]}`}>{r.tag}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <p className="text-[11px] text-slate-500 mt-3 leading-relaxed">
                  <b>Reading it —</b> <span className="text-amber-600">amber = above the line</span> (paying more than current ROE justifies: a bet on higher future ROE/growth);
                  <span className="text-blue-600"> blue = below the line</span> (skepticism or a structural discount); grey = fair.
                  <b> Caveat:</b> the residual mixes expected-change with structural premium/discount{isBank ? ' — PSUs sit below the line for governance reasons, not an ROE-fall forecast' : ' — and IT growth is 1-year YoY (noisy); a premium can mean durable growth, high margins, or franchise quality'}. Isolating the pure expectation needs a <b>forward</b> estimate (framework §6.7).
                </p>
              </div>
            )}
          </div>

          <p className="text-[11px] text-slate-400 mt-4 leading-relaxed">
            {isBank
              ? <>Bank stance rules: valuation = P/B percentile in own range · quality = YoY ΔGNPA · momentum = 6M vs Bank Nifty · the regime button switches which rule dominates, per validated per-regime P/B rank-ICs. Hard caveats: n=3 yrs/regime, returns gap-locked, not operational without a live regime detector.</>
              : <>IT stance rules: valuation = P/E percentile in own range · momentum = 6M vs Nifty IT. NO regime factor (bank-specific) — IT’s real lens is the P/E→growth decode above (P/E is driven by growth, not ROE). Descriptive tilts, not a validated signal.</>}
            {' '}Positioning tilts, not advice.
          </p>
        </>
      )}
    </div>
  );
};
