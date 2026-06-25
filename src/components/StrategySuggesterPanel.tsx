import React, { useState, useMemo } from 'react';
import { OptionRow } from '../types';
import { suggestStrategies, calculatePayoffCurve } from '../lib/analytics';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts';
import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart,Sliders } from 'lucide-react';

interface Props {
  rows: OptionRow[];
  spot: number;
  atmIV: number;
  capital: number;
  onCapitalChange: (val: number) => void;
  selectedOutlook: 'bullish' | 'bearish' | 'neutral' | 'volatile';
  onOutlookChange: (val: 'bullish' | 'bearish' | 'neutral' | 'volatile') => void;
}

export const StrategySuggesterPanel: React.FC<Props> = ({
  rows,
  spot,
  atmIV,
  capital,
  onCapitalChange,
  selectedOutlook,
  onOutlookChange,
}) => {
  const [ivEnv, setIvEnv] = useState<'low' | 'moderate' | 'high'>(
    atmIV > 16 ? 'high' : atmIV < 11 ? 'low' : 'moderate'
  );

  const recommendations = useMemo(() => {
    return suggestStrategies(rows, spot, selectedOutlook, ivEnv);
  }, [rows, spot, selectedOutlook, ivEnv]);

  const [activeStrategyId, setActiveStrategyId] = useState<string>(
    recommendations[0]?.id || 'iron_condor'
  );

  // Sync active strategy when list changes
  const activeStrategy = useMemo(() => {
    return recommendations.find((r) => r.id === activeStrategyId) || recommendations[0];
  }, [recommendations, activeStrategyId]);

  const payoffPoints = useMemo(() => {
    if (!activeStrategy) return [];
    return calculatePayoffCurve(activeStrategy.legs, spot);
  }, [activeStrategy, spot]);

  // Calculate required margin/capital rough estimate
  const estimatedCapitalReq = useMemo(() => {
    if (!activeStrategy) return 50000;
    const shortLegs = activeStrategy.legs.filter((l) => l.action === 'SELL').length;
    return Math.max(15000, shortLegs * 75000);
  }, [activeStrategy]);

  return (
    <div className="space-y-8">
      {/* Top Filter Banner */}
      <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 text-white p-6 rounded-2xl shadow-lg border border-slate-800 flex flex-col xl:flex-row items-start xl:items-center justify-between gap-6">
        <div className="space-y-1">
          <div className="inline-flex items-center gap-1.5 text-amber-400 text-xs font-bold uppercase tracking-wider">
            <Sparkles className="w-4 h-4 animate-spin" /> Quantitative Strategy Suggester
          </div>
          <h2 className="text-2xl font-black tracking-tight">
            Optimal Nifty Positioning Engine
          </h2>
          <p className="text-slate-300 text-xs max-w-xl">
            Calculates exact Black-Scholes Greeks, strike boundaries (rounded to 50s NSE standard), and expiration payoff profiles based on your directional thesis and IV regime.
          </p>
        </div>

        {/* Outlook & Capital Controls */}
        <div className="flex flex-wrap items-center gap-4 w-full xl:w-auto bg-slate-800/90 p-3 rounded-xl border border-slate-700">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Thesis Outlook</label>
            <div className="inline-flex rounded-lg bg-slate-900 p-1">
              {(['bullish', 'bearish', 'neutral', 'volatile'] as const).map((out) => (
                <button
                  key={out}
                  onClick={() => onOutlookChange(out)}
                  className={`px-3 py-1.5 rounded-md text-xs font-bold capitalize transition cursor-pointer ${
                    selectedOutlook === out ? 'bg-indigo-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {out}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">IV Regime</label>
            <div className="inline-flex rounded-lg bg-slate-900 p-1">
              {(['low', 'moderate', 'high'] as const).map((iv) => (
                <button
                  key={iv}
                  onClick={() => setIvEnv(iv)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-bold capitalize transition cursor-pointer ${
                    ivEnv === iv ? 'bg-amber-500 text-slate-950 shadow' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {iv}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1 min-w-[140px]">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Available Capital (₹)</label>
            <div className="relative flex items-center">
              <span className="absolute left-2.5 text-xs font-bold text-slate-400">₹</span>
              <input
                type="number"
                step="25000"
                value={capital}
                onChange={(e) => onCapitalChange(parseFloat(e.target.value) || 0)}
                className="w-full pl-6 pr-3 py-1.5 bg-slate-900 text-white font-mono font-bold text-xs rounded-lg border border-slate-700 outline-none focus:border-indigo-500"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Main Grid: Left Strategy Selector | Right Payoff Visualizer */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Left List */}
        <div className="lg:col-span-5 space-y-4">
          <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500 px-1">
            <span>Recommended Structures ({recommendations.length})</span>
            <span className="text-indigo-600">ranked by thesis match</span>
          </div>

          <div className="space-y-3">
            {recommendations.map((rec) => {
              const isSelected = rec.id === activeStrategy?.id;
              const isAffordable = capital >= (rec.legs.filter(l => l.action === 'SELL').length * 75000 || 25000);

              return (
                <div
                  key={rec.id}
                  onClick={() => setActiveStrategyId(rec.id)}
                  className={`p-5 rounded-2xl border-2 transition cursor-pointer text-left relative ${
                    isSelected ? 'bg-indigo-50/60 border-indigo-600 shadow-md ring-2 ring-indigo-500/20' :
                    'bg-white border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-900 text-base">{rec.name}</span>
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase ${
                          rec.riskProfile === 'Defined Risk' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
                        }`}>
                          {rec.riskProfile}
                        </span>
                      </div>
                      <p className="text-xs text-slate-600 mt-1 leading-relaxed">
                        {rec.rationale}
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-slate-200/60 text-xs font-semibold">
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase">Net Flow</span>
                      <span className={rec.netPremium <= 0 ? 'text-emerald-600 font-mono font-bold' : 'text-rose-600 font-mono font-bold'}>
                        {rec.netPremium <= 0 ? `Credit ₹${Math.abs(Math.round(rec.netPremium * 25))}` : `Debit ₹${Math.round(rec.netPremium * 25)}`}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase">Max Profit</span>
                      <span className="text-emerald-700 font-mono font-bold">{rec.maxProfit}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block text-[10px] uppercase">Max Loss</span>
                      <span className={rec.maxLoss === 'Unlimited' ? 'text-rose-600 font-bold' : 'text-slate-800 font-mono font-bold'}>
                        {rec.maxLoss}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Detail Pane */}
        {activeStrategy && (
          <div className="lg:col-span-7 bg-white rounded-2xl border border-slate-200 p-6 shadow-sm flex flex-col space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-100">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-xl font-black text-slate-900">{activeStrategy.name}</h3>
                  <span className="px-2.5 py-0.5 rounded-full bg-blue-100 text-blue-800 text-xs font-bold">
                    PoP: {activeStrategy.probabilityOfProfit}%
                  </span>
                </div>
                <div className="text-xs text-slate-500 font-mono mt-1">
                  Breakeven Strikes: {activeStrategy.breakevens.map(b => `₹${Math.round(b)}`).join(' & ')}
                </div>
              </div>

              <div className="text-right">
                <span className="text-xs text-slate-400 block uppercase font-semibold">Est. Margin Req</span>
                <span className={`text-sm font-bold font-mono ${capital < estimatedCapitalReq ? 'text-rose-600' : 'text-emerald-700'}`}>
                  ₹{estimatedCapitalReq.toLocaleString()}
                </span>
              </div>
            </div>

            {/* Payoff Curve Chart */}
            <div className="space-y-2 flex-1">
              <div className="flex items-center justify-between text-xs font-bold uppercase text-slate-500">
                <span>Expiration Payoff Curve (Lot Size = 25)</span>
                <span className="text-emerald-600 font-mono">Spot ₹{spot}</span>
              </div>
              <div className="w-full h-[320px] bg-slate-900 rounded-xl p-4 border border-slate-800">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={payoffPoints} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <defs>
                      <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10B981" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#EF4444" stopOpacity={0.4} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.5} />
                    <XAxis
                      dataKey="price"
                      stroke="#94a3b8"
                      fontSize={11}
                      tickFormatter={(val) => `₹${val}`}
                    />
                    <YAxis
                      stroke="#94a3b8"
                      fontSize={11}
                      tickFormatter={(val) => `₹${(val / 1000).toFixed(0)}k`}
                    />
                    <Tooltip
                      content={({ active, payload }) => {
                        if (!active || !payload || !payload.length) return null;
                        const pt = payload[0].payload;
                        return (
                          <div className="bg-slate-950 text-white p-3 rounded-xl border border-slate-700 text-xs space-y-1 shadow-2xl">
                            <div className="font-bold text-indigo-300 border-b border-slate-800 pb-1">
                              Underlying at Expiry: ₹{pt.price}
                            </div>
                            <div className={`font-mono text-sm font-bold ${pt.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                              Net Expiry P&amp;L: {pt.pnl >= 0 ? `+₹${pt.pnl.toLocaleString()}` : `-₹${Math.abs(pt.pnl).toLocaleString()}`}
                            </div>
                          </div>
                        );
                      }}
                    />
                    <ReferenceLine y={0} stroke="#cbd5e1" strokeWidth={1.5} strokeDasharray="4 4" />
                    <ReferenceLine x={spot} stroke="#3B82F6" strokeWidth={2} label={{ value: 'SPOT', fill: '#60A5FA', fontSize: 10, position: 'insideTopLeft' }} />
                    <Area
                      type="monotone"
                      dataKey="pnl"
                      stroke="#34D399"
                      strokeWidth={2.5}
                      fillOpacity={1}
                      fill="url(#pnlGrad)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Leg Execution Matrix */}
            <div className="space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                <Sliders className="w-4 h-4 text-indigo-600" /> Leg Execution Matrix (1 Lot = 25 Qty)
              </h4>
              <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full text-xs text-left">
                  <thead className="bg-slate-50 text-slate-600 uppercase font-bold border-b border-slate-200">
                    <tr>
                      <th className="p-3">Action</th>
                      <th className="p-3">Option Type</th>
                      <th className="p-3">Strike Price</th>
                      <th className="p-3">Est. Premium</th>
                      <th className="p-3">Lot Ratio</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 font-medium text-slate-800">
                    {activeStrategy.legs.map((leg, idx) => (
                      <tr key={idx} className="hover:bg-slate-50/50">
                        <td className="p-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            leg.action === 'BUY' ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'
                          }`}>
                            {leg.action}
                          </span>
                        </td>
                        <td className="p-3 font-bold">{leg.type}</td>
                        <td className="p-3 font-mono font-bold text-slate-900">₹{leg.strike}</td>
                        <td className="p-3 font-mono">₹{leg.premium}</td>
                        <td className="p-3">{leg.qtyRatio}x</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Defense Guide */}
            <div className="bg-amber-50/80 rounded-xl p-4 border border-amber-200 text-amber-950 text-xs flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <span className="font-bold block uppercase tracking-wider text-[11px] text-amber-800">Desk Defense Rule</span>
                <p className="leading-relaxed font-medium">
                  {activeStrategy.adjustmentRule}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
