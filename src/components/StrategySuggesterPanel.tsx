import React, { useState, useMemo } from 'react';
import { OptionRow, RiskConfig } from '../types';
import { suggestStrategies, calculatePayoffCurve } from '../lib/analytics';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts';
import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart,Sliders } from 'lucide-react';

interface Props {
  rows: OptionRow[];
  spot: number;
  atmIV: number;
  riskConfig: RiskConfig;
  onRiskConfigChange: (val: any) => void;
  mockTrade: any;
  onMockTradeChange: (val: any) => void;
  selectedOutlook: 'bullish' | 'bearish' | 'neutral' | 'volatile';
  onOutlookChange: (val: 'bullish' | 'bearish' | 'neutral' | 'volatile') => void;
  pipelineRes?: any;
}

export const StrategySuggesterPanel: React.FC<Props> = ({
  rows,
  spot,
  atmIV,
  riskConfig,
  onRiskConfigChange,
  mockTrade,
  onMockTradeChange,
  selectedOutlook,
  onOutlookChange,
  pipelineRes,
}) => {
  const [ivEnv, setIvEnv] = useState<'low' | 'moderate' | 'high'>(
    atmIV > 16 ? 'high' : atmIV < 11 ? 'low' : 'moderate'
  );

  const recommendations = useMemo(() => {
    return suggestStrategies(rows, spot, selectedOutlook, ivEnv, riskConfig.lot_size);
  }, [rows, spot, selectedOutlook, ivEnv, riskConfig.lot_size]);

  const [activeStrategyId, setActiveStrategyId] = useState<string>(
    recommendations[0]?.id || 'iron_condor'
  );

  // Sync active strategy when list changes
  React.useEffect(() => {
    if (recommendations.length > 0 && !activeStrategyId) {
      setActiveStrategyId(recommendations[0].id);
    }
  }, [recommendations, activeStrategyId]);

  const activeStrategy = useMemo(() => {
    return recommendations.find((r) => r.id === activeStrategyId) || recommendations[0];
  }, [recommendations, activeStrategyId]);

  const payoffPoints = useMemo(() => {
    if (!activeStrategy) return [];
    return calculatePayoffCurve(activeStrategy.legs, spot, riskConfig.lot_size);
  }, [activeStrategy, spot, riskConfig.lot_size]);

  // Calculate required margin/capital rough estimate
  const estimatedCapitalReq = useMemo(() => {
    if (!activeStrategy) return 50000;
    const shortLegs = activeStrategy.legs.filter((l) => l.action === 'SELL').length;
    return Math.max(15000, shortLegs * 75000);
  }, [activeStrategy]);

  return (
    <div className="space-y-8">
      {/* Pipeline Strategy & RND Results */}
      {pipelineRes && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* News vs Market & Suggestion */}
          <div className="bg-gradient-to-br from-indigo-950 to-slate-900 rounded-2xl p-6 border border-slate-800 shadow-xl text-white">
            <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-400 mb-4">News vs Market View</h3>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700">
                <span className="text-[10px] uppercase text-slate-400 block mb-1">News Direction</span>
                <span className="text-xl font-black">{pipelineRes.comparison.news_dir}</span>
                <span className="text-xs text-slate-400 block mt-1">Bias {pipelineRes.bias > 0 ? '+' : ''}{pipelineRes.bias.toFixed(2)}</span>
              </div>
              <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700">
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Market Direction</span>
                <span className="text-xl font-black">{pipelineRes.comparison.market_dir}</span>
                <span className="text-xs text-slate-400 block mt-1">P(down) {(pipelineRes.comparison.market_prices_downside * 100).toFixed(0)}%</span>
              </div>
            </div>

            <div className={`p-4 rounded-xl text-sm font-semibold mb-6 border ${
              pipelineRes.comparison.relation.includes('CONFIRMED_UNDERPRICED') ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' :
              pipelineRes.comparison.relation.includes('DIVERGENT') ? 'bg-rose-500/20 text-rose-300 border-rose-500/30' :
              'bg-blue-500/20 text-blue-300 border-blue-500/30'
            }`}>
              <AlertCircle className="w-4 h-4 inline-block mr-2 -mt-0.5" />
              {pipelineRes.comparison.relation} — {pipelineRes.suggestion.why || pipelineRes.suggestion.edge_note}
            </div>

            <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-400 mb-3">Suggested Structure</h3>
            {pipelineRes.suggestion.action === 'TRADE' ? (
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                <div className="text-lg font-black text-amber-400 mb-2">{pipelineRes.suggestion.structure}</div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div><span className="text-slate-400 block text-[10px] uppercase">Direction</span><span className="font-bold">{pipelineRes.suggestion.direction}</span></div>
                  <div><span className="text-slate-400 block text-[10px] uppercase">Vol State</span><span className="font-bold">{pipelineRes.suggestion.vol_state}</span></div>
                  <div><span className="text-slate-400 block text-[10px] uppercase">Size Mult</span><span className="font-bold">{pipelineRes.suggestion.size_mult}</span></div>
                </div>
              </div>
            ) : (
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 text-lg font-black text-slate-300">
                {pipelineRes.suggestion.action}
              </div>
            )}
          </div>

          {/* RND Chart */}
          <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4">Risk-Neutral Distribution</h3>
            <div className="grid grid-cols-4 gap-2 mb-6">
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Exp. Move</span>
                <span className="text-base font-black">±{pipelineRes.rnd.sd.toFixed(0)}</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Below)</span>
                <span className="text-base font-black">{(pipelineRes.rnd.p_below_spot * 100).toFixed(0)}%</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Above)</span>
                <span className="text-base font-black">{(pipelineRes.rnd.p_above_spot * 100).toFixed(0)}%</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Skew</span>
                <span className="text-base font-black">{pipelineRes.rnd.skew > 0 ? '+' : ''}{pipelineRes.rnd.skew.toFixed(2)}</span>
              </div>
            </div>
            <div className="flex-1 w-full min-h-[140px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={pipelineRes.rnd.grid.map((strike: number, i: number) => ({ strike, dens: pipelineRes.rnd.dens[i] }))} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="rndGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#6366f1" stopOpacity={0.05} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="strike" stroke="#94a3b8" fontSize={10} tickFormatter={val => val.toFixed(0)} minTickGap={30} />
                  <Tooltip 
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    formatter={(val: number) => [val.toFixed(6), 'Density']}
                    labelFormatter={(label) => `Strike: ${Number(label).toFixed(0)}`}
                  />
                  <ReferenceLine x={pipelineRes.rnd.spot} stroke="#ef4444" strokeWidth={2} strokeDasharray="3 3" label={{ value: 'SPOT', fill: '#ef4444', fontSize: 10, position: 'top' }} />
                  <Area type="monotone" dataKey="dens" stroke="#6366f1" strokeWidth={2} fillOpacity={1} fill="url(#rndGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Complacency & Sizing Section */}
          <div className="lg:col-span-2 grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Complacency Gauge */}
            {pipelineRes.complacency && (
              <div className="bg-slate-50 rounded-2xl p-6 border border-slate-200 shadow-sm">
                <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4">Complacency Gauge</h3>
                <div className="flex items-center justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-end gap-2 mb-2">
                      <span className="text-4xl font-black">{pipelineRes.complacency.score.toFixed(1)}</span>
                      <span className="text-sm font-bold text-slate-400 mb-1">/ 100</span>
                    </div>
                    <div className={`text-sm font-bold uppercase px-2 py-1 rounded inline-block ${
                      pipelineRes.complacency.score >= 70 ? 'bg-rose-100 text-rose-700' :
                      pipelineRes.complacency.score >= 45 ? 'bg-amber-100 text-amber-700' :
                      'bg-emerald-100 text-emerald-700'
                    }`}>
                      {pipelineRes.complacency.label}
                    </div>
                  </div>
                  <div className="flex-1 text-xs text-slate-600 space-y-1">
                    <div className="flex justify-between"><span>IV Cheapness:</span><span className="font-bold">{pipelineRes.complacency.components.iv_cheapness.toFixed(2)}</span></div>
                    <div className="flex justify-between"><span>Put Writing:</span><span className="font-bold">{pipelineRes.complacency.components.put_writing.toFixed(2)}</span></div>
                    <div className="flex justify-between"><span>PCR Lean:</span><span className="font-bold">{pipelineRes.complacency.components.pcr_lean.toFixed(2)}</span></div>
                    <div className="flex justify-between"><span>Skew Flatness:</span><span className="font-bold">{pipelineRes.complacency.components.skew_flatness.toFixed(2)}</span></div>
                  </div>
                </div>
                <div className="mt-4 text-xs text-slate-500 italic border-t border-slate-200 pt-3">
                  {pipelineRes.complacency.reading}
                </div>
              </div>
            )}

            {/* Risk Sizing Decision */}
            {pipelineRes.sizing && (
              <div className={`rounded-2xl p-6 border shadow-sm ${
                pipelineRes.sizing.approved ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'
              }`}>
                <div className="flex items-center justify-between mb-4">
                  <h3 className={`text-sm font-bold uppercase tracking-wider ${
                    pipelineRes.sizing.approved ? 'text-emerald-700' : 'text-rose-700'
                  }`}>Risk Gate: <span className="text-slate-900 ml-1">{mockTrade.trade_structure || pipelineRes.suggestion?.structure || 'Suggested Trade'}</span></h3>
                  <div className={`px-3 py-1 rounded-full text-xs font-black uppercase ${
                    pipelineRes.sizing.approved ? 'bg-emerald-200 text-emerald-800' : 'bg-rose-200 text-rose-800'
                  }`}>
                    {pipelineRes.sizing.approved ? 'APPROVED' : 'VETOED'}
                  </div>
                </div>

                {pipelineRes.sizing.approved ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4">
                      <div className="flex-1 bg-white p-3 rounded-lg border border-emerald-100 text-center">
                        <span className="block text-[10px] uppercase text-emerald-600 font-bold mb-1">Max Lots</span>
                        <span className="text-3xl font-black text-emerald-700">{pipelineRes.sizing.lots}</span>
                      </div>
                      <div className="flex-1 bg-white p-3 rounded-lg border border-emerald-100 text-center">
                        <span className="block text-[10px] uppercase text-emerald-600 font-bold mb-1">Total Max Loss</span>
                        <span className="text-xl font-bold text-emerald-700">₹{(pipelineRes.sizing.trade_max_loss_rs * pipelineRes.sizing.lots).toLocaleString('en-IN')}</span>
                      </div>
                      <div className="flex-1 bg-white p-3 rounded-lg border border-emerald-100 text-center">
                        <span className="block text-[10px] uppercase text-emerald-600 font-bold mb-1">Est. Total Profit</span>
                        <span className="text-xl font-bold text-emerald-700">
                          {(() => {
                            const structName = mockTrade.trade_structure || pipelineRes.suggestion?.structure;
                            const sizedStrat = recommendations.find(r => r.name === structName) || activeStrategy;
                            if (!sizedStrat || sizedStrat.maxProfit === 'Unlimited') return 'Unlimited';
                            const pPerLot = parseInt(sizedStrat.maxProfit.replace(/[^0-9]/g, ''), 10);
                            return isNaN(pPerLot) ? 'N/A' : `₹${(pPerLot * pipelineRes.sizing.lots).toLocaleString('en-IN')}`;
                          })()}
                        </span>
                      </div>
                    </div>
                    <div className="text-xs text-emerald-800 font-medium">
                      Binding Constraint: <span className="font-bold uppercase">{pipelineRes.sizing.binding_constraint}</span>
                    </div>
                  </div>
                ) : (
                  <div className="bg-white p-4 rounded-xl border border-rose-200">
                    <div className="text-sm font-bold text-rose-800 mb-2">Trade Rejected</div>
                    <p className="text-xs text-rose-600 leading-relaxed font-medium">
                      {pipelineRes.sizing.reason}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Risk Budget Configuration */}
      <div className="bg-slate-900 text-white p-6 rounded-2xl shadow-lg border border-slate-800 space-y-6">
        <div className="flex items-center gap-2 text-amber-400 text-sm font-bold uppercase tracking-wider">
          <Shield className="w-5 h-5" /> Risk Budget & Sizing Constraints
        </div>
        
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Total Capital (₹)</label>
            <input
              type="number"
              value={riskConfig.capital}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, capital: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Risk Per Trade (%)</label>
            <input
              type="number"
              step="0.005"
              value={riskConfig.risk_per_trade_pct}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, risk_per_trade_pct: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Max Port Heat (%)</label>
            <input
              type="number"
              step="0.01"
              value={riskConfig.max_portfolio_heat_pct}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, max_portfolio_heat_pct: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Max Drawdown (%)</label>
            <input
              type="number"
              step="0.01"
              value={riskConfig.max_drawdown_pct}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, max_drawdown_pct: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Max Net Delta (Units)</label>
            <input
              type="number"
              value={riskConfig.max_net_delta_units}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, max_net_delta_units: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Max Net Vega (₹)</label>
            <input
              type="number"
              value={riskConfig.max_net_vega_rupees}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, max_net_vega_rupees: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Lot Size</label>
            <input
              type="number"
              value={riskConfig.lot_size}
              onChange={(e) => onRiskConfigChange({ ...riskConfig, lot_size: parseFloat(e.target.value) || 0 })}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
        </div>
      </div>

      {/* Mock Trade Inputs */}
      <div className="bg-slate-50 p-6 rounded-2xl shadow-sm border border-slate-200 space-y-6">
        <div className="flex items-center gap-2 text-slate-700 text-sm font-bold uppercase tracking-wider">
          <Sliders className="w-5 h-5 text-indigo-500" /> Mock Trade Inputs (Stress Testing)
        </div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          <div className="flex flex-col gap-1 md:col-span-2">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Trade Structure Name</label>
            <select
              value={recommendations.find(r => r.name === mockTrade.trade_structure)?.id || mockTrade.trade_structure}
              onChange={(e) => {
                const val = e.target.value;
                const rec = recommendations.find((r) => r.id === val);
                
                if (rec) {
                  let lossPts = 500; // default for undefined risk
                  if (rec.maxLoss !== 'Unlimited') {
                    const lossRs = parseInt(rec.maxLoss.replace(/[^0-9]/g, ''), 10);
                    if (!isNaN(lossRs) && riskConfig.lot_size > 0) {
                      lossPts = Math.round(lossRs / riskConfig.lot_size);
                    }
                  }
                  
                  // Keep hardcoded greeks for testing until pricer is built
                  const stratMap: Record<string, {delta: number, vega: number}> = {
                    'iron_condor': { delta: 5, vega: -1500 },
                    'short_strangle': { delta: 10, vega: -3000 },
                    'bull_put_spread': { delta: 25, vega: -1200 },
                    'bear_call_spread': { delta: -25, vega: -1200 },
                    'bull_call_spread': { delta: 45, vega: 1800 },
                    'bear_put_spread': { delta: -45, vega: 1800 }
                  };
                  const greeks = stratMap[rec.id] || { delta: 0, vega: 0 };

                  onMockTradeChange({
                    ...mockTrade,
                    trade_structure: rec.name,
                    is_premium_sell: rec.netPremium < 0,
                    trade_max_loss_pts: lossPts,
                    trade_delta: greeks.delta,
                    trade_vega: greeks.vega
                  });
                } else {
                  onMockTradeChange({ ...mockTrade, trade_structure: val });
                }
              }}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none w-full"
            >
              <option value="">Custom (Manual Entry)</option>
              {recommendations.map(r => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Is Premium Sell?</label>
            <select
              value={mockTrade.is_premium_sell ? 'yes' : 'no'}
              onChange={(e) => onMockTradeChange({ ...mockTrade, is_premium_sell: e.target.value === 'yes' })}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none"
            >
              <option value="no">No</option>
              <option value="yes">Yes</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Current Drawdown (%)</label>
            <input
              type="number"
              step="0.01"
              value={mockTrade.drawdown_pct}
              onChange={(e) => onMockTradeChange({ ...mockTrade, drawdown_pct: parseFloat(e.target.value) || 0 })}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Trade Max Loss (Pts)</label>
            <input
              type="number"
              value={mockTrade.trade_max_loss_pts}
              onChange={(e) => onMockTradeChange({ ...mockTrade, trade_max_loss_pts: parseFloat(e.target.value) || 0 })}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Trade Delta (Net)</label>
            <input
              type="number"
              value={mockTrade.trade_delta}
              onChange={(e) => onMockTradeChange({ ...mockTrade, trade_delta: parseFloat(e.target.value) || 0 })}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-500 px-1">Trade Vega (₹)</label>
            <input
              type="number"
              value={mockTrade.trade_vega}
              onChange={(e) => onMockTradeChange({ ...mockTrade, trade_vega: parseFloat(e.target.value) || 0 })}
              className="bg-white p-2 rounded-lg border border-slate-300 text-sm focus:border-indigo-500 outline-none"
            />
          </div>
        </div>
      </div>

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
              const isAffordable = riskConfig.capital >= (rec.legs.filter(l => l.action === 'SELL').length * 75000 || 25000);

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
                <span className={`text-sm font-bold font-mono ${riskConfig.capital < estimatedCapitalReq ? 'text-rose-600' : 'text-emerald-700'}`}>
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
