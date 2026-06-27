import React from 'react';
import { Newspaper, TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface Props {
  pipelineRes?: any;
}

export const SectorNewsPanel: React.FC<Props> = ({ pipelineRes }) => {
  const getToneBadge = (score: number) => {
    if (score > 0) return 'bg-emerald-100 text-emerald-800 border-emerald-200';
    if (score < 0) return 'bg-rose-100 text-rose-800 border-rose-200';
    return 'bg-slate-100 text-slate-700 border-slate-200';
  };

  return (
    <div className="space-y-6">
      {/* Regime Banner from Pipeline */}
      {pipelineRes && pipelineRes.regime && (
        <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 rounded-2xl p-6 text-white border border-slate-800 shadow-lg">
          {pipelineRes.regime.flipped_from && (
            <div className="mb-4 bg-amber-500/20 border border-amber-500/50 text-amber-200 px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-2">
              <TrendingDown className="w-4 h-4" />
              ⚠️ Regime ROTATED: {pipelineRes.regime.flipped_from} → {pipelineRes.regime.dominant} — sentiment driver just changed.
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Driver</div>
              <div className="text-xl font-black capitalize text-indigo-400">{pipelineRes.regime.dominant.replace('_', ' ')}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Conviction</div>
              <div className="text-xl font-black">{(pipelineRes.regime.conviction * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Vol State</div>
              <div className="text-xl font-black">{pipelineRes.regime.vol_expansion ? "Expansion" : "Range"}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-indigo-300 font-bold tracking-wider mb-1">Corroboration</div>
              <div className="text-xl font-black">{pipelineRes.regime.surfaces.length} surfaces</div>
              {pipelineRes.regime.surfaces.length > 0 && (
                <div className="text-xs text-indigo-300 mt-1 truncate">
                  {pipelineRes.regime.surfaces.join(", ")}
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
        <div className="text-xs text-slate-400">
          Powered by Gemini AI
        </div>
      </div>

      {pipelineRes && pipelineRes.coverage < 0.35 && (
        <div className="bg-rose-50 text-rose-800 p-4 rounded-xl border border-rose-200 text-sm font-bold flex items-center gap-2">
          <TrendingDown className="w-4 h-4 text-rose-600" />
          ⚠️ Low Coverage ({Math.round(pipelineRes.coverage * 100)}%): The model's sentiment output may be relying heavily on baseline heuristics or missing active sectors.
        </div>
      )}

      {/* Net Sector Aggregator Cards */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 px-1 flex items-center justify-between">
          <span>Aggregated Net Sector Bias Score</span>
          {pipelineRes && (
            <span className={`text-xs font-mono font-black ${pipelineRes.bias > 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
              Index Bias: {pipelineRes.bias > 0 ? '+' : ''}{pipelineRes.bias.toFixed(2)}
            </span>
          )}
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {pipelineRes && Object.entries(pipelineRes.sector_sentiment)
            .sort((a, b) => Number(b[1]) - Number(a[1]))
            .map(([sec, rawScore]) => {
              const score = Number(rawScore);
              const weight = pipelineRes.sector_weights[sec] || 0;
              return (
                <div key={sec} className={`p-3.5 rounded-xl border flex flex-col justify-between shadow-sm ${
                  score > 0 ? 'bg-emerald-50/80 border-emerald-200' :
                  score < 0 ? 'bg-rose-50/80 border-rose-200' :
                  'bg-white border-slate-200'
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
      </div>

      {/* Render Headlines from Pipeline Result */}
      <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
        <h3 className="text-sm font-bold text-slate-800 mb-4 flex items-center gap-2">
          <Newspaper className="w-4 h-4 text-indigo-500" />
          Recent Processed Headlines
        </h3>
        
        {pipelineRes && pipelineRes.articles && pipelineRes.articles.length > 0 ? (
          <div className="space-y-3">
            {pipelineRes.articles.map((a: any, idx: number) => {
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
