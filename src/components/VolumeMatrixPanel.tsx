import React, { useEffect, useState, useCallback } from 'react';
import { Activity, RefreshCw, AlertTriangle } from 'lucide-react';

/*
 * VolumeMatrixPanel
 * -----------------
 * Per-constituent abnormal traded-value (volume×close) z-score across three session
 * windows — Morning (09:15–10:00), Whole-day, End-of-day (14:45–15:30 IST) — each
 * z-scored vs the symbol's own history for that window. Heatmap: red = unusually
 * heavy volume (attention/flow); shows WHO is trading abnormally and WHEN.
 */

interface Props { date: string; }

const WIN_LABEL: Record<string, string> = { day: 'Whole day', gap: 'O/N gap', morning: 'Morning', midday: 'Midday', eod: 'End of day', open15: 'Open 15m' };
const WIN_SUB: Record<string, string> = { day: 'vs prev close', gap: 'move: pc→open · vol: pre-open 09:00–09:15', morning: '09:15–10:00', midday: '10:00–14:45', eod: '14:45–15:30', open15: '09:15–09:30 · ⊂ morning' };
// open15 is a diagnostic zoom INSIDE morning (block/bulk) — not part of the additive chain
const DIAGNOSTIC = new Set(['open15']);

export const VolumeMatrixPanel: React.FC<Props> = ({ date }) => {
  const [res, setRes] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [topN, setTopN] = useState(15);
  const [sortBy, setSortBy] = useState<'pts' | 'z'>('pts');   // rank by index-points move or volume σ

  const load = useCallback(async () => {
    if (!date) return;
    setLoading(true); setErr('');
    try {
      const r = await fetch(`/api/volume-window-matrix?date=${encodeURIComponent(date)}`);
      const j = await r.json();
      if (!j.success) { setErr(j.detail || 'no data for this date'); setRes(null); }
      else setRes(j);
    } catch (e) { setErr('Cannot reach /api.'); }
    finally { setLoading(false); }
  }, [date]);

  useEffect(() => { load(); }, [load]);

  const cell = (z: number | null) => {
    if (z === null || z === undefined) return { bg: '#f8fafc', fg: '#cbd5e1' };
    const a = Math.min(Math.abs(z) / 2.5, 1);           // ~2.5σ saturates
    const bg = z >= 0 ? `rgba(244,63,94,${0.08 + a * 0.8})` : `rgba(59,130,246,${0.08 + a * 0.8})`;
    return { bg, fg: a >= 0.5 ? '#fff' : '#334155' };
  };

  const windows = res?.windows || ['day', 'gap', 'morning', 'midday', 'eod', 'open15'];
  // rank by the largest absolute value (points move, or volume σ) across the windows
  const rows = [...(res?.rows || [])]
    .sort((a: any, b: any) => {
      const m = (r: any) => Math.max(...windows.map((w: string) => Math.abs(r[w]?.[sortBy] ?? 0)));
      return m(b) - m(a);
    })
    .slice(0, topN);

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-indigo-600" />
          <h2 className="text-base font-bold text-slate-800">Volume Window Matrix</h2>
        </div>
        <div className="flex items-center gap-2">
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as any)} className="text-xs border border-slate-200 rounded-lg px-2 py-1" title="Rank rows by the biggest index-points move or the biggest volume z-score">
            <option value="pts">sort by points move</option>
            <option value="z">sort by volume σ</option>
          </select>
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className="text-xs border border-slate-200 rounded-lg px-2 py-1">
            {[10, 15, 25, 50].map((n) => <option key={n} value={n}>top {n}</option>)}
          </select>
          <button onClick={load} className="p-1 rounded hover:bg-slate-100" title="Reload"><RefreshCw className={`w-3.5 h-3.5 text-slate-400 ${loading ? 'animate-spin' : ''}`} /></button>
        </div>
      </div>
      <div className="text-[11px] text-slate-400 mb-3">
        Close &amp; Δ% (vs prev close) per name · volume z-score (colour) + index-points (pts) per window · {date} · {res?.n_symbols ?? 0} names
      </div>

      {res?.nifty && (
        <div className="flex items-center gap-3 flex-wrap text-[11px] mb-2 bg-slate-50 border border-slate-100 rounded-lg px-3 py-1.5">
          <span className="font-semibold text-slate-500">NIFTY move:</span>
          {windows.map((w: string) => {
            const p = res.nifty[w]?.pts;
            return (
              <span key={w} className="text-slate-600">
                {WIN_LABEL[w]} <span className={`font-mono font-semibold ${p == null ? 'text-slate-300' : p >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{p == null ? '—' : `${p > 0 ? '+' : ''}${p} pt`}</span>
              </span>
            );
          })}
        </div>
      )}

      {/* Additive reconciliation: gap + morning + midday + eod = whole day */}
      {res?.nifty && res?.additive_chain && (
        <div className="text-[11px] mb-3 px-3 py-1.5 bg-indigo-50 border border-indigo-100 rounded-lg text-slate-600">
          {res.additive_chain.map((w: string, i: number) => {
            const p = res.nifty[w]?.pts;
            return (
              <span key={w}>
                {i > 0 && <span className="text-slate-400"> + </span>}
                <span className="text-slate-500">{WIN_LABEL[w]}</span>{' '}
                <span className={`font-mono font-semibold ${p == null ? 'text-slate-300' : p >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{p == null ? '—' : `${p > 0 ? '+' : ''}${p}`}</span>
              </span>
            );
          })}
          <span className="text-slate-400"> = </span>
          <span className="font-semibold text-slate-500">Whole day</span>{' '}
          <span className={`font-mono font-bold ${res.nifty.day?.pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{res.nifty.day?.pts >= 0 ? '+' : ''}{res.nifty.day?.pts} pt</span>
          <span className="text-[10px] text-indigo-400 ml-1">· O/N gap volume = pre-open auction (09:00–09:15), where the gap clears</span>
        </div>
      )}

      {err && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mb-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}
        </div>
      )}

      {res && rows.length > 0 && (
        <>
          <div className="overflow-auto">
            <table className="text-[11px] border-separate" style={{ borderSpacing: 2 }}>
              <thead>
                <tr>
                  <th className="p-1 text-left text-slate-400 font-normal">Stock <span className="text-slate-300">(wt%)</span></th>
                  <th className="p-1 text-right text-slate-400 font-normal pr-2">Close</th>
                  <th className="p-1 text-right text-slate-400 font-normal pr-3 border-r border-slate-100">Δ%</th>
                  {windows.map((w: string) => (
                    <th key={w} className={`p-1 text-center font-semibold w-24 ${DIAGNOSTIC.has(w) ? 'text-amber-600 border-l-2 border-dashed border-amber-200 bg-amber-50/40' : 'text-slate-500'}`}>
                      {WIN_LABEL[w] || w}<div className={`text-[9px] font-normal ${DIAGNOSTIC.has(w) ? 'text-amber-500' : 'text-slate-400'}`}>{WIN_SUB[w]}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any) => (
                  <tr key={r.symbol}>
                    <td className="p-1 text-slate-600 whitespace-nowrap pr-2 font-semibold">
                      {r.symbol} <span className="text-slate-400 font-normal">{r.weight ? `${r.weight}%` : ''}</span>
                      {r.open_spike != null && r.open_spike >= 5 && (
                        <span title={`Opening single-minute volume ${r.open_spike}× the day's typical minute (09:15–09:30) — possible block/bulk at the open`}
                          className="ml-1 text-[9px] font-bold bg-amber-100 text-amber-700 px-1 rounded cursor-help">⚡{r.open_spike}×</span>
                      )}
                    </td>
                    <td className="p-1 text-right font-mono text-slate-500 pr-2 whitespace-nowrap">{r.close != null ? r.close.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                    <td className={`p-1 text-right font-mono font-semibold pr-3 border-r border-slate-100 whitespace-nowrap ${r.chg_pct == null ? 'text-slate-300' : r.chg_pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{r.chg_pct == null ? '—' : `${r.chg_pct > 0 ? '+' : ''}${r.chg_pct}%`}</td>
                    {windows.map((w: string) => {
                      const d = r[w] || {};
                      const z = d.z; const pts = d.pts;
                      const c = cell(z);
                      return (
                        <td key={w} title={`${r.symbol} · ${WIN_LABEL[w]}: vol ${z ?? '—'}σ · ${pts ?? '—'} index pts (ret ${d.ret ?? '—'}%)${DIAGNOSTIC.has(w) ? ' — diagnostic (⊂ morning, not summed)' : ''}`}
                          className={`text-center font-mono w-24 h-10 rounded leading-tight ${DIAGNOSTIC.has(w) ? 'border-l-2 border-dashed border-amber-200' : ''}`} style={{ background: c.bg, color: c.fg }}>
                          <div>{z === null || z === undefined ? '—' : `${z > 0 ? '+' : ''}${z}σ`}</div>
                          <div className="text-[9px] opacity-80">{pts === null || pts === undefined ? '' : `${pts > 0 ? '+' : ''}${pts} pt`}</div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-slate-400 mt-2">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: 'rgba(244,63,94,0.8)' }} /> unusually heavy volume (+z)</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: '#f8fafc' }} /> normal / no history</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: 'rgba(59,130,246,0.8)' }} /> unusually light (−z)</span>
            <span className="flex items-center gap-1 text-amber-500"><span className="inline-block w-0 h-3 border-l-2 border-dashed border-amber-300" /> diagnostic zoom (⊂ morning, not in sum)</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-2">
            Read a <span className="font-semibold">row</span> to see when a stock is active (morning-only = opening-driven; all three = sustained; EOD-only = closing-auction). A <span className="font-semibold">column</span> lit up in a few high-weight names = a concentrated (fragile) index move; spread across many = broad participation.
          </div>
        </>
      )}
      {res && rows.length === 0 && !err && (
        <div className="text-xs text-slate-400">No constituent volume with enough history for {date}.</div>
      )}
    </div>
  );
};
