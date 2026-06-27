import React, { useState, useMemo } from 'react';
import { SAMPLE_CHAIN, GLOBAL_MAP, SAMPLE_NEWS, CONFIG } from './lib/constants';
import {
  parseChain,
  estimateSpot,
  calculateMaxPain,
  calculateATM,
  calculatePCR,
  generateReads,
  generateStructureContext,
  calculateComplacency,
  generateGlobalCues,
} from './lib/analytics';
import { OIPositioningPanel } from './components/OIPositioningPanel';
import { ComplacencyPanel } from './components/ComplacencyPanel';
import { GlobalCuesPanel } from './components/GlobalCuesPanel';
import { SectorNewsPanel } from './components/SectorNewsPanel';
import { StrategySuggesterPanel } from './components/StrategySuggesterPanel';
import { AICopilotModal } from './components/AICopilotModal';
import { ChainEditorDrawer } from './components/ChainEditorDrawer';
import {
  BarChart2,
  Activity,
  Globe,
  Newspaper,
  Sparkles,
  Bot,
  Database,
  Layers,
  AlertTriangle,
  RefreshCw,
  TrendingUp,
  Clock,
  ExternalLink,
} from 'lucide-react';

export default function App() {
  const [rawChain, setRawChain] = useState<string>(SAMPLE_CHAIN);
  const [spotOverride, setSpotOverride] = useState<number>(0);

  // Global index % moves state
  const [pctMap, setPctMap] = useState<Record<string, number>>(() => {
    const init: Record<string, number> = {};
    for (const [key, val] of Object.entries(GLOBAL_MAP)) {
      init[key] = val.defaultPct;
    }
    return init;
  });

  const [activeTab, setActiveTab] = useState<'oi' | 'complacency' | 'global' | 'news' | 'strategy'>('oi');

  // Modals & Drawers
  const [isChainDrawerOpen, setIsChainDrawerOpen] = useState(false);
  const [isCopilotOpen, setIsCopilotOpen] = useState(false);

  const [traderOutlook, setTraderOutlook] = useState<'bullish' | 'bearish' | 'neutral' | 'volatile'>('neutral');

  const [riskConfig, setRiskConfig] = useState({
    capital: 1000000.0,
    risk_per_trade_pct: 0.015,
    max_portfolio_heat_pct: 0.06,
    max_net_delta_units: 150.0,
    max_net_vega_rupees: 50000.0,
    max_drawdown_pct: 0.10,
    lot_size: CONFIG.lot_size,
    complacency_block: 70.0,
    complacency_halve: 55.0
  });

  // Quant Pipeline State
  const [pipelineRes, setPipelineRes] = useState<any>(null);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [daysToExpiry, setDaysToExpiry] = useState<number>(5.0); // 30th June Expiry

  // Analytics Calculation Pipeline
  const analytics = useMemo(() => {
    try {
      const chainRows = parseChain(rawChain);
      const spot = spotOverride > 0 ? spotOverride : estimateSpot(chainRows);
      const maxPain = calculateMaxPain(chainRows);
      const atmMeta = calculateATM(chainRows, spot);
      const pcr = calculatePCR(chainRows);
      const readsMeta = generateReads(chainRows, spot, maxPain, atmMeta);
      const structureContext = generateStructureContext(readsMeta.resRow, readsMeta.supRow);
      const complacencyMetrics = calculateComplacency(chainRows, spot, atmMeta.iv);
      const globalCues = generateGlobalCues(pctMap);

      return {
        success: true as const,
        chainRows,
        spot,
        maxPain,
        atmMeta,
        pcr,
        reads: readsMeta.reads,
        resRow: readsMeta.resRow,
        supRow: readsMeta.supRow,
        structureContext,
        complacencyMetrics,
        globalCues,
      };
    } catch (err: any) {
      return {
        success: false as const,
        error: err.message || "Failed to parse options dashboard metrics.",
      };
    }
  }, [rawChain, spotOverride, pctMap]);

  const handleResetPct = () => {
    const reset: Record<string, number> = {};
    for (const [key, val] of Object.entries(GLOBAL_MAP)) {
      reset[key] = val.defaultPct;
    }
    setPctMap(reset);
  };

  const [mockTrade, setMockTrade] = useState({
    drawdown_pct: 0.0,
    trade_max_loss_pts: 120.0,
    trade_delta: 25.0,
    trade_vega: -1200.0,
    trade_structure: "",
    is_premium_sell: false
  });

  const runQuantPipeline = async (forceNewsRefresh: boolean = false) => {
    if (!analytics.success) return;
    setIsPipelineRunning(true);
    try {
      // Build option chain payload
      const strikes = analytics.chainRows.map(r => r.strike);
      const call_ltp = analytics.chainRows.map(r => r.call_ltp);
      const put_ltp = analytics.chainRows.map(r => r.put_ltp);
      const put_oichg = analytics.chainRows.map(r => r.put_oichg);
      const chainPayload = {
        strikes,
        call_ltp,
        put_ltp,
        put_oichg,
        pcr: analytics.pcr,
        atm_iv: analytics.atmMeta.iv,
        spot: analytics.spot,
        days: daysToExpiry,
        r: 0.0655
      };

      // No articlesPayload needed—backend fetches RSS itself.
      const res = await fetch('/api/run-pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chain: chainPayload,
          prev_regime: null,
          risk_cfg: riskConfig,
          book: [],
          current_drawdown_pct: mockTrade.drawdown_pct,
          trade_max_loss_pts: mockTrade.trade_max_loss_pts,
          trade_delta: mockTrade.trade_delta,
          trade_vega: mockTrade.trade_vega,
          override_structure: mockTrade.trade_structure || undefined,
          override_is_premium_sell: mockTrade.is_premium_sell,
          force_news_refresh: forceNewsRefresh
        })
      });
      const data = await res.json();
      if (data.success) {
        setPipelineRes(data.result);
        setActiveTab('strategy');
      } else {
        alert("Pipeline failed: " + data.detail);
      }
    } catch (err) {
      alert("Error running pipeline: " + err);
    } finally {
      setIsPipelineRunning(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-100/80 text-slate-900 font-sans pb-16">
      {/* Top Header Navigation */}
      <header className="sticky top-0 z-40 bg-slate-950 text-white border-b border-slate-800 shadow-xl backdrop-blur-md bg-opacity-95">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 to-blue-500 flex items-center justify-center font-black text-lg shadow-lg shadow-indigo-500/30">
              N50
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base sm:text-lg font-black tracking-tight">
                  NIFTY Institutional Derivatives Desk <span className="text-xs font-mono text-indigo-400 bg-indigo-950/80 px-2 py-0.5 rounded border border-indigo-800">v2.5</span>
                </h1>
              </div>
              <p className="text-[11px] text-slate-400 hidden sm:block">
                Terrain positioning, vol complacency gauge, global transmission matrix &amp; quant strategy engine
              </p>
            </div>
          </div>

          {/* Action Tools */}
          <div className="flex items-center gap-2 sm:gap-3">
            <button
              onClick={() => setIsChainDrawerOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition border border-slate-700/80 cursor-pointer"
            >
              <Database className="w-3.5 h-3.5 text-blue-400" />
              <span className="hidden md:inline">Edit Chain Data</span>
              {analytics.success && <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse"></span>}
            </button>

            <button
              onClick={() => runQuantPipeline(false)}
              disabled={!analytics.success || isPipelineRunning}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-white text-xs font-bold transition shadow-lg cursor-pointer ${isPipelineRunning ? 'bg-slate-600 cursor-not-allowed' : 'bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 shadow-emerald-500/30'
                }`}
            >
              <Activity className={`w-4 h-4 ${isPipelineRunning ? 'animate-spin' : ''}`} />
              <span>{isPipelineRunning ? 'Running...' : 'Run Quant Engine'}</span>
            </button>
            <button
              onClick={() => runQuantPipeline(true)}
              disabled={!analytics.success || isPipelineRunning}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-500 hover:from-blue-500 hover:to-indigo-400 text-white text-xs font-bold transition shadow-lg shadow-blue-500/30 cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${isPipelineRunning ? 'animate-spin' : ''}`} />
              <span className="hidden md:inline">Refresh News & Run</span>
            </button>

            <button
              onClick={() => setIsCopilotOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white text-xs font-bold transition shadow-lg shadow-indigo-600/30 cursor-pointer"
            >
              <Bot className="w-4 h-4 animate-bounce" />
              <span>Quant Desk AI Copilot</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-6 space-y-6">
        {/* Error Banner */}
        {!analytics.success && (
          <div className="bg-rose-50 border-2 border-rose-300 rounded-2xl p-6 text-rose-900 shadow-md flex items-start gap-4">
            <AlertTriangle className="w-8 h-8 text-rose-600 shrink-0 mt-0.5" />
            <div className="space-y-2 flex-1">
              <h3 className="text-lg font-black">Option Chain Parsing Error</h3>
              <p className="text-sm">{analytics.error}</p>
              <button
                onClick={() => setIsChainDrawerOpen(true)}
                className="px-4 py-2 rounded-xl bg-rose-600 text-white font-bold text-xs hover:bg-rose-700 transition inline-block"
              >
                Open Option Chain Editor
              </button>
            </div>
          </div>
        )}

        {/* Live Ticker Quick Bar */}
        {analytics.success && (
          <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-sm flex flex-wrap items-center justify-between gap-4 text-xs font-bold">
            <div className="flex items-center gap-6 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase text-[10px]">Spot Underlying</span>
                <span className="text-base font-black text-slate-900">₹{analytics.spot.toLocaleString('en-IN')}</span>
              </div>
              <div className="h-6 w-[1px] bg-slate-200 hidden sm:block"></div>
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase text-[10px]">Max Pain</span>
                <span className="text-base font-black text-amber-600">₹{analytics.maxPain}</span>
              </div>
              <div className="h-6 w-[1px] bg-slate-200 hidden sm:block"></div>
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase text-[10px]">PCR (OI)</span>
                <span className={`text-base font-black ${analytics.pcr >= 1.2 ? 'text-emerald-600' : analytics.pcr <= 0.8 ? 'text-rose-600' : 'text-slate-800'}`}>
                  {analytics.pcr.toFixed(2)}
                </span>
              </div>
              <div className="h-6 w-[1px] bg-slate-200 hidden sm:block"></div>
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase text-[10px]">ATM Vol</span>
                <span className="text-base font-black text-indigo-600">{analytics.atmMeta.iv.toFixed(1)}%</span>
              </div>
            </div>

            <div className="flex items-center gap-2 text-slate-500 font-normal">
              <Clock className="w-3.5 h-3.5" />
              <span>NSE Weekly/Monthly Expiry Grid</span>
            </div>
          </div>
        )}

        {/* Tab Selection Navigation */}
        <div className="bg-white p-2 rounded-2xl border border-slate-200 shadow-sm flex flex-wrap items-center gap-2">
          <button
            onClick={() => setActiveTab('oi')}
            className={`flex-1 min-w-[150px] py-3 px-4 rounded-xl font-bold text-xs sm:text-sm flex items-center justify-center gap-2 transition cursor-pointer ${activeTab === 'oi' ? 'bg-slate-900 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
          >
            <BarChart2 className={`w-4 h-4 ${activeTab === 'oi' ? 'text-indigo-400' : 'text-slate-400'}`} />
            <span>1. OI Positioning</span>
          </button>

          <button
            onClick={() => setActiveTab('complacency')}
            className={`flex-1 min-w-[150px] py-3 px-4 rounded-xl font-bold text-xs sm:text-sm flex items-center justify-center gap-2 transition cursor-pointer ${activeTab === 'complacency' ? 'bg-slate-900 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
          >
            <Activity className={`w-4 h-4 ${activeTab === 'complacency' ? 'text-amber-400' : 'text-slate-400'}`} />
            <span>2. Vol Complacency</span>
          </button>

          <button
            onClick={() => setActiveTab('global')}
            className={`flex-1 min-w-[150px] py-3 px-4 rounded-xl font-bold text-xs sm:text-sm flex items-center justify-center gap-2 transition cursor-pointer ${activeTab === 'global' ? 'bg-slate-900 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
          >
            <Globe className={`w-4 h-4 ${activeTab === 'global' ? 'text-blue-400' : 'text-slate-400'}`} />
            <span>3. Global Cues</span>
          </button>

          <button
            onClick={() => setActiveTab('news')}
            className={`flex-1 min-w-[150px] py-3 px-4 rounded-xl font-bold text-xs sm:text-sm flex items-center justify-center gap-2 transition cursor-pointer ${activeTab === 'news' ? 'bg-slate-900 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
          >
            <Newspaper className={`w-4 h-4 ${activeTab === 'news' ? 'text-emerald-400' : 'text-slate-400'}`} />
            <span>4. Sector News</span>
          </button>

          <button
            onClick={() => setActiveTab('strategy')}
            className={`flex-1 min-w-[180px] py-3 px-4 rounded-xl font-black text-xs sm:text-sm flex items-center justify-center gap-2 transition cursor-pointer bg-gradient-to-r ${activeTab === 'strategy' ? 'from-indigo-600 to-blue-600 text-white shadow-lg shadow-indigo-500/25 ring-2 ring-indigo-400' : 'from-indigo-50 to-blue-50 text-indigo-900 hover:from-indigo-100 hover:to-blue-100 border border-indigo-200'
              }`}
          >
            <Sparkles className="w-4 h-4 text-amber-400 animate-spin" />
            <span>5. Strategy Suggester</span>
          </button>
        </div>

        {/* Active Tab Panel Rendering */}
        {analytics.success && (
          <div className="transition duration-300">
            {activeTab === 'oi' && (
              <OIPositioningPanel
                rows={analytics.chainRows}
                spot={analytics.spot}
                maxPain={analytics.maxPain}
                pcr={analytics.pcr}
                reads={analytics.reads}
                structureContext={analytics.structureContext}
              />
            )}

            {activeTab === 'complacency' && (
              <ComplacencyPanel
                metrics={analytics.complacencyMetrics}
                spot={analytics.spot}
              />
            )}

            {activeTab === 'global' && (
              <GlobalCuesPanel
                cues={analytics.globalCues}
                pctMap={pctMap}
                onPctChange={(name, val) => setPctMap((prev) => ({ ...prev, [name]: val }))}
                onResetDefaults={handleResetPct}
                pipelineRes={pipelineRes}
              />
            )}

            {activeTab === 'news' && (
              <SectorNewsPanel
                pipelineRes={pipelineRes}
              />
            )}

            {activeTab === 'strategy' && (
              <StrategySuggesterPanel
                rows={analytics.chainRows}
                spot={analytics.spot}
                atmIV={analytics.atmMeta.iv}
                riskConfig={riskConfig}
                onRiskConfigChange={setRiskConfig}
                mockTrade={mockTrade}
                onMockTradeChange={setMockTrade}
                selectedOutlook={traderOutlook}
                onOutlookChange={setTraderOutlook}
                pipelineRes={pipelineRes}
              />
            )}
          </div>
        )}
      </main>

      {/* AI Copilot Modal */}
      {analytics.success && (
        <AICopilotModal
          isOpen={isCopilotOpen}
          onClose={() => setIsCopilotOpen(false)}
          dashboardState={{
            chainRows: analytics.chainRows,
            spot: analytics.spot,
            maxPain: analytics.maxPain,
            pcr: analytics.pcr,
            complacencyScore: analytics.complacencyMetrics.score,
            complacencyVerdict: analytics.complacencyMetrics.verdict,
            globalCues: analytics.globalCues,
            newsSentiment: pipelineRes ? pipelineRes.sector_sentiment : {},
            traderOutlook,
            capital: riskConfig.capital,
          }}
        />
      )}

      {/* Option Chain Editor Drawer */}
      <ChainEditorDrawer
        isOpen={isChainDrawerOpen}
        onClose={() => setIsChainDrawerOpen(false)}
        rawChain={rawChain}
        onSaveChain={(newChain, newSpot, newDte) => {
          setRawChain(newChain);
          setSpotOverride(newSpot);
          setDaysToExpiry(newDte);
        }}
        currentSpotInput={spotOverride}
        currentDteInput={daysToExpiry}
      />
    </div>
  );
}
