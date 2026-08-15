import React, { useEffect, useState, useCallback } from 'react';
import { ShieldAlert, RefreshCw, AlertTriangle, Sunrise, Activity } from 'lucide-react';

/*
 * DrawdownInsurancePanel
 * ----------------------
 * Two-stage liquidity-derisk overlay driving a tail hedge (max-drawdown insurance):
 *   LEAD    — derisk_preopen: overnight cross-asset tape (crude spike, GIFT gap,
 *             overnight gold/silver selling, USDINR) read as-of ~09:14, BEFORE the
 *             cash open, so the hedge can be bought cheap.
 *   CONFIRM — derisk_liquidity: the Indian cash session as it falls (coincident).
 * The hedge is sized off whichever armed first (usually the pre-open lead).
 */

interface Props { date?: string; }

const LEAD_COMPS = [
  { key: 'crude_shock', label: 'Crude spike', hint: 'CRUDEOIL up overnight — the causal trigger' },
  { key: 'gift_gap', label: 'GIFT gap', hint: 'GIFT-NIFTY down overnight — leads the cash open' },
  { key: 'haven_selloff', label: 'Haven selling', hint: 'gold/silver sold overnight — the liquidation tell' },
  { key: 'ndf_usd', label: 'USDINR up', hint: 'rupee weakening overnight / dash for USD' },
];
const SESSION_COMPS = [
  { key: 'haven_failure', label: 'Haven failure', hint: 'gold/silver falling WITH equities intraday' },
  { key: 'breadth_collapse', label: 'Breadth collapse', hint: 'constituents down / moving >1%' },
  { key: 'cross_asset_comove', label: 'Cross-asset co-move', hint: 'NIFTY, gold, silver, GIFT all down' },
  { key: 'persistence', label: 'Persistence', hint: 'selling still accelerating' },
  { key: 'usdinr_up', label: 'USDINR up', hint: 'rupee weakening intraday' },
];

const Stage: React.FC<{ icon: any; title: string; sub: string; stage: any; comps: any[]; trigger: number }> = ({ icon: Icon, title, sub, stage, comps, trigger }) => {
  const noData = !stage || stage.status === 'NO_DATA';
  const inten = stage?.intensity ?? 0;
  const armed = !!stage?.armed;
  const band = inten >= 0.7 ? '#e11d48' : inten >= trigger ? '#f59e0b' : '#64748b';
  return (
    <div className={`rounded-xl border p-3 ${armed ? (inten >= 0.7 ? 'bg-rose-50 border-rose-200' : 'bg-amber-50 border-amber-200') : 'bg-slate-50 border-slate-200'}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icon className="w-3.5 h-3.5" style={{ color: band }} />
          <span className="text-[11px] font-bold text-slate-600">{title}</span>
          <span className="text-[9px] text-slate-400">{sub}</span>
        </div>
        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full border" style={{ color: band, borderColor: band }}>
          {noData ? 'NO DATA' : armed ? 'ARMED' : 'CLEAR'}
        </span>
      </div>
      {!noData && (
        <>
          <div className="flex items-baseline gap-2 mt-1.5">
            <span className="text-2xl font-black" style={{ color: band }}>{(inten * 100).toFixed(0)}</span>
            <div className="flex-1 h-1.5 rounded-full bg-white/70 overflow-hidden relative">
              <div className="h-full rounded-full" style={{ width: `${inten * 100}%`, background: band }} />
              <div className="absolute top-0 bottom-0 border-l border-slate-400" style={{ left: `${trigger * 100}%` }} />
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5">
            {comps.map((c) => {
              const v = stage.components?.[c.key] ?? 0;
              return (
                <span key={c.key} className="text-[10px]" title={c.hint} style={{ color: v >= 0.6 ? band : '#94a3b8' }}>
                  {c.label} <span className="font-mono font-semibold">{(v * 100).toFixed(0)}</span>
                </span>
              );
            })}
          </div>
        </>
      )}
      {noData && <div className="text-[10px] text-slate-400 mt-1">cross-asset series not in DB for this session</div>}
    </div>
  );
};

export const DrawdownInsurancePanel: React.FC<Props> = ({ date }) => {
  const [res, setRes] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const q = date ? `?date=${encodeURIComponent(date)}` : '';
      const r = await fetch(`/api/strategy/drawdown-insurance${q}`);
      const j = await r.json();
      if (j.error) { setErr(j.error); setRes(null); } else setRes(j);
    } catch (e) { setErr('Cannot reach /api.'); }
    finally { setLoading(false); }
  }, [date]);

  useEffect(() => { load(); }, [load]);

  const trigger: number = res?.trigger ?? 0.45;
  const fired: boolean = !!res?.fired;
  const hedge = res?.hedge;
  const drive: number = res?.drive_intensity ?? 0;
  const band = drive >= 0.7 ? '#e11d48' : drive >= trigger ? '#f59e0b' : '#64748b';
  const preArmed = res?.preopen?.armed;
  const sesArmed = res?.session?.armed;
  const preReads = res?.preopen?.reads || {};

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-5 h-5" style={{ color: band }} />
          <h2 className="text-base font-bold text-slate-800">Drawdown Insurance</h2>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border" style={{ color: band, borderColor: band }}>
            {fired ? 'HEDGE ON' : 'NO HEDGE'}
          </span>
        </div>
        <button onClick={load} className="p-1 rounded hover:bg-slate-100" title="Reload">
          <RefreshCw className={`w-3.5 h-3.5 text-slate-400 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
      <div className="text-[11px] text-slate-400 mb-3">
        LEAD (pre-open, overnight) → CONFIRM (cash session) · hedge fires at intensity ≥ {trigger} · {date || 'latest'}
      </div>

      {err && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mb-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}
        </div>
      )}

      {res && (
        <div className="space-y-3">
          {preArmed && !sesArmed && (
            <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
              ⚠ Pre-open lead armed <span className="font-semibold">before the cash open</span> — buy the hedge at the open, ahead of the session confirming.
            </div>
          )}
          <div className="grid md:grid-cols-2 gap-3">
            <Stage icon={Sunrise} title="LEAD · pre-open" sub="~09:14, overnight tape" stage={res.preopen} comps={LEAD_COMPS} trigger={trigger} />
            <Stage icon={Activity} title="CONFIRM · session" sub="cash, coincident" stage={res.session} comps={SESSION_COMPS} trigger={trigger} />
          </div>

          {/* overnight reads strip */}
          {(preReads.crude_overnight_pct != null || preReads.giftnifty_overnight_pct != null) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] bg-slate-50 border border-slate-100 rounded-lg px-3 py-1.5">
              <span className="font-semibold text-slate-500">Overnight:</span>
              {[['Crude', preReads.crude_overnight_pct], ['GIFT', preReads.giftnifty_overnight_pct], ['Gold', preReads.gold_overnight_pct], ['Silver', preReads.silver_overnight_pct], ['USDINR', preReads.usdinr_overnight_pct]].map(([k, v]: any) => (
                v == null ? null : (
                  <span key={k} className="text-slate-500">{k} <span className={`font-mono font-semibold ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{v > 0 ? '+' : ''}{v}%</span></span>
                )
              ))}
            </div>
          )}

          {/* hedge */}
          <div className="rounded-xl border border-slate-200 p-4">
            {fired && hedge?.long_put ? (
              <>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wide mb-1">Recommended tail hedge</div>
                <div className="text-lg font-black text-slate-800">
                  Buy {hedge.lots}× {hedge.long_put.strike} PUT
                  <span className="text-xs font-semibold text-slate-400 ml-2">{hedge.sigma_otm}σ OTM · sized off {(drive * 100).toFixed(0)} intensity</span>
                </div>
                <div className="text-xs text-slate-500 mt-1">
                  Premium ≈ <span className="font-mono">{hedge.long_put.premium_pts} pts</span> · cost{' '}
                  <span className="font-mono font-semibold text-rose-600">₹{Number(hedge.long_put.cost_inr_total).toLocaleString('en-IN')}</span>
                  <span className="text-slate-400"> ({hedge.long_put.protection})</span>
                </div>
                {hedge.put_spread_ref && (
                  <div className="text-[11px] text-slate-400 mt-2 border-t border-slate-100 pt-2">
                    Cheaper variant — put spread {hedge.put_spread_ref.long}/{hedge.put_spread_ref.short}:
                    cost <span className="font-mono">₹{Number(hedge.put_spread_ref.cost_inr_total).toLocaleString('en-IN')}</span> <span className="text-slate-300">({hedge.put_spread_ref.protection})</span>
                  </div>
                )}
              </>
            ) : (
              <div className="text-xs text-slate-500">
                <span className="font-semibold text-slate-600">No hedge.</span> Neither the overnight lead nor the session confirm cleared the trigger — no liquidity-derisk signature, so no drawdown insurance is warranted.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
