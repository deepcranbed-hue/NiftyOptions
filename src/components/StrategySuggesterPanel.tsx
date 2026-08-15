import React, { useState, useMemo } from 'react';
import { OptionRow, RiskConfig } from '../types';
import { suggestStrategies, calculatePayoffCurve } from '../lib/analytics';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts';
import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart, Sliders, ChevronDown, ChevronRight, Database, Play, Trash2, RefreshCw, Plus, Zap } from 'lucide-react';
import { FormulaTooltip } from './FormulaTooltip';
import { LegExecutionMatrix } from './LegExecutionMatrix';
import { ProvenanceBadge } from './ProvenanceBadge';

interface Props {
  rows: OptionRow[];
  spot: number;
  atmIV: number;
  riskConfig: RiskConfig;
  captureId?: string;
  onRiskConfigChange: (val: any) => void;
  mockTrade: any;
  onMockTradeChange: (val: any) => void;
  selectedOutlook: 'bullish' | 'bearish' | 'neutral' | 'volatile';
  onOutlookChange: (val: 'bullish' | 'bearish' | 'neutral' | 'volatile') => void;
  pipelineRes?: any;
  optWeights: {ev: number, pop: number, rr: number, oi: number};
  setOptWeights: (w: any) => void;
  optBias: number;
  setOptBias: (b: number) => void;
  optMinPop: number;
  setOptMinPop: (p: number) => void;
  optAllowUndefined: boolean;
  setOptAllowUndefined: (b: boolean) => void;
  optCostPerLeg: number;
  setOptCostPerLeg: (n: number) => void;
  optWindowPts: number;
  setOptWindowPts: (n: number) => void;
  optMaxWing: number;
  setOptMaxWing: (n: number) => void;
  optTopN: number;
  setOptTopN: (n: number) => void;
  optMaxLossBudget: number;
  setOptMaxLossBudget: (n: number) => void;
  optAllowBadRnd: boolean;
  setOptAllowBadRnd: (b: boolean) => void;
  onRunPipeline: () => void;
  uploadFile?: File | null;
  setUploadFile?: (f: File | null) => void;
  uploadSpot?: number;
  setUploadSpot?: (n: number) => void;
  uploadExpiryDate?: string;
  setUploadExpiryDate?: (s: string) => void;
  uploadVix?: number;
  setUploadVix?: (n: number | undefined) => void;
  onUploadPipeline?: () => void;
  
  // Comparative RND and capture controls props
  currentPipelineRes?: any;
  captures?: any[];
  optionChainMode?: 'historical' | 'live';
  setOptionChainMode?: (mode: 'historical' | 'live') => void;
  selectedDate?: string;
  setSelectedDate?: (d: string) => void;
  selectedCaptureId?: string;
  setSelectedCaptureId?: (id: string) => void;
  loadSelectedCapture?: (id: string) => void;
  handleDeleteCapture?: () => void;
  isPipelineRunning?: boolean;
  onRefreshCaptures?: () => void;
  breezeExpiry?: string;
  setBreezeExpiry?: (s: string) => void;
  oiPanel?: React.ReactNode;
  volPanel?: React.ReactNode;
}

export const StrategySuggesterPanel: React.FC<Props> = ({
  rows,
  spot,
  atmIV,
  riskConfig,
  captureId,
  onRiskConfigChange,
  mockTrade,
  onMockTradeChange,
  selectedOutlook,
  onOutlookChange,
  pipelineRes,
  optWeights, setOptWeights, optBias, setOptBias, optMinPop, setOptMinPop, optAllowUndefined, setOptAllowUndefined,
  optCostPerLeg, setOptCostPerLeg, optWindowPts, setOptWindowPts, optMaxWing, setOptMaxWing, optTopN, setOptTopN, optMaxLossBudget, setOptMaxLossBudget, optAllowBadRnd, setOptAllowBadRnd, onRunPipeline, uploadFile, setUploadFile, uploadSpot, setUploadSpot, uploadExpiryDate, setUploadExpiryDate, uploadVix, setUploadVix, onUploadPipeline,
  currentPipelineRes, captures = [], optionChainMode = 'historical', setOptionChainMode, selectedDate = '', setSelectedDate, selectedCaptureId = '', setSelectedCaptureId, loadSelectedCapture, handleDeleteCapture, isPipelineRunning, onRefreshCaptures,
  breezeExpiry = '2026-07-09T06:00:00.000Z', setBreezeExpiry,
  oiPanel, volPanel
}) => {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [subTab, setSubTab] = useState<'directional' | 'optimizer' | 'rnd' | 'oi' | 'vol'>('directional');
  const [expiryOptions, setExpiryOptions] = useState<string[]>([]);
  const [isSyncing, setIsSyncing] = useState(false);
  const [selectedTime, setSelectedTime] = useState<string>("15:30");

  React.useEffect(() => {
    const activeCap = captures.find(c => c.capture_id.toString() === selectedCaptureId);
    if (activeCap) {
      const labelTime = activeCap.label ? activeCap.label.slice(11, 16) : null;
      if (labelTime) {
        setSelectedTime(labelTime);
      }
    }
  }, [selectedCaptureId, captures]);

  React.useEffect(() => {
    const fetchExpiries = async () => {
      try {
        const res = await fetch('/api/exchange-expiries');
        const json = await res.json();
        if (json.success && json.expiries && json.expiries.length > 0) {
          // Only show expiries that have NOT already passed — an expired contract
          // (e.g. 7 Jul when today is 12 Jul) can't be traded going forward.
          const todayYMD = new Date().toISOString().slice(0, 10);
          const live = json.expiries.filter((e: string) => String(e).slice(0, 10) >= todayYMD);
          const opts = live.length > 0 ? live : json.expiries;   // fall back if all past
          setExpiryOptions(opts);
          const stored = localStorage.getItem('breezeExpiryDate');
          if (!stored || stored === '2026-07-09T06:00:00.000Z' || !opts.includes(stored)) {
            setBreezeExpiry?.(opts[0]);
            localStorage.setItem('breezeExpiryDate', opts[0]);
          }
        }
      } catch (e) {
        console.error("Failed to load exchange expiries in suggester panel", e);
      }
    };
    fetchExpiries();
  }, []);

  React.useEffect(() => {
    localStorage.setItem('breezeExpiryDate', breezeExpiry);
  }, [breezeExpiry]);

  const provRecords = pipelineRes?.provenance?.records || [];
  const getProvenance = (componentName: string) => 
    provRecords.find((r: any) => r.component === componentName);
  const [ivEnv, setIvEnv] = useState<'low' | 'moderate' | 'high'>(
    atmIV > 16 ? 'high' : atmIV < 11 ? 'low' : 'moderate'
  );

  const [customStrikes, setCustomStrikes] = useState<Record<string, number[]>>({});

  const recommendations = useMemo(() => {
    if (pipelineRes?.strategy_suggestion?.suggestions) {
      return pipelineRes.strategy_suggestion.suggestions.map((s: any) => ({
        id: s.family,
        name: s.action + " " + s.family.replace(/_/g, " "),
        rationale: s.rationale,
        riskProfile: (s.family.includes("strangle") && s.action === "SELL") || (s.family.includes("straddle") && s.action === "SELL") ? "Undefined Risk" : "Defined Risk",
        caution: s.caution,
        fits: s.fits,
        isMacro: true
      }));
    }
    // Fallback if pipeline not run
    return suggestStrategies(rows, spot, selectedOutlook, ivEnv, riskConfig.lot_size, customStrikes).map(r => ({...r, fits: [], caution: "", isMacro: false}));
  }, [pipelineRes, rows, spot, selectedOutlook, ivEnv, riskConfig.lot_size, customStrikes]);

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

  const activeRndRes = currentPipelineRes; // Historical Selected Capture RND
  const latestRndRes = pipelineRes;        // Current/Latest Capture RND

  const formatExpiry = (isoStr?: string) => {
    if (!isoStr) return 'N/A';
    try {
      const d = new Date(isoStr);
      return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    } catch (e) {
      return isoStr.split('T')[0];
    }
  };

  return (
    <div className="space-y-8">

      {/* Date Ingest & Historical Selectors */}
      <div className="bg-slate-950 text-white p-6 rounded-2xl shadow-lg border border-slate-800 space-y-4">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
                <Database className="w-5 h-5" /> Option Chain Selector
              </h3>
              
              <div className="flex bg-slate-900 p-0.5 rounded-lg border border-slate-800">
                <button
                  onClick={() => setOptionChainMode?.('historical')}
                  className={`px-2.5 py-0.75 rounded-md text-[10px] font-bold transition-all ${
                    optionChainMode === 'historical'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  Historical Backfill
                </button>
                <button
                  onClick={() => setOptionChainMode?.('live')}
                  className={`px-2.5 py-0.75 rounded-md text-[10px] font-bold transition-all ${
                    optionChainMode === 'live'
                      ? 'bg-emerald-600 text-white shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  Live Ingest
                </button>
              </div>
            </div>
            <p className="text-[11px] text-slate-400 mt-0.5">Select a capture from {optionChainMode === 'live' ? 'live' : 'historical backfilled'} data to analyze or compare its RND profile side-by-side.</p>
          </div>
          
          <div className="flex flex-wrap items-center gap-2">
            {captures.length > 0 && (() => {
              // Lookup closest capture matching local inputs
              const triggerClosestLookup = (newDate: string, newTime: string) => {
                const targetDtStr = `${newDate}T${newTime}:00`;
                const targetTimeMs = new Date(targetDtStr).getTime();
                
                let closestCap = null;
                let minDiff = Infinity;
                
                // Filter captures belonging to the selected date
                const dateCaps = captures.filter(c => c.captured_at?.startsWith(newDate));
                if (dateCaps.length > 0) {
                  for (const c of dateCaps) {
                    const capTimeMs = new Date(c.captured_at).getTime();
                    const diff = Math.abs(capTimeMs - targetTimeMs);
                    if (diff < minDiff) {
                      minDiff = diff;
                      closestCap = c;
                    }
                  }
                } else {
                  // Fallback to searching all captures if no exact date match
                  for (const c of captures) {
                    const capTimeMs = new Date(c.captured_at).getTime();
                    const diff = Math.abs(capTimeMs - targetTimeMs);
                    if (diff < minDiff) {
                      minDiff = diff;
                      closestCap = c;
                    }
                  }
                }

                if (closestCap) {
                  setSelectedCaptureId?.(closestCap.capture_id.toString());
                  loadSelectedCapture?.(closestCap.capture_id.toString());
                }
              };

              return (
                <div className="flex items-center gap-1.5">
                  <input 
                    type="date"
                    value={selectedDate}
                    onChange={(e) => {
                      const newDate = e.target.value;
                      setSelectedDate?.(newDate);
                      triggerClosestLookup(newDate, selectedTime);
                    }}
                    className="bg-slate-800 text-slate-300 text-xs px-2.5 py-1.5 rounded-xl border border-slate-700 outline-none font-bold"
                  />

                  <div className="flex items-center gap-1 bg-slate-800 rounded-xl border border-slate-700 px-2.5 py-1.5">
                    <label className="text-[10px] uppercase font-bold text-slate-500">Time</label>
                    <input 
                      type="time" 
                      value={selectedTime}
                      onChange={(e) => {
                        const newTime = e.target.value;
                        setSelectedTime(newTime);
                        triggerClosestLookup(selectedDate, newTime);
                      }}
                      className="bg-transparent text-slate-300 text-xs outline-none font-mono"
                    />
                  </div>
                  
                  {onRefreshCaptures && (
                    <button
                      onClick={onRefreshCaptures}
                      className="p-1.5 rounded-xl bg-slate-700 text-slate-300 hover:bg-slate-600 transition cursor-pointer"
                      title="Refresh snapshots list"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  
                  {handleDeleteCapture && (
                    <button
                      onClick={handleDeleteCapture}
                      disabled={!selectedCaptureId}
                      className="p-1.5 rounded-xl bg-rose-900/40 text-rose-400 hover:bg-rose-900/60 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                      title="Delete selected capture"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              );
            })()}

            <select
              value={breezeExpiry}
              onChange={(e) => setBreezeExpiry(e.target.value)}
              title="Breeze Option Expiry Date"
              className="bg-slate-800 text-slate-300 text-xs px-2.5 py-1.5 rounded-xl border border-slate-700 outline-none w-48 font-bold"
            >
              {expiryOptions.map(exp => (
                <option key={exp} value={exp}>
                  {new Date(exp).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })} ({exp.split('T')[0]})
                </option>
              ))}
              {expiryOptions.length === 0 && (
                <option value={breezeExpiry}>{breezeExpiry}</option>
              )}
            </select>

            <button
              onClick={() => {
                onRunPipeline();
              }}
              disabled={isPipelineRunning}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-white text-xs font-bold transition shadow-lg cursor-pointer ${
                isPipelineRunning ? 'bg-slate-600 cursor-not-allowed' : 'bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 shadow-emerald-500/30'
              }`}
            >
              <Play className={`w-3 h-3 ${isPipelineRunning ? 'animate-spin' : ''}`} />
              <span>{isPipelineRunning ? 'Running...' : 'Update Option Chain'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Suggester sub-tabs */}
      <div className="flex flex-wrap gap-1 bg-slate-100 p-1 rounded-xl border border-slate-200">
        {([
          ['directional', 'Directional Suggester'],
          ['optimizer', 'Strike Optimizer'],
          ['rnd', 'RND Comparison'],
          ['oi', 'OI'],
          ['vol', 'Vol'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setSubTab(key)}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition cursor-pointer ${
              subTab === key ? 'bg-indigo-600 text-white shadow' : 'text-slate-500 hover:bg-white'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {!pipelineRes ? (
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-10 text-center text-slate-400 mt-6 shadow-2xl flex flex-col items-center justify-center gap-4">
          <div className="p-4 bg-slate-950 rounded-full border border-slate-850 shadow-inner">
            <Zap className="w-8 h-8 text-indigo-400 animate-pulse" />
          </div>
          <div>
            <h4 className="text-white font-bold text-base">Strategy Optimizer is Idle</h4>
            <p className="text-xs text-slate-500 mt-1 max-w-sm">
              To keep your machine cool and save energy, calculations do not run automatically. Click below or use the "Update Option Chain" button above to run the optimizer.
            </p>
          </div>
          <button
            onClick={() => onRunPipeline()}
            className="mt-2 inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-white text-xs font-bold bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 shadow-lg shadow-indigo-500/20 cursor-pointer transition"
          >
            <Play className="w-3.5 h-3.5" />
            <span>Run Strategy Optimizer</span>
          </button>
        </div>
      ) : (
        <>
          {/* RND Comparison Plots — RND Comparison tab */}
          {subTab === 'rnd' && latestRndRes && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left RND Plot: Current/Latest Date */}
          <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-4 flex flex-wrap items-center gap-2">
              <span className="bg-indigo-100 text-indigo-800 text-[10px] px-2 py-0.5 rounded-full font-bold">CURRENT</span>
              <span>Risk-Neutral Distribution (RND)</span>
              <span className="bg-slate-100 text-slate-600 text-[10px] px-2 py-0.5 rounded-full font-semibold">Expiry: {formatExpiry(latestRndRes?.chain_meta?.expiry)}</span>
              {latestRndRes.formulas?.rnd && <FormulaTooltip trace={latestRndRes.formulas.rnd} />}
            </h3>
            
            <div className="grid grid-cols-5 gap-2 mb-6">
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Spot Price</span>
                <span className="text-sm font-black text-indigo-600">₹{latestRndRes.rnd.spot.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Exp. Move</span>
                <span className="text-sm font-black">±{latestRndRes.rnd.sd.toFixed(0)}</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Below)</span>
                <span className="text-sm font-black">{(latestRndRes.rnd.p_below_spot * 100).toFixed(0)}%</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Above)</span>
                <span className="text-sm font-black">{(latestRndRes.rnd.p_above_spot * 100).toFixed(0)}%</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-400 block mb-1">Skew</span>
                <span className="text-sm font-black">{latestRndRes.rnd.skew > 0 ? '+' : ''}{latestRndRes.rnd.skew.toFixed(2)}</span>
              </div>
            </div>
            
            <div className="flex-1 w-full min-h-[140px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={latestRndRes.rnd.grid.map((strike: number, i: number) => ({ strike, dens: latestRndRes.rnd.dens[i] }))} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="currentRndGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#4f46e5" stopOpacity={0.05} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="strike" stroke="#94a3b8" fontSize={9} tickFormatter={val => val.toFixed(0)} minTickGap={30} />
                  <Tooltip 
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    formatter={(val: number) => [val.toFixed(6), 'Density']}
                    labelFormatter={(label) => `Strike: ${Number(label).toFixed(0)}`}
                  />
                  <ReferenceLine x={latestRndRes.rnd.spot} stroke="#ef4444" strokeWidth={2} strokeDasharray="3 3" label={{ value: 'SPOT', fill: '#ef4444', fontSize: 9, position: 'top' }} />
                  <Area type="monotone" dataKey="dens" stroke="#4f46e5" strokeWidth={2} fillOpacity={1} fill="url(#currentRndGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-4 flex flex-wrap items-center gap-2">
              <span className="bg-amber-100 text-amber-800 text-[10px] px-2 py-0.5 rounded-full font-bold">HISTORICAL</span>
              <span>RND ({selectedDate || 'Select historical date above'})</span>
              {activeRndRes && (
                <span className="bg-amber-50 text-amber-700 text-[10px] px-2 py-0.5 rounded-full font-semibold border border-amber-200">
                  Expiry: {formatExpiry(activeRndRes?.chain_meta?.expiry)}
                </span>
              )}
            </h3>
            
            {activeRndRes && selectedCaptureId ? (
              <>
                <div className="grid grid-cols-5 gap-2 mb-6">
                  <div>
                    <span className="text-[10px] uppercase text-slate-400 block mb-1">Spot Price</span>
                    <span className="text-sm font-black text-amber-600">₹{activeRndRes.rnd.spot.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-400 block mb-1">Exp. Move</span>
                    <span className="text-sm font-black">±{activeRndRes.rnd.sd.toFixed(0)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Below)</span>
                    <span className="text-sm font-black">{(activeRndRes.rnd.p_below_spot * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-400 block mb-1">P(Above)</span>
                    <span className="text-sm font-black">{(activeRndRes.rnd.p_above_spot * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-400 block mb-1">Skew</span>
                    <span className="text-sm font-black">{activeRndRes.rnd.skew > 0 ? '+' : ''}{activeRndRes.rnd.skew.toFixed(2)}</span>
                  </div>
                </div>
                
                <div className="flex-1 w-full min-h-[140px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={activeRndRes.rnd.grid.map((strike: number, i: number) => ({ strike, dens: activeRndRes.rnd.dens[i] }))} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="selectedRndGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.05} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                      <XAxis dataKey="strike" stroke="#94a3b8" fontSize={9} tickFormatter={val => val.toFixed(0)} minTickGap={30} />
                      <Tooltip 
                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                        formatter={(val: number) => [val.toFixed(6), 'Density']}
                        labelFormatter={(label) => `Strike: ${Number(label).toFixed(0)}`}
                      />
                      <ReferenceLine x={activeRndRes.rnd.spot} stroke="#ef4444" strokeWidth={2} strokeDasharray="3 3" label={{ value: 'SPOT', fill: '#ef4444', fontSize: 9, position: 'top' }} />
                      <Area type="monotone" dataKey="dens" stroke="#f59e0b" strokeWidth={2} fillOpacity={1} fill="url(#selectedRndGrad)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center border border-dashed border-slate-200 rounded-xl min-h-[140px] text-slate-400 p-4 text-center">
                <Sliders className="w-8 h-8 mb-2 opacity-50 animate-pulse text-indigo-500" />
                <span className="text-xs font-semibold">No historical date/time selected</span>
                <span className="text-[10px] text-slate-400 mt-1">Select a calendar date and time from the snapshot controls above to overlay historical distribution parameters</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Pipeline Strategy & Analysis Side-by-Side — Directional Suggester tab */}
      {subTab === 'directional' && (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Column: News vs Market & Suggestion */}
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

            {pipelineRes.comparison.flow_divergence && (
              <div className="p-4 rounded-xl text-sm font-semibold mb-6 border bg-amber-500/20 text-amber-300 border-amber-500/30">
                <AlertCircle className="w-4 h-4 inline-block mr-2 -mt-0.5" />
                {pipelineRes.comparison.flow_divergence}
              </div>
            )}

            <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-400 mb-3">Recommended Family</h3>
            {pipelineRes.suggestion.suggestions && pipelineRes.suggestion.suggestions.length > 0 ? (
              <div className="space-y-3">
                {pipelineRes.suggestion.suggestions.map((s: any, idx: number) => (
                  <div key={idx} className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <div className="flex justify-between items-center mb-2">
                      <div className="text-lg font-black text-amber-400">{s.family} <span className="text-xs text-slate-400 font-normal">({s.action})</span></div>
                    </div>
                    <p className="text-sm text-slate-300">{s.rationale}</p>
                    {s.caution && <div className="mt-2 text-xs text-rose-400 font-semibold flex gap-1 items-start"><AlertCircle className="w-3 h-3 mt-0.5 shrink-0"/> {s.caution}</div>}
                  </div>
                ))}
                {pipelineRes.suggestion.notes && pipelineRes.suggestion.notes.map((n: string, idx: number) => (
                  <div key={idx} className="text-xs text-amber-300 bg-amber-500/10 p-2 rounded border border-amber-500/20 flex gap-2">
                    <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                    <span>{n}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 text-lg font-black text-slate-300">
                STAND ASIDE
              </div>
            )}
          </div>

          {/* Right Column: Volatility Attribution */}
          {pipelineRes.vol_attribution && (
            <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
                <span>Volatility Attribution</span>
              </h3>
              
              <div className="flex items-center gap-4 mb-4">
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase text-slate-400 font-bold">Chain ATM IV</span>
                  <span className="text-xl font-black text-slate-800">{pipelineRes.vol_attribution.chain_atm_iv_pct}%</span>
                </div>
                <div className="h-8 w-px bg-slate-200"></div>
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase text-slate-400 font-bold">India VIX (News)</span>
                  <span className="text-xl font-black text-slate-800">{pipelineRes.vol_attribution.india_vix ? `${pipelineRes.vol_attribution.india_vix}%` : 'N/A'}</span>
                </div>
                {pipelineRes.vol_attribution.iv_vs_vix_gap && (
                  <>
                    <div className="h-8 w-px bg-slate-200"></div>
                    <div className="flex flex-col">
                      <span className="text-[10px] uppercase text-slate-400 font-bold">Gap</span>
                      <span className="text-xl font-black text-amber-500">+{pipelineRes.vol_attribution.iv_vs_vix_gap}%</span>
                    </div>
                  </>
                )}
              </div>

              <div className="space-y-3 mt-2 flex-1 overflow-y-auto max-h-[400px]">
                {pipelineRes.vol_attribution.causes.map((c: any, i: number) => (
                  <div key={i} className={`p-3 rounded-lg border ${c.harvestable === true ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'}`}>
                    <div className="flex items-center justify-between mb-1">
                      <span className={`text-xs font-bold uppercase tracking-wider ${c.harvestable === true ? 'text-emerald-700' : 'text-rose-700'}`}>
                        {c.cause.replace(/_/g, ' ')}
                      </span>
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${c.harvestable === 'conditionally' ? 'bg-amber-100 text-amber-800' : c.harvestable === true ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>
                        {c.harvestable === 'conditionally' ? 'TRAP / CONDITIONALLY HARVESTABLE' : c.harvestable === true ? 'HARVESTABLE' : 'TRAP'}
                      </span>
                    </div>
                    <p className="text-sm text-slate-700 mb-2">{c.detail}</p>
                    {c.warning && <p className="text-xs font-medium text-slate-500 italic">"{c.warning}"</p>}
                  </div>
                ))}
              </div>

              <div className={`mt-4 p-3 rounded-lg border ${pipelineRes.vol_attribution.sell_premium_verdict.startsWith('CAUTION') ? 'bg-rose-50 border-rose-200 text-rose-800' : 'bg-emerald-50 border-emerald-200 text-emerald-800'} text-sm font-semibold`}>
                {pipelineRes.vol_attribution.sell_premium_verdict}
              </div>
            </div>
          )}
        </div>
      )}
      

      {/* Settings Panel — Strike Optimizer tab */}
      {subTab === 'optimizer' && (<>
      <div className="lg:col-span-2 mb-2 rounded-lg border border-slate-700/50 bg-slate-800/30 overflow-hidden">
            <button 
              onClick={() => setSettingsOpen(!settingsOpen)}
              className="w-full flex items-center justify-between p-3 bg-slate-800/80 hover:bg-slate-700/80 transition-colors"
            >
              <div className="flex items-center space-x-2 text-sm font-medium text-slate-200">
                <Sliders className="w-4 h-4 text-slate-400" />
                <span>Optimizer Settings</span>
              </div>
              {settingsOpen ? <ChevronDown className="w-4 h-4 text-slate-400"/> : <ChevronRight className="w-4 h-4 text-slate-400"/>}
            </button>
            
            {settingsOpen && (
              <div className="p-4 space-y-5 border-t border-slate-700/50 bg-slate-900/50">
                {/* Weights */}
                <div>
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Objective Blending Weights</div>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    {['ev', 'pop', 'rr', 'oi'].map((key) => (
                      <div key={key} className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span className="uppercase">{key} Weight</span>
                          <span>{optWeights[key as keyof typeof optWeights].toFixed(2)}</span>
                        </div>
                        <input type="range" min="0" max="1" step="0.05"
                          value={optWeights[key as keyof typeof optWeights]}
                          onChange={e => setOptWeights({...optWeights, [key]: parseFloat(e.target.value)})}
                          className="w-full accent-indigo-500"
                        />
                      </div>
                    ))}
                  </div>
                </div>
                
                {/* Bias & Limits */}
                <div className="pt-2 border-t border-slate-700/50">
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Constraints & Bias</div>
                  
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-6">
                    <div className="space-y-4">
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span>Directional Bias (Tilted View)</span>
                          <span className={optBias > 0 ? "text-emerald-400 font-bold" : optBias < 0 ? "text-rose-400 font-bold" : ""}>{optBias.toFixed(2)}</span>
                        </div>
                        <input type="range" min="-1" max="1" step="0.05"
                          value={optBias}
                          onChange={e => setOptBias(parseFloat(e.target.value))}
                          className="w-full accent-purple-500"
                        />
                        <div className="flex justify-between text-[10px] text-slate-500">
                          <span>Bearish (-1)</span>
                          <span>Neutral (0)</span>
                          <span>Bullish (+1)</span>
                        </div>
                      </div>

                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span>Min Probability of Profit (PoP)</span>
                          <span>{optMinPop.toFixed(2)}</span>
                        </div>
                        <input type="range" min="0" max="0.95" step="0.05"
                          value={optMinPop}
                          onChange={e => setOptMinPop(parseFloat(e.target.value))}
                          className="w-full accent-emerald-500"
                        />
                      </div>
                      
                      <div className="flex items-center justify-between pt-2">
                        <span className="text-xs text-slate-300">Allow Undefined Risk (e.g. naked short strangles)</span>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" className="sr-only peer" checked={optAllowUndefined} onChange={e => setOptAllowUndefined(e.target.checked)} />
                          <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-rose-500"></div>
                        </label>
                      </div>

                      <div className="flex items-center justify-between pt-2">
                        <span className="text-xs text-slate-300">Allow Bad RND (bypass optimizer block on stale/implausible density)</span>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" className="sr-only peer" checked={optAllowBadRnd} onChange={e => setOptAllowBadRnd(e.target.checked)} />
                          <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-amber-500"></div>
                        </label>
                      </div>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-400">
                          <span>Cost per Leg (pts)</span>
                          <span>{optCostPerLeg.toFixed(1)}</span>
                        </div>
                        <input type="range" min="0" max="50" step="1"
                          value={optCostPerLeg}
                          onChange={e => setOptCostPerLeg(parseFloat(e.target.value))}
                          className="w-full accent-blue-500"
                        />
                      </div>
                      
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-400">
                          <span>Search Window (pts)</span>
                          <span>{optWindowPts}</span>
                        </div>
                        <input type="range" min="100" max="2000" step="50"
                          value={optWindowPts}
                          onChange={e => setOptWindowPts(parseInt(e.target.value))}
                          className="w-full accent-blue-500"
                        />
                      </div>
                      
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-400">
                          <span>Max Wing Width</span>
                          <span>{optMaxWing}</span>
                        </div>
                        <input type="range" min="50" max="1000" step="50"
                          value={optMaxWing}
                          onChange={e => setOptMaxWing(parseInt(e.target.value))}
                          className="w-full accent-blue-500"
                        />
                      </div>
                      
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-400">
                          <span>Top N Results</span>
                          <span>{optTopN}</span>
                        </div>
                        <input type="range" min="1" max="20" step="1"
                          value={optTopN}
                          onChange={e => setOptTopN(parseInt(e.target.value))}
                          className="w-full accent-blue-500"
                        />
                      </div>
                      
                      <div className="space-y-1 col-span-2 mt-2">
                        <div className="flex justify-between text-xs text-slate-400 mb-1">
                          <span>Max Loss Budget Pts (0 = Auto from capital)</span>
                          <span className="font-bold text-amber-400">{optMaxLossBudget > 0 ? optMaxLossBudget.toFixed(0) : "Auto"}</span>
                        </div>
                        <input type="range" min="0" max="1000" step="10"
                          value={optMaxLossBudget}
                          onChange={e => setOptMaxLossBudget(parseFloat(e.target.value))}
                          className="w-full accent-amber-500"
                        />
                      </div>
                    </div>
                  </div>
                </div>
                

                <button 
                  onClick={() => {
                    if (onRunPipeline) onRunPipeline();
                  }}
                  className="w-full py-3 mt-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-bold tracking-wide transition-colors shadow-lg"
                >

                  Apply & Re-Run Pipeline
                </button>
              </div>
            )}
          </div>
          {/* Strike Optimizer Output */}

          {pipelineRes.optimizer && pipelineRes.optimizer.status === 'rnd_uncalibrated' && (
            <div className="lg:col-span-2 bg-amber-400 rounded-2xl p-6 border border-amber-500 shadow-xl text-black">
              <h3 className="text-sm font-bold uppercase tracking-wider text-black mb-2">⚠ Optimizer Blocked</h3>
              <p className="text-sm text-amber-950 font-medium">{pipelineRes.optimizer.rnd_warning}</p>
              <p className="text-xs text-amber-900 mt-4 font-semibold">You can bypass this by checking "Allow Bad RND" in the settings.</p>
            </div>
          )}

          {pipelineRes.optimizer && pipelineRes.optimizer.status === 'ok' && (
            <div className="lg:col-span-2 bg-gradient-to-br from-slate-900 to-indigo-950 rounded-2xl p-6 border border-slate-800 shadow-xl text-white">
              <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-400 mb-4 flex justify-between items-center">
                <span>Strike Optimizer (Top {pipelineRes.optimizer.ranked.length})</span>
                <span className="text-xs font-normal text-slate-400">Objective: {pipelineRes.optimizer.objective.toUpperCase()}</span>
              </h3>
              
              <div className="space-y-4">
                {pipelineRes.optimizer.ranked.map((strat: any, i: number) => (
                  <div key={i} className="bg-slate-800/80 rounded-xl p-4 border border-slate-700">
                    <div className="flex justify-between items-start mb-3">
                      <div>
                        <div className="font-bold text-lg text-amber-400 capitalize">{strat.kind.replace(/_/g, ' ')}</div>
                        <div className="text-xs text-slate-400 font-mono mt-1">
                          {Object.entries(strat.legs).map(([leg, px]) => `${leg} (₹${px})`).join(' | ')}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className={`px-2 py-1 rounded text-xs font-bold inline-block ${strat.sizing_gate?.approved ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
                          {strat.sizing_gate?.approved ? `APPROVED: ${strat.sizing_gate.lots} Lots` : 'BLOCKED'}
                        </div>
                        <div className="text-[10px] text-slate-400 mt-1 max-w-[150px] leading-tight">
                          {strat.sizing_gate?.reason}
                        </div>
                      </div>
                    </div>
                    

                    {strat.vol_caution && (
                      <div className="mt-3 p-2 bg-rose-500/10 border border-rose-500/30 rounded-lg flex gap-2 items-start">
                        <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                        <div className="text-xs text-rose-200 font-medium leading-tight">
                          {strat.vol_caution}
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4">
                      <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 uppercase">EV (Pts)</div>
                        <div className="font-mono text-sm">{strat.ev_pts > 0 ? '+' : ''}{strat.ev_pts}</div>
                      </div>
                      <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 uppercase">PoP</div>
                        <div className="font-mono text-sm">{(strat.prob_of_profit * 100).toFixed(0)}%</div>
                      </div>
                      <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 uppercase">Max Risk</div>
                        <div className="font-mono text-sm">₹{strat.rupees?.max_loss?.toLocaleString() || 0}</div>
                      </div>
                      <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 uppercase">Max Reward</div>
                        <div className="font-mono text-sm">₹{strat.rupees?.max_profit?.toLocaleString() || 0}</div>
                      </div>
                      <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 uppercase">OI Edge</div>
                        <div className="font-mono text-sm flex items-center gap-1">
                          {strat.oi?.oi_edge || 0}
                          {strat.oi?.wall_alignment > 0 && <Shield className="w-3 h-3 text-emerald-400" />}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              
              {pipelineRes.optimizer.caveats && pipelineRes.optimizer.caveats.length > 0 && (
                <div className="mt-6 space-y-1">
                  <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Optimizer Caveats</div>
                  {pipelineRes.optimizer.caveats.map((c: string, idx: number) => (
                    <div key={idx} className="text-[10px] text-slate-400 flex gap-2">
                      <span className="text-slate-600">•</span> {c}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>)}
        </>
      )}

      {/* Risk Budget Configuration — Strike Optimizer tab */}
      {subTab === 'optimizer' && (<>
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
      </>)}

      {/* Top Filter Banner — Directional Suggester tab */}
      {subTab === 'directional' && (<>
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

                  {!rec.isMacro && (
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
                  )}
                  {rec.isMacro && (
                    <div className="mt-4 pt-3 border-t border-slate-200/60 text-xs font-semibold text-slate-500">
                      <span className="block text-[10px] uppercase text-slate-400">Signals Matched</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rec.fits.map((f: string, i: number) => <span key={i} className="bg-slate-100 px-1.5 py-0.5 rounded text-[10px]">{f}</span>)}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Detail Pane */}
        {activeStrategy && (
          <div className="lg:col-span-7 bg-white rounded-2xl border border-slate-200 p-6 shadow-sm flex flex-col space-y-6">
            {activeStrategy.isMacro ? (
              <div>
                <h3 className="text-xl font-black text-slate-900 mb-2">{activeStrategy.name}</h3>
                <p className="text-sm text-slate-600 mb-4">{activeStrategy.rationale}</p>
                {activeStrategy.caution && (
                  <div className="bg-amber-50 border border-amber-200 text-amber-800 p-3 rounded-lg text-xs font-medium mb-4 flex gap-2 items-start">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    <span>{activeStrategy.caution}</span>
                  </div>
                )}
                <LegExecutionMatrix 
                  family={activeStrategy.id}
                  capture={pipelineRes?.capture || {spot, strikes: rows.map(r => r.strike), call_ltp: rows.map(r => r.ceLTP), put_ltp: rows.map(r => r.peLTP), capture_id: captureId || 0}}
                  expectedMove={pipelineRes?.rnd?.stats?.expected_move_pts || 200}
                  onTrade={async (legs, val) => {
                    if (!captureId) {
                       alert("Can't trade offline CSV without capture_id right now. Use DB chain.");
                       return;
                    }
                    try {
                      const res = await fetch("http://127.0.0.1:8000/api/portfolio/add", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                          legs: legs.map(l => [l.side, l.strike, l.sign]),
                          expiry: pipelineRes?.chain_meta?.expiry || "2026-06-30",
                          entry_capture_id: parseInt(captureId),
                          source: "recommended",
                          lineage: {
                            family: activeStrategy.id,
                            rationale: activeStrategy.rationale,
                            regime_context: pipelineRes?.strategy_suggestion?.regime
                          }
                        })
                      });
                      const data = await res.json();
                      if (data.success) {
                        alert("Position added to Portfolio!");
                        // Switch to portfolio tab ideally... handled by parent app if we bubble it up, but for now just alert
                      } else {
                        alert("Failed: " + data.detail);
                      }
                    } catch(e: any) {
                      alert(e.message);
                    }
                  }}
                  onCancel={() => {}}
                />
              </div>
            ) : (
            <>
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
                <div className="flex items-center gap-4 mt-3 text-xs font-semibold">
                  <div>
                    <span className="text-slate-400 uppercase">Net Flow: </span>
                    <span className={activeStrategy.netPremium <= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                      {activeStrategy.netPremium <= 0 ? `Credit ₹${Math.abs(Math.round(activeStrategy.netPremium * riskConfig.lot_size))}` : `Debit ₹${Math.round(activeStrategy.netPremium * riskConfig.lot_size)}`}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 uppercase">Max Profit: </span>
                    <span className="text-emerald-600">{activeStrategy.maxProfit}</span>
                  </div>
                  <div>
                    <span className="text-slate-400 uppercase">Max Loss: </span>
                    <span className={activeStrategy.maxLoss === 'Unlimited' ? 'text-rose-600' : 'text-slate-700'}>{activeStrategy.maxLoss}</span>
                  </div>
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
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-bold uppercase text-slate-500">
                <span>Expiration Payoff Curve (Lot Size = {riskConfig.lot_size})</span>
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
                <Sliders className="w-4 h-4 text-indigo-600" /> Leg Execution Matrix (1 Lot = {riskConfig.lot_size} Qty)
              </h4>
              <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full text-xs text-left">
                  <thead className="bg-slate-50 text-slate-600 uppercase font-bold border-b border-slate-200">
                    <tr>
                      <th className="p-3">Action</th>
                      <th className="p-3">Option Type</th>
                      <th className="p-3">Strike Price</th>
                      <th className="p-3">Expiry</th>
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
                        <td className="p-3">
                          <select
                            value={leg.strike}
                            onChange={(e) => {
                              const newStrike = parseInt(e.target.value, 10);
                              setCustomStrikes(prev => {
                                const stratStrikes = prev[activeStrategy.id] || activeStrategy.legs.map(l => l.strike);
                                const newStrikes = [...stratStrikes];
                                newStrikes[idx] = newStrike;
                                return { ...prev, [activeStrategy.id]: newStrikes };
                              });
                            }}
                            className="bg-slate-100 p-1 rounded-md border border-slate-300 font-mono text-xs font-bold text-slate-900 outline-none hover:border-indigo-400 focus:border-indigo-500 cursor-pointer w-20"
                          >
                            {Array.from(new Set([...rows.map(r => r.strike), leg.strike]))
                              .sort((a, b) => a - b)
                              .map(strike => (
                              <option key={strike} value={strike}>₹{strike}</option>
                            ))}
                          </select>
                        </td>
                        <td className="p-3">
                          {/* single-expiry structure: all legs share the chain expiry,
                              chosen at the top selector — shown here per leg for clarity */}
                          <select
                            value={pipelineRes?.chain_meta?.expiry || breezeExpiry}
                            onChange={(e) => setBreezeExpiry(e.target.value)}
                            title="Option expiry for this leg (shared across the structure)"
                            className="bg-slate-100 p-1 rounded-md border border-slate-300 font-mono text-xs font-bold text-slate-900 outline-none hover:border-indigo-400 focus:border-indigo-500 cursor-pointer"
                          >
                            {Array.from(new Set([...(expiryOptions || []), pipelineRes?.chain_meta?.expiry || breezeExpiry].filter(Boolean))).map(exp => (
                              <option key={exp} value={exp}>{String(exp).slice(0, 10)}</option>
                            ))}
                          </select>
                        </td>
                        <td className="p-3 font-mono">₹{leg.premium}</td>
                        <td className="p-3">{leg.qtyRatio}x</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>


            <div className="pt-4 border-t border-slate-100 flex justify-end gap-2">
              <button
                onClick={async () => {
                  // Send the CURRENT (strike-adjusted) structure to the Desk Book so it
                  // can be picked for backtesting. Legs carry the adjusted strikes;
                  // entry prices are left empty (priced at the backtest entry time).
                  const expiry = pipelineRes?.chain_meta?.expiry || breezeExpiry || null;
                  const strikes = activeStrategy.legs.map((l: any) => l.strike).join('/');
                  try {
                    const res = await fetch("http://127.0.0.1:8000/api/strategy/portfolio/add", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        kind: "option_strategy",
                        family: activeStrategy.id,
                        legs: activeStrategy.legs.map((l: any) => [l.type.toLowerCase(), l.strike, l.action === 'BUY' ? 1 : -1]),
                        entry_prices: {},
                        exchange: "NFO",
                        expiry,
                        label: `${activeStrategy.id} ${strikes} · NFO${expiry ? ` · exp ${String(expiry).slice(0, 10)}` : ''}`,
                      }),
                    });
                    const data = await res.json();
                    if (data.ok || data.success) alert("Added to Desk Book — pick it in the Desk tab to backtest.");
                    else alert("Failed: " + (data.error || data.detail || 'unknown'));
                  } catch (e: any) { alert(e.message); }
                }}
                className="bg-slate-700 hover:bg-slate-600 text-white px-5 py-2 rounded-lg text-sm font-bold shadow transition-all flex items-center gap-2"
                title="Send this strike-adjusted structure to the Desk Book portfolio for backtesting"
              >
                <Plus className="w-4 h-4" /> Add to Desk Book
              </button>
              <button
                onClick={async () => {
                  if (!captureId) {
                     alert("Can't trade offline CSV without capture_id. Use DB chain.");
                     return;
                  }
                  try {
                    const res = await fetch("http://127.0.0.1:8000/api/portfolio/add", {
                      method: "POST",
                      headers: {"Content-Type": "application/json"},
                      body: JSON.stringify({
                        legs: activeStrategy.legs.map((l: any) => [l.type.toLowerCase(), l.strike, l.action === 'BUY' ? 1 : -1]),
                        expiry: pipelineRes?.chain_meta?.expiry || breezeExpiry || "2026-06-30",
                        entry_capture_id: parseInt(captureId),
                        source: "recommended",
                        lineage: {
                          family: activeStrategy.id,
                          rationale: activeStrategy.rationale,
                        }
                      })
                    });
                    const data = await res.json();
                    if (data.success) {
                      alert("Position added to Portfolio!");
                    } else {
                      alert("Failed: " + data.detail);
                    }
                  } catch(e: any) {
                    alert(e.message);
                  }
                }}
                className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded-lg text-sm font-bold shadow-lg shadow-indigo-900/50 transition-all flex items-center gap-2"
              >
                <Play className="w-4 h-4" /> Trade to Portfolio
              </button>
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
            </>
            )}
          </div>
        )}
      </div>
      </>)}
      {subTab === 'oi' && <div className="mt-2">{oiPanel}</div>}
      {subTab === 'vol' && <div className="mt-2">{volPanel}</div>}
    </div>
  );
};
