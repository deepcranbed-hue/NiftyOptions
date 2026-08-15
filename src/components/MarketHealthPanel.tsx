import React, { useState, useEffect, useCallback } from 'react';
import { Gauge, RefreshCw, AlertTriangle, BookOpen, ChevronDown, ChevronRight } from 'lucide-react';

/*
 * MarketHealthPanel
 * -----------------
 * The DAILY market-health / trend gauge (0-100) — a slow, positional read that
 * complements the intraday desk. Everything shown comes from
 * /api/strategy/market-health, i.e. strategy_framework/market_health/. No score,
 * band, weight or MA is computed here; the panel only renders what the backend
 * (single source of truth) returns, so it can never drift from the agent's numbers.
 */

const BAND_COLOR = (label?: string) => {
  switch (label) {
    case 'Strong uptrend': return { bar: 'bg-emerald-500', text: 'text-emerald-600', ring: 'stroke-emerald-500' };
    case 'Healthy uptrend': return { bar: 'bg-emerald-400', text: 'text-emerald-600', ring: 'stroke-emerald-400' };
    case 'Neutral / consolidation': return { bar: 'bg-amber-400', text: 'text-amber-600', ring: 'stroke-amber-400' };
    case 'Weakening': return { bar: 'bg-orange-400', text: 'text-orange-600', ring: 'stroke-orange-500' };
    default: return { bar: 'bg-rose-500', text: 'text-rose-600', ring: 'stroke-rose-500' };
  }
};

const fmt = (v: any) => (typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : String(v));

const Gauge0100: React.FC<{ score: number | null; band?: string }> = ({ score, band }) => {
  const c = BAND_COLOR(band);
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  const R = 52, C = Math.PI * R;            // half-circle arc length
  const off = C * (1 - pct / 100);
  return (
    <div className="relative w-[150px] h-[86px]">
      <svg viewBox="0 0 130 74" className="w-full h-full">
        <path d="M 13 66 A 52 52 0 0 1 117 66" fill="none" stroke="#e2e8f0" strokeWidth="11" strokeLinecap="round" />
        <path d="M 13 66 A 52 52 0 0 1 117 66" fill="none" strokeWidth="11" strokeLinecap="round"
          className={c.ring} strokeDasharray={C} strokeDashoffset={off} style={{ transition: 'stroke-dashoffset .6s' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-end pb-1">
        <span className={`text-3xl font-bold ${c.text}`}>{score == null ? '—' : score}</span>
        <span className="text-[10px] text-slate-400 -mt-1">/ 100</span>
      </div>
    </div>
  );
};

export const MarketHealthPanel: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [openRow, setOpenRow] = useState<string | null>(null);   // expanded component
  const [showDefs, setShowDefs] = useState(false);               // definitions legend

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = await fetch('/api/strategy/market-health');
      const j = await r.json();
      if (!r.ok || j.error) setErr(j.error || `backend ${r.status}`);
      else setData(j);
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const c = BAND_COLOR(data?.band);
  const comps: [string, any][] = data ? Object.entries(data.components) : [];
  // label maps served by the backend (single source; nothing hardcoded here)
  const meta = data?.meta || {};
  const compLabel = (k: string) => meta.components?.[k] || k.replace(/_/g, ' ');
  const layerLabel = (k: string) => meta.layers?.[k] || k.replace(/_/g, ' ');
  const fieldLabel = (k: string) => meta.fields?.[k]?.label || k;
  const fieldHelp = (k: string) => meta.fields?.[k]?.help || '';
  const HIDDEN = ['points', 'data_ready', 'score01', 'awarded'];

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      <div className="flex items-center gap-2 mb-1">
        <Gauge className="w-5 h-5 text-indigo-600" />
        <h2 className="text-base font-bold text-slate-800">Market Health</h2>
        <span className="text-[11px] text-slate-400">daily trend gauge</span>
        <button onClick={load} className="ml-auto text-slate-400 hover:text-indigo-600" title="Refresh">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
      <div className="text-[11px] text-slate-400 mb-4">
        Slow, positional read from daily bars — where NIFTY sits in its trend/cycle. Complements the intraday desk.
      </div>

      {err && (
        <div className="mb-3 flex items-start gap-2 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span>{err}</span>
        </div>
      )}

      {data && (
        <>
          <div className="flex items-center gap-5 mb-4">
            <Gauge0100 score={data.score} band={data.band} />
            <div>
              <div className={`text-lg font-bold ${c.text}`}>{data.band || '—'}</div>
              <div className="text-xs text-slate-500 mt-0.5">
                as-of {data.as_of} · {data.sessions} daily sessions
              </div>
              <div className="text-[11px] text-slate-400 mt-1">
                coverage <b>{data.coverage_pct}%</b> of the intended model
                {data.coverage_pct < 100 && ' — some layers pending data'}
              </div>
            </div>
          </div>

          {/* band scale */}
          <div className="flex h-1.5 rounded-full overflow-hidden mb-1">
            <div className="bg-rose-500" style={{ width: '35%' }} />
            <div className="bg-orange-400" style={{ width: '15%' }} />
            <div className="bg-amber-400" style={{ width: '15%' }} />
            <div className="bg-emerald-400" style={{ width: '15%' }} />
            <div className="bg-emerald-500" style={{ width: '20%' }} />
          </div>
          <div className="flex justify-between text-[9px] text-slate-400 mb-4">
            <span>Defensive</span><span>Weakening</span><span>Neutral</span><span>Healthy</span><span>Strong</span>
          </div>

          {/* layers */}
          <div className="grid grid-cols-2 gap-2 mb-4">
            {Object.entries(data.layers).map(([name, lv]: [string, any]) => (
              <div key={name} className="border border-slate-100 rounded-lg p-2.5">
                <div className="text-[11px] text-slate-500">{layerLabel(name)}</div>
                {lv.data_ready ? (
                  <>
                    <div className="text-sm font-bold text-slate-800">{lv.awarded}<span className="text-slate-400 font-normal">/{lv.available_points}</span></div>
                    <div className="h-1.5 bg-slate-100 rounded mt-1"><div className={`h-1.5 rounded ${c.bar}`} style={{ width: `${lv.pct}%` }} /></div>
                  </>
                ) : <div className="text-xs text-slate-400 mt-1">pending data</div>}
              </div>
            ))}
          </div>

          {/* component detail — click a row for the raw numbers, each labelled */}
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] text-slate-500 font-semibold">Components <span className="font-normal text-slate-400">(daily · click a row for the figures)</span></span>
            <button onClick={() => setShowDefs((s) => !s)} className="ml-auto flex items-center gap-1 text-[10px] text-indigo-500 hover:text-indigo-700">
              <BookOpen className="w-3 h-3" /> {showDefs ? 'hide' : 'what do these mean?'}
            </button>
          </div>

          {/* definitions legend (served by the backend, not hardcoded here) */}
          {showDefs && meta.concepts && (
            <div className="mb-3 p-3 bg-slate-50 border border-slate-200 rounded-lg space-y-1.5">
              {Object.entries(meta.concepts).map(([term, def]: [string, any]) => (
                <div key={term} className="text-[11px]">
                  <span className="font-semibold text-slate-700">{term}</span>
                  <span className="text-slate-500"> — {def}</span>
                </div>
              ))}
            </div>
          )}

          <div className="space-y-0.5 mb-4">
            {comps.map(([name, v]: [string, any]) => {
              const open = openRow === name;
              const fields = Object.entries(v).filter(([kk]) => !HIDDEN.includes(kk) && kk !== 'note');
              return (
                <div key={name} className="border-b border-slate-50 last:border-0">
                  <button
                    onClick={() => setOpenRow(open ? null : name)}
                    className="w-full flex items-center gap-2 text-[11px] py-1.5 text-left hover:bg-slate-50 rounded"
                  >
                    {v.data_ready ? (open ? <ChevronDown className="w-3 h-3 text-slate-400" /> : <ChevronRight className="w-3 h-3 text-slate-300" />) : <span className="w-3" />}
                    <span className="w-44 text-slate-600 truncate">{compLabel(name)}</span>
                    {v.data_ready ? (
                      <>
                        <div className="flex-1 h-2.5 bg-slate-100 rounded"><div className={`h-2.5 rounded ${c.bar}`} style={{ width: `${(v.score01 * 100).toFixed(0)}%` }} /></div>
                        <span className="w-14 text-right font-mono text-slate-600">{v.awarded}/{v.points}</span>
                      </>
                    ) : (
                      <span className="flex-1 text-slate-400 italic">pending — {v.note || 'no data yet'}</span>
                    )}
                  </button>

                  {/* expanded: each raw number with its friendly label + meaning */}
                  {open && v.data_ready && (
                    <div className="ml-5 mb-2 grid grid-cols-2 gap-x-4 gap-y-1.5">
                      {fields.map(([kk, vv]: [string, any]) => (
                        <div key={kk} className="text-[11px]">
                          <div className="flex justify-between gap-2">
                            <span className="text-slate-500">{fieldLabel(kk)}</span>
                            <span className="font-mono font-semibold text-slate-700">{fmt(vv)}</span>
                          </div>
                          {fieldHelp(kk) && <div className="text-[10px] text-slate-400 leading-snug">{fieldHelp(kk)}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* omitted + notes */}
          {data.omitted_layers?.length > 0 && (
            <div className="text-[10px] text-slate-400 border-t border-slate-100 pt-2">
              <b>Not scored (no feed):</b> {data.omitted_layers.join(' · ')}
            </div>
          )}
          {data.notes?.map((n: string, i: number) => (
            <div key={i} className="text-[10px] text-amber-600 mt-1 flex items-start gap-1">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" /><span>{n}</span>
            </div>
          ))}
          <div className="text-[10px] text-slate-400 mt-2 italic">{data.disclaimer}</div>
        </>
      )}
    </div>
  );
};
