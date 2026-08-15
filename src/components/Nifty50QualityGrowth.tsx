import React, { useCallback, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react';
import { card } from './nifty50Shared';

/**
 * Nifty50QualityGrowth — the weight-ordered quality screen, its rejection walk, and
 * the two backtests.
 *
 * WHAT THE WALK IS
 *   Start at the heaviest constituent. Does it pass three tests? Keep it if yes, move
 *   to the next heaviest if no, continue to the bottom of the index. Weight is the
 *   ORDER OF INSPECTION, never a test — which is why a 0.5%-weight name can be in the
 *   list and an 8%-weight one can be out.
 *
 * WHY THE THRESHOLDS ARE CONTROLS
 *   70 / 10 / 15 are priors, not measurements. A list handed over at fixed cutoffs
 *   invites the reader to treat the cutoff as knowledge. Every name in the payload —
 *   passed AND failed — arrives with its full history attached, so moving a threshold
 *   re-screens all 50 in the browser and the list visibly reorganises. The backtest
 *   cannot follow, because pricing a different set means re-running the script; when
 *   the live thresholds differ from the ones that produced the JSON, the backtest says
 *   so instead of quietly describing a different portfolio.
 *
 * Convention: NO auto-run — nothing fetches until the button is pressed.
 */

type QGSeries = { period: string; sales: number | null; net_profit: number; shares_cr: number | null; eps: number | null };
type QGQuarter = { period: string; sales: number | null; net_profit: number; operating_profit: number | null; yoy_pct: number | null };

export type QGName = {
  symbol: string; company: string; sector: string; weight_pct: number;
  consistency_pct: number | null; years_grown: number | null; years_measured: number | null;
  profit_cagr_5y_pct: number | null; profit_cagr_3y_pct: number | null;
  worst_year_pct: number | null; corporate_action_years: number | null;
  trailing_pe: number | null; forward_pe: number | null;
  implied_growth_pct: number | null; analysts: number | null;
  failed?: string[];
  guidance?: string | null; position?: string | null;
  latest_quarter?: { period: string; as_of: string; points: string[] } | null;
  tailwinds?: string[]; headwinds?: string[];
  series?: QGSeries[]; yoy_net_profit_pct?: (number | null)[];
  share_move_flags?: (number | null)[]; quarters?: QGQuarter[];
  sector_rank_cagr?: number | null; sector_peers?: number | null;
  sector_median_cagr_pct?: number | null;
};

type QGHolding = { symbol: string; buy_date: string; buy: number; last: number; ret_pct: number; cagr_pct: number | null };
type QGBacktest = {
  label: string; error?: string;
  start: string; end: string; years: number; n_holdings: number;
  missing_price_history: string[];
  portfolio_ret_pct: number; portfolio_cagr_pct: number;
  index_ret_pct: number; index_cagr_pct: number; excess_pp: number;
  beat_index: number; median_ret_pct: number;
  best: QGHolding; worst: QGHolding; holdings: QGHolding[];
};

export type QGDoc = {
  as_of: string; source: string; method: string; note: string;
  screen: {
    thresholds: { consistency_min_pct: number; delivered_cagr_min_pct: number; forward_growth_min_pct: number; target_n: number };
    walk_length: number; more_passed_than_target: number;
    selected: QGName[]; overflow: QGName[]; rejected: QGName[];
    no_data: { symbol: string; weight_pct: number; sector: string; reason: string }[];
    selected_weight_pct: number; expectation_captured_at: string | null;
  };
  point_in_time: { cutoff: string; considered: number; legs: string[]; note: string; picked: { symbol: string; sector: string; weight_pct: number; consistency_pct: number; cagr_pct: number | null; n_years: number; last_period: string }[] };
  backtest: {
    today_screen: QGBacktest; point_in_time: QGBacktest;
    lookahead_inflation_pp: number | null;
    overlap: string[]; only_in_today_screen: string[]; caveats: string[];
  };
};

const LEG_TONE: Record<string, string> = {
  consistency: 'bg-amber-50 text-amber-700 border-amber-200',
  delivered: 'bg-rose-50 text-rose-600 border-rose-200',
  forward: 'bg-violet-50 text-violet-600 border-violet-200',
};
const legTone = (leg: string) =>
  LEG_TONE[leg.split(' ')[0]] ?? 'bg-slate-50 text-slate-500 border-slate-200';

const fy = (p: string) => `FY${(Number(p.slice(0, 4)) % 100).toString().padStart(2, '0')}`;
const q = (p: string) => `${p.slice(2, 4)}${['', 'Mar', 'Jun', 'Sep', 'Dec'][Math.ceil(Number(p.slice(5, 7)) / 3)]}`;

/** Annual net profit, one bar per year. Height is the LEVEL, colour is the direction —
 *  the two things a growth chart has to say at once. A year where the share count moved
 *  more than 5% gets a marker, because a merger year that reads as a collapse in EPS is
 *  the single most common way this kind of chart lies. */
function ProfitBars({ n }: { n: QGName }) {
  const s = n.series ?? [];
  if (!s.length) return <div className="text-[10px] text-slate-400">no annual history</div>;
  const max = Math.max(...s.map((x) => Math.abs(x.net_profit)), 1);
  const yoy = n.yoy_net_profit_pct ?? [];
  const flags = n.share_move_flags ?? [];
  return (
    <div>
      <div className="flex items-end gap-1 h-24">
        {s.map((x, i) => {
          const g = i === 0 ? null : yoy[i - 1];
          const up = g === null || g === undefined ? null : g > 0;
          return (
            <div key={x.period} className="flex-1 flex flex-col items-center justify-end h-full min-w-0">
              <span className={`text-[8px] font-mono leading-none mb-0.5 ${up === null ? 'text-slate-300' : up ? 'text-emerald-600' : 'text-rose-500'}`}>
                {g === null || g === undefined ? '' : `${g > 0 ? '+' : ''}${Math.round(g)}%`}
              </span>
              <div
                className={`w-full rounded-sm ${up === null ? 'bg-slate-300' : up ? 'bg-emerald-400' : 'bg-rose-400'}`}
                style={{ height: `${Math.max(2, (Math.abs(x.net_profit) / max) * 100)}%` }}
                title={`${fy(x.period)} · net profit ₹${x.net_profit.toLocaleString('en-IN')} cr${x.eps ? ` · EPS ₹${x.eps}` : ''}`}
              />
            </div>
          );
        })}
      </div>
      <div className="flex gap-1 mt-1">
        {s.map((x, i) => (
          <div key={x.period} className="flex-1 text-center min-w-0">
            <div className="text-[8px] text-slate-400 leading-none">{fy(x.period)}</div>
            {i > 0 && flags[i - 1] ? (
              <div className="text-[8px] text-indigo-500 leading-none" title={`share count moved ${flags[i - 1]}% — corporate action`}>▲</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Quarterly net profit with year-on-year beside it. YoY is vs the SAME quarter last
 *  year, never the previous quarter — Indian earnings are seasonal enough that a
 *  sequential read is mostly the calendar. */
function QuarterSpark({ n }: { n: QGName }) {
  const qs = n.quarters ?? [];
  if (!qs.length) return <div className="text-[10px] text-slate-400">no quarterly history</div>;
  const max = Math.max(...qs.map((x) => Math.abs(x.net_profit)), 1);
  return (
    <div>
      <div className="flex items-end gap-[3px] h-16">
        {qs.map((x) => (
          <div key={x.period} className="flex-1 flex flex-col justify-end h-full min-w-0">
            <div
              className={`w-full rounded-sm ${x.yoy_pct === null ? 'bg-slate-300' : x.yoy_pct > 0 ? 'bg-sky-400' : 'bg-orange-400'}`}
              style={{ height: `${Math.max(2, (Math.abs(x.net_profit) / max) * 100)}%` }}
              title={`${x.period} · ₹${x.net_profit.toLocaleString('en-IN')} cr${x.yoy_pct === null ? '' : ` · YoY ${x.yoy_pct > 0 ? '+' : ''}${x.yoy_pct}%`}`}
            />
          </div>
        ))}
      </div>
      <div className="flex gap-[3px] mt-1">
        {qs.map((x) => (
          <div key={x.period} className="flex-1 text-center text-[8px] text-slate-400 leading-none min-w-0">{q(x.period)}</div>
        ))}
      </div>
      <div className="text-[9px] text-slate-500 mt-1">
        latest quarter{' '}
        <b className={qs[qs.length - 1].yoy_pct === null ? 'text-slate-500'
          : qs[qs.length - 1].yoy_pct! > 0 ? 'text-emerald-700' : 'text-rose-600'}>
          {qs[qs.length - 1].yoy_pct === null ? 'n/a'
            : `${qs[qs.length - 1].yoy_pct! > 0 ? '+' : ''}${qs[qs.length - 1].yoy_pct}% YoY`}
        </b>{' '}
        <span className="text-slate-400">({qs[qs.length - 1].period})</span>
      </div>
    </div>
  );
}

function BacktestCard({ bt, tone, badge }: { bt: QGBacktest; tone: string; badge?: string }) {
  if (bt.error) return <div className={`${card} text-xs text-slate-500`}>{bt.label}: {bt.error}</div>;
  return (
    <div className={`rounded-2xl border px-3.5 py-3 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-black uppercase tracking-wide text-slate-500">{bt.label}</span>
        {badge && (
          <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full border border-rose-300 bg-rose-50 text-rose-600">
            {badge}
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mt-2">
        <div>
          <div className="text-[9px] uppercase font-black text-slate-400">Portfolio</div>
          <div className="text-xl font-black font-mono text-slate-800 leading-tight">
            {bt.portfolio_ret_pct >= 0 ? '+' : ''}{bt.portfolio_ret_pct}%
          </div>
          <div className="text-[9px] text-slate-400">{bt.portfolio_cagr_pct}% CAGR</div>
        </div>
        <div>
          <div className="text-[9px] uppercase font-black text-slate-400">Nifty 50</div>
          <div className="text-xl font-black font-mono text-slate-500 leading-tight">
            {bt.index_ret_pct >= 0 ? '+' : ''}{bt.index_ret_pct}%
          </div>
          <div className="text-[9px] text-slate-400">{bt.index_cagr_pct}% CAGR</div>
        </div>
        <div>
          <div className="text-[9px] uppercase font-black text-slate-400">Excess</div>
          <div className={`text-xl font-black font-mono leading-tight ${bt.excess_pp >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
            {bt.excess_pp >= 0 ? '+' : ''}{bt.excess_pp}pp
          </div>
          <div className="text-[9px] text-slate-400">{bt.beat_index}/{bt.n_holdings} beat index</div>
        </div>
      </div>
      <div className="text-[9px] text-slate-400 mt-1.5">
        {bt.start} → {bt.end} · {bt.years}y · equal weight, no rebalancing, price return ·
        median holding {bt.median_ret_pct >= 0 ? '+' : ''}{bt.median_ret_pct}% ·
        best {bt.best.symbol} {bt.best.ret_pct >= 0 ? '+' : ''}{bt.best.ret_pct}% ·
        worst {bt.worst.symbol} {bt.worst.ret_pct >= 0 ? '+' : ''}{bt.worst.ret_pct}%
        {bt.missing_price_history.length > 0 && ` · no price history: ${bt.missing_price_history.join(', ')}`}
      </div>
    </div>
  );
}

export function Nifty50QualityGrowth({ onSymbolClick }: { onSymbolClick?: (sym: string) => void }) {
  const [doc, setDoc] = useState<QGDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [showRejected, setShowRejected] = useState(false);
  const [showHoldings, setShowHoldings] = useState(false);
  const [cons, setCons] = useState(70);
  const [cagr, setCagr] = useState(10);
  const [fwd, setFwd] = useState(15);
  const [n, setN] = useState(20);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch('/api/nifty-quality-growth');
      const j = await r.json();
      if (j.success) {
        setDoc(j.quality);
        const t = j.quality?.screen?.thresholds;
        if (t) { setCons(t.consistency_min_pct); setCagr(t.delivered_cagr_min_pct); setFwd(t.forward_growth_min_pct); setN(t.target_n); }
      } else setErr(j.detail || 'Failed to load the quality screen');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  // Every inspected name, back in walk order. This is the pool the thresholds re-screen.
  const pool = useMemo<QGName[]>(() => {
    if (!doc) return [];
    return [...doc.screen.selected, ...doc.screen.overflow, ...doc.screen.rejected]
      .sort((a, b) => b.weight_pct - a.weight_pct);
  }, [doc]);

  const live = useMemo(() => {
    const pass = pool.filter((r) =>
      r.consistency_pct !== null && r.consistency_pct >= cons &&
      r.profit_cagr_5y_pct !== null && r.profit_cagr_5y_pct >= cagr &&
      r.implied_growth_pct !== null && r.implied_growth_pct >= fwd);
    return { picked: pass.slice(0, n), passers: pass.length };
  }, [pool, cons, cagr, fwd, n]);

  const stored = doc?.screen.thresholds;
  const atStored = !!stored && cons === stored.consistency_min_pct &&
    cagr === stored.delivered_cagr_min_pct && fwd === stored.forward_growth_min_pct &&
    n === stored.target_n;

  const toggle = (s: string) => setOpen((p) => {
    const nx = new Set(p); nx.has(s) ? nx.delete(s) : nx.add(s); return nx;
  });

  const fails = useMemo(() => {
    const m: Record<string, QGName[]> = {};
    pool.forEach((r) => {
      const bad: string[] = [];
      if (r.consistency_pct === null || r.consistency_pct < cons) bad.push('consistency');
      if (r.profit_cagr_5y_pct === null || r.profit_cagr_5y_pct < cagr) bad.push('delivered');
      if (r.implied_growth_pct === null || r.implied_growth_pct < fwd) bad.push('forward');
      if (bad.length) (m[bad.join(' + ')] ||= []).push(r);
    });
    return Object.entries(m).sort((a, b) => b[1].length - a[1].length);
  }, [pool, cons, cagr, fwd]);

  return (
    <div className="space-y-4">
      <div className={card}>
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
            Quality growth — walk the index by weight, keep what delivered {doc ? `(${doc.as_of})` : ''}
          </span>
          <button onClick={load} disabled={loading}
            className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
            title="Reads quality_growth.json — written by data_agent/fundamentals/quality_growth.py. Nothing is computed on request.">
            {loading ? 'Loading…' : doc ? 'Reload screen' : 'Load screen'}
          </button>
        </div>

        {err && (
          <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            {err}
          </div>
        )}

        {doc && (
          <div className="mt-3 space-y-3">
            <p className="text-[11px] text-slate-600 leading-relaxed">{doc.method}</p>

            {/* The three thresholds, as controls. */}
            <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5">
              {([
                ['Consistency ≥', cons, setCons, '% of measured years net profit grew', 5, 0, 100],
                ['5y profit CAGR ≥', cagr, setCagr, 'net profit, 5-year compound', 1, -20, 60],
                ['Implied forward ≥', fwd, setFwd, 'trailing P/E ÷ forward P/E − 1', 1, -20, 80],
                ['Take at most', n, setN, 'names, in weight order', 1, 1, 50],
              ] as const).map(([label, val, set, hint, step, lo, hi]) => (
                <label key={label} className="flex flex-col gap-0.5">
                  <span className="text-[9px] font-black uppercase tracking-wide text-slate-400">{label}</span>
                  <input type="number" value={val as number} step={step} min={lo} max={hi}
                    onChange={(e) => (set as (v: number) => void)(Number(e.target.value))}
                    className="w-20 rounded-lg border border-slate-300 px-2 py-1 text-xs font-mono font-bold text-slate-800" />
                  <span className="text-[8px] text-slate-400">{hint}</span>
                </label>
              ))}
              <div className="text-[10px] text-slate-500 pb-1">
                <b className="font-mono text-slate-800">{live.picked.length}</b> selected of{' '}
                <b className="font-mono">{live.passers}</b> that pass, from{' '}
                <b className="font-mono">{doc.screen.walk_length}</b> walked
                {doc.screen.no_data.length > 0 && (
                  <> · <span title={doc.screen.no_data.map((x) => x.symbol).join(', ')}>
                    {doc.screen.no_data.length} with no financial history
                  </span></>
                )}
              </div>
            </div>

            {!atStored && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                <p className="text-[10px] text-amber-800 leading-snug">
                  Thresholds moved off the stored set ({stored?.consistency_min_pct} / {stored?.delivered_cagr_min_pct} / {stored?.forward_growth_min_pct}).
                  The table below re-screens live. <b>The backtest does not</b> — it prices the stored set and
                  cannot follow a slider. Re-run{' '}
                  <code className="font-mono">quality_growth.py --consistency {cons} --delivered {cagr} --forward {fwd} --n {n}</code>{' '}
                  to price this one.
                </p>
              </div>
            )}

            <p className="text-[10px] text-slate-400 leading-snug">{doc.note}</p>
          </div>
        )}
      </div>

      {doc && (
        <>
          {/* ---- the selected names ---- */}
          <div className={card}>
            <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-2">
              Selected · {live.picked.reduce((s, r) => s + r.weight_pct, 0).toFixed(1)}% of index weight
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-[9px] uppercase font-black text-slate-400 border-b border-slate-200">
                    <th className="py-1.5 pr-2 text-left">Stock</th>
                    <th className="py-1.5 px-2 text-right">Wt%</th>
                    <th className="py-1.5 px-2 text-right">Grew</th>
                    <th className="py-1.5 px-2 text-right">5y CAGR</th>
                    <th className="py-1.5 px-2 text-right">3y CAGR</th>
                    <th className="py-1.5 px-2 text-right">Worst yr</th>
                    <th className="py-1.5 px-2 text-right">P/E t → f</th>
                    <th className="py-1.5 px-2 text-right">Priced for</th>
                    <th className="py-1.5 pl-2 text-left">In sector</th>
                  </tr>
                </thead>
                <tbody>
                  {live.picked.map((r) => {
                    const isOpen = open.has(r.symbol);
                    return (
                      <React.Fragment key={r.symbol}>
                        <tr className="border-b border-slate-50 hover:bg-slate-50/60 cursor-pointer"
                          onClick={() => toggle(r.symbol)}>
                          <td className="py-1.5 pr-2">
                            <div className="flex items-center gap-1">
                              {isOpen ? <ChevronDown className="w-3 h-3 text-slate-400" /> : <ChevronRight className="w-3 h-3 text-slate-300" />}
                              <button
                                onClick={(e) => { e.stopPropagation(); onSymbolClick?.(r.symbol); }}
                                className="font-bold text-slate-800 hover:text-indigo-600">
                                {r.symbol}
                              </button>
                              {!!r.corporate_action_years && (
                                <span className="text-[8px] text-indigo-500" title={`${r.corporate_action_years} year(s) with a >5% share-count move — corporate action`}>▲{r.corporate_action_years}</span>
                              )}
                            </div>
                            <div className="text-[9px] text-slate-400 leading-none">{r.sector}</div>
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">{r.weight_pct.toFixed(1)}</td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-700">
                            {r.years_grown}/{r.years_measured}
                            <span className="text-slate-400"> ({r.consistency_pct}%)</span>
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono font-bold text-slate-800">{r.profit_cagr_5y_pct}%</td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">{r.profit_cagr_3y_pct ?? '—'}%</td>
                          <td className={`py-1.5 px-2 text-right font-mono ${(r.worst_year_pct ?? 0) < 0 ? 'text-rose-600' : 'text-slate-500'}`}>
                            {r.worst_year_pct === null ? '—' : `${r.worst_year_pct > 0 ? '+' : ''}${r.worst_year_pct}%`}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                            {r.trailing_pe ?? '—'} → {r.forward_pe ?? '—'}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono font-bold text-slate-700">
                            +{r.implied_growth_pct}%
                          </td>
                          <td className="py-1.5 pl-2 text-left text-[10px] text-slate-500">
                            {r.sector_rank_cagr ? `#${r.sector_rank_cagr} of ${r.sector_peers}` : '—'}
                            {r.sector_median_cagr_pct !== null && r.sector_median_cagr_pct !== undefined && (
                              <span className="text-slate-400"> · med {r.sector_median_cagr_pct}%</span>
                            )}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-slate-100 bg-slate-50/40">
                            <td colSpan={9} className="py-3 px-3">
                              <div className="grid md:grid-cols-2 gap-4">
                                <div>
                                  <div className="text-[9px] font-black uppercase text-slate-400 mb-1">
                                    Net profit by year (₹ cr) — colour is the year-on-year direction
                                  </div>
                                  <ProfitBars n={r} />
                                </div>
                                <div>
                                  <div className="text-[9px] font-black uppercase text-slate-400 mb-1">
                                    Quarterly net profit — colour is year-on-year, not sequential
                                  </div>
                                  <QuarterSpark n={r} />
                                </div>
                              </div>
                              <div className="mt-3 space-y-1.5">
                                {r.position && (
                                  <p className="text-[11px] text-slate-700 leading-snug">
                                    <b className="text-[9px] uppercase text-slate-400 mr-1">Position</b>{r.position}
                                  </p>
                                )}
                                <p className="text-[11px] leading-snug">
                                  <b className="text-[9px] uppercase text-slate-400 mr-1">Guidance</b>
                                  {r.guidance ? <span className="text-slate-700">{r.guidance}</span> : (
                                    <span className="text-slate-400">
                                      no dated guidance statement on file. The <b>Priced for +{r.implied_growth_pct}%</b> column
                                      is what the market is paying for, which is not the same as what management has
                                      committed to — this field stays empty rather than borrowing the multiple to fill it.
                                    </span>
                                  )}
                                </p>
                                {r.latest_quarter && (
                                  <div className="text-[10px] text-slate-600 leading-snug">
                                    <b className="text-[9px] uppercase text-slate-400 mr-1">
                                      Last print · {r.latest_quarter.period}
                                    </b>
                                    <span className="text-slate-400">({r.latest_quarter.as_of})</span>
                                    <ul className="mt-0.5 space-y-0.5">
                                      {r.latest_quarter.points.map((p, i) => (
                                        <li key={i} className="flex gap-1.5">
                                          <span className="text-slate-300">·</span><span>{p}</span>
                                        </li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                                {!!r.tailwinds?.length && (
                                  <p className="text-[10px] text-emerald-800 leading-snug">
                                    <b className="text-[9px] uppercase text-emerald-600 mr-1">For</b>{r.tailwinds.join(' · ')}
                                  </p>
                                )}
                                {!!r.headwinds?.length && (
                                  <p className="text-[10px] text-rose-700 leading-snug">
                                    <b className="text-[9px] uppercase text-rose-500 mr-1">Against</b>{r.headwinds.join(' · ')}
                                  </p>
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
            </div>
            {live.picked.length === 0 && (
              <p className="text-[11px] text-slate-500 py-3">
                Nothing passes at these thresholds. That is an answer, not an error — loosen one and watch which leg was binding.
              </p>
            )}
          </div>

          {/* ---- the rejection walk ---- */}
          <div className={card}>
            <button onClick={() => setShowRejected((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-wide text-slate-400 hover:text-slate-600">
              {showRejected ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              The walk — {pool.length - live.passers} names inspected and passed over
            </button>
            {showRejected && (
              <div className="mt-3 space-y-3">
                <p className="text-[10px] text-slate-500 leading-snug">
                  Grouped by the leg that failed. A name failing only the <b>forward</b> leg is priced for less growth
                  than the threshold — that is a statement about the multiple, not about the business, and it is where
                  a cheap compounder would hide. A name failing only <b>consistency</b> has the CAGR but got it in
                  bursts.
                </p>
                {fails.map(([legs, names]) => (
                  <div key={legs}>
                    <div className="flex flex-wrap items-center gap-1.5 mb-1">
                      {legs.split(' + ').map((l) => (
                        <span key={l} className={`text-[9px] font-black px-1.5 py-0.5 rounded-full border ${legTone(l)}`}>{l}</span>
                      ))}
                      <span className="text-[9px] text-slate-400">{names.length} name{names.length > 1 ? 's' : ''}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {names.map((r) => (
                        <span key={r.symbol}
                          className="text-[10px] rounded-lg border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-slate-600"
                          title={`${r.company} · wt ${r.weight_pct}% · grew ${r.years_grown}/${r.years_measured} (${r.consistency_pct}%) · 5y CAGR ${r.profit_cagr_5y_pct}% · priced for ${r.implied_growth_pct === null ? 'n/a' : `${r.implied_growth_pct}%`}`}>
                          <b className="text-slate-800">{r.symbol}</b>{' '}
                          <span className="text-slate-400">
                            {r.consistency_pct ?? '—'}% · {r.profit_cagr_5y_pct ?? '—'}% · {r.implied_growth_pct === null ? 'n/a' : `${r.implied_growth_pct}%`}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
                {doc.screen.no_data.length > 0 && (
                  <div>
                    <div className="text-[9px] font-black uppercase text-slate-400 mb-1">No financial history</div>
                    <p className="text-[10px] text-slate-500">
                      {doc.screen.no_data.map((x) => x.symbol).join(', ')} — not rejected, never tested. The Screener
                      export is missing or too thin to build a series, so these names never reached the three gates.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ---- the backtests ---- */}
          <div className={card}>
            <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-2">
              What the list would have returned — two versions, and the gap between them
            </div>
            <div className="grid md:grid-cols-2 gap-2.5">
              <BacktestCard bt={doc.backtest.point_in_time} tone="border-emerald-200 bg-emerald-50/40" />
              <BacktestCard bt={doc.backtest.today_screen} tone="border-slate-200 bg-slate-50" badge="look-ahead — not evidence" />
            </div>

            {doc.backtest.lookahead_inflation_pp !== null && (
              <div className="mt-2.5 rounded-xl border border-slate-300 bg-white px-3 py-2">
                <p className="text-[11px] text-slate-700 leading-snug">
                  <b className="font-mono text-rose-600">+{doc.backtest.lookahead_inflation_pp}pp</b> of the
                  right-hand number is hindsight. Screening on 2026 fundamentals and buying in{' '}
                  {doc.backtest.point_in_time.start?.slice(0, 4)} picks the names that turned out well; the
                  walk-forward version, which knew only what was published at the time, kept{' '}
                  <b>{doc.backtest.overlap.length}</b> of the same names and returned{' '}
                  <b className="font-mono">{doc.backtest.point_in_time.excess_pp >= 0 ? '+' : ''}{doc.backtest.point_in_time.excess_pp}pp</b>{' '}
                  over the index rather than{' '}
                  <b className="font-mono">{doc.backtest.today_screen.excess_pp >= 0 ? '+' : ''}{doc.backtest.today_screen.excess_pp}pp</b>.
                  {doc.backtest.only_in_today_screen.length > 0 && (
                    <> The contamination is concentrated in{' '}
                      <span className="font-mono text-slate-500">{doc.backtest.only_in_today_screen.join(', ')}</span> —
                      in today's screen, absent from the point-in-time one.</>
                  )}
                </p>
                <p className="text-[10px] text-slate-500 leading-snug mt-1.5">
                  {doc.point_in_time.note}
                </p>
              </div>
            )}

            <button onClick={() => setShowHoldings((v) => !v)}
              className="flex items-center gap-1.5 mt-3 text-[10px] font-black uppercase tracking-wide text-slate-400 hover:text-slate-600">
              {showHoldings ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              Holding-level returns, walk-forward portfolio
            </button>
            {showHoldings && !doc.backtest.point_in_time.error && (
              <div className="overflow-x-auto mt-2">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-[9px] uppercase font-black text-slate-400 border-b border-slate-200">
                      <th className="py-1.5 pr-2 text-left">Stock</th>
                      <th className="py-1.5 px-2 text-right">Bought</th>
                      <th className="py-1.5 px-2 text-right">Entry</th>
                      <th className="py-1.5 px-2 text-right">Last</th>
                      <th className="py-1.5 px-2 text-right">Return</th>
                      <th className="py-1.5 px-2 text-right">CAGR</th>
                      <th className="py-1.5 pl-2 text-right">vs index</th>
                    </tr>
                  </thead>
                  <tbody>
                    {doc.backtest.point_in_time.holdings.map((h) => {
                      const vs = h.ret_pct - doc.backtest.point_in_time.index_ret_pct;
                      return (
                        <tr key={h.symbol} className="border-b border-slate-50">
                          <td className="py-1 pr-2 font-bold text-slate-700">{h.symbol}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-400">{h.buy_date}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{h.buy}</td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{h.last}</td>
                          <td className={`py-1 px-2 text-right font-mono font-bold ${h.ret_pct >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                            {h.ret_pct >= 0 ? '+' : ''}{h.ret_pct}%
                          </td>
                          <td className="py-1 px-2 text-right font-mono text-slate-500">{h.cagr_pct}%</td>
                          <td className={`py-1 pl-2 text-right font-mono ${vs >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                            {vs >= 0 ? '+' : ''}{vs.toFixed(1)}pp
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <ul className="mt-3 space-y-1">
              {doc.backtest.caveats.map((c, i) => (
                <li key={i} className="text-[10px] text-slate-500 leading-snug flex gap-1.5">
                  <span className="text-slate-300">·</span><span>{c}</span>
                </li>
              ))}
            </ul>
            <p className="text-[9px] text-slate-400 mt-2 leading-snug">{doc.source}</p>
          </div>
        </>
      )}

      {!doc && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-indigo-600">Load screen</span> to walk the index.
          </p>
        </div>
      )}
    </div>
  );
}
