import React, { useState } from 'react';
import { NewsHeadline } from '../types';
import { Newspaper, TrendingUp, TrendingDown, Minus, Edit3, CheckCircle2 } from 'lucide-react';
import { SAMPLE_NEWS } from '../lib/constants';

interface Props {
  headlines: NewsHeadline[];
  aggregated: Record<string, number>;
  rawNews: string;
  onNewsChange: (val: string) => void;
}

export const SectorNewsPanel: React.FC<Props> = ({
  headlines,
  aggregated,
  rawNews,
  onNewsChange,
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [tempText, setTempText] = useState(rawNews);

  const handleSave = () => {
    onNewsChange(tempText);
    setIsEditing(false);
  };

  const getToneBadge = (score: number) => {
    if (score > 0) return 'bg-emerald-100 text-emerald-800 border-emerald-200';
    if (score < 0) return 'bg-rose-100 text-rose-800 border-rose-200';
    return 'bg-slate-100 text-slate-700 border-slate-200';
  };

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Newspaper className="w-5 h-5 text-indigo-600" /> Per-Sector News Sentiment Analyzer
          </h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Classifies raw pasted headlines against sector lexicons (IT, Banking, Auto, Energy, Defence...) and quantifies net optimism vs pessimism (capex/order wins vs probes/guidance cuts).
          </p>
        </div>
        <button
          onClick={() => {
            if (isEditing) {
              handleSave();
            } else {
              setTempText(rawNews);
              setIsEditing(true);
            }
          }}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold transition cursor-pointer shadow-sm"
        >
          {isEditing ? <CheckCircle2 className="w-4 h-4" /> : <Edit3 className="w-4 h-4" />}
          {isEditing ? 'Analyze & Update' : 'Paste New Headlines'}
        </button>
      </div>

      {isEditing && (
        <div className="bg-slate-900 p-5 rounded-2xl border border-slate-800 text-white space-y-3 animate-in fade-in duration-200">
          <div className="flex justify-between items-center">
            <label className="text-xs font-bold uppercase tracking-wider text-indigo-300">
              Paste News Headlines (One per line)
            </label>
            <button
              onClick={() => setTempText(SAMPLE_NEWS)}
              className="text-xs text-slate-400 hover:text-white underline"
            >
              Load Sample Headlines
            </button>
          </div>
          <textarea
            rows={6}
            value={tempText}
            onChange={(e) => setTempText(e.target.value)}
            className="w-full p-3 bg-slate-800 text-slate-100 rounded-xl border border-slate-700 font-mono text-xs focus:ring-2 focus:ring-indigo-500 outline-none leading-relaxed"
            placeholder="Paste news headlines here..."
          />
          <div className="flex justify-end gap-3 pt-2">
            <button
              onClick={() => setIsEditing(false)}
              className="px-4 py-1.5 rounded-lg bg-slate-800 text-slate-300 text-xs font-medium hover:bg-slate-700"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="px-4 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold hover:bg-indigo-500"
            >
              Run Natural Language Classification
            </button>
          </div>
        </div>
      )}

      {/* Net Sector Aggregator Cards */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 px-1">
          Aggregated Net Sector Bias Score
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {(Object.entries(aggregated) as [string, number][])
            .sort((a, b) => b[1] - a[1])
            .map(([sec, rawScore]) => {
              const score = Number(rawScore);
              return (
                <div key={sec} className={`p-3.5 rounded-xl border flex flex-col justify-between shadow-sm ${
                  score > 0 ? 'bg-emerald-50/80 border-emerald-200' :
                  score < 0 ? 'bg-rose-50/80 border-rose-200' :
                  'bg-white border-slate-200'
                }`}>
                  <div className="text-xs font-bold text-slate-700 truncate" title={sec}>{sec}</div>
                  <div className="flex items-center justify-between mt-2 pt-2 border-t border-slate-200/50">
                    <span className={`text-lg font-black font-mono ${
                      score > 0 ? 'text-emerald-700' : score < 0 ? 'text-rose-700' : 'text-slate-500'
                    }`}>
                      {score > 0 ? `+${score}` : score}
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

      {/* Classified Headline Table */}
      <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
        <div className="p-4 bg-slate-50 border-b border-slate-200 font-bold text-xs uppercase tracking-wider text-slate-600 flex justify-between items-center">
          <span>Classified Headline Feed ({headlines.length})</span>
          <span className="text-[11px] font-normal lowercase text-slate-400">tagged by keyword match</span>
        </div>
        <div className="divide-y divide-slate-100 max-h-[420px] overflow-y-auto">
          {headlines.map((h, i) => (
            <div key={i} className="p-4 flex items-center justify-between gap-4 hover:bg-slate-50/70 transition">
              <div className="flex items-center gap-3 min-w-0">
                <span className={`px-2.5 py-0.5 rounded text-[11px] font-bold shrink-0 border ${getToneBadge(h.score)}`}>
                  {h.score > 0 ? `+${h.score}` : h.score === 0 ? '0' : h.score}
                </span>
                <span className="text-sm text-slate-800 font-medium leading-snug truncate">{h.headline}</span>
              </div>
              <span className="px-2.5 py-1 bg-slate-100 text-slate-700 font-semibold text-xs rounded-lg shrink-0">
                {h.sector}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
