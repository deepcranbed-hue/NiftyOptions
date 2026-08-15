import React, { useCallback, useMemo, useState } from 'react';
import { Zap, RefreshCw, AlertTriangle, TrendingUp, TrendingDown } from 'lucide-react';

/**
 * ShockRecoveryPanel — the VIX-filtered dip-buy flagger.
 * Data: /api/shock-recovery (validated over-bouncer stats + today's live shock/VIX/200DMA state).
 * Convention: NO auto-run — loads only on button press.
 *
 * HONEST FRAMING (per SECTOR_INTELLIGENCE_FRAMEWORK.md): this is a TECHNICAL mean-reversion edge,
 * not a quality signal — ROE has ~0 correlation with the bounce. The VIX filter is the edge; the
 * over-bouncers are high-beta cyclicals; size for the tail (worst), and stand aside in a downtrend.
 */

type Stock = {
  sym: string; setupA_n: number; setupA_mean: number; setupA_pos: number;
  setupA_worst: number; roe: number | null; above_200dma: boolean | null;
};
type Data = {
  as_of?: string; thresh: number; hivix: number; roe_corr: number | null;
  nifty_ret: number | null; vix: number | null; shock_today: boolean; vix_elevated: boolean;
  setup_active: boolean; live_error?: string; stocks: Stock[];
};

const NAME: Record<string, string> = {
  JSWSTEEL: 'JSW Steel', TATASTEEL: 'Tata Steel', HINDALCO: 'Hindalco', ADANIENT: 'Adani Ent',
  GRASIM: 'Grasim', INDIGO: 'IndiGo', COALINDIA: 'Coal India', BHARTIARTL: 'Bharti Airtel',
  ASIANPAINT: 'Asian Paints', INDUSINDBK: 'IndusInd', AUBANK: 'AU SFB', TRENT: 'Trent',
};
const nm = (s: string) => NAME[s] || s;

export const ShockRecoveryPanel: React.FC = () => {
  const [d, setD] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (force = false) => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/shock-recovery${force ? '?force=true' : ''}`);
      const j = await r.json();
      if (j.success) { setD(j); if (j.live_error) setErr(`Live state unavailable — ${j.live_error}`); }
      else setErr(j.detail || 'Failed to load');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  const rows = useMemo(() => (d ? [...d.stocks].sort((a, b) => b.setupA_mean - a.setupA_mean) : []), [d]);

  return (
    <div className="bg-white rounded-2xl p-6 sm:p-8 border border-slate-200 shadow-sm mt-6">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div>
          <h3 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-500" /> Shock-Recovery — VIX-filtered dip-buy
          </h3>
          <p className="text-sm text-slate-500 mt-1 max-w-3xl">
            After a macro shock (NIFTY &lt; {d?.thresh ?? -1.5}%), stocks bounce next day — but the edge
            lives only when <b>VIX is elevated</b>. This is <b>technical mean-reversion, NOT quality</b>
            {d?.roe_corr != null && <> (ROE↔bounce corr = {d.roe_corr})</>}. Size for the tail; stand aside in a downtrend.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => load(false)} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-amber-600 hover:bg-amber-700 transition flex items-center gap-2 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> {d ? 'Reload' : 'Load'}
          </button>
          {d && <button onClick={() => load(true)} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold text-slate-700 bg-slate-100 hover:bg-slate-200 transition">Refresh live</button>}
        </div>
      </div>

      {err && <div className="bg-amber-50 text-amber-700 p-3 rounded-xl text-sm font-medium border border-amber-100 mb-4">{err}</div>}

      {!d && !loading && (
        <div className="text-sm text-slate-400 py-10 text-center border border-dashed border-slate-200 rounded-xl">
          Press <b className="text-slate-600">Load</b> to check today's shock regime and the dip-buy list.
        </div>
      )}

      {d && (
        <>
          {/* Live regime banner */}
          <div className={`rounded-xl p-4 mb-5 border flex flex-wrap items-center gap-x-6 gap-y-2 ${d.setup_active ? 'bg-emerald-50 border-emerald-200' : 'bg-slate-50 border-slate-200'}`}>
            <div className="text-sm font-black flex items-center gap-2">
              {d.setup_active
                ? <><TrendingUp className="w-5 h-5 text-emerald-600" /><span className="text-emerald-700">DIP-BUY SETUP ACTIVE</span></>
                : <><AlertTriangle className="w-5 h-5 text-slate-400" /><span className="text-slate-600">No setup today</span></>}
            </div>
            <div className="text-sm text-slate-600">NIFTY today <b className={`${(d.nifty_ret ?? 0) < 0 ? 'text-rose-600' : 'text-emerald-600'}`}>{d.nifty_ret == null ? '—' : `${d.nifty_ret > 0 ? '+' : ''}${d.nifty_ret}%`}</b>
              {' '}· {d.shock_today ? <span className="text-rose-600 font-bold">shock ✓</span> : <span className="text-slate-400">no shock (need &lt; {d.thresh}%)</span>}</div>
            <div className="text-sm text-slate-600">VIX <b>{d.vix ?? '—'}</b> · {d.vix_elevated ? <span className="text-amber-600 font-bold">elevated ✓ (≥{d.hivix})</span> : <span className="text-slate-400">calm (&lt; {d.hivix})</span>}</div>
            <div className="text-xs text-slate-400">
              {d.setup_active ? 'Both conditions met → the over-bouncers below that are above their 200DMA are the candidates.'
                : 'Setup fires only when a shock day AND elevated VIX coincide. Otherwise the bounce edge is weak — wait.'}
            </div>
          </div>

          {/* Table */}
          <div className="border border-slate-200 rounded-xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500 border-b border-slate-200">
                <tr className="text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2.5 text-left font-semibold">Stock</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Next-day bounce</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Hit-rate</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Worst (tail)</th>
                  <th className="px-3 py-2.5 text-right font-semibold">n</th>
                  <th className="px-3 py-2.5 text-center font-semibold">Trend (200DMA)</th>
                  <th className="px-3 py-2.5 text-left font-semibold">Read</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map(s => {
                  const strong = s.setupA_mean >= 0.9 && s.setupA_pos >= 0.65;
                  const avoid = s.setupA_mean <= 0.1;
                  const eligible = s.above_200dma === true;
                  const read = avoid
                    ? { t: 'Avoid — doesn’t bounce', c: 'bg-rose-50 text-rose-600 border-rose-200' }
                    : strong
                      ? { t: 'Over-bouncer', c: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
                      : { t: 'Mild bouncer', c: 'bg-slate-50 text-slate-500 border-slate-200' };
                  return (
                    <tr key={s.sym} className={`hover:bg-slate-50 ${d.setup_active && strong && eligible ? 'bg-emerald-50/40' : ''}`}>
                      <td className="px-3 py-2.5 whitespace-nowrap"><span className="font-bold text-slate-800">{nm(s.sym)}</span><span className="block text-[10px] text-slate-400">{s.sym}</span></td>
                      <td className={`px-3 py-2.5 text-right font-bold ${s.setupA_mean > 0.05 ? 'text-emerald-600' : s.setupA_mean < -0.05 ? 'text-rose-600' : 'text-slate-400'}`}>{s.setupA_mean > 0 ? '+' : ''}{s.setupA_mean.toFixed(2)}%</td>
                      <td className="px-3 py-2.5 text-right text-slate-600">{Math.round(s.setupA_pos * 100)}%</td>
                      <td className="px-3 py-2.5 text-right text-rose-500 font-mono">{s.setupA_worst.toFixed(1)}%</td>
                      <td className="px-3 py-2.5 text-right text-slate-400">{s.setupA_n}</td>
                      <td className="px-3 py-2.5 text-center">
                        {s.above_200dma == null ? <span className="text-slate-300">—</span>
                          : s.above_200dma ? <span className="inline-flex items-center gap-1 text-emerald-600 text-xs font-bold"><TrendingUp className="w-3.5 h-3.5" />above</span>
                            : <span className="inline-flex items-center gap-1 text-rose-500 text-xs font-bold"><TrendingDown className="w-3.5 h-3.5" />below</span>}
                      </td>
                      <td className="px-3 py-2.5"><span className={`inline-block px-2 py-1 rounded-md text-[11px] font-bold border ${read.c}`}>{read.t}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-slate-400 mt-4 leading-relaxed">
            Rule: take a dip only when <b>NIFTY &lt; {d.thresh}%</b> AND <b>VIX ≥ {d.hivix}</b> AND the name is <b>above its 200DMA</b>
            (a shaded green row when the setup is live). Bounce/hit-rate/worst are the historical stats for exactly that setup.
            The over-bouncers are high-beta cyclicals (steel, aluminium, Grasim) — <b>mean-reversion, not quality</b>. The tail is real
            (worst column) and clusters in trending bears; this is a tactical trade sized for the worst case, not a system. Not advice.
          </p>
        </>
      )}
    </div>
  );
};
