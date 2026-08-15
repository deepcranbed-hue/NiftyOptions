import React from 'react';
import { TrendingUp, TrendingDown, Minus, Briefcase } from 'lucide-react';

export function SectorEarningsPanel({ pipelineRes }: { pipelineRes: any }) {
  if (!pipelineRes) {
    return (
      <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm text-center">
        <Briefcase className="w-8 h-8 text-slate-300 mx-auto mb-3" />
        <h3 className="text-sm font-bold text-slate-900">No Data Available</h3>
        <p className="text-xs text-slate-500 mt-1">Run the quant pipeline to fetch data.</p>
      </div>
    );
  }

  // Gate the panel
  const isEarningsSeason = pipelineRes.conclusion?.earnings_season;
  if (!isEarningsSeason) {
    return (
      <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm text-center">
        <Briefcase className="w-8 h-8 text-slate-300 mx-auto mb-3" />
        <h3 className="text-sm font-bold text-slate-900">No active earnings season</h3>
        <p className="text-xs text-slate-500 mt-1">Macro drivers lead. Earnings gap risk is low.</p>
      </div>
    );
  }

  // Parse articles for earnings results
  const articles = pipelineRes.articles || [];
  const sectorWeights = pipelineRes.sector_weights || {};
  
  // Calculate momentum
  const sectorMomentum: Record<string, { momentum: number, hits: number, beats: number, misses: number }> = {};
  
  articles.forEach((a: any) => {
    if (a.earnings && ['beat', 'miss'].includes(a.earnings)) {
      const isBeat = a.earnings === 'beat';
      const sectors = a.sectors_affected || [];
      
      sectors.forEach((s: string) => {
        if (!sectorMomentum[s]) {
          sectorMomentum[s] = { momentum: 0, hits: 0, beats: 0, misses: 0 };
        }
        
        const weight = sectorWeights[s] || 0.1;
        
        sectorMomentum[s].hits += 1;
        if (isBeat) {
          sectorMomentum[s].beats += 1;
          sectorMomentum[s].momentum += weight;
        } else {
          sectorMomentum[s].misses += 1;
          sectorMomentum[s].momentum -= weight;
        }
      });
    }
  });

  const sortedSectors = Object.entries(sectorMomentum)
    .filter(([_, data]) => data.hits > 0)
    .sort((a, b) => Math.abs(b[1].momentum) - Math.abs(a[1].momentum));

  if (sortedSectors.length === 0) {
    return (
      <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm text-center">
        <Briefcase className="w-8 h-8 text-indigo-300 mx-auto mb-3 animate-pulse" />
        <h3 className="text-sm font-bold text-slate-900">Active Earnings Season</h3>
        <p className="text-xs text-slate-500 mt-1">No major earnings news in the current window.</p>
      </div>
    );
  }

  const dominantDriver = sortedSectors[0];

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-br from-indigo-900 to-slate-900 text-white rounded-2xl p-6 shadow-lg border border-indigo-500/20">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-black tracking-tight flex items-center gap-2">
              <Briefcase className="w-5 h-5 text-indigo-400" />
              Sector Earnings Momentum
            </h2>
            <p className="text-indigo-200/80 text-sm mt-1 max-w-2xl">
              Weighted by NIFTY 50 impact. Tracks company earnings surprises (beats/misses) and maps them to index movers.
            </p>
          </div>
          <div className="bg-white/10 px-3 py-1 rounded-full text-xs font-bold border border-white/10">
            Earnings Window Active
          </div>
        </div>
      </div>

      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
        <h3 className="text-sm font-bold text-slate-900 mb-4 border-b border-slate-100 pb-2">Dominant Earnings Driver</h3>
        <div className="flex items-center gap-4">
          <div className={`p-4 rounded-xl ${dominantDriver[1].momentum > 0 ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'}`}>
            {dominantDriver[1].momentum > 0 ? <TrendingUp className="w-8 h-8" /> : <TrendingDown className="w-8 h-8" />}
          </div>
          <div>
            <div className="text-xl font-black text-slate-900">{dominantDriver[0]}</div>
            <div className="text-sm font-medium text-slate-500 flex items-center gap-2">
              Weighted Momentum: {dominantDriver[1].momentum > 0 ? '+' : ''}{dominantDriver[1].momentum.toFixed(2)}%
              <span className="text-slate-300">|</span>
              {dominantDriver[1].beats} Beats, {dominantDriver[1].misses} Misses
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50">
          <h3 className="text-sm font-bold text-slate-900">All Sectors</h3>
        </div>
        <div className="divide-y divide-slate-100">
          {sortedSectors.map(([sector, data]) => (
            <div key={sector} className="p-4 flex items-center justify-between hover:bg-slate-50/50 transition">
              <div className="font-bold text-slate-800 text-sm">{sector}</div>
              <div className="flex items-center gap-6">
                <div className="text-xs text-slate-500 font-medium">
                  {data.beats} B / {data.misses} M
                </div>
                <div className={`w-24 text-right font-black text-sm ${
                  data.momentum > 0 ? 'text-emerald-600' : data.momentum < 0 ? 'text-rose-600' : 'text-slate-400'
                }`}>
                  {data.momentum > 0 ? '+' : ''}{data.momentum.toFixed(2)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
