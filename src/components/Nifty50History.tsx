import React, { useCallback, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { card } from './nifty50Shared';

/**
 * Nifty50History — historical measurement only. No forecast lives on this tab.
 *
 * The split from the Outlook tab is deliberate and load-bearing: everything here is
 * something that HAPPENED, and everything on Outlook is arithmetic conditional on
 * inputs nobody can predict. Mixing them on one surface is how a measured sector
 * contribution ends up being quoted as a target.
 *
 * CHART RULES APPLIED HERE
 *   · No dual axes, ever. Quantities in index points, dollars and contract counts are
 *     STACKED on a shared time axis with their own scales, never overlaid — giving each
 *     its own axis on one chart lets the choice of bounds decide where the lines appear
 *     to cross, which is a real degree of freedom to manufacture a relationship.
 *   · The sector matrix is a DIVERGING scale (added / nothing / subtracted) with a
 *     neutral gray midpoint, not a rainbow.
 *   · Index EPS is drawn as a STEP because it changes on one day a year.
 *
 * Convention: NO auto-run — nothing fetches until the button is pressed.
 */

type SecRow = { sector: string; n: number; contrib_pp: number[]; share_latest_pct: number };
type Pub = { fy: string; published: string; month_index: number | null; index_eps: number; yoy_pct: number | null };
type QRow = { period: string; yoy_pct: number; panel: number; month_index: number | null };
type TtmRow = { period: string; panel: number; ttm_profit_cr: number; increment_cr?: number };
type WinPt = { d: string; nifty: number; wti: number; fii_net_short: number | null };
type Book = { net_min: number; net_max: number; net_over_gross_median_pct: number; always_same_sign: boolean };

type Finding = { id: string; claim: string; verdict: string; detail: string };
type Handoff = { input: string; value: string; carry: string };

export type HistDoc = {
  as_of: string; note: string;
  conclusions: { survived: Finding[]; handoff: Handoff[]; note: string };
  sector_matrix: { fy_labels: string[]; rows: SecRow[]; index_growth_pp: number[]; panel: number; note: string };
  cycle: { monthly: { m: string; close: number }[]; publications: Pub[]; quarterly: QRow[]; ttm: TtmRow[]; ttm_note: string };
  window_2026: { points: WinPt[]; from: string; to: string; nifty_low: WinPt; fii_short_peak: WinPt; eps_frozen_at: number; note: string; scales_note: string };
  flows: {
    available: boolean; first: string; last: string; sessions: number;
    fii: { gross_buy_cr: number; gross_sell_cr: number; net_cr: number; gross_turnover_cr: number; net_over_gross_pct: number; daily_ratio_median_pct: number };
    dii: { net_cr: number; net_over_gross_pct: number };
    daily: { d: string; fii_buy: number; fii_sell: number; fii_net: number; dii_net: number }[];
    index_futures: Book; stock_futures: Book; positioning_sessions: number;
    note: string; caveat: string;
  };
};

const INK = { pos: '#2a78d6', neg: '#e34948', mid: '#f0efec', s1: '#2a78d6', s2: '#eb6834', s3: '#1baf7a', rule: '#e2e1dc', muted: '#7c7b76', text: '#0b0b0b', surf: '#fcfcfb' };
const cr = (n: number) => Math.round(n).toLocaleString('en-IN');
const sgn = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;

/** Diverging fill: one hue each side of a neutral gray midpoint, intensity by |value|. */
function divFill(v: number) {
  const t = Math.min(Math.sqrt(Math.abs(v) / 6), 1);
  if (v === 0) return INK.mid;
  return `color-mix(in oklab, ${v > 0 ? INK.pos : INK.neg} ${Math.round(t * 100)}%, ${INK.mid})`;
}

/** One stacked panel set on a SHARED x-axis. Each panel keeps its own absolute scale. */
function Stack({ n, panels, marks, band, tick }: {
  n: number;
  panels: { label: string; color: string; at: (i: number) => number | null; lo: number; hi: number; ticks: number[]; fmt: (v: number) => string }[];
  marks?: { i: number; label: string }[];
  band?: [number, number];
  tick: (i: number) => string | null;
}) {
  const W = 1000, L = 78, R = 26, PH = 108, GAP = 26;
  const H = panels.length * (PH + GAP) + 34;
  const X = (i: number) => L + (i / Math.max(n - 1, 1)) * (W - L - R);
  const el: React.ReactNode[] = [];
  panels.forEach((p, pi) => {
    const y0 = pi * (PH + GAP) + 20, y1 = y0 + PH;
    const Y = (v: number) => y1 - ((v - p.lo) / (p.hi - p.lo)) * (y1 - y0);
    p.ticks.forEach((v) => {
      el.push(<line key={`g${pi}-${v}`} x1={L} y1={Y(v)} x2={W - R} y2={Y(v)} stroke={INK.rule} strokeWidth={1} />);
      el.push(<text key={`t${pi}-${v}`} x={L - 7} y={Y(v) + 3.5} textAnchor="end" fontSize={9} fill={INK.muted}>{p.fmt(v)}</text>);
    });
    const pts: string[] = [];
    for (let i = 0; i < n; i++) { const v = p.at(i); if (v !== null) pts.push(`${X(i)},${Y(v)}`); }
    el.push(<polyline key={`p${pi}`} points={pts.join(' ')} fill="none" stroke={p.color} strokeWidth={2.2} strokeLinejoin="round" />);
    el.push(<text key={`l${pi}`} x={L} y={y0 - 6} fontSize={10.5} fontWeight={800} fill={p.color}>{p.label}</text>);
    const lastV = p.at(n - 1);
    if (lastV !== null) {
      el.push(<circle key={`c${pi}`} cx={X(n - 1)} cy={Y(lastV)} r={4.5} fill={p.color} stroke={INK.surf} strokeWidth={2} />);
      el.push(<text key={`v${pi}`} x={X(n - 1) - 6} y={Y(lastV) - 10} textAnchor="end" fontSize={11} fontWeight={800} fill={INK.text}>{p.fmt(lastV)}</text>);
    }
    for (let i = 0; i < n; i++) {
      const v = p.at(i);
      if (v === null) continue;
      el.push(<circle key={`h${pi}-${i}`} cx={X(i)} cy={Y(v)} r={8} fill="transparent">
        <title>{`${tick(i) ?? i} · ${p.label} ${p.fmt(v)}`}</title></circle>);
    }
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }} role="img" aria-label={panels.map((p) => p.label).join(', ')}>
      {band && <rect x={X(band[0])} y={8} width={X(band[1]) - X(band[0])} height={H - 40} fill={INK.muted} opacity={0.1} />}
      {marks?.map((m) => (
        <g key={m.label}>
          <line x1={X(m.i)} y1={8} x2={X(m.i)} y2={H - 32} stroke={INK.text} strokeWidth={1} strokeDasharray="3 3" opacity={0.55} />
          <text x={X(m.i)} y={H - 20} textAnchor="middle" fontSize={9} fontWeight={700} fill={INK.muted}>{m.label}</text>
        </g>
      ))}
      {el}
      {Array.from({ length: n }, (_, i) => tick(i) && (
        <text key={`x${i}`} x={X(i)} y={H - 5} textAnchor="middle" fontSize={9} fill={INK.muted}>{tick(i)}</text>
      ))}
    </svg>
  );
}

export function Nifty50History() {
  const [doc, setDoc] = useState<HistDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch('/api/nifty-history');
      const j = await r.json();
      if (j.success) setDoc(j.history);
      else setErr(j.detail || 'Failed to load history');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  const cyc = doc?.cycle;
  const monthly = cyc?.monthly ?? [];
  const epsAt = useMemo(() => {
    // Step, not interpolation — the number in the price changes on one day a year.
    if (!cyc) return () => null;
    const pub = cyc.publications.filter((p) => p.month_index !== null);
    return (i: number) => {
      let v: number | null = null;
      pub.forEach((p) => { if ((p.month_index as number) <= i) v = p.index_eps; });
      return v;
    };
  }, [cyc]);
  const qAt = useMemo(() => {
    if (!cyc) return () => null;
    // Explicit tuple type: without it Map infers <unknown, unknown> from an array
    // literal and every downstream comparison fails to typecheck.
    const m = new Map<number, number>(
      cyc.quarterly.filter((q) => q.month_index !== null)
        .map((q) => [q.month_index as number, q.yoy_pct] as [number, number]));
    const idx: number[] = Array.from(m.keys()).sort((a, b) => a - b);
    return (i: number) => {
      if (!idx.length || i < idx[0]) return null;
      let v: number | null = null;
      idx.forEach((k) => { if (k <= i) v = m.get(k) as number; });
      return v;
    };
  }, [cyc]);

  const w = doc?.window_2026;

  return (
    <div className="space-y-4">
      <div className={card}>
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] font-black uppercase tracking-wide text-slate-400">
            Earnings history — measurement, no forecast {doc ? `(${doc.as_of})` : ''}
          </span>
          <button onClick={load} disabled={loading}
            className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white disabled:opacity-50"
            title="Reads nifty_history.json — written by backend/quant/nifty_history.py.">
            {loading ? 'Loading…' : doc ? 'Reload' : 'Load history'}
          </button>
        </div>
        {err && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">{err}</div>}
        {doc && <p className="text-[10px] text-slate-400 mt-2 leading-snug">{doc.note} · Forecast lives on the Outlook tab.</p>}
      </div>

      {doc && (
        <>
          {/* ---- sector contribution matrix ---- */}
          <div className={card}>
            <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
              1 · Sector contribution to index profit growth, by year
            </div>
            <p className="text-[11px] text-slate-600 leading-snug mb-2">
              <code className="text-[10px]">(sector profit<sub>t</sub> − profit<sub>t−1</sub>) ÷ total profit<sub>t−1</sub></code>,
              in pp. <b>Rows sum exactly to index growth</b> — a decomposition, not a set of ratios.
              Share says who earns; contribution says who moved the number.
            </p>
            <div className="flex flex-wrap gap-4 text-[10px] text-slate-500 mb-2">
              <span><span className="inline-block w-3 h-3 rounded-sm align-[-1px] mr-1" style={{ background: INK.neg }} />subtracted</span>
              <span><span className="inline-block w-3 h-3 rounded-sm align-[-1px] mr-1 border border-slate-200" style={{ background: INK.mid }} />≈ nothing</span>
              <span><span className="inline-block w-3 h-3 rounded-sm align-[-1px] mr-1" style={{ background: INK.pos }} />added</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]" style={{ borderSpacing: 2, borderCollapse: 'separate' }}>
                <thead>
                  <tr className="text-[9px] uppercase font-black text-slate-400">
                    <th className="text-left py-1 pr-2">Sector</th><th className="text-right px-1">n</th>
                    {doc.sector_matrix.fy_labels.map((f) => <th key={f} className="text-right px-1">{f}</th>)}
                    <th className="text-right pl-2">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.sector_matrix.rows.map((r) => (
                    <tr key={r.sector}>
                      <td className="py-1 pr-2 text-slate-600">{r.sector}</td>
                      <td className="text-right px-1 text-slate-400">{r.n}</td>
                      {r.contrib_pp.map((v, i) => (
                        <td key={i} className="text-right px-1.5 py-1 rounded font-mono"
                          style={{ background: divFill(v), color: Math.sqrt(Math.abs(v) / 6) > 0.55 ? INK.surf : INK.text }}
                          title={`${r.sector} · ${doc.sector_matrix.fy_labels[i]} · ${sgn(v)}pp`}>
                          {sgn(v)}
                        </td>
                      ))}
                      <td className="text-right pl-2 text-slate-500">{r.share_latest_pct}%</td>
                    </tr>
                  ))}
                  <tr className="font-black">
                    <td className="pt-2 border-t border-slate-200">Index growth</td><td className="border-t border-slate-200" />
                    {doc.sector_matrix.index_growth_pp.map((g, i) => (
                      <td key={i} className="text-right px-1 pt-2 border-t border-slate-200 font-mono">{sgn(g)}</td>
                    ))}
                    <td className="border-t border-slate-200" />
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-[9px] text-slate-400 mt-2 leading-snug">{doc.sector_matrix.note}</p>
          </div>

          {/* ---- the cycle ---- */}
          <div className={card}>
            <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
              2 · The earnings cycle — did earnings lead or lag the market?
            </div>
            <p className="text-[11px] text-slate-600 leading-snug mb-2">
              Four panels, <b>one shared time axis</b> — the correct alternative to a dual-axis chart.
              Index EPS is a <b>step</b>: the number in the price changes on one day a year and is flat
              between. The quarterly overlay only starts where a fixed-membership panel exists.
            </p>
            <Stack n={monthly.length}
              tick={(i) => (i % 12 === 0 ? monthly[i]?.m.slice(0, 4) : null)}
              marks={cyc!.publications.filter((p) => p.month_index !== null && p.yoy_pct !== null)
                .map((p) => ({ i: p.month_index as number, label: p.fy }))}
              panels={[
                { label: 'NIFTY LEVEL', color: INK.s1, at: (i) => monthly[i]?.close ?? null, lo: 8000, hi: 27000, ticks: [10000, 15000, 20000, 25000], fmt: (v) => `${(v / 1000).toFixed(0)}k` },
                { label: 'INDEX EPS · steps on publication', color: INK.s2, at: epsAt, lo: 350, hi: 1250, ticks: [500, 800, 1100], fmt: (v) => `₹${Math.round(v)}` },
                { label: 'QUARTERLY PAT GROWTH, YoY', color: INK.s3, at: qAt, lo: 0, hi: 24, ticks: [0, 10, 20], fmt: (v) => `${v.toFixed(0)}%` },
              ]} />
            <div className="mt-3">
              <div className="text-[9px] font-black uppercase text-slate-400 mb-1">Annual growth, by fiscal year</div>
              <div className="flex items-end gap-1.5 h-16">
                {cyc!.publications.filter((p) => p.yoy_pct !== null).map((p) => {
                  const mx = Math.max(...cyc!.publications.map((x) => Math.abs(x.yoy_pct ?? 0)));
                  const v = p.yoy_pct as number;
                  return (
                    <div key={p.fy} className="flex-1 flex flex-col items-center justify-end h-full">
                      <span className="text-[9px] font-mono font-bold leading-none mb-0.5" style={{ color: v >= 0 ? INK.pos : INK.neg }}>{sgn(v)}</span>
                      <div className="w-full rounded-sm" style={{ height: `${Math.max(3, (Math.abs(v) / mx) * 100)}%`, background: v >= 0 ? INK.pos : INK.neg }} title={`${p.fy} ${sgn(v)}%`} />
                      <span className="text-[8px] text-slate-400 leading-none mt-0.5">{p.fy}</span>
                    </div>
                  );
                })}
              </div>
              <p className="text-[11px] text-slate-600 leading-snug mt-2">
                <b>The index led the two big turns and lagged this one.</b> Price fell to its 2020 low
                months before the contraction printed, and ran through 2020–21 well before the boom was
                published. It did <b>not</b> lead the current deceleration — the index made highs into late
                2025 while growth was already rolling over, then re-priced in the first quarter of 2026.
              </p>
            </div>
          </div>

          {/* ---- 3 · current regime ---- */}
          <div className={card}>
            <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
              3 · Current earnings regime — accelerating or decelerating?
            </div>
            <p className="text-[11px] text-slate-600 leading-snug mb-2">
              The annual figure is an average. The regime question is about the <b>exit rate</b> and the
              direction of travel, and the TTM increments answer it without any percentage arithmetic at all.
            </p>
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <div className="text-[9px] font-black uppercase text-slate-400 mb-1">Quarterly YoY, fixed panel</div>
                <div className="flex items-end gap-2 h-20">
                  {cyc!.quarterly.map((q) => {
                    const mx = Math.max(...cyc!.quarterly.map((x) => x.yoy_pct));
                    return (
                      <div key={q.period} className="flex-1 flex flex-col items-center justify-end h-full">
                        <span className="text-[9px] font-mono font-bold text-slate-700 leading-none mb-0.5">{q.yoy_pct}</span>
                        <div className="w-full rounded-sm" style={{ height: `${Math.max(4, (q.yoy_pct / mx) * 100)}%`, background: INK.s3 }}
                          title={`${q.period} · ${q.yoy_pct}% YoY · ${q.panel} names`} />
                        <span className="text-[8px] text-slate-400 leading-none mt-0.5">{q.period.slice(2, 7)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div>
                <div className="text-[9px] font-black uppercase text-slate-400 mb-1">TTM profit — the increments</div>
                <table className="w-full text-[10px]">
                  <tbody>
                    {cyc!.ttm.filter((t) => t.increment_cr !== undefined).map((t) => (
                      <tr key={t.period}>
                        <td className="text-slate-500 py-0.5">{t.period.slice(0, 7)}</td>
                        <td className="text-right font-mono text-slate-600">₹{cr(t.ttm_profit_cr)}</td>
                        <td className="text-right font-mono font-bold" style={{ color: INK.pos }}>+{cr(t.increment_cr as number)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <p className="text-[11px] text-rose-800 font-bold mt-2 leading-snug">
              Five consecutive steps down, no reversals. Aggregate profit is still growing; it has almost
              stopped accelerating. That is the regime, and it is the growth input the Forecast tab starts from.
            </p>
            <p className="text-[9px] text-slate-400 leading-snug mt-1">{cyc!.ttm_note}</p>
          </div>

          {/* ---- the 2026 window ---- */}
          {w && (
            <div className={card}>
              <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
                4 · {w.from} → {w.to} · the de-rating, with EPS unchanged
              </div>
              <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50/50 px-3 py-2 mb-3">
                <AlertTriangle className="w-3.5 h-3.5 text-rose-500 shrink-0 mt-0.5" />
                <p className="text-[11px] text-rose-900 leading-snug">
                  <b>Earnings frozen at {cr(w.eps_frozen_at)} across this whole window.</b> No annual result
                  was published, so <b>every point of index movement here is the multiple</b> — the one clean
                  natural experiment in the sample for the valuation channel.
                </p>
              </div>
              <Stack n={w.points.length}
                tick={(i) => (i % 4 === 0 || i === w.points.length - 1 ? w.points[i].d.slice(5) : null)}
                marks={[
                  { i: w.points.findIndex((p) => p.d === w.nifty_low.d), label: 'Nifty low' },
                  { i: w.points.findIndex((p) => p.d === w.fii_short_peak.d), label: 'FII short peak' },
                ].filter((m) => m.i >= 0)}
                panels={[
                  { label: 'NIFTY LEVEL', color: INK.s1, at: (i) => w.points[i].nifty, lo: 22000, hi: 26500, ticks: [22000, 23500, 25000, 26000], fmt: cr },
                  { label: 'WTI CRUDE, $/bbl', color: INK.s2, at: (i) => w.points[i].wti, lo: 50, hi: 115, ticks: [60, 75, 90, 105], fmt: (v) => `$${Math.round(v)}` },
                  { label: 'FII NET SHORT, index futures (contracts)', color: INK.s3, at: (i) => w.points[i].fii_net_short, lo: 110000, hi: 290000, ticks: [125000, 175000, 225000, 275000], fmt: (v) => `${(v / 1000).toFixed(0)}k` },
                ]} />
              <p className="text-[10px] text-slate-600 leading-snug mt-2">
                <b>The short peaked after the low.</b> Nifty bottomed at {cr(w.nifty_low.nifty)} on{' '}
                {w.nifty_low.d}; the FII index short topped at {cr(w.fii_short_peak.fii_net_short ?? 0)} on{' '}
                {w.fii_short_peak.d}, with the index already above its low. The short built up during and after
                the sell-off and peaked once the low was in — evidence against treating the FII index
                short as a leading bearish signal.
              </p>
              <p className="text-[9px] text-slate-400 mt-1.5 leading-snug">{w.scales_note}</p>
              <p className="text-[9px] text-slate-400 mt-1 leading-snug">{w.note}</p>
            </div>
          )}

          {/* ---- 5 · positioning ---- */}
          {doc.flows?.available && (
            <div className={card}>
              <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
                5 · FII positioning — bearish view, or a hedge?
              </div>
              <p className="text-[11px] text-slate-600 leading-snug mb-2">
                "FII are net short index futures" is true on every session observed. Read alone it sounds
                like a directional bet. Read beside the stock-futures book it is something else.
              </p>
              <table className="w-full text-[11px]">
                <thead><tr className="text-[9px] uppercase font-black text-slate-400 border-b border-slate-200">
                  <th className="text-left py-1">FII book</th><th className="text-right px-2">net range</th>
                  <th className="text-right px-2">net ÷ gross</th><th className="text-left pl-2">shape</th>
                </tr></thead>
                <tbody>
                  <tr className="border-b border-slate-50"><td className="py-1 text-slate-600">Cash equity</td>
                    <td className="text-right px-2 font-mono">₹{cr(doc.flows.fii.net_cr)} cr</td>
                    <td className="text-right px-2 font-mono font-bold">{doc.flows.fii.net_over_gross_pct}%</td>
                    <td className="pl-2 text-slate-500">churning, no direction</td></tr>
                  <tr className="border-b border-slate-50"><td className="py-1 text-slate-600">Index futures</td>
                    <td className="text-right px-2 font-mono">{cr(doc.flows.index_futures.net_min)} … {cr(doc.flows.index_futures.net_max)}</td>
                    <td className="text-right px-2 font-mono font-bold">{doc.flows.index_futures.net_over_gross_median_pct}%</td>
                    <td className="pl-2 text-slate-500">one-sided <b>short</b>, every session</td></tr>
                  <tr><td className="py-1 text-slate-600">Stock futures</td>
                    <td className="text-right px-2 font-mono">+{cr(doc.flows.stock_futures.net_min)} … +{cr(doc.flows.stock_futures.net_max)}</td>
                    <td className="text-right px-2 font-mono font-bold">{doc.flows.stock_futures.net_over_gross_median_pct}%</td>
                    <td className="pl-2 text-slate-500">one-sided <b>long</b>, every session</td></tr>
                </tbody>
              </table>
              <p className="text-[11px] text-slate-700 leading-snug mt-2">
                <b>Long single stocks, short the index</b> — a long-alpha / short-beta structure. Relative
                value, not a bearish directional bet. It also reconciles two findings that never sat
                together: always net short (H26), yet that short is only ~1% of their cash equity so it
                cannot be a cash hedge either (H27). This is consistent with the index short being a hedge
                against stock-futures longs.
              </p>
              <p className="text-[9px] text-slate-400 mt-1.5 leading-snug">{doc.flows.caveat}</p>
            </div>
          )}

          {/* ---- 6 · flows, gross ---- */}
          {doc.flows?.available && (
            <div className={card}>
              <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-2">
                6 · Gross FII flows — why headline net selling misleads
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {[
                  ['FII gross buy', `₹${cr(doc.flows.fii.gross_buy_cr)} cr`, `${doc.flows.sessions} sessions`],
                  ['FII gross sell', `₹${cr(doc.flows.fii.gross_sell_cr)} cr`, ''],
                  ['FII net', `₹${cr(doc.flows.fii.net_cr)} cr`, 'the headline number'],
                  ['Net ÷ gross', `${doc.flows.fii.net_over_gross_pct}%`, `DII run at ${doc.flows.dii.net_over_gross_pct}%`],
                ].map(([k, v, s]) => (
                  <div key={k} className="rounded-xl border border-slate-200 px-3 py-2">
                    <div className="text-[9px] font-black uppercase text-slate-400">{k}</div>
                    <div className="text-base font-black font-mono text-slate-800 leading-tight">{v}</div>
                    <div className="text-[9px] text-slate-400">{s}</div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-rose-800 leading-snug mt-2.5">
                <b>FII net of ₹{cr(doc.flows.fii.net_cr)} cr is {doc.flows.fii.net_over_gross_pct}% of
                ₹{cr(doc.flows.fii.gross_turnover_cr)} cr of gross activity.</b> Daily net/gross runs at a
                median of {doc.flows.fii.daily_ratio_median_pct}%. This is the likeliest reason the whole
                FII-predicts-returns battery found nothing — there is almost no directional content in the
                number the headlines quote.
              </p>
            </div>
          )}

          {/* ---- 7 · what survived, and what carries forward ---- */}
          {doc.conclusions && (
            <div className={card}>
              <div className="text-[11px] font-black uppercase tracking-wide text-slate-400 mb-1">
                7 · Historical conclusions — only what survived a test
              </div>
              <div className="space-y-2 mt-2">
                {doc.conclusions.survived.map((f) => (
                  <div key={f.id} className="flex items-start gap-2.5">
                    <span className="text-[9px] font-black px-1.5 py-0.5 rounded-full border border-slate-300 bg-slate-50 text-slate-500 shrink-0 mt-0.5">{f.id}</span>
                    <div className="min-w-0">
                      <div className="text-[11px] leading-snug">
                        <span className="text-slate-500">{f.claim}</span>{' — '}
                        <b className={/DEAD|NOT DETECTABLE|MISLEADING/.test(f.verdict) ? 'text-rose-700' : 'text-amber-700'}>{f.verdict}</b>
                      </div>
                      <div className="text-[10px] text-slate-500 leading-snug">{f.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-slate-500 leading-snug mt-2.5 pt-2 border-t border-slate-100">
                {doc.conclusions.note}
              </p>

              <div className="text-[10px] font-black uppercase tracking-wide text-slate-400 mt-4 mb-1.5">
                What this page hands to the Forecast tab
              </div>
              <div className="grid md:grid-cols-3 gap-2">
                {doc.conclusions.handoff.map((h) => (
                  <div key={h.input} className="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2">
                    <div className="text-[9px] font-black uppercase text-slate-400">{h.input}</div>
                    <div className="text-[12px] font-black text-slate-800 leading-tight mt-0.5">{h.value}</div>
                    <div className="text-[10px] text-slate-500 leading-snug mt-1">{h.carry}</div>
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-slate-600 leading-snug mt-2.5">
                Those three are the whole handoff. The Forecast tab's job from here is one equation —
                <b> forward EPS × exit multiple → Nifty level</b> — with both inputs stated rather than
                assumed, and nothing on this page quoted as a target.
              </p>
            </div>
          )}
        </>
      )}

      {!doc && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">
            Press <span className="text-indigo-600">Load history</span> to measure the cycle.
          </p>
        </div>
      )}
    </div>
  );
}
