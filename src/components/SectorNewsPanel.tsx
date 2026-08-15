import React, { useState } from 'react';
import { Newspaper, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { FormulaTooltip } from './FormulaTooltip';
import { ProvenanceBadge } from './ProvenanceBadge';

interface Props {
  data: any;
}

export const SectorNewsPanel: React.FC<Props> = ({ data }) => {
  const [selectedSector, setSelectedSector] = useState<string | null>(null);
  
  const getToneBadge = (score: number) => {
    if (score > 0) return 'bg-emerald-100 text-emerald-800 border-emerald-200';
    if (score < 0) return 'bg-rose-100 text-rose-800 border-rose-200';
    return 'bg-slate-100 text-slate-700 border-slate-200';
  };

  const sector_sentiment = data?.sector_sentiment || {};
  const articles = data?.articles || [];
  const timestamps = data?.timestamps || {};
  const regime = data?.regime;
  const coverage = data?.coverage;
  const bias = data?.bias;
  const sector_weights = data?.sector_weights || {};
  const provRecords = data?.provenance?.records || [];
  
  const getProvenance = (componentName: string) =>
    provRecords.find((r: any) => r.component === componentName);

  // Articles that fed a given sector's score: either the LLM tagged the sector
  // directly (sectors_affected) or a named constituent maps to it. Sorted most
  // negative first, so the headlines that DRAGGED the sector down surface at the
  // top and the ones that PUSHED it up sit below.
  const sectorArticles = (sec: string) =>
    (articles as any[])
      .filter((a) =>
        (a.sectors_affected || []).includes(sec) ||
        (a.constituents || []).some((c: any) => c.sector === sec)
      )
      .sort((a, b) => Number(a.sentiment || 0) - Number(b.sentiment || 0));

  const newsAsOf = timestamps.news ? new Date(timestamps.news).toLocaleTimeString() : 'Unknown';

  return (
    <div className="space-y-6">
      {/* Regime Banner from Pipeline */}
      {regime && (
        <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 rounded-2xl p-6 text-white border border-slate-800 shadow-lg">
          {regime.flipped_from && (
            <div className="mb-4 bg-amber-500/20 border border-amber-500/50 text-amber-200 px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-2">
              <TrendingDown className="w-4 h-4" />
              ⚠️ Regime ROTATED: {regime.flipped_from} → {regime.dominant} — sentiment driver just changed.
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Driver</div>
              <div className="text-xl font-black capitalize text-indigo-400">{regime.dominant.replace('_', ' ')}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Conviction</div>
              <div className="text-xl font-black">{(regime.conviction * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Vol State</div>
              <div className="text-xl font-black">{regime.vol_expansion ? "Expansion" : "Range"}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Corroboration</div>
              <div className="text-xl font-black">{regime.surfaces.length} surfaces</div>
              {regime.surfaces.length > 0 && (
                <div className="text-xs text-indigo-300 mt-1 truncate">
                  {regime.surfaces.join(", ")}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Newspaper className="w-5 h-5 text-indigo-600" /> Live Sector Sentiment Analyzer
          </h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Real-time RSS feeds are tagged securely via Gemini for market sentiment and canonical sector impacts.
          </p>
        </div>
        <div className="text-xs font-mono text-slate-500 bg-slate-100 px-3 py-1.5 rounded-lg font-bold border border-slate-200 flex items-center gap-2">
          {newsAsOf !== 'Unknown' ? `As of: ${newsAsOf}` : 'Stale'}
          <ProvenanceBadge record={getProvenance('news_state')} />
        </div>
      </div>

      {data && data.coverage < 0.35 && (
        <div className="bg-rose-50 text-rose-800 p-4 rounded-xl border border-rose-200 text-sm font-bold flex items-center gap-2">
          <TrendingDown className="w-4 h-4 text-rose-600" />
          ⚠️ Low Coverage ({Math.round(data.coverage * 100)}%): The model's sentiment output may be relying heavily on baseline heuristics or missing active sectors.
        </div>
      )}

      {data?.interpretations?.republish?.warning && (
        <div className="bg-amber-50 text-amber-800 p-4 rounded-xl border border-amber-200 text-sm font-bold flex items-center gap-2">
          ⚠️ {data.interpretations.republish.warning}
        </div>
      )}

      {/* Net Sector Aggregator Cards */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 px-1 flex items-center justify-between">
          <span className="flex items-center gap-2">
            Aggregated Net Sector Bias Score
            <ProvenanceBadge record={getProvenance('sentiment')} />
            <ProvenanceBadge record={getProvenance('coverage')} />
          </span>
          {data && (
            <span className={`text-xs font-mono font-black flex items-center ${data.bias > 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
              Index Bias: {data.bias > 0 ? '+' : ''}{data.bias.toFixed(2)}
              <FormulaTooltip trace={data.formulas?.bias} />
            </span>
          )}
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {data && Object.entries(data.sector_sentiment)
            .sort((a, b) => Number(b[1]) - Number(a[1]))
            .map(([sec, rawScore]) => {
              const score = Number(rawScore);
              const weight = data.sector_weights[sec] || 0;
              return (
                <div 
                  key={sec} 
                  onClick={() => setSelectedSector(selectedSector === sec ? null : sec)}
                  className={`p-3.5 rounded-xl border flex flex-col justify-between shadow-sm cursor-pointer transition-colors ${
                  score > 0 ? (selectedSector === sec ? 'bg-emerald-100 border-emerald-300' : 'bg-emerald-50/80 border-emerald-200 hover:bg-emerald-100/50') :
                  score < 0 ? (selectedSector === sec ? 'bg-rose-100 border-rose-300' : 'bg-rose-50/80 border-rose-200 hover:bg-rose-100/50') :
                  (selectedSector === sec ? 'bg-slate-100 border-slate-300' : 'bg-white border-slate-200 hover:bg-slate-50')
                }`}>
                  <div className="flex justify-between items-start">
                    <div className="text-xs font-bold text-slate-700 truncate" title={sec}>{sec}</div>
                    {weight !== null && <div className="text-[9px] font-mono text-slate-400">{weight.toFixed(1)}% wgt</div>}
                  </div>
                  <div className="flex items-center justify-between mt-2 pt-2 border-t border-slate-200/50">
                    <span className={`text-lg font-black font-mono ${
                      score > 0 ? 'text-emerald-700' : score < 0 ? 'text-rose-700' : 'text-slate-500'
                    }`}>
                      {score > 0 ? `+${score.toFixed(2)}` : score.toFixed(2)}
                    </span>
                    {score > 0 ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
                     score < 0 ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
                     <Minus className="w-4 h-4 text-slate-400" />}
                  </div>
                </div>
              );
            })}
        </div>
        
        {/* Drill-down Section */}
        {selectedSector && (
          <div className="mt-4 p-4 bg-slate-50 rounded-xl border border-slate-200 animate-in fade-in slide-in-from-top-2 duration-200 space-y-5">
            {/* Contributing headlines — what pushed the sector up / dragged it down */}
            {(() => {
              const contrib = sectorArticles(selectedSector);
              const drags = contrib.filter((a) => Number(a.sentiment) < 0);
              const boosts = contrib.filter((a) => Number(a.sentiment) > 0);
              return (
                <div>
                  <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                    {selectedSector} — Contributing Headlines
                    <span className="font-mono text-[10px] text-slate-400 normal-case">
                      {drags.length} drag{drags.length !== 1 ? 's' : ''} · {boosts.length} boost{boosts.length !== 1 ? 's' : ''}
                    </span>
                  </h4>
                  {contrib.length === 0 ? (
                    <div className="text-xs text-slate-400 italic">No headlines tagged to this sector in the current window.</div>
                  ) : (
                    <div className="space-y-1.5">
                      {contrib.map((a: any, i: number) => {
                        const s = Number(a.sentiment || 0);
                        return (
                          <div
                            key={i}
                            className={`flex items-start gap-3 p-2 rounded-lg border text-xs ${
                              s < 0 ? 'bg-rose-50/70 border-rose-100'
                                : s > 0 ? 'bg-emerald-50/70 border-emerald-100'
                                : 'bg-white border-slate-100'
                            }`}
                          >
                            <span
                              className={`font-mono font-black tabular-nums shrink-0 w-14 text-right ${
                                s < 0 ? 'text-rose-600' : s > 0 ? 'text-emerald-600' : 'text-slate-400'
                              }`}
                            >
                              {s > 0 ? '+' : ''}{(s * 100).toFixed(0)}%
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="font-semibold text-slate-700 leading-snug">{a.title}</p>
                              {a.published_at && (
                                <span className="text-[10px] text-slate-400">
                                  {new Date(a.published_at).toLocaleString()} · via {a.source || 'RSS'}
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Industry & company attribution (weighted contribution numbers) */}
            {data?.drilldown?.[selectedSector] && (
            <div>
            <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
              {selectedSector} — Industry & Company Contributions
            </h4>
            <div className="space-y-4">
              {Object.entries(data.drilldown[selectedSector] || {}).map(([ind, comps]: any) => (
                <div key={ind} className="border-l-2 border-indigo-200 pl-3">
                  <div className="text-sm font-bold text-slate-700 mb-2">{ind}</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                    {Object.entries(comps).map(([comp, rawScore]: any) => {
                      const cScore = Number(rawScore);
                      return (
                        <div key={comp} className="flex justify-between items-center bg-white p-2 rounded border border-slate-100 shadow-sm text-xs">
                          <span className="font-semibold text-slate-600 truncate mr-2" title={comp}>{comp}</span>
                          <span className={`font-mono font-black ${
                            cScore > 0 ? 'text-emerald-600' : cScore < 0 ? 'text-rose-600' : 'text-slate-400'
                          }`}>
                            {cScore > 0 ? '+' : ''}{cScore.toFixed(3)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
              {Object.keys(data.drilldown[selectedSector] || {}).length === 0 && (
                <div className="text-xs text-slate-400 italic">No direct company mentions found (derived macro hits only).</div>
              )}
            </div>
            </div>
            )}
          </div>
        )}
      </div>

      {/* Render Headlines from Pipeline Result */}
      <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
        <h3 className="text-sm font-bold text-slate-800 mb-4 flex items-center gap-2">
          <Newspaper className="w-4 h-4 text-indigo-500" />
          Recent Processed Headlines
        </h3>
        
        {data && data.articles && data.articles.length > 0 ? (
          <div className="space-y-3">
            {data.articles.map((a: any, idx: number) => {
              const toneClass = getToneBadge(a.sentiment);
              return (
                <div key={idx} className="flex flex-col sm:flex-row gap-4 py-3 border-b border-slate-100 last:border-0 hover:bg-slate-50 transition p-2 rounded-lg">
                  <div className="flex-1">
                    <p className="text-sm font-bold text-slate-800 leading-snug">{a.title}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs">
                      {a.published_at && (
                        <span className="text-slate-400">{new Date(a.published_at).toLocaleString()}</span>
                      )}
                      {a.sectors_affected && a.sectors_affected.length > 0 && (
                        <span className="font-medium text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">
                          {a.sectors_affected.join(', ')}
                        </span>
                      )}
                      <span className="text-slate-400 italic">via {a.source || "RSS"}</span>
                    </div>
                  </div>
                  <div className="flex items-start sm:items-center">
                    <span className={`px-2 py-1 rounded-full text-[10px] font-black uppercase tracking-wide border ${toneClass}`}>
                      {(a.sentiment * 100).toFixed(0)}% Impact
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-12 text-slate-500 text-sm">
            No pipeline result yet. Click "Run Quant Engine" on the top bar to fetch live RSS news, tag it with Gemini, and calculate market sentiment!
          </div>
        )}
      </div>
    </div>
  );
};
