import React, { useState } from 'react';
import { GlobalCueItem } from '../types';
import { Globe, ArrowUpRight, ArrowDownRight, RefreshCw, HelpCircle, Activity, Minus } from 'lucide-react';
import { FormulaTooltip } from './FormulaTooltip';
import { ProvenanceBadge } from './ProvenanceBadge';

interface Props {
  cues: GlobalCueItem[];
  pctMap: Record<string, number>;
  onPctChange: (name: string, val: number) => void;
  onResetDefaults: () => void;
  pipelineRes?: any;
  onRunPipeline?: () => Promise<void> | void;   // re-run pipeline to refresh provenance badges
}

export const GlobalCuesPanel: React.FC<Props> = ({
  cues,
  pctMap,
  onPctChange,
  onResetDefaults,
  pipelineRes,
  onRunPipeline,
}) => {
  const [isFetching, setIsFetching] = useState(false);
  const [closeLevels, setCloseLevels] = useState<Record<string, number>>({});
  const provRecords = pipelineRes?.provenance?.records || [];
  const getProvenance = (componentName: string) => 
    provRecords.find((r: any) => r.component === componentName);

  const fetchCues = async (forceRefresh: boolean) => {
    setIsFetching(true);
    try {
      let data: any = null;
      let liveFailed = '';
      if (forceRefresh) {
        // Force refresh → POST /api/update-cues, which force-fetches the LIVE source
        // AND writes the cues_state the pipeline reads (a bare GET updates numbers
        // only, so the provenance/STALE badges would never clear).
        try {
          const res = await fetch('/api/update-cues', { method: 'POST' });
          data = await res.json();
          if (!res.ok || !data?.success) {
            liveFailed = data?.detail || `HTTP ${res.status}`;
            data = null;
          }
        } catch (e: any) {
          liveFailed = String(e?.message || e);
          data = null;
        }
        // Live source failed (rate-limited / down) → fall back to the last cached
        // values so the panel still works, and say why.
        if (!data) {
          const cached = await fetch('/api/fetch-global-cues?force_refresh=false');
          data = await cached.json();
          if (liveFailed) {
            alert(`Live refresh failed — showing last cached values.\n\nReason: ${liveFailed}\n\n` +
                  `The external global-cues source is likely rate-limited or unreachable right now; try again in a minute.`);
          }
        }
      } else {
        const res = await fetch('/api/fetch-global-cues?force_refresh=false');
        data = await res.json();
      }

      if (data?.success && data.cues) {
        Object.entries(data.cues).forEach(([key, val]) => {
          if (typeof val === 'number') onPctChange(key, val);
        });
        if (data.close_levels) setCloseLevels(data.close_levels);
        // Re-run the pipeline so the provenance / session-state badges refresh
        // (from fresh state on success, or last-good state on fallback). No tab switch.
        if (forceRefresh && onRunPipeline) await onRunPipeline();
      } else if (!liveFailed) {
        alert("No global cues available yet (no live source and no cache). Try a forced refresh once the source is reachable.");
      }
    } catch (e: any) {
      alert("Error contacting the cues API: " + String(e?.message || e));
    } finally {
      setIsFetching(false);
    }
  };

  React.useEffect(() => {
    fetchCues(false);
  }, []);

  return (
    <div className="space-y-6">
      {/* Pipeline Corroboration Panel */}
      {pipelineRes && pipelineRes.regime && (
        <div className="bg-gradient-to-r from-blue-900 via-indigo-900 to-slate-900 rounded-2xl p-6 text-white border border-slate-800 shadow-lg">
          <div className="flex items-center gap-2 mb-4">
            <Globe className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-black tracking-tight">Cross-Asset Corroboration</h3>
          </div>
          <p className="text-xs text-blue-200 mb-4 max-w-2xl">
            How many independent market surfaces echo the dominant driver — this is the leading signal your global-cues feed should carry.
          </p>

          {pipelineRes.regime.surfaces && pipelineRes.regime.surfaces.length > 0 ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {pipelineRes.regime.surfaces.map((s: string) => (
                  <div key={s} className="bg-slate-800/50 border border-slate-700 p-3 rounded-xl flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-300">{s}</span>
                    <span className="text-[10px] uppercase text-emerald-400 font-black">Confirms</span>
                  </div>
                ))}
              </div>
              <div className={`p-3 rounded-xl text-xs font-bold border ${
                pipelineRes.regime.surfaces.length >= 3 ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' : 'bg-blue-500/20 text-blue-300 border-blue-500/30'
              }`}>
                {pipelineRes.regime.surfaces.length} surfaces agree → {pipelineRes.regime.surfaces.length >= 3 ? 'high' : 'moderate'} conviction the move is global/structural, not a single headline.
              </div>
            </div>
          ) : (
            <div className="bg-slate-800/50 border border-slate-700 p-4 rounded-xl text-slate-400 text-xs font-medium">
              No cross-asset confirmation yet — driver is local/unconfirmed.
            </div>
          )}
          
          <div className="mt-4 pt-4 border-t border-slate-700/50 flex items-center justify-between">
            <span className="text-xs font-bold uppercase text-slate-400">News Momentum</span>
            <span className="font-mono text-lg font-black text-blue-400">{(pipelineRes.momentum * 100).toFixed(0)}%</span>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Globe className="w-5 h-5 text-indigo-600" /> Global Macro & Sector Transmission Matrix
            <ProvenanceBadge record={getProvenance('cues_state')} />
            <ProvenanceBadge record={getProvenance('macro_state')} />
          </h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Overnight global index moves cue specific domestic Indian sectors. Note that inverse mappings (e.g., Brent Oil & Dollar DXY) act as headwinds when rising.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => fetchCues(false)}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-blue-100 hover:bg-blue-200 text-blue-800 text-xs font-bold transition cursor-pointer disabled:opacity-50"
          >
            {isFetching ? 'Loading...' : 'Load Cached Cues'}
          </button>
          <button
            onClick={() => fetchCues(true)}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-100 hover:bg-emerald-200 text-emerald-800 text-xs font-bold transition cursor-pointer disabled:opacity-50"
          >
            {isFetching ? 'Fetching...' : 'Force Refresh Live'}
          </button>
          <button
            onClick={onResetDefaults}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold transition cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Reset Default Moves
          </button>
        </div>
      </div>

      {/* Metals Barometer Panel */}
      {pipelineRes?.cues?.metals_barometer && (
        <div className="bg-slate-900 rounded-2xl p-6 border border-slate-800 shadow-md">
          <div className="flex items-center gap-2 mb-4 text-white">
            <Activity className="w-5 h-5 text-amber-400" />
            <h3 className="text-lg font-black tracking-tight">Metals Economic Barometer</h3>
            <FormulaTooltip trace={pipelineRes.cues.metals_barometer.formula_trace} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div className="bg-slate-800 p-3 rounded-xl border border-slate-700">
              <div className="text-[10px] uppercase text-slate-400 font-bold mb-1">Growth (Copper)</div>
              <div className={`text-lg font-black ${pipelineRes.cues.metals_barometer.growth_signal > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {pipelineRes.cues.metals_barometer.growth_signal > 0 ? '+' : ''}{pipelineRes.cues.metals_barometer.growth_signal}
              </div>
            </div>
            <div className="bg-slate-800 p-3 rounded-xl border border-slate-700">
              <div className="text-[10px] uppercase text-slate-400 font-bold mb-1">Fear (Gold)</div>
              <div className={`text-lg font-black ${pipelineRes.cues.metals_barometer.fear_signal > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>
                {pipelineRes.cues.metals_barometer.fear_signal > 0 ? '+' : ''}{pipelineRes.cues.metals_barometer.fear_signal}
              </div>
            </div>
            <div className="bg-slate-800 p-3 rounded-xl border border-slate-700 col-span-2">
              <div className="text-[10px] uppercase text-slate-400 font-bold mb-1">Regime</div>
              <div className="text-sm font-bold text-white capitalize">{pipelineRes.cues.metals_barometer.regime.replace(/_/g, ' ')}</div>
              <div className="text-xs text-slate-400 mt-1">{pipelineRes.cues.metals_barometer.note}</div>
            </div>
          </div>
        </div>
      )}

      {/* Semi Transmission Panel */}
      {pipelineRes?.cues?.semi_transmission && (
        <div className="bg-indigo-900 rounded-2xl p-6 border border-indigo-800 shadow-md">
          <div className="flex items-center gap-2 mb-2 text-white">
            <Activity className="w-5 h-5 text-indigo-400" />
            <h3 className="text-lg font-black tracking-tight">Semiconductor Transmission (^SOX)</h3>
            <span className={`text-xs font-bold px-2 py-1 rounded ml-auto ${
              pipelineRes.cues.semi_transmission.structural ? 'bg-rose-500/20 text-rose-300 border border-rose-500/50' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/50'
            }`}>
              {pipelineRes.cues.semi_transmission.structural ? 'STRUCTURAL' : 'ROTATIONAL'}
            </span>
          </div>
          <div className="text-sm font-medium text-indigo-100">
            {pipelineRes.cues.semi_transmission.india_read}
          </div>
          {pipelineRes.cues.semi_transmission.caveat && (
            <div className="text-xs text-indigo-300 mt-2 italic">
              * {pipelineRes.cues.semi_transmission.caveat}
            </div>
          )}
        </div>
      )}

      {/* Target Netting Verdicts Strip */}
      {pipelineRes?.net_verdicts && (
        <div className="space-y-3">
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 px-1">
            Sector Netting Verdicts & Signals
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {Object.entries(pipelineRes.net_verdicts).map(([target, info]: [string, any]) => (
              <div key={target} className={`p-4 rounded-xl border flex flex-col justify-between shadow-sm transition ${
                info.verdict === 'tailwind' ? 'bg-emerald-50/80 border-emerald-200' :
                info.verdict === 'headwind' ? 'bg-rose-50/80 border-rose-200' :
                'bg-slate-50 border-slate-200'
              }`}>
                <div>
                  <div className="flex justify-between items-start mb-2">
                    <span className="text-xs font-black uppercase text-slate-500 tracking-wider">
                      {target.replace(/_/g, ' ')}
                    </span>
                    {info.divergence_flag && (
                      <span className="bg-amber-100 text-amber-800 text-[9px] font-black px-1.5 py-0.5 rounded-full animate-pulse">
                        CONFLICTED
                      </span>
                    )}
                  </div>
                  <div className="flex justify-between items-baseline mb-2">
                    <span className={`text-xl font-black ${
                      info.net_score > 0 ? 'text-emerald-700' :
                      info.net_score < 0 ? 'text-rose-700' :
                      'text-slate-700'
                    }`}>
                      {info.net_score > 0 ? '+' : ''}{info.net_score}
                    </span>
                    <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded ${
                      info.verdict === 'tailwind' ? 'bg-emerald-200 text-emerald-950' :
                      info.verdict === 'headwind' ? 'bg-rose-200 text-rose-950' :
                      'bg-slate-200 text-slate-700'
                    }`}>
                      {info.verdict}
                    </span>
                  </div>
                </div>
                
                {info.contributions && info.contributions.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-slate-200/50 text-[10px] space-y-1 font-mono text-slate-500">
                    {info.contributions.map((c: any) => (
                      <div key={c.key} className="flex justify-between">
                        <span>{c.key} (w:{c.weight})</span>
                        <span className={c.strength > 0 ? 'text-emerald-600 font-bold' : 'text-rose-600 font-bold'}>
                          {c.strength > 0 ? '+' : ''}{c.strength}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {info.excluded && info.excluded.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-slate-200/30 text-[9px] text-slate-400 italic">
                    Excluded: {info.excluded.map((e: any) => `${e.key} (${e.reason})`).join(', ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Inputs Grid */}
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 pb-2 border-b border-slate-100 flex items-center justify-between">
          <span>Overnight Moves (% Chg / Yield bp)</span>
          <span className="text-xs font-normal lowercase italic text-slate-400">adjust values to simulate impact</span>
        </h3>
        
        {(() => {
          const states = pipelineRes?.cues?.session_states || pipelineRes?.session_states || {};
          const stale = Object.keys(states).filter((k) => states[k] === 'STALE');
          if (!stale.length) return null;
          return (
            <div className="mb-4 flex items-start gap-2 text-[11px] text-orange-800 bg-orange-50 border border-orange-200 rounded-lg px-3 py-2">
              <span className="font-black">{stale.length} stale:</span>
              <span>
                <span className="font-semibold">{stale.join(', ')}</span> — each has no quote newer than the
                previous trading session (older than “market closed”). These are <span className="font-semibold">down-weighted
                to 0.25</span> in the netting. “Forced refresh” updates the numbers but not these badges —
                re-run the full pipeline to refresh them, and if a feed stays stale its source simply has no newer data.
              </span>
            </div>
          );
        })()}

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
          {Object.keys(pctMap).map((name) => {
            const val = pctMap[name] || 0;
            const price = closeLevels[name];
            const state = (pipelineRes?.cues?.session_states || pipelineRes?.session_states || {})[name] || 'LIVE';
            const asOf = (pipelineRes?.cues?.cue_as_of || pipelineRes?.cue_as_of || {})[name];   // last-quote timestamp
            const isHoliday = state === 'HOLIDAY';
            // one freshness chip per cue, so it's obvious WHICH are behind and WHY
            const chip =
              state === 'STALE' ? { t: 'STALE', c: 'bg-orange-100 text-orange-700', tip: 'Last quote is older than the previous trading session — this feed is behind. A refresh only helps if the source actually has newer data.' } :
              state === 'HOLIDAY' ? { t: 'HOLIDAY', c: 'bg-amber-100 text-amber-800', tip: 'Market closed today (holiday/weekend) — no new data expected.' } :
              state === 'CLOSED_FINAL' ? { t: 'CLOSED', c: 'bg-slate-100 text-slate-500', tip: 'Market closed; showing the last session close. This is normal and NOT stale.' } :
              state === 'ERROR' ? { t: 'ERROR', c: 'bg-rose-100 text-rose-700', tip: 'Failed to fetch this cue from its source.' } :
              null;   // LIVE → no chip
            return (
              <div key={name} className={`p-3 rounded-xl border transition ${
                isHoliday ? 'bg-amber-50/50 border-amber-200 opacity-80'
                : state === 'STALE' ? 'bg-orange-50/40 border-orange-200'
                : 'bg-slate-50 border-slate-200/80'
              }`}>
                <div className="flex justify-between items-center mb-1">
                  <div className="flex items-center gap-1 min-w-0">
                    <label className="block text-xs font-bold text-slate-700 truncate" title={name}>
                      {name}
                    </label>
                    {chip && (
                      <span title={`${chip.tip}${asOf ? ` (last: ${String(asOf).slice(0, 16).replace('T', ' ')})` : ''}`}
                            className={`${chip.c} text-[8px] font-black px-1 rounded cursor-help`}>
                        {chip.t}
                      </span>
                    )}
                  </div>
                  {price !== undefined && price !== 0 && (
                    <span className="text-[10px] text-slate-400 font-bold font-mono">
                      {name.includes('India') ? `${price}bp` : price}
                    </span>
                  )}
                </div>
                <div className="relative flex items-center">
                  <input
                    type="number"
                    step="0.01"
                    value={val}
                    onChange={(e) => onPctChange(name, parseFloat(e.target.value) || 0)}
                    disabled={isHoliday}
                    className={`w-full px-2.5 py-1.5 text-sm font-mono font-bold rounded-lg border focus:ring-2 focus:ring-indigo-500 outline-none ${
                      isHoliday ? 'bg-amber-50/20 text-amber-900 border-amber-200 cursor-not-allowed' :
                      val > 0 ? 'text-emerald-700 bg-emerald-50/50 border-emerald-300' :
                      val < 0 ? 'text-rose-700 bg-rose-50/50 border-rose-300' :
                      'text-slate-700 bg-white border-slate-300'
                    }`}
                  />
                  <span className="absolute right-2 text-xs text-slate-400 font-bold pointer-events-none">
                    {name.includes('India') ? 'bp' : '%'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Transmission Reads Cards */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 px-1">
          Transmission Reads & Domestic Sector Bias
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {cues.map((cue, i) => {
            const isRate = cue.name.includes('India');
            const levelVal = closeLevels[cue.name];
            
            // Custom text/formatting for G-sec and slope
            let cardTitle = `${cue.name} (${cue.pct > 0 ? '+' : ''}${cue.pct}%)`;
            let cardDesc = cue.inverse ? '* Inverse macro sensitivity' : '* Direct momentum correlation';
            let extraNote = null;
            
            if (isRate && pipelineRes?.curve_regime) {
              if (cue.name === "India 2S10S") {
                const regLabel = (pipelineRes.curve_regime.regime || "QUIET").toLowerCase().replace(/_/g, ' ');
                const slopeVal = pipelineRes.curve_regime.slope_bp;
                cardTitle = `2s10s ${slopeVal}bp (${cue.pct > 0 ? '+' : ''}${cue.pct}bp, ${regLabel})`;
                cardDesc = pipelineRes.curve_regime.note || "* Yield curve slope regime classification";
                extraNote = (
                  <div className="text-[10px] text-amber-600 bg-amber-50 p-2 rounded-lg border border-amber-200 mt-2 font-medium">
                    Bank NIM Caveat: A steeper curve expands NIM (Net Interest Margin) for banks, but causes immediate duration/mark-to-market losses on bond books.
                  </div>
                );
              } else {
                const zVal = cue.pct / 3.0; // daily change / vol
                cardTitle = `${cue.name} ${levelVal ? levelVal.toFixed(2) : '0.00'}% (${cue.pct > 0 ? '+' : ''}${cue.pct}bp, z ${zVal >= 0 ? '+' : ''}${zVal.toFixed(1)})`;
                cardDesc = cue.pct > 0 
                  ? "Yields rising represents a corporate borrowing and valuation headwind." 
                  : "Yields falling represents an easing of borrowing pressure.";
              }
            }

            return (
              <div
                key={i}
                className={`p-4 rounded-xl border flex flex-col justify-between transition shadow-sm ${
                  cue.arrow === 'tailwind' ? 'bg-emerald-50/80 border-emerald-200 text-emerald-950' :
                  cue.arrow === 'headwind' ? 'bg-rose-50/80 border-rose-200 text-rose-950' :
                  'bg-slate-50 border-slate-200 text-slate-800'
                }`}
              >
                <div className="flex items-start gap-3.5">
                  <div className={`p-2 rounded-lg shrink-0 mt-0.5 ${
                    cue.arrow === 'tailwind' ? 'bg-emerald-100 text-emerald-700' :
                    cue.arrow === 'headwind' ? 'bg-rose-100 text-rose-700' :
                    'bg-slate-100 text-slate-500'
                  }`}>
                    {cue.arrow === 'tailwind' ? <ArrowUpRight className="w-5 h-5" /> : 
                     cue.arrow === 'headwind' ? <ArrowDownRight className="w-5 h-5" /> : 
                     <Minus className="w-5 h-5" />}
                  </div>
                  <div className="space-y-1 flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-bold text-xs md:text-sm truncate" title={cardTitle}>{cardTitle}</span>
                      <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded shrink-0 ${
                        cue.arrow === 'tailwind' ? 'bg-emerald-200/80 text-emerald-900' : 
                        cue.arrow === 'headwind' ? 'bg-rose-200/80 text-rose-900' : 
                        'bg-slate-200 text-slate-700'
                      }`}>
                        {cue.arrow}
                      </span>
                    </div>
                    <div className="text-[10px] font-semibold opacity-90 truncate">
                      Target: <span className="underline decoration-dotted">{cue.sector}</span>
                    </div>
                    <div className="text-xs opacity-80 pt-1 leading-snug">
                      {cardDesc}
                    </div>
                    {extraNote}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
