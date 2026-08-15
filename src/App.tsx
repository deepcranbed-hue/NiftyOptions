import React, { useState, useMemo, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { OptionRow } from './types';
import { SAMPLE_CHAIN, GLOBAL_MAP, SAMPLE_NEWS, CONFIG } from './lib/constants';
import { hydrateContractParams } from './lib/signalRoster';
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
import { EventCalendarPanel } from './components/EventCalendarPanel';
import { SectorEarningsPanel } from './components/SectorEarningsPanel';
import { StrategySuggesterPanel } from './components/StrategySuggesterPanel';
import { StrategyDeskPanel } from './components/StrategyDeskPanel';
import { DeskStrategyView } from './components/DeskStrategyView';
import { SignalBacktestView } from './components/SignalBacktestView';
import { MarketStateView } from './components/MarketStateView';
import { MarketHealthPanel } from './components/MarketHealthPanel';
import { PortfolioPanel } from './components/PortfolioPanel';
import { FlowsPanel } from './components/FlowsPanel';
import { MoneySentimentPanel } from './components/MoneySentimentPanel';
import { ImpactMonitorBanner } from './components/ImpactMonitorBanner';
import { BreezeSyncPanel } from './components/BreezeSyncPanel';
import { DataAgentPanel } from './components/DataAgentPanel';
import { PriceChartPanel } from './components/PriceChartPanel';
import { FundamentalScreenPanel } from './components/FundamentalScreenPanel';
import { IntradayPanel } from './components/IntradayPanel';
import { MacroShockTab } from './components/MacroShockTab';
import { AIInfraThemePanel } from './components/AIInfraThemePanel';
import { SectorViewPanel } from './components/SectorViewPanel';
import { Nifty50Panel } from './components/Nifty50Panel';
import { ShockRecoveryPanel } from './components/ShockRecoveryPanel';
import { AICopilotModal } from './components/AICopilotModal';
import { CaptureComparePanel } from './components/CaptureComparePanel';
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
  Calendar,
  Briefcase,
  DownloadCloud,
  PieChart,
  Trash2,
  Crosshair,
  Zap,
  Server,
} from 'lucide-react';
import { Info, AlertOctagon, Shield, Gauge, Landmark } from 'lucide-react';

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const [rawChain, setRawChain] = useState<string>(SAMPLE_CHAIN);
  const [spotOverride, setSpotOverride] = useState<number>(0);
  const [niftyMove, setNiftyMove] = useState<any>(null);
  useEffect(() => {
    const f = () => fetch('/api/nifty-move').then((r) => r.json()).then((j) => { if (j.success) setNiftyMove(j); }).catch(() => {});
    f();
    const t = setInterval(f, 5 * 60 * 1000); // refresh the live move every 5 minutes
    return () => clearInterval(t);
  }, []);

  // Global index % moves state
  const [pctMap, setPctMap] = useState<Record<string, number>>(() => {
    const init: Record<string, number> = {};
    for (const [key, val] of Object.entries(GLOBAL_MAP)) {
      init[key] = val.defaultPct;
    }
    return init;
  });

  const [activeTab, setActiveTab] = useState<'oi' | 'complacency' | 'global' | 'news' | 'calendar' | 'earnings' | 'flows' | 'strategy' | 'sync' | 'compare' | 'portfolio'>('oi');

  // Modals & Drawers
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

  // Contract params (lot size) come from exchange_config.py via /api/strategy/config;
  // CONFIG holds only a bootstrap value for the first paint. Re-seed riskConfig once
  // the real value lands so sizing/payoff never run on the stale literal.
  useEffect(() => {
    hydrateContractParams().then((lot) => {
      if (lot) setRiskConfig((rc) => (rc.lot_size === lot ? rc : { ...rc, lot_size: lot }));
    });
  }, []);

  // Learn the current (nearest) expiry so the optimizer can auto-run only for it.
  useEffect(() => {
    fetch('http://127.0.0.1:8000/api/exchange-expiries?symbol=NIFTY&segment_filter=OPT')
      .then((r) => r.json())
      .then((d) => { if (d?.success && d.expiries?.length) setLatestExpiry(d.expiries[0]); })
      .catch(() => { /* leave '' → expiry check passes so the latest capture still runs */ });
  }, []);

  // Quant Pipeline State

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadSpot, setUploadSpot] = useState<number>(0);
  const [uploadExpiryDate, setUploadExpiryDate] = useState<string>('');
  const [uploadVix, setUploadVix] = useState<number | undefined>(undefined);

  const [csvChainRows, setCsvChainRows] = useState<OptionRow[] | null>(null);


  const [pipelineRes, setPipelineRes] = useState<any>(null);
  const [currentPipelineRes, setCurrentPipelineRes] = useState<any>(null);
  const [historicalPipelineRes, setHistoricalPipelineRes] = useState<any>(null);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [isNewsUpdating, setIsNewsUpdating] = useState(false);
  const [isFlowsUpdating, setIsFlowsUpdating] = useState(false);
  const [daysToExpiry, setDaysToExpiry] = useState<number>(5.0); // 30th June Expiry
  const [loadedExpiry, setLoadedExpiry] = useState<string>(''); // Dynamically loaded expiry
  const [breezeExpiry, setBreezeExpiry] = useState<string>(() => localStorage.getItem('breezeExpiryDate') || '2026-07-09T06:00:00.000Z');
  // Nearest active expiry, from /api/exchange-expiries (ascending → [0] is nearest).
  // Used ONLY to decide whether the strike optimizer may AUTO-run: it fires
  // automatically only on the latest capture + latest expiry; every other selection
  // (historical capture, a different expiry) waits for an explicit Run. '' until loaded.
  const [latestExpiry, setLatestExpiry] = useState<string>('');

  const [captures, setCaptures] = useState<any[]>([]);
  const [optionChainMode, setOptionChainMode] = useState<'historical' | 'live'>('historical');
  const [selectedCaptureId, setSelectedCaptureId] = useState<string>('');
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [selectedTime, setSelectedTime] = useState<string>('');

  React.useEffect(() => {
    if (selectedCaptureId) {
      loadHistoricalRnd(selectedCaptureId);
    }
  }, [breezeExpiry]);

  React.useEffect(() => {
    if (loadedExpiry && selectedCaptureId && captures.length > 0) {
      const active = captures.find(c => c.capture_id.toString() === selectedCaptureId);
      if (active) {
        const startDt = new Date(active.captured_at);
        const endDt = new Date(loadedExpiry);
        const diffDays = (endDt.getTime() - startDt.getTime()) / (1000 * 60 * 60 * 24);
        if (diffDays >= 0) {
          setDaysToExpiry(parseFloat(diffDays.toFixed(4)));
        }
      }
    }
  }, [loadedExpiry, selectedCaptureId, captures]);

  // '' = idle · 'auto' = triggered by selection/load (gated to latest+latest)
  //        · 'force' = explicit user action (always runs)
  const [pendingPipelineRun, setPendingPipelineRun] = useState<'' | 'auto' | 'force'>('');

  // The strike optimizer may AUTO-run only for the LATEST capture + LATEST expiry.
  // captures[0] is the newest capture; latestExpiry is the nearest active expiry.
  // Any historical capture, or a non-current expiry, requires an explicit Run — this
  // is what stops run-pipeline re-firing on every expiry switch / capture click.
  // (latestExpiry === '' → not loaded yet → expiry check passes, so startup on the
  //  latest capture still runs; explicit Run is never gated.)
  const autoRunAllowed = false;

  const loadHistoricalRnd = async (capId: string) => {
    if (!capId) {
      setHistoricalPipelineRes(null);
      return;
    }
    try {
      const exp = localStorage.getItem('breezeExpiryDate') || "";
      const res = await fetch(`http://127.0.0.1:8000/api/load-capture/${capId}?expiry=${encodeURIComponent(exp)}&mode=${optionChainMode}`);
      if (!res.ok) throw new Error("Failed to load capture");
      const data = await res.json();
      if (data.success && data.capture) {
        const cap = data.capture;
        const strikes = cap.strikes;
        const call_ltp = cap.call_ltp || strikes.map(() => 0);
        const put_ltp = cap.put_ltp || strikes.map(() => 0);
        const call_oi = cap.call_oi || strikes.map(() => 0);
        const put_oi = cap.put_oi || strikes.map(() => 0);

        // Find ATM index
        const spotVal = cap.spot;
        const atmIdx = strikes.reduce((bestIdx: number, val: number, idx: number, arr: number[]) =>
          Math.abs(val - spotVal) < Math.abs(arr[bestIdx] - spotVal) ? idx : bestIdx, 0);
        const atm_iv = cap.call_iv?.[atmIdx] || cap.put_iv?.[atmIdx] || 14.0;

        // Calculate dynamic days-to-expiry relative to capture time
        let dynamicDays = 5.0;
        if (cap.captured_at && cap.expiry) {
          const startDt = new Date(cap.captured_at);
          const endDt = new Date(cap.expiry);
          const diffDays = (endDt.getTime() - startDt.getTime()) / (1000 * 60 * 60 * 24);
          if (diffDays >= 0) {
            dynamicDays = parseFloat(diffDays.toFixed(4));
          }
        }

        // AUTO-run the optimizer here ONLY for the latest capture + latest expiry.
        // For a historical capture or a non-current expiry, skip the run-pipeline call
        // (this is the "called for every expiry" spike) — the user runs it explicitly.
        if (!autoRunAllowed) return;

        const chainPayload = {
          strikes,
          call_ltp,
          put_ltp,
          put_oi_chg_pct: strikes.map(() => 0),
          call_oi_chg_pct: strikes.map(() => 0),
          call_oi,
          put_oi,
          pcr: cap.pcr || 1.0,
          atm_iv,
          spot: spotVal,
          days: dynamicDays,
          r: 0.0655,
          expiry: cap.expiry
        };

        const pRes = await fetch('/api/run-pipeline', {
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
            opt_weights: optWeights,
            opt_bias: optBias,
            opt_min_pop: optMinPop,
            opt_allow_undefined: optAllowUndefined,
            opt_cost_per_leg: optCostPerLeg,
            opt_window_pts: optWindowPts,
            opt_max_wing: optMaxWing,
            opt_top_n: optTopN,
            opt_max_loss_budget: optMaxLossBudget,
            opt_allow_bad_rnd: optAllowBadRnd
          })
        });
        const pData = await pRes.json();
        if (pData.success) {
          setHistoricalPipelineRes(pData.result);
        }
      }
    } catch (e) {
      console.error("Failed to load historical RND:", e);
    }
  };

  const fetchCaptures = async (autoLoadFirst = false) => {
    try {
      const r = await fetch(`http://127.0.0.1:8000/api/captures?mode=${optionChainMode}`);
      const d = await r.json();
      if (d.success && d.captures) {
        setCaptures(d.captures);
        if (d.captures.length > 0) {
          const firstId = d.captures[0].capture_id.toString();
          setSelectedCaptureId(firstId);
          if (autoLoadFirst) {
            loadSelectedCapture(firstId);
          }
        } else {
          setCaptures([]);
          setSelectedCaptureId('');
          setCsvChainRows([]);
        }
      }
    } catch (e) {
      console.error("Failed to load captures", e);
    }
  };

  React.useEffect(() => {
    fetchCaptures(true); // Auto-load when mode changes
  }, [optionChainMode]);

  React.useEffect(() => {
    if (selectedCaptureId && captures.length > 0) {
      const active = captures.find(c => c.capture_id.toString() === selectedCaptureId);
      if (active && active.captured_at) {
        // e.g. "2026-07-03T14:30:00.000Z" -> date: "2026-07-03", time: "14:30"
        const parts = active.captured_at.split('T');
        if (parts[0]) setSelectedDate(parts[0]);
        if (parts[1]) setSelectedTime(parts[1].slice(0, 5));
      }
    }
  }, [selectedCaptureId, captures]);

  const loadSelectedCapture = async (capId: string) => {
    if (!capId) return;
    try {
      const exp = localStorage.getItem('breezeExpiryDate') || "";
      const res = await fetch(`http://127.0.0.1:8000/api/load-capture/${capId}?expiry=${encodeURIComponent(exp)}&mode=${optionChainMode}`);
      if (!res.ok) throw new Error("Failed to load capture");
      const data = await res.json();
      if (data.success && data.capture) {
        // Need to parse it back into the format expected by the app.
        // The chain_store returns the raw parsed dictionary. We can populate csvChainRows.
        const cap = data.capture;
        const rows: OptionRow[] = [];
        for (let i = 0; i < cap.strikes.length; i++) {
          rows.push({
            strike: cap.strikes[i],
            call_oi: cap.call_oi[i] || 0,
            call_oichg: cap.call_oi_chg[i] || 0,
            call_ltp: cap.call_ltp[i] || 0,
            call_iv: cap.call_iv[i] || 0,
            put_oi: cap.put_oi[i] || 0,
            put_oichg: cap.put_oi_chg[i] || 0,
            put_ltp: cap.put_ltp[i] || 0,
            put_iv: cap.put_iv[i] || 0,
          } as any);
        }
        setCsvChainRows(rows);
        setSpotOverride(cap.spot);
        setLoadedExpiry(cap.expiry || "");
        setPendingPipelineRun('auto');   // gated: runs only if this is latest+latest
      }
    } catch (e) {
      console.error(e);
      alert("Error loading capture");
    }
  };

  const handleDeleteCapture = async () => {
    if (!selectedCaptureId) return;
    if (!confirm("Are you sure you want to delete this capture?")) return;
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/captures/${selectedCaptureId}?mode=${optionChainMode}`, {
        method: "DELETE"
      });
      if (!res.ok) throw new Error("Failed to delete");
      alert("Capture deleted successfully");
      fetchCaptures();
    } catch (e: any) {
      console.error(e);
      alert("Error deleting capture: " + e.message);
    }
  };

  // Analytics Calculation Pipeline
  const analytics = useMemo(() => {
    try {
      const chainRows = csvChainRows || parseChain(rawChain);
      console.log('Analytics Re-run. USING CSV?', !!csvChainRows, 'Rows:', chainRows.length);
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
        chainRows: [],
        spot: 0,
        maxPain: 0,
        atmMeta: { strike: 0, iv: 0, call_ltp: 0, put_ltp: 0 },
        pcr: 0,
        reads: [],
        resRow: null,
        supRow: null,
        structureContext: { sentiment: 'Neutral', text: 'No option chain data loaded. Please fetch or select a capture.' },
        complacencyMetrics: { score: 0, verdict: 'Neutral', dvol_pct: 0, dvol_pcr: 0 },
        globalCues: [],
      };
    }
  }, [rawChain, spotOverride, pctMap, csvChainRows]);

  const handleResetPct = () => {
    const reset: Record<string, number> = {};
    for (const [key, val] of Object.entries(GLOBAL_MAP)) {
      reset[key] = val.defaultPct;
    }
    setPctMap(reset);
  };

  const [optWeights, setOptWeights] = useState({ ev: 0.5, pop: 0.3, rr: 0.05, oi: 0.15 });
  const [optBias, setOptBias] = useState(0.0);
  const [optMinPop, setOptMinPop] = useState(0.0);
  const [optAllowUndefined, setOptAllowUndefined] = useState(false);
  const [optCostPerLeg, setOptCostPerLeg] = useState(20.0);
  const [optWindowPts, setOptWindowPts] = useState(500);
  const [optMaxWing, setOptMaxWing] = useState(300);
  const [optTopN, setOptTopN] = useState(6);
  const [optMaxLossBudget, setOptMaxLossBudget] = useState(0.0);
  const [optAllowBadRnd, setOptAllowBadRnd] = useState(false);

  const [mockTrade, setMockTrade] = useState({
    drawdown_pct: 0.0,
    trade_max_loss_pts: 120.0,
    trade_delta: 25.0,
    trade_vega: -1200.0,
    trade_structure: "",
    is_premium_sell: false
  });

  const updateNews = async () => {
    setIsNewsUpdating(true);
    try {
      const res = await fetch('/api/update-news', { method: 'POST' });
      const data = await res.json();
      if (!data.success) alert("News Update Failed: " + data.detail);
      else {
        const hasGeminiDown = data.state?.articles?.some((a: any) => a.gemini_down);
        if (hasGeminiDown) {
          alert("News updated (GEMINI DOWN - used keyword fallback)");
        } else {
          alert("News updated successfully!");
        }
        if (pipelineRes) {
          runQuantPipeline();
        }
      }
    } catch (err) {
      alert("Error updating news: " + err);
    } finally {
      setIsNewsUpdating(false);
    }
  };

  const updateFlows = async () => {
    setIsFlowsUpdating(true);
    try {
      const res = await fetch('/api/update-flows', { method: 'POST' });
      const data = await res.json();
      if (!data.success) alert("Flows Update Failed: " + data.detail);
      else alert("Flows updated successfully!");
    } catch (err) {
      alert("Error updating flows: " + err);
    } finally {
      setIsFlowsUpdating(false);
    }
  };


  const onUploadPipeline = async (optConfig?: any) => {
    if (!uploadFile || uploadSpot <= 0 || !uploadExpiryDate) {
      alert("Please provide a CSV file, Spot Price, and Expiry Date.");
      return;
    }
    setIsPipelineRunning(true);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      formData.append("spot", uploadSpot.toString());
      formData.append("expiry", uploadExpiryDate);
      if (uploadVix !== undefined && !isNaN(uploadVix)) {
        formData.append("vix", uploadVix.toString());
      }

      const cleanOptConfig = (optConfig && optConfig._reactName) ? {} : (optConfig || {});
      const payload = {
        half_life_hours: 12.0,
        risk_cfg: riskConfig,
        book: [],

        current_drawdown_pct: mockTrade.drawdown_pct,
        trade_max_loss_pts: mockTrade.trade_max_loss_pts,
        trade_delta: mockTrade.trade_delta,
        trade_vega: mockTrade.trade_vega,
        override_structure: mockTrade.trade_structure || undefined,
        override_is_premium_sell: mockTrade.is_premium_sell,
        opt_weights: optWeights,
        opt_bias: optBias,
        opt_min_pop: optMinPop,
        opt_allow_undefined: optAllowUndefined,
        opt_cost_per_leg: optCostPerLeg,
        opt_window_pts: optWindowPts,
        opt_max_wing: optMaxWing,
        opt_top_n: optTopN,
        opt_max_loss_budget: optMaxLossBudget,
        opt_allow_bad_rnd: optAllowBadRnd,

        ...cleanOptConfig
      };
      formData.append("payload", JSON.stringify(payload));

      const res = await fetch("http://127.0.0.1:8000/api/upload-chain", {
        method: "POST",
        body: formData
      });
      if (!res.ok) {
        throw new Error(`Pipeline API Error: ${res.statusText}`);
      }
      const data = await res.json();
      setPipelineRes(data);
      if (data.chain_meta && data.chain_meta.rows) {
        setCsvChainRows(data.chain_meta.rows);
      }
      fetchCaptures(); // Refresh dropdown
    } catch (err: any) {
      console.error(err);
      alert(`Pipeline failed: ${err.message}`);
    } finally {
      setIsPipelineRunning(false);
    }
  };

  const updateOptionChainAndRun = async () => {
    setIsPipelineRunning(true);
    try {
      const r = await fetch('http://127.0.0.1:8000/api/captures');
      const d = await r.json();
      if (d.success && d.captures && d.captures.length > 0) {
        const latestId = d.captures[0].capture_id.toString();
        // If we are already on the latest, just run
        if (selectedCaptureId === latestId && csvChainRows) {
          runQuantPipeline();
          return;
        }

        setCaptures(d.captures);
        setSelectedCaptureId(latestId);

        const res = await fetch(`http://127.0.0.1:8000/api/load-capture/${latestId}`);
        const data = await res.json();
        if (data.success && data.capture) {
          const cap = data.capture;
          const rows: OptionRow[] = [];
          for (let i = 0; i < cap.strikes.length; i++) {
            rows.push({
              strike: cap.strikes[i],
              call_oi: cap.call_oi[i] || 0,
              call_oi_chg_pct: cap.call_oi_chg ? cap.call_oi_chg[i] || 0 : 0,
              call_oichg: cap.call_oi_chg ? cap.call_oi_chg[i] || 0 : 0,
              call_ltp: cap.call_ltp[i] || 0,
              call_iv: cap.call_iv[i] || 0,
              put_oi: cap.put_oi[i] || 0,
              put_oi_chg_pct: cap.put_oi_chg ? cap.put_oi_chg[i] || 0 : 0,
              put_oichg: cap.put_oi_chg ? cap.put_oi_chg[i] || 0 : 0,
              put_ltp: cap.put_ltp[i] || 0,
              put_iv: cap.put_iv[i] || 0,
            } as any);
          }
          setCsvChainRows(rows);
          setSpotOverride(cap.spot);
          setLoadedExpiry(cap.expiry || "");
          setPendingPipelineRun('force'); // explicit "update & run" — always runs
        } else {
          setIsPipelineRunning(false);
        }
      } else {
        setIsPipelineRunning(false);
      }
    } catch (err) {
      console.error(err);
      setIsPipelineRunning(false);
      alert("Failed to update option chain");
    }
  };

  React.useEffect(() => {
    if (pendingPipelineRun && analytics.success) {
      // 'force' (explicit user action) always runs; 'auto' (selection/load) runs
      // only for the latest capture + latest expiry. Either way clear the flag; the
      // chain is already loaded for display regardless.
      if (pendingPipelineRun === 'force' || autoRunAllowed) runQuantPipeline();
      setPendingPipelineRun('');
    }
  }, [pendingPipelineRun, analytics]);

  const runQuantPipeline = async ({ switchTab = true }: { switchTab?: boolean } = {}) => {
    if (!analytics.success) return;
    setIsPipelineRunning(true);
    try {
      // Build option chain payload
      const strikes = analytics.chainRows.map(r => r.strike);
      const call_ltp = analytics.chainRows.map(r => r.call_ltp);
      const put_ltp = analytics.chainRows.map(r => r.put_ltp);
      const put_oichg = analytics.chainRows.map(r => r.put_oichg);
      const call_oi = analytics.chainRows.map(r => r.call_oi);
      const put_oi = analytics.chainRows.map(r => r.put_oi);
      const call_oichg = analytics.chainRows.map(r => r.call_oichg);

      const chainPayload = {
        strikes,
        call_ltp,
        put_ltp,
        put_oichg,
        call_oi,
        put_oi,
        call_oi_chg_pct: call_oichg,
        put_oi_chg_pct: put_oichg,
        pcr: analytics.pcr,
        atm_iv: analytics.atmMeta.iv,
        spot: analytics.spot,
        days: daysToExpiry,
        r: 0.0655,
        expiry: loadedExpiry
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
          force_news_refresh: false,
          opt_weights: optWeights,
          opt_bias: optBias,
          opt_min_pop: optMinPop,
          opt_allow_undefined: optAllowUndefined,
          opt_cost_per_leg: optCostPerLeg,
          opt_window_pts: optWindowPts,
          opt_max_wing: optMaxWing,
          opt_top_n: optTopN,
          opt_max_loss_budget: optMaxLossBudget,
          opt_allow_bad_rnd: optAllowBadRnd
        })
      });
      const data = await res.json();
      if (data.success) {
        setPipelineRes(data.result);
        const isLatest = !selectedCaptureId || selectedCaptureId === captures[0]?.capture_id?.toString();
        if (isLatest) {
          setCurrentPipelineRes(data.result);
        }
        if (switchTab) setActiveTab('strategy');
      } else {
        alert("Pipeline failed: " + data.detail);
      }
    } catch (err) {
      alert("Error running pipeline: " + err);
    } finally {
      setIsPipelineRunning(false);
    }
  };

  const renderTrustBanner = () => {
    if (!pipelineRes || !pipelineRes.provenance) return null;
    const { overall, headline, degraded, records } = pipelineRes.provenance;

    // Check for GEMINI-DOWN
    const hasGeminiDown = records.some((r: any) => r.detail && r.detail.gemini_down === true);

    if (hasGeminiDown) {
      return (
        <div className="w-full bg-rose-600 text-white px-4 py-2 flex items-center justify-center gap-2 text-sm font-bold shadow-md">
          <AlertOctagon className="w-5 h-5 animate-pulse" />
          ⛔ GEMINI DOWN — sentiment running on keyword fallback; bias UNRELIABLE
        </div>
      );
    }

    if (overall === 'PRIMARY') {
      return (
        <div className="w-full bg-emerald-600 text-white px-4 py-1.5 flex items-center justify-center gap-2 text-xs font-medium shadow-sm opacity-90">
          <Info className="w-4 h-4" />
          {headline}
        </div>
      );
    }

    const isFallback = overall === 'FALLBACK' || overall === 'UNAVAILABLE';
    const bgColor = isFallback ? 'bg-rose-600' : 'bg-amber-600';

    return (
      <div className={`w-full ${bgColor} text-white px-4 py-2 shadow-md`}>
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-2">
          <div className="flex items-center gap-2 font-bold text-sm">
            <AlertTriangle className="w-4 h-4" />
            {headline}
          </div>
          <div className="flex flex-wrap gap-2 text-xs opacity-90">
            {degraded.map((r: any, i: number) => (
              <span key={i} className="bg-white/20 px-2 py-0.5 rounded">
                <strong className="uppercase">{r.component}:</strong> {r.quality} ({r.reason.split('—')[0].trim()})
              </span>
            ))}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-100/80 text-slate-900 font-sans pb-16">
      {/* Top Header Navigation */}
      <header className="sticky top-0 z-40 bg-slate-950 text-white border-b border-slate-800 shadow-xl  bg-opacity-95">
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
              onClick={updateNews}
              disabled={isNewsUpdating}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-500 hover:from-blue-500 hover:to-indigo-400 text-white text-xs font-bold transition shadow-lg shadow-blue-500/30 cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${isNewsUpdating ? 'animate-spin' : ''}`} />
              <span className="hidden md:inline">{isNewsUpdating ? 'Updating...' : 'Update News'}</span>
            </button>
            <button
              onClick={updateFlows}
              disabled={isFlowsUpdating}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-amber-600 to-orange-500 hover:from-amber-500 hover:to-orange-400 text-white text-xs font-bold transition shadow-lg shadow-amber-500/30 cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${isFlowsUpdating ? 'animate-spin' : ''}`} />
              <span className="hidden md:inline">{isFlowsUpdating ? 'Updating...' : 'Update Flows'}</span>
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

      {/* Trust Banner */}
      {renderTrustBanner()}

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-6 space-y-6">
        {/* Error Banner */}
        {!analytics.success && (
          <div className="bg-rose-50 border-2 border-rose-300 rounded-2xl p-6 text-rose-900 shadow-md flex items-start gap-4">
            <AlertTriangle className="w-8 h-8 text-rose-600 shrink-0 mt-0.5" />
            <div className="space-y-2 flex-1">
              <h3 className="text-lg font-black">Option Chain Parsing Error</h3>
              <p className="text-sm">{analytics.error}</p>

            </div>
          </div>
        )}

        {/* Live Ticker Quick Bar */}
        {analytics.success && (
          <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-sm flex flex-wrap items-center justify-between gap-4 text-xs font-bold">
            <div className="flex items-center gap-6 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase text-[10px]">Nifty Index</span>
                <span className="text-base font-black text-slate-900" title={niftyMove?.source === 'yfinance' ? 'live (yfinance ^NSEI)' : 'from local data'}>
                  ₹{(niftyMove?.spot ?? pipelineRes?.chain_meta?.spot ?? analytics.spot ?? 0).toLocaleString('en-IN')}
                </span>
                {niftyMove && niftyMove.chg_pts != null && (
                  <span className={`text-xs font-black ${niftyMove.chg_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}
                        title={`vs prev close ${niftyMove.prev_close?.toLocaleString('en-IN')}`}>
                    {niftyMove.chg_pts >= 0 ? '▲' : '▼'} {niftyMove.chg_pts >= 0 ? '+' : '−'}{Math.abs(niftyMove.chg_pts)} ({niftyMove.chg_pct >= 0 ? '+' : '−'}{Math.abs(niftyMove.chg_pct)}%)
                  </span>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 text-slate-500 font-normal">
              <Clock className="w-3.5 h-3.5" />
              <span>NSE Weekly/Monthly Expiry Grid</span>
            </div>
          </div>
        )}

        {/* Live impact monitor — sits right under the Nifty index bar; flashes high-impact news, refreshes every 5 min */}
        <ImpactMonitorBanner />

        {/* NEW WORKSPACE SHELL */}
        <div className="flex flex-col md:flex-row gap-6">
          {/* Left Rail */}
          <div className="w-full md:w-56 shrink-0 space-y-2">

            {/* WORKSPACE NAV */}
            <div className="bg-slate-900 text-white p-2 rounded-2xl shadow-xl border border-slate-800 mb-6">
              <div className="text-[10px] uppercase font-black text-slate-500 px-2 mb-2 tracking-wider">Workspaces</div>
              <div className="flex md:flex-col gap-1 overflow-x-auto pb-1 md:pb-0">
                <button onClick={() => navigate('/intel/global')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/intel') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>1. Intelligence</button>
                <button onClick={() => navigate('/structure/intraday')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/structure') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>2. Structure</button>
                <button onClick={() => navigate('/trade/suggester')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/trade') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>3. Trade</button>
                <button onClick={() => navigate('/data/ingest')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/data') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>4. Data & Ops</button>
              </div>
            </div>

            {/* TAB NAV (Dynamic based on workspace) */}
            <div className="bg-white p-2 rounded-2xl border border-slate-200 shadow-sm">
              <div className="text-[10px] uppercase font-black text-slate-400 px-2 mb-2 tracking-wider">Views</div>
              <div className="flex md:flex-col gap-1 overflow-x-auto">

                {location.pathname.startsWith('/intel') && (
                  <>
                    <button onClick={() => navigate('/intel/global')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/global' ? 'bg-blue-50 text-blue-600' : 'text-slate-600 hover:bg-slate-50'}`}><Globe className="w-4 h-4" /> Global</button>
                    <button onClick={() => navigate('/intel/sector')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/sector' ? 'bg-emerald-50 text-emerald-600' : 'text-slate-600 hover:bg-slate-50'}`}><Newspaper className="w-4 h-4" /> Sector</button>
                    <button onClick={() => navigate('/intel/flows')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/flows' ? 'bg-teal-50 text-teal-600' : 'text-slate-600 hover:bg-slate-50'}`}><Activity className="w-4 h-4" /> Flows</button>
                    <button onClick={() => navigate('/intel/moneyflow')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/moneyflow' ? 'bg-violet-50 text-violet-600' : 'text-slate-600 hover:bg-slate-50'}`}><Layers className="w-4 h-4" /> Money vs Sentiment</button>
                    <button onClick={() => navigate('/intel/events')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/events' ? 'bg-pink-50 text-pink-600' : 'text-slate-600 hover:bg-slate-50'}`}><Calendar className="w-4 h-4" /> Events</button>
                    <button onClick={() => navigate('/intel/fundamental')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/fundamental' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Shield className="w-4 h-4" /> Fundamentals</button>
                    <button onClick={() => navigate('/intel/macro')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/macro' ? 'bg-amber-50 text-amber-600' : 'text-slate-600 hover:bg-slate-50'}`}><Zap className="w-4 h-4" /> Macro Shock</button>
                    <button onClick={() => navigate('/intel/aiinfra')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/aiinfra' ? 'bg-cyan-50 text-cyan-600' : 'text-slate-600 hover:bg-slate-50'}`}><Server className="w-4 h-4" /> AI Infra</button>
                    <button onClick={() => navigate('/intel/sector-view')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/sector-view' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Landmark className="w-4 h-4" /> Sector View</button>
                    <button onClick={() => navigate('/intel/nifty50')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/nifty50' ? 'bg-rose-50 text-rose-600' : 'text-slate-600 hover:bg-slate-50'}`}><PieChart className="w-4 h-4" /> Nifty 50</button>
                    <button onClick={() => navigate('/intel/shock-recovery')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/shock-recovery' ? 'bg-amber-50 text-amber-600' : 'text-slate-600 hover:bg-slate-50'}`}><Zap className="w-4 h-4" /> Shock Recovery</button>
                  </>
                )}

                {location.pathname.startsWith('/structure') && (
                  <>
                    <button onClick={() => navigate('/structure/intraday')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/intraday' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Clock className="w-4 h-4" /> Intraday</button>
                    <button onClick={() => navigate('/structure/chart')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/chart' ? 'bg-blue-50 text-blue-600' : 'text-slate-600 hover:bg-slate-50'}`}><TrendingUp className="w-4 h-4" /> Chart</button>
                    <button onClick={() => navigate('/structure/compare')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/compare' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Layers className="w-4 h-4" /> Compare</button>
                    <button onClick={() => navigate('/structure/marketstate')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/marketstate' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Gauge className="w-4 h-4" /> Market State</button>
                  </>
                )}

                {location.pathname.startsWith('/trade') && (
                  <>
                    <button onClick={() => navigate('/trade/suggester')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/suggester' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Sparkles className="w-4 h-4" /> Suggester</button>
                    <button onClick={() => navigate('/trade/desk')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/desk' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Crosshair className="w-4 h-4" /> Desk</button>
                    <button onClick={() => navigate('/trade/deskbook')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/deskbook' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Briefcase className="w-4 h-4" /> Desk Book</button>
                    <button onClick={() => navigate('/trade/signaltest')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/signaltest' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Activity className="w-4 h-4" /> Signal Test</button>
                    <button onClick={() => navigate('/trade/health')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/health' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Gauge className="w-4 h-4" /> Market Health</button>
                    <button onClick={() => navigate('/trade/portfolio')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/portfolio' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Briefcase className="w-4 h-4" /> Portfolio</button>
                  </>
                )}

                {location.pathname.startsWith('/data') && (
                  <>
                    <button onClick={() => navigate('/data/ingest')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/data/ingest' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><DownloadCloud className="w-4 h-4" /> Ingestion</button>
                    <button onClick={() => navigate('/data/agent')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/data/agent' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><DownloadCloud className="w-4 h-4" /> Data Agent</button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Main Content Area - Renders ALL panels but hides inactive ones to preserve state! */}
          <div className="flex-1 w-full overflow-hidden">

            <Routes>
              <Route path="/" element={<Navigate to="/trade/suggester" replace />} />
            </Routes>

            <div className="transition duration-300">

              {/* === INTELLIGENCE === */}
              <div style={{ display: location.pathname === '/intel/global' ? 'block' : 'none' }}>
                <GlobalCuesPanel cues={analytics.globalCues} pctMap={pctMap} onPctChange={(name, val) => setPctMap((prev) => ({ ...prev, [name]: val }))} onResetDefaults={handleResetPct} pipelineRes={pipelineRes} onRunPipeline={() => runQuantPipeline({ switchTab: false })} />
              </div>

              <div style={{ display: location.pathname === '/intel/sector' ? 'block' : 'none' }}>
                <SectorNewsPanel data={pipelineRes} />
                <div className="mt-6"><SectorEarningsPanel pipelineRes={pipelineRes} /></div>
              </div>

              <div style={{ display: location.pathname === '/intel/flows' ? 'block' : 'none' }}>
                <FlowsPanel />
              </div>

              <div style={{ display: location.pathname === '/intel/moneyflow' ? 'block' : 'none' }}>
                <MoneySentimentPanel />
              </div>

              <div style={{ display: location.pathname === '/intel/events' ? 'block' : 'none' }}>
                <EventCalendarPanel conclusion={pipelineRes?.conclusion} />
              </div>

              <div style={{ display: location.pathname === '/intel/fundamental' ? 'block' : 'none' }}>
                <FundamentalScreenPanel />
              </div>

              <div style={{ display: location.pathname === '/intel/macro' ? 'block' : 'none' }}>
                <MacroShockTab />
              </div>

              <div style={{ display: location.pathname === '/intel/aiinfra' ? 'block' : 'none' }}>
                <AIInfraThemePanel />
              </div>

              <div style={{ display: location.pathname === '/intel/sector-view' ? 'block' : 'none' }}>
                <SectorViewPanel />
              </div>

              <div style={{ display: location.pathname === '/intel/nifty50' ? 'block' : 'none' }}>
                <Nifty50Panel />
              </div>

              <div style={{ display: location.pathname === '/intel/shock-recovery' ? 'block' : 'none' }}>
                <ShockRecoveryPanel />
              </div>

              {/* === STRUCTURE === */}
              <div style={{ display: location.pathname === '/structure/intraday' ? 'block' : 'none' }}>
                <IntradayPanel />
              </div>

              <div style={{ display: location.pathname === '/structure/chart' ? 'block' : 'none' }}>
                <PriceChartPanel />
              </div>

              <div style={{ display: location.pathname === '/structure/compare' ? 'block' : 'none' }}>
                <CaptureComparePanel captures={captures} />
              </div>

              <div style={{ display: location.pathname === '/structure/marketstate' ? 'block' : 'none' }}>
                <MarketStateView />
              </div>

              {/* === TRADE === */}
              <div style={{ display: location.pathname === '/trade/desk' ? 'block' : 'none' }}>
                <StrategyDeskPanel />
              </div>

              <div style={{ display: location.pathname === '/trade/deskbook' ? 'block' : 'none' }}>
                <DeskStrategyView />
              </div>

              <div style={{ display: location.pathname === '/trade/signaltest' ? 'block' : 'none' }}>
                <SignalBacktestView />
              </div>

              <div style={{ display: location.pathname === '/trade/health' ? 'block' : 'none' }}>
                <MarketHealthPanel />
              </div>

              <div style={{ display: location.pathname === '/trade/suggester' ? 'block' : 'none' }}>
                <StrategySuggesterPanel
                  oiPanel={<OIPositioningPanel rows={analytics.chainRows} spot={analytics.spot} maxPain={analytics.maxPain} pcr={analytics.pcr} reads={analytics.reads} structureContext={analytics.structureContext} breadthInterpretation={pipelineRes?.interpretations?.breadth} />}
                  volPanel={<ComplacencyPanel metrics={analytics.complacencyMetrics} spot={analytics.spot} />}
                  pipelineRes={pipelineRes} rows={analytics.chainRows} spot={analytics.spot} atmIV={analytics.atmMeta.iv}
                  riskConfig={riskConfig} captureId={selectedCaptureId} onRiskConfigChange={setRiskConfig}
                  mockTrade={mockTrade} onMockTradeChange={setMockTrade} selectedOutlook={traderOutlook}
                  onOutlookChange={setTraderOutlook} optWeights={optWeights} setOptWeights={setOptWeights}
                  optBias={optBias} setOptBias={setOptBias} optMinPop={optMinPop} setOptMinPop={setOptMinPop}
                  optAllowUndefined={optAllowUndefined} setOptAllowUndefined={setOptAllowUndefined}
                  optCostPerLeg={optCostPerLeg} setOptCostPerLeg={setOptCostPerLeg} optWindowPts={optWindowPts}
                  setOptWindowPts={setOptWindowPts} optMaxWing={optMaxWing} setOptMaxWing={setOptMaxWing}
                  optTopN={optTopN} setOptTopN={setOptTopN} optMaxLossBudget={optMaxLossBudget}
                  setOptMaxLossBudget={setOptMaxLossBudget} optAllowBadRnd={optAllowBadRnd}
                  setOptAllowBadRnd={setOptAllowBadRnd} onRunPipeline={updateOptionChainAndRun}
                  uploadFile={uploadFile} setUploadFile={setUploadFile} uploadSpot={uploadSpot} setUploadSpot={setUploadSpot}
                  uploadExpiryDate={uploadExpiryDate} setUploadExpiryDate={setUploadExpiryDate} uploadVix={uploadVix} setUploadVix={setUploadVix}
                  onUploadPipeline={onUploadPipeline}
                  currentPipelineRes={historicalPipelineRes || currentPipelineRes}
                  captures={captures}
                  optionChainMode={optionChainMode}
                  setOptionChainMode={setOptionChainMode}
                  selectedDate={selectedDate}
                  setSelectedDate={setSelectedDate}
                  selectedCaptureId={selectedCaptureId}
                  setSelectedCaptureId={setSelectedCaptureId}
                  loadSelectedCapture={loadHistoricalRnd}
                  handleDeleteCapture={handleDeleteCapture}
                  isPipelineRunning={isPipelineRunning}
                  onRefreshCaptures={() => fetchCaptures(false)}
                  breezeExpiry={breezeExpiry}
                  setBreezeExpiry={setBreezeExpiry}
                />
              </div>

              <div style={{ display: location.pathname === '/trade/portfolio' ? 'block' : 'none' }}>
                <PortfolioPanel captures={captures} />
              </div>

              {/* === DATA & OPS === */}
              <div style={{ display: location.pathname === '/data/agent' ? 'block' : 'none' }}>
                <DataAgentPanel />
              </div>

              <div style={{ display: location.pathname === '/data/ingest' ? 'block' : 'none' }}>
                <BreezeSyncPanel
                  onBreezeDataLoaded={(rows, spot) => {
                    setCsvChainRows(rows);
                    setSpotOverride(spot);
                    alert("ICICI Breeze Option Chain Loaded Successfully!");
                  }}
                  onCaptureSaved={() => fetchCaptures(false)}
                />
              </div>
            </div>
          </div>
        </div>

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
    </div>
  );
}
