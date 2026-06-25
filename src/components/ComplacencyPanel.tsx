import React from 'react';
import { ComplacencyMetrics } from '../types';
import { AlertTriangle, Activity, Zap, TrendingUp, Info } from 'lucide-react';

interface Props {
  metrics: ComplacencyMetrics;
  spot: number;
}

export const ComplacencyPanel: React.FC<Props> = ({ metrics, spot }) => {
  const getGaugeColor = (score: number) => {
    if (score >= 65) return 'text-rose-600 bg-rose-500';
    if (score >= 40) return 'text-amber-500 bg-amber-500';
    return 'text-emerald-600 bg-emerald-500';
  };

  const getToneIcon = (tone: string) => {
    switch (tone) {
      case 'caution': return <AlertTriangle className="w-6 h-6 text-rose-500 shrink-0" />;
      case 'neutral': return <Info className="w-6 h-6 text-amber-500 shrink-0" />;
      default: return <Zap className="w-6 h-6 text-emerald-500 shrink-0" />;
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white p-6 rounded-2xl shadow-lg border border-slate-800 flex flex-col md:flex-row items-center justify-between gap-6">
        <div className="space-y-2 max-w-xl">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-500/20 border border-indigo-400/30 text-indigo-300 text-xs font-semibold uppercase tracking-wider">
            <Activity className="w-3.5 h-3.5 animate-pulse" /> Complacency & Tail Risk Gauge
          </div>
          <h2 className="text-2xl font-bold tracking-tight">
            Is Volatility Mispricing the Catastrophic Tail?
          </h2>
          <p className="text-slate-300 text-sm leading-relaxed">
            Blends ATM Implied Volatility discount against near-ATM Put Writing bursts.
            Low IV combined with aggressive Put OI crowding warns of extreme vulnerability to sudden downside repricing.
          </p>
        </div>

        {/* Complacency Score Display */}
        <div className="bg-slate-800/80 p-6 rounded-2xl border border-slate-700/80 flex items-center gap-6 min-w-[280px] justify-center">
          <div className="relative flex items-center justify-center">
            <svg className="w-28 h-28 transform -rotate-90">
              <circle
                cx="56"
                cy="56"
                r="46"
                stroke="currentColor"
                strokeWidth="10"
                fill="transparent"
                className="text-slate-700"
              />
              <circle
                cx="56"
                cy="56"
                r="46"
                stroke="currentColor"
                strokeWidth="10"
                fill="transparent"
                strokeDasharray={289}
                strokeDashoffset={289 - (289 * metrics.score) / 100}
                strokeLinecap="round"
                className={getGaugeColor(metrics.score).split(' ')[0]}
              />
            </svg>
            <div className="absolute flex flex-col items-center justify-center">
              <span className="text-3xl font-black">{metrics.score}</span>
              <span className="text-[10px] uppercase font-bold text-slate-400 tracking-tighter">Score / 100</span>
            </div>
          </div>

          <div className="space-y-1">
            <div className="text-xs text-slate-400 uppercase font-semibold">Risk Level</div>
            <div className={`text-lg font-black uppercase ${getGaugeColor(metrics.score).split(' ')[0]}`}>
              {metrics.score >= 65 ? 'Extreme' : metrics.score >= 40 ? 'Elevated' : 'Normal'}
            </div>
            <div className="text-[11px] text-slate-300">
              IV Floor Weight: 60%
            </div>
          </div>
        </div>
      </div>

      {/* Metrics Breakdown Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500">
            <span>ATM Implied Volatility</span>
            <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700">Live</span>
          </div>
          <div className="text-3xl font-bold text-slate-900">{metrics.iv.toFixed(1)}%</div>
          <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
            <div
              className="bg-indigo-600 h-2 rounded-full"
              style={{ width: `${Math.min(100, Math.max(10, (metrics.iv / 25) * 100))}%` }}
            ></div>
          </div>
          <div className="text-xs text-slate-500 flex justify-between">
            <span>Rock Bottom (8%)</span>
            <span>Stressed (18%+)</span>
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500">
            <span>Put Writing Bursts (&gt;100% OI)</span>
            <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">±250 ATM</span>
          </div>
          <div className="text-3xl font-bold text-emerald-600 flex items-baseline gap-2">
            {metrics.bursts} <span className="text-sm font-normal text-slate-400">strikes near spot</span>
          </div>
          <div className="text-xs font-semibold text-slate-700">
            Max Burst Velocity: <span className="text-emerald-700 font-bold">+{metrics.max_burst.toFixed(0)}%</span>
          </div>
          <div className="text-[11px] text-slate-400 leading-tight">
            Writers aggressively crowding ATM support strikes.
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500">
            <span>IV Compression Score</span>
            <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800">Index</span>
          </div>
          <div className="text-3xl font-bold text-blue-600">{metrics.comp_iv.toFixed(0)} / 100</div>
          <div className="text-xs text-slate-600">
            Put-Writing Acceleration: <span className="font-bold">{metrics.accel.toFixed(0)} / 100</span>
          </div>
          <div className="text-[11px] text-slate-400 leading-tight">
            Higher compression means cheaper insurance protection.
          </div>
        </div>
      </div>

      {/* Verdict Card */}
      <div className={`p-6 rounded-2xl border-2 flex items-start gap-4 ${
        metrics.score >= 65 ? 'bg-rose-50 border-rose-200 text-rose-950' :
        metrics.score >= 40 ? 'bg-amber-50 border-amber-200 text-amber-950' :
        'bg-emerald-50 border-emerald-200 text-emerald-950'
      }`}>
        {getToneIcon(metrics.verdict.tone)}
        <div className="space-y-1">
          <h3 className="text-lg font-bold tracking-tight">
            Institutional Desk Verdict: {metrics.score >= 65 ? 'CAUTION — EXTREME COMPLACENCY' : metrics.score >= 40 ? 'ELEVATED COMPLACENCY' : 'STABLE VOL REGIME'}
          </h3>
          <p className="text-sm leading-relaxed font-medium opacity-90">
            {metrics.verdict.msg}
          </p>
        </div>
      </div>
    </div>
  );
};
