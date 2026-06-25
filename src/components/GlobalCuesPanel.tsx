import React from 'react';
import { GlobalCueItem } from '../types';
import { Globe, ArrowUpRight, ArrowDownRight, RefreshCw, HelpCircle } from 'lucide-react';

interface Props {
  cues: GlobalCueItem[];
  pctMap: Record<string, number>;
  onPctChange: (name: string, val: number) => void;
  onResetDefaults: () => void;
}

export const GlobalCuesPanel: React.FC<Props> = ({
  cues,
  pctMap,
  onPctChange,
  onResetDefaults,
}) => {
  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Globe className="w-5 h-5 text-indigo-600" /> Global Macro & Sector Transmission Matrix
          </h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Overnight global index moves cue specific domestic Indian sectors. Note that inverse mappings (e.g., Brent Oil & Dollar DXY) act as headwinds when rising.
          </p>
        </div>
        <button
          onClick={onResetDefaults}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold transition cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Reset Default Moves
        </button>
      </div>

      {/* Inputs Grid */}
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 pb-2 border-b border-slate-100 flex items-center justify-between">
          <span>Overnight Moves (% Chg)</span>
          <span className="text-xs font-normal lowercase italic text-slate-400">adjust values to simulate impact</span>
        </h3>
        
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
          {Object.keys(pctMap).map((name) => {
            const val = pctMap[name] || 0;
            return (
              <div key={name} className="p-3 bg-slate-50 rounded-xl border border-slate-200/80">
                <label className="block text-xs font-bold text-slate-700 mb-1 truncate" title={name}>
                  {name}
                </label>
                <div className="relative flex items-center">
                  <input
                    type="number"
                    step="0.1"
                    value={val}
                    onChange={(e) => onPctChange(name, parseFloat(e.target.value) || 0)}
                    className={`w-full px-2.5 py-1.5 text-sm font-mono font-bold rounded-lg border focus:ring-2 focus:ring-indigo-500 outline-none ${
                      val > 0 ? 'text-emerald-700 bg-emerald-50/50 border-emerald-300' :
                      val < 0 ? 'text-rose-700 bg-rose-50/50 border-rose-300' :
                      'text-slate-700 bg-white border-slate-300'
                    }`}
                  />
                  <span className="absolute right-2 text-xs text-slate-400 font-bold pointer-events-none">%</span>
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
          {cues.map((cue, i) => (
            <div
              key={i}
              className={`p-4 rounded-xl border flex items-start gap-3.5 transition shadow-sm ${
                cue.arrow === 'tailwind' ? 'bg-emerald-50/80 border-emerald-200 text-emerald-950' :
                cue.arrow === 'headwind' ? 'bg-rose-50/80 border-rose-200 text-rose-950' :
                'bg-slate-50 border-slate-200 text-slate-800'
              }`}
            >
              <div className={`p-2 rounded-lg shrink-0 mt-0.5 ${
                cue.arrow === 'tailwind' ? 'bg-emerald-100 text-emerald-700' :
                cue.arrow === 'headwind' ? 'bg-rose-100 text-rose-700' :
                'bg-slate-200 text-slate-600'
              }`}>
                {cue.arrow === 'tailwind' ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownRight className="w-5 h-5" />}
              </div>
              <div className="space-y-1 flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-sm truncate">{cue.name} ({cue.pct > 0 ? '+' : ''}{cue.pct}%)</span>
                  <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${
                    cue.arrow === 'tailwind' ? 'bg-emerald-200/80 text-emerald-900' : 'bg-rose-200/80 text-rose-900'
                  }`}>
                    {cue.arrow}
                  </span>
                </div>
                <div className="text-xs font-semibold opacity-90 truncate">
                  Target: <span className="underline decoration-dotted">{cue.sector}</span>
                </div>
                <div className="text-xs opacity-80 pt-1 leading-snug">
                  {cue.inverse ? '* Inverse macro sensitivity' : '* Direct momentum correlation'}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
