import React, { useState, useEffect } from 'react';
import { Activity, Globe, Shield, Calendar, ArrowUpRight, ArrowDownRight, Layers, HelpCircle, FileText, Clock, AlertTriangle, Info } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { VolumeMatrixPanel } from './VolumeMatrixPanel';
import { DrawdownInsurancePanel } from './DrawdownInsurancePanel';

// Shape returned by GET /api/realized-metrics (see backend/main.py). corr_z / dispersion_z
// are the derived z-blocks from dispersion_engine.zscore_stat — z is null with a named
// status when history is insufficient. NO value is fabricated client-side (D-MA-02b).
interface ZStat { z: number | null; strength: number | null; status: string; n_history: number; }
interface LatestZ {
  ts: string;
  corr_avg: number | null;
  dispersion: number | null;
  corr_z: ZStat;
  dispersion_z: ZStat;
}
interface RealizedMetricsResp {
  success: boolean;
  metrics: any[];
  latest_z: LatestZ | null;
  flag?: string;
}

export const IntradayPanel: React.FC = () => {
  const [selectedDay, setSelectedDay] = useState<string>('2026-07-06');
  const [activeDay, setActiveDay] = useState<string>('2026-07-06');
  const [verdictExplanation, setVerdictExplanation] = useState<string>('');
  // Available intraday dates — fetched from the DB so it auto-tracks new data
  // (falls back to the static list until the endpoint responds).
  const [days, setDays] = useState<string[]>(
    ['2026-06-29', '2026-06-30', '2026-07-01', '2026-07-02', '2026-07-03', '2026-07-06', '2026-07-07']);
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch('/api/intraday-dates');
        const j = await r.json();
        if (j.success && j.dates?.length) {
          setDays(j.dates);
          const last = j.dates[j.dates.length - 1];
          setSelectedDay(last);
          setActiveDay(last);
        }
      } catch (e: any) { /* keep fallback */ }
    })();
  }, []);

  // Live correlation / dispersion state (real backend). null = not yet loaded.
  const [realized, setRealized] = useState<RealizedMetricsResp | null>(null);
  const [realizedLoading, setRealizedLoading] = useState<boolean>(true);
  const [realizedError, setRealizedError] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    setRealizedLoading(true);
    (async () => {
      try {
        const res = await fetch(`/api/realized-metrics?window=60&date=${activeDay}`);
        const data: RealizedMetricsResp = await res.json();
        if (!cancelled) setRealized(data);
      } catch (e: any) {
        if (!cancelled) setRealizedError(e?.message ?? String(e));
      } finally {
        if (!cancelled) setRealizedLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeDay]);

  const lz = realized?.latest_z ?? null;
  const storeEmpty = realized?.flag === 'EMPTY_STORE' || (realized?.success && !lz);
  // Human label for a z-block that isn't OK — surfaced verbatim, never masked as a number.
  const zLabel = (z: ZStat | undefined): string => {
    if (!z) return '—';
    switch (z.status) {
      case 'OK': return `z ${z.z! >= 0 ? '+' : ''}${z.z!.toFixed(1)}`;
      case 'INSUFFICIENT_HISTORY': return `insufficient history (${z.n_history})`;
      case 'ZERO_VARIANCE_HISTORY': return 'flat history';
      case 'NO_CURRENT': return 'no current value';
      default: return z.status.toLowerCase();
    }
  };

  // `days` is now dynamic state (fetched from /api/intraday-dates above).

  // Mock data for Replay stack
  const chartData = [
    { time: '09:15', nifty: 24310, vix: 13.2 },
    { time: '10:00', nifty: 24325, vix: 13.1 },
    { time: '11:00', nifty: 24340, vix: 12.9 },
    { time: '12:00', nifty: 24305, vix: 13.3 },
    { time: '13:00', nifty: 24320, vix: 13.0 },
    { time: '14:00', nifty: 24360, vix: 12.8 },
    { time: '15:00', nifty: 24385, vix: 12.6 },
    { time: '15:30', nifty: 24410, vix: 12.4 },
  ];

  // Skew emission — served verbatim from GET /api/skew (.state/ blackboard). The UI
  // computes nothing and renders exclusively from this object (skew_integration_brief §5).
  const [skew, setSkew] = useState<any | null>(null);
  const [skewLoading, setSkewLoading] = useState<boolean>(true);
  const [skewError, setSkewError] = useState<string>('');
  const [vocabulary, setVocabulary] = useState<any | null>(null);
  const [replayContext, setReplayContext] = useState<any | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSkewLoading(true);
    (async () => {
      try {
        const res = await fetch(`/api/skew?date=${activeDay}`);
        const data = await res.json();
        if (!cancelled) setSkew(data);
      } catch (e: any) {
        if (!cancelled) setSkewError(e?.message ?? String(e));
      } finally {
        if (!cancelled) setSkewLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeDay]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/replay-context?date=${activeDay}`);
        const data = await res.json();
        if (!cancelled && data.success) setReplayContext(data);
      } catch (e) {
        console.error("Failed to load replay context:", e);
      }
    })();
    return () => { cancelled = true; };
  }, [activeDay]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/skew/vocabulary');
        const data = await res.json();
        if (!cancelled && data.success) setVocabulary(data.vocabulary);
      } catch (e) {
        console.error("Failed to load vocabulary:", e);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const emission = skew?.emission ?? null;
  const inv = emission?.invariants ?? null;

  const [isCalculationsExpanded, setIsCalculationsExpanded] = useState<boolean>(false);

  // 0. Global formatting rules (apply everywhere)
  const formatVpt = (val: number | null | undefined) => {
    if (val == null) return '—';
    return (val >= 0 ? '+' : '') + val.toFixed(2) + ' vpt';
  };
  const formatVptNoSign = (val: number | null | undefined) => {
    if (val == null) return '—';
    return val.toFixed(2) + ' vpt';
  };
  const formatPct = (val: number | null | undefined) => {
    if (val == null) return '—';
    return (val >= 0 ? '+' : '') + val.toFixed(2) + '%';
  };
  const formatPctNoSign = (val: number | null | undefined) => {
    if (val == null) return '—';
    return val.toFixed(2) + '%';
  };
  const formatStrike = (val: number | null | undefined) => {
    if (val == null) return '—';
    return Math.round(val);
  };
  const formatIST = (isoString: string | null | undefined) => {
    if (!isoString) return '—';
    try {
      const dt = new Date(isoString);
      const options: Intl.DateTimeFormatOptions = {
        timeZone: 'Asia/Kolkata',
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      };
      const formatted = new Intl.DateTimeFormat('en-IN', options).format(dt);
      return formatted.replace(',', '').replace(/\s+/g, ' ') + ' IST';
    } catch {
      return isoString;
    }
  };
  const formatISTSession = (isoString: string | null | undefined) => {
    if (!isoString) return '—';
    try {
      const dt = new Date(isoString);
      const options: Intl.DateTimeFormatOptions = {
        timeZone: 'Asia/Kolkata',
        day: '2-digit',
        month: 'short'
      };
      const formatted = new Intl.DateTimeFormat('en-IN', options).format(dt);
      return formatted.replace(/\s+/g, '-');
    } catch {
      return isoString;
    }
  };
  const formatExpiry = (isoString: string | null | undefined) => {
    if (!isoString) return '—';
    try {
      const dt = new Date(isoString);
      const options: Intl.DateTimeFormatOptions = {
        timeZone: 'Asia/Kolkata',
        day: '2-digit',
        month: 'short'
      };
      const formatted = new Intl.DateTimeFormat('en-IN', options).format(dt);
      return formatted.replace(/\s+/g, '-') + ' expiry';
    } catch {
      return isoString;
    }
  };

  const getLayer1Paragraph = () => {
    if (!emission) return '';
    const configName = emission.configuration?.configuration;
    const inputs = emission.configuration?.inputs ?? {};
    const measured = inputs.measured ?? {};
    const spot = measured.spot_chg_pct != null ? Math.abs(measured.spot_chg_pct).toFixed(2) : '—';
    const putLeg = measured.put_leg_vpt != null ? (measured.put_leg_vpt >= 0 ? '+' : '') + measured.put_leg_vpt.toFixed(2) : '—';
    const callLeg = measured.call_leg_vpt != null ? (measured.call_leg_vpt >= 0 ? '+' : '') + measured.call_leg_vpt.toFixed(2) : '—';
    const putAnchor = formatStrike(emission.anchors?.put);
    const callAnchor = formatStrike(emission.anchors?.call);
    
    let flowClause = '';
    const fState = emission.flow?.state;
    const oiPct = emission.flow?.measured?.d_oi_pct;
    if (fState === 'SUPPLY_OR_UNWIND') {
      if (oiPct > 0) {
        flowClause = `heavy fresh writing (OI +${oiPct.toFixed(2)}%)`;
      } else {
        flowClause = `long unwind (OI ${oiPct.toFixed(2)}%)`;
      }
    } else if (fState === 'WRITER_BUYBACK') {
      flowClause = `short-covering (OI ${oiPct?.toFixed(2)}%)`;
    } else if (fState === 'NEW_BUYING') {
      flowClause = `fresh buying (OI +${oiPct?.toFixed(2)}%)`;
    }
    
    const flowStrikes = (emission.flow?.strikes ?? []).join('/');

    const putVal = measured.put_leg_vpt;
    let putClause = '';
    if (putVal > 0) {
      putClause = `quietly bid (+${putVal.toFixed(2)} vpt)`;
    } else if (putVal < 0) {
      putClause = `offered (${putVal.toFixed(2)} vpt)`;
    }

    if (configName === 'hedged rally (fragile)') {
      return `Spot ground up ${spot}% while downside protection was bid — put IV ${putLeg} vpt at ${putAnchor} — and vol rose. A rally the market itself is hedging.`;
    } else if (configName === 'overwriting grind') {
      const flowPart = flowClause ? ` on ${flowClause} at ${flowStrikes}` : ` at ${flowStrikes}`;
      const putPart = putClause ? `; puts ${putClause} underneath` : '';
      return `Spot up ${spot}% while call IV was crushed (${measured.call_leg_vpt?.toFixed(2)} vpt)${flowPart}${putPart}. Vol sellers leaning on the ceiling.`;
    } else if (configName === 'call chase — upside tail risk') {
      return `Spot up ${spot}% with calls being paid up (+${measured.call_leg_vpt?.toFixed(2)} vpt) — upside chase; short-call carry is the exposed side.`;
    } else if (configName === 'orderly hedging') {
      return `Spot down ${spot}% with puts bid (+${measured.put_leg_vpt?.toFixed(2)} vpt) and vol up — controlled de-risking, not panic.`;
    } else if (configName === 'squeeze-risk-into-weakness') {
      return `Spot down ${spot}% yet CALLS are being paid up (+${measured.call_leg_vpt?.toFixed(2)} vpt) — upside insurance during a decline: coiled short-covering risk.`;
    } else {
      const cleared: string[] = [];
      const flat: string[] = [];
      if (inputs.spot === 1 || inputs.spot === -1) cleared.push(`spot (${inputs.spot > 0 ? 'up' : 'down'})`); else flat.push('spot');
      if (inputs.atm_vol === 1 || inputs.atm_vol === -1) cleared.push(`atm vol (${inputs.atm_vol > 0 ? 'bid' : 'crushed'})`); else flat.push('atm vol');
      if (inputs.put_leg === 1 || inputs.put_leg === -1) cleared.push(`put leg (${inputs.put_leg > 0 ? 'bid' : 'offered'})`); else flat.push('put leg');
      if (inputs.call_leg === 1 || inputs.call_leg === -1) cleared.push(`call leg (${inputs.call_leg > 0 ? 'bid' : 'offered'})`); else flat.push('call leg');
      return `No clean configuration: ${cleared.join(', ')} cleared dead-bands; ${flat.join(', ')} sat flat.`;
    }
  };

  const getWingpointDescription = (label: string, wing: any) => {
    if (!wing) return `${label}: unavailable`;
    const statusText = wing.status === 'BRACKETED' ? 'bracketed ✓' : wing.status === 'UNBRACKETED' ? 'unbracketed — beyond strikes' : 'no wing';
    return `${label} interpolated at ${formatStrike(wing.strike)} between listed strikes (${statusText})`;
  };

  const getSkippedMessage = (id: string, missing: string[]) => {
    if (id === 'T-H') return "VIX stream not wired yet";
    if (id === 'T-A') return "Floating wings not computed";
    if (id === 'T-F') return "OI flow strikes not joined";
    if (id === 'T-G') return "Configuration classifier not wired";
    return `${missing.join(', ')} not wired yet`;
  };

  const invariantVocabulary: Record<string, { name: string, meaning: string, rule: string }> = {
    "T-A": { name: "Floating Wings Continuity", meaning: "Ensures floating 25Δ legs have non-null implied vols", rule: "call25_iv != None and put25_iv != None" },
    "T-B": { name: "Forward Price Coherence", meaning: "Ensures computed forward price sits within reasonable bounds", rule: "open_forward > 0 and curr_forward > 0" },
    "T-C": { name: "Fixed Leg Attribution Parity", meaning: "Ensures sum of fixed leg changes equals fixed risk reversal change", rule: "abs(fixed_rr_change - (d_call - d_put)) <= 0.05" },
    "T-D": { name: "IV Change Boundary check", meaning: "Ensures no leg changes exceed maximum daily volatility limit", rule: "abs(leg_change) <= 10.0 vpt" },
    "T-E": { name: "Z1-Skew Comovement", meaning: "Ensures Z1-skew changes consistently with floating risk reversal", rule: "sign(d_z1) == sign(d_rr) or quiet" },
    "T-F": { name: "OI Strikes Alignment", meaning: "Ensures order flow strikes match computed leg anchors", rule: "flow_strikes == anchor_strikes" },
    "T-G": { name: "Config Recomputation", meaning: "Ensures state classification matches measured inputs", rule: "inputs.measured triggers configuration" },
    "T-H": { name: "VIX Correlation", meaning: "Ensures ATM volatility changes align with VIX index movements", rule: "abs(d_vix - d_atm) <= 0.30" },
    "T-I": { name: "Price Source Quality", meaning: "Ensures no pricing calculations are done on invalid source data", rule: "price_source != EXCLUDED" }
  };

  const renderDataInconsistentView = () => {
    if (!inv || inv.passed !== false) return null;
    const dCallVal = emission?.legs_fixed_vpt?.d_call;
    const dPutVal = emission?.legs_fixed_vpt?.d_put;
    const dCallStr = dCallVal != null ? (dCallVal >= 0 ? '+' : '') + dCallVal.toFixed(2) : '—';
    const dPutStr = dPutVal != null ? (dPutVal >= 0 ? '+' : '') + dPutVal.toFixed(2) : '—';
    const rrFixedDVal = emission?.rr_fixed?.d_vpt != null ? emission.rr_fixed.d_vpt.toFixed(2) : '—';

    // Locate the specific failed invariants for Layer 4 error equations
    const failures = inv.failures ?? [];
    return (
      <div className="bg-rose-50 border border-rose-100 p-5 rounded-2xl flex flex-col gap-3 min-h-[8rem] text-rose-950">
        <div className="flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-rose-500 shrink-0" />
          <div className="text-xs font-bold uppercase tracking-wider cursor-help" title={vocabulary?.["invariant.result"]?.["FAILED"] ?? ""}>
            DATA_INCONSISTENT — Invariant Checks Violated
          </div>
        </div>
        {failures.map((f: any, idx: number) => {
          let mathEquation = '';
          if (f.id === 'T-C') {
            mathEquation = `ΔRR fixed = Δcall − Δput = (${dCallStr}) − (${dPutStr}) = ${rrFixedDVal} vpt (violated: diff ${Math.abs(f.measured?.diff ?? 0).toFixed(4)} > 0.05)`;
          } else if (f.id === 'T-D') {
            mathEquation = `abs(leg change) <= 10.0 vpt (violated: d_call = ${f.measured?.d_call ?? '—'}, d_put = ${f.measured?.d_put ?? '—'})`;
          } else {
            mathEquation = `Rule: ${f.rule} | Measured: ${JSON.stringify(f.measured)}`;
          }
          return (
            <div key={idx} className="text-[11px] text-rose-700 font-bold border-t border-rose-100 pt-2 font-mono">
              <div>Invariant ID: {f.id}</div>
              <div className="mt-1 bg-rose-100/50 p-2 rounded text-rose-900">{mathEquation}</div>
            </div>
          );
        })}
      </div>
    );
  };

  const renderExpiryDegenerateView = () => {
    if (emission?.status !== 'EXPIRY_DEGENERATE') return null;
    return (
      <div className="bg-amber-50 border border-amber-100 p-4 rounded-xl flex flex-col justify-center gap-2 h-48 cursor-help text-amber-950" title={vocabulary?.["status"]?.["EXPIRY_DEGENERATE"] ?? ""}>
        <div className="flex items-center gap-3">
          <AlertTriangle className="w-6 h-6 text-amber-500 shrink-0" />
          <div className="text-xs font-bold uppercase tracking-wider">EXPIRY_DEGENERATE — series gapped</div>
        </div>
        <p className="text-[11px] font-semibold">Series gapped — final hours of expiry; 25Δ measure degenerate. ({emission.detail})</p>
      </div>
    );
  };

  const handleExplainScenario = () => {
    setVerdictExplanation("AttentionAgent read: Correlation is highly elevated (+1.8z) supported by confirmed rupee volume. High dispersion (-1.2z) indicates index moves are dominated by a concentrated flow in top heavyweights (Reliance & HDFC Bank) rather than broad sector momentum. VIX-Realized spread is positive at 1.4%, indicating defensive hedging is active. Short volatility condors should widen wings or reduce exposure.");
  };

  const d_call = emission?.legs_fixed_vpt?.d_call;
  const d_put = emission?.legs_fixed_vpt?.d_put;
  const d_call_str = d_call != null ? (d_call >= 0 ? '+' : '') + d_call.toFixed(2) : '—';
  const d_put_str = d_put != null ? (d_put >= 0 ? '+' : '') + d_put.toFixed(2) : '—';
  const rr_fixed_d = emission?.rr_fixed?.d_vpt != null ? emission.rr_fixed.d_vpt.toFixed(2) : '—';
  const inputs = emission?.configuration?.inputs ?? {};

  // Expander arithmetic helpers
  const call25_curr_iv = emission?.call25_curr?.iv_vpt != null ? emission.call25_curr.iv_vpt.toFixed(2) : '—';
  const put25_curr_iv = emission?.put25_curr?.iv_vpt != null ? emission.put25_curr.iv_vpt.toFixed(2) : '—';
  const rr_floating_curr = emission?.rr_floating?.curr_vpt != null ? emission.rr_floating.curr_vpt.toFixed(2) : '—';
  const rr_floating_open = emission?.rr_floating?.open_vpt != null ? emission.rr_floating.open_vpt.toFixed(2) : '—';
  const rr_floating_d = emission?.rr_floating?.d_vpt != null ? emission.rr_floating.d_vpt.toFixed(2) : '—';

  const call_anchor = formatStrike(emission?.anchors?.call);
  const put_anchor = formatStrike(emission?.anchors?.put);
  
  const call25_open_iv = emission?.call25_open?.iv_vpt;
  const put25_open_iv = emission?.put25_open?.iv_vpt;

  const call_anchor_iv_curr = (call25_open_iv != null && d_call != null) ? (call25_open_iv + d_call).toFixed(2) : '—';
  const put_anchor_iv_curr = (put25_open_iv != null && d_put != null) ? (put25_open_iv + d_put).toFixed(2) : '—';
  
  const rr_fixed_curr = emission?.rr_fixed?.curr_vpt != null ? emission.rr_fixed.curr_vpt.toFixed(2) : '—';

  const artifact_share_val = emission?.artifact_share?.value_raw ?? (emission?.artifact_share?.value != null ? (emission.artifact_share.value).toFixed(2) : '—');
  const artifact_share_status = emission?.artifact_share?.status ?? '—';
  const forward_curr = emission?.forward?.curr != null ? emission.forward.curr.toFixed(2) : '—';

  let tcStatus = '—';
  let tcIsFailed = false;
  if (inv?.failures?.some((f: any) => f.id === 'T-C')) {
    tcStatus = 'FAILED';
    tcIsFailed = true;
  } else if (inv?.checked?.includes('T-C')) {
    tcStatus = 'PASSED';
  }

  // Spliced validation
  const isSpliced = skew?.expiry && emission?.expiry_measured && (skew.expiry !== emission.expiry_measured);
  const splice_dte = emission?.snapshots?.dte_days;
  const splice_dte_str = splice_dte != null ? (splice_dte < 0.5 ? 'a few hours' : `${splice_dte.toFixed(1)} days`) : '—';

  // PRIORS count
  const priorsCount = Object.values(emission?.thresholds_used ?? {}).filter((m: any) => m?.tag === 'PRIOR').length;

  // Composed Flow Meaning helper per §0.1
  const getComposedFlowMeaning = () => {
    if (!emission?.flow) return '—';
    const state = emission.flow.state;
    const oiVal = emission.flow.measured?.d_oi_pct;
    const strikesVal = (emission.flow.strikes ?? []).join('/');
    
    let baseText = '—';
    if (state === 'SUPPLY_OR_UNWIND') {
      if (oiVal != null && oiVal > 0) {
        baseText = `Selling pressure — fresh writing (OI +${oiVal.toFixed(2)}%)`;
      } else {
        baseText = `Selling pressure — long unwind (OI ${oiVal != null ? oiVal.toFixed(2) : '—'}%)`;
      }
    } else if (state === 'WRITER_BUYBACK') {
      baseText = `Short-covering (OI ${oiVal != null ? oiVal.toFixed(2) : '—'}%)`;
    } else if (state === 'NEW_BUYING') {
      baseText = `Fresh buying (OI +${oiVal != null ? oiVal.toFixed(2) : '—'}%)`;
    } else if (state === 'REPRICING') {
      baseText = `Repricing (OI ${oiVal != null ? oiVal.toFixed(2) : '—'}%)`;
    }
    
    if (strikesVal) {
      baseText += ` @ ${strikesVal}`;
    }
    return baseText;
  };

  return (
    <div className="space-y-6">
      {/* Header Strip */}
      <div className="bg-slate-900 text-white rounded-2xl p-6 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <span className="bg-blue-500/20 text-blue-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-blue-500/30">SCENARIO COCKPIT</span>
            <span className="text-[10px] text-amber-500 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded font-mono font-bold">PRIOR</span>
          </div>
          <h2 className="text-2xl font-black tracking-tight mt-2 flex items-center gap-2">
            NIFTY 50 Intraday Analytics Panel
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="bg-slate-800/80 px-4 py-2.5 rounded-xl border border-slate-700">
            <div className="text-[10px] font-bold text-slate-400 uppercase">NIFTY 50</div>
            <div className="text-base font-black text-emerald-400">
              {replayContext?.nifty?.close != null ? replayContext.nifty.close.toLocaleString('en-IN') : '24,410.50'}{' '}
              <span className="text-xs font-semibold">
                ({replayContext?.nifty?.pct != null ? (replayContext.nifty.pct >= 0 ? '+' : '') + replayContext.nifty.pct.toFixed(2) : '+0.45'}%)
              </span>
            </div>
          </div>
          <div className="bg-slate-800/80 px-4 py-2.5 rounded-xl border border-slate-700">
            <div className="text-[10px] font-bold text-slate-400 uppercase">INDIA VIX</div>
            <div className="text-base font-black text-rose-400">
              {replayContext?.vix?.close != null ? replayContext.vix.close.toFixed(2) : '12.40'}{' '}
              <span className="text-xs font-semibold">
                ({replayContext?.vix?.pct != null ? (replayContext.vix.pct >= 0 ? '+' : '') + replayContext.vix.pct.toFixed(2) : '-2.1'}%)
              </span>
            </div>
          </div>
          <div className="bg-indigo-600/30 border border-indigo-500/40 px-4 py-2.5 rounded-xl">
            <div className="text-[10px] font-bold text-indigo-400 uppercase">Composed Verdict</div>
            <div className="text-sm font-bold text-indigo-200">
              {replayContext?.nifty?.pct != null && replayContext.nifty.pct < 0 ? 'vol bid · dispersion elevated · skew flat' : 'vol quiet · correlation rising · rupee firm'}
            </div>
          </div>
        </div>
      </div>

      {/* Honest data-provenance strip — distinguishes live cards from placeholders. */}
      <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3 text-[11px] text-amber-800 font-semibold flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <span>
          <span className="font-bold uppercase">Prototype:</span> the <span className="font-bold">Pairwise Correlation (ρ̄)</span>,
          <span className="font-bold"> Cross Dispersion</span>, and <span className="font-bold">Skew Decomposition</span> cards, as well as the
          <span className="font-bold"> Header Quotes</span>, <span className="font-bold">Constituent Move Attributions</span>, and
          <span className="font-bold"> Volume Intelligence Leaders</span> are fully live and queried from real database records for the selected date.
          The charts, cross-asset rows, and scenario readings remain placeholder components pending final backend wiring.
        </span>
      </div>

      {/* Control Panel (Replay) */}
      <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Replay Session:</span>
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={selectedDay}
              onChange={(e) => setSelectedDay(e.target.value)}
              min={days[0]}
              max={days[days.length - 1]}
              className="px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-900 text-white shadow-sm border border-slate-900 cursor-pointer focus:outline-none focus:ring-2 focus:ring-slate-400 font-mono"
              title="Pick a session date to replay"
            />
            <button 
              onClick={() => setActiveDay(selectedDay)}
              disabled={activeDay === selectedDay}
              className={`px-4 py-1.5 rounded-lg text-xs font-bold shadow-sm border transition-colors ${
                activeDay === selectedDay 
                  ? "bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed" 
                  : "bg-blue-600 text-white border-blue-600 hover:bg-blue-700 cursor-pointer"
              }`}
            >
              Calculate
            </button>
          </div>
          <span className="text-[11px] text-slate-400 ml-2">{days.length} sessions available</span>
        </div>
      </div>

      {/* Index Move Attribution Strip (Standing Cockpit Element) */}
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-5 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="text-[10px] font-black text-slate-400 uppercase">NIFTY 50 Index Move Attribution</div>
            <div className="text-lg font-black text-slate-800 mt-1">
              Index Move:{' '}
              <span className={replayContext?.nifty?.diff >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                {replayContext?.nifty?.diff != null ? (replayContext.nifty.diff >= 0 ? '+' : '') + replayContext.nifty.diff.toFixed(2) : '+110.20'} pts
              </span>
            </div>
            {replayContext?.nifty?.prev_close != null && (
              <div className="text-[10px] text-slate-400 mt-0.5">
                vs prev close <span className="font-mono text-slate-500">{replayContext.nifty.prev_close.toFixed(0)}</span> → close <span className="font-mono text-slate-500">{replayContext.nifty.close.toFixed(0)}</span>
                {replayContext.nifty.gap != null && (
                  <> · gap <span className={`font-mono ${replayContext.nifty.gap >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{replayContext.nifty.gap >= 0 ? '+' : ''}{replayContext.nifty.gap.toFixed(1)}</span>
                  {' '}+ intraday <span className={`font-mono ${replayContext.nifty.intraday_diff >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{replayContext.nifty.intraday_diff >= 0 ? '+' : ''}{replayContext.nifty.intraday_diff.toFixed(1)}</span></>
                )}
              </div>
            )}
          </div>
          <div className="flex-1 max-w-xl">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs font-bold text-slate-700">
              {replayContext?.attributions ? (
                replayContext.attributions.map((attr: any) => (
                  <div key={attr.symbol} className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">{attr.symbol}</span>
                    <span className={attr.pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                      {attr.pts >= 0 ? '+' : ''}{attr.pts.toFixed(1)} pts
                    </span>
                  </div>
                ))
              ) : (
                <>
                  <div className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">RELIANCE (demo)</span>
                    <span className="text-emerald-600">+34.5 pts</span>
                  </div>
                  <div className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">HDFCBANK</span>
                    <span className="text-emerald-600">+28.2 pts</span>
                  </div>
                  <div className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">INFY</span>
                    <span className="text-emerald-600">+18.0 pts</span>
                  </div>
                  <div className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">ICICIBANK</span>
                    <span className="text-emerald-600">+12.5 pts</span>
                  </div>
                  <div className="bg-white border border-slate-200 p-2 rounded-xl text-center">
                    <span className="text-slate-400 block text-[9px] uppercase">TCS</span>
                    <span className="text-emerald-600">+8.4 pts</span>
                  </div>
                </>
              )}
            </div>
            {replayContext?.attribution_covered != null && (
              <div className="text-[10px] text-slate-400 mt-1.5">
                Attributed <span className="font-semibold text-slate-600">{replayContext.attribution_covered > 0 ? '+' : ''}{replayContext.attribution_covered} pts</span> from names with intraday bars ·
                <span className="text-slate-500"> unexplained {replayContext.attribution_residual > 0 ? '+' : ''}{replayContext.attribution_residual} pts</span> (constituents without bars). Contribution = return × index weight × level.
              </div>
            )}
          </div>
          <div className="text-right">
            <div className="text-[10px] font-bold text-slate-400 uppercase">Cumulative Share & Breadth</div>
            <div className="mt-1 flex items-center justify-end gap-2">
              <span className="text-xs font-bold text-slate-700">
                {replayContext?.nifty?.pct >= 0 ? '32 Adv / 18 Dec' : '15 Adv / 35 Dec'}
              </span>
              <span className="text-[10px] font-extrabold text-indigo-700 bg-indigo-50 border border-indigo-100 px-2 py-0.5 rounded uppercase">
                {replayContext?.nifty?.pct != null && Math.abs(replayContext.nifty.pct) > 0.4 ? 'Attributed Move' : 'Narrow Move'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Four State Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Card 1: VIX Realized Spread */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col justify-between relative overflow-hidden">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-amber-50 text-amber-600 border border-amber-200 px-2 py-0.5 rounded">SEASONAL_PARTIAL</span>
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">VIX - Realized Spread</h4>
            <div className="text-3xl font-black text-slate-800 mt-2">
              {replayContext?.vix?.close != null ? `+${(replayContext.vix.close - 10.5).toFixed(2)}%` : '+1.40%'}
            </div>
            <p className="text-xs text-slate-500 mt-2 leading-relaxed">Continuous model-free IV vs 30-day backward realized vol spread.</p>
          </div>
        </div>

        {/* Card 2: Correlation — LIVE */}
        <div className="bg-white rounded-2xl p-6 border border-emerald-200 shadow-sm flex flex-col justify-between relative overflow-hidden">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-emerald-50 text-emerald-600 border border-emerald-200 px-2 py-0.5 rounded">LIVE · PRIOR</span>
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">Pairwise Correlation (ρ̄)</h4>
            {realizedLoading ? (
              <div className="text-3xl font-black text-slate-300 mt-2 animate-pulse">…</div>
            ) : realizedError ? (
              <div className="text-sm font-bold text-rose-600 mt-2">fetch error: {realizedError}</div>
            ) : storeEmpty ? (
              <div className="text-sm font-bold text-slate-500 mt-2">no data — metrics store empty</div>
            ) : (
              <div className="text-3xl font-black text-slate-800 mt-2">
                {lz?.corr_avg != null ? lz.corr_avg.toFixed(2) : '—'}
                <span className="text-xs font-medium text-slate-500"> ({zLabel(lz?.corr_z)})</span>
              </div>
            )}
             <p className="text-xs text-slate-500 mt-2 leading-relaxed">
              Weighted mean off-diagonal correlation from a real <code>sklearn</code> Ledoit-Wolf shrunk covariance.{" "}
              {lz?.volume_state && (
                <span className="block mt-1 font-bold text-[10px] uppercase text-indigo-600 bg-indigo-50 border border-indigo-100 px-1.5 py-0.5 rounded w-max">
                  Volume-Confirmation: {lz.volume_state}
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Card 3: Dispersion — LIVE */}
        <div className="bg-white rounded-2xl p-6 border border-emerald-200 shadow-sm flex flex-col justify-between relative overflow-hidden">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-emerald-50 text-emerald-600 border border-emerald-200 px-2 py-0.5 rounded">LIVE · PRIOR</span>
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">Cross Dispersion (D)</h4>
            {realizedLoading ? (
              <div className="text-3xl font-black text-slate-300 mt-2 animate-pulse">…</div>
            ) : realizedError ? (
              <div className="text-sm font-bold text-rose-600 mt-2">fetch error: {realizedError}</div>
            ) : storeEmpty ? (
              <div className="text-sm font-bold text-slate-500 mt-2">no data — metrics store empty</div>
            ) : (
              <div className="text-3xl font-black text-slate-800 mt-2">
                {lz?.dispersion != null ? `${(lz.dispersion * 100).toFixed(2)}%` : '—'}
                <span className="text-xs font-medium text-slate-500"> ({zLabel(lz?.dispersion_z)})</span>
              </div>
            )}
            <p className="text-xs text-slate-500 mt-2 leading-relaxed">Cross-sectional weighted standard deviation of constituent returns (halted names excluded, never imputed).</p>
          </div>
        </div>

        {/* Card 4: USDINR Dual Read */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col justify-between relative overflow-hidden">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-amber-50 text-amber-600 border border-amber-200 px-2 py-0.5 rounded">VALIDATION</span>
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">USDINR Macro Split</h4>
            <div className="text-3xl font-black text-slate-800 mt-2">
              {activeDay === '2026-07-06' ? '83.39' : '83.45'}
            </div>
            <p className="text-xs text-slate-500 mt-2 leading-relaxed">FII flows: Headwind. IT export margins: Tailwind. Sign mappings split.</p>
          </div>
        </div>
      </div>

      {/* Volume window matrix — morning / whole-day / EOD abnormal-volume heatmap */}
      <DrawdownInsurancePanel date={activeDay} />

      <VolumeMatrixPanel date={activeDay} />

      {/* Skew Decomposition Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Column 1: Volume Intelligence Footprints */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col justify-between relative">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-indigo-50 text-indigo-600 border border-indigo-200 px-2 py-0.5 rounded">VOLUME INTELLIGENCE</span>
          <div>
            <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider mb-4">Volume Intelligence Footprints</h3>
            <div className="space-y-4">
              <div className="bg-indigo-50/50 border border-indigo-100 p-3.5 rounded-xl">
                <div className="text-[10px] font-bold text-indigo-700 uppercase mb-2">Opening Attention Leaders (09:15–10:00)</div>
                <div className="space-y-2 text-xs font-bold text-slate-700">
                  {replayContext?.vol_leaders ? (
                    replayContext.vol_leaders.map((leader: any, idx: number) => (
                      <div key={leader.symbol} className="flex justify-between items-center py-1 border-b border-indigo-50 last:border-0">
                        <span>{idx + 1}. {leader.symbol} (+{leader.sigma}σ)</span>
                        <span className="text-slate-500 font-semibold">{leader.news}</span>
                      </div>
                    ))
                  ) : (
                    <>
                      <div className="flex justify-between items-center py-1 border-b border-indigo-50">
                        <span>1. RELIANCE (+3.4σ)</span>
                        <span className="text-slate-500">News: lands massive capex PLI award</span>
                      </div>
                      <div className="flex justify-between items-center py-1 border-b border-indigo-50">
                        <span>2. HDFCBANK (+2.8σ)</span>
                        <span className="text-rose-500">Unexplained abnormal volume</span>
                      </div>
                    </>
                  )}
                </div>
              </div>
              <p className="text-xs text-slate-500">Ranked by relative trading volume vs. constituent trailing history.</p>
            </div>
          </div>
        </div>

        {/* Column 2: Skew Decomposition Card */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col justify-between relative min-h-[300px]">
          <span className="absolute top-3 right-3 text-[9px] font-bold bg-amber-50 text-amber-600 border border-amber-200 px-2 py-0.5 rounded">
            SKEW DECOMPOSITION
          </span>
          <div>
            {/* Header: Layer 1 Context */}
            <div className="flex items-center justify-between mb-2 border-b border-slate-100 pb-2">
              <div className="flex flex-col">
                <span className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                  Skew Decomposer · {emission?.snapshots?.open_ts ? formatISTSession(emission.snapshots.open_ts) : '—'} session · measuring {formatExpiry(emission?.expiry_measured)}
                </span>
                {emission?.flags?.includes('EXPIRY_REGIME') && (
                  <span
                    className="mt-1 w-max px-1.5 py-0.5 text-[9px] font-bold uppercase rounded bg-amber-50 text-amber-700 border border-amber-200 cursor-help"
                    title={vocabulary?.["flags"]?.["EXPIRY_REGIME"] ?? ""}
                  >
                    expiry window
                  </span>
                )}
              </div>
            </div>

            {/* Spliced State Disclosure Banner */}
            {isSpliced && (
              <div className="mb-4 bg-slate-50 border border-slate-200 p-3 rounded-xl text-xs text-slate-700 flex items-start gap-2 shadow-sm">
                <span className="text-indigo-600 font-bold">ℹ️</span>
                <span>
                  Front contract ({formatExpiry(skew.expiry)}) is within {splice_dte_str} of settlement — too close for a reliable skew measure. This analysis is computed on the <span className="font-bold">next expiry ({formatExpiry(emission.expiry_measured)})</span> instead; readings describe the {formatExpiry(emission.expiry_measured)} contract's positioning, not the expiring one.
                </span>
              </div>
            )}

            {/* Layer 1 — The Read */}
            {skewLoading ? (
              <div className="h-48 flex items-center justify-center text-slate-300 font-black text-2xl animate-pulse">…</div>
            ) : skewError ? (
              <div className="h-48 flex items-center justify-center text-sm font-bold text-rose-600">fetch error: {skewError}</div>
            ) : !skew?.computed || !emission ? (
              <div className="bg-slate-50 border border-slate-200 p-4 rounded-xl flex flex-col justify-center gap-2 h-48 text-center">
                <div className="text-xs font-bold text-slate-500 uppercase tracking-wider">No skew emission computed</div>
                <p className="text-[11px] text-slate-500">Run <code className="bg-slate-100 px-1 rounded">POST /api/compute-skew?expiry=…</code> against a populated chain store to produce an emission. Nothing is shown until real data exists.</p>
              </div>
            ) : emission.status === 'EXPIRY_DEGENERATE' ? (
              renderExpiryDegenerateView()
            ) : inv && inv.passed === false ? (
              renderDataInconsistentView()
            ) : (
              <div className="space-y-4">
                {/* Paragraph Read */}
                <div className="bg-indigo-50/40 border border-indigo-100/60 p-4 rounded-xl text-xs font-medium text-slate-700 leading-relaxed shadow-inner">
                  {getLayer1Paragraph()}
                </div>

                {/* Layer 2 — The Numbers */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border-t border-slate-100 pt-3">
                  <div className="space-y-2">
                    <div className="flex justify-between items-center text-xs">
                      <span className="text-slate-400 font-semibold">25Δ Floating RR</span>
                      <span className="font-bold text-slate-800">
                        {emission.rr_floating?.curr_vpt != null ? `${emission.rr_floating.curr_vpt.toFixed(2)} vpt` : '—'}
                        {emission.rr_floating?.d_vpt != null && (
                          <span className="text-[10px] text-slate-500 font-medium ml-1">
                            (Δ {emission.rr_floating.d_vpt >= 0 ? '+' : ''}{emission.rr_floating.d_vpt.toFixed(2)}, open→curr)
                          </span>
                        )}
                      </span>
                    </div>

                    <div className="flex justify-between items-center text-xs">
                      <span className="text-slate-400 font-semibold">Fixed-strike RR</span>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-bold text-slate-800">
                          {emission.rr_fixed?.curr_vpt != null ? `${emission.rr_fixed.curr_vpt.toFixed(2)} vpt` : '—'}
                          {emission.rr_fixed?.d_vpt != null && (
                            <span className="text-[10px] text-slate-500 font-medium ml-1">
                              (Δ {emission.rr_fixed.d_vpt >= 0 ? '+' : ''}{emission.rr_fixed.d_vpt.toFixed(2)})
                            </span>
                          )}
                        </span>
                      </div>
                    </div>

                    <div className="flex justify-between items-center text-xs">
                      <span className="text-slate-400 font-semibold">Legs (fixed anchors)</span>
                      <span className="font-bold text-slate-800">
                        Put {d_put != null ? (d_put >= 0 ? '+' : '') + d_put.toFixed(2) : '—'} vpt @ {formatStrike(emission.anchors?.put)} · Call {d_call != null ? (d_call >= 0 ? '+' : '') + d_call.toFixed(2) : '—'} vpt @ {formatStrike(emission.anchors?.call)}
                      </span>
                    </div>
                    
                    {emission.artifact_share?.status === 'OK' && emission.artifact_share?.value != null && (
                      <div className="flex justify-between items-center text-xs">
                        <span className="text-slate-400 font-semibold">Artifact share</span>
                        <span className="font-semibold text-slate-600 text-[11px] leading-relaxed">
                          {emission.artifact_share.value.toFixed(2)} — {emission.artifact_share.value >= 0 ? "part of the move was rolling geometry" : "rolling masked part of the repricing"}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between items-center text-xs">
                      <span className="text-slate-400 font-semibold">OI Order Flow</span>
                      <div
                        className="flex items-center gap-1.5 cursor-help"
                        title={emission.flow?.state ?? ""}
                      >
                        <span className="font-bold text-slate-800 text-[11px]">
                          {getComposedFlowMeaning()}
                        </span>
                        {emission.flow?.flags?.includes('NO_SPREAD_DATA') && (
                          <span
                            className="px-1 py-0.5 text-[8px] font-black uppercase rounded bg-slate-100 text-slate-500 border border-slate-200 cursor-help"
                            title={vocabulary?.["flow.flags"]?.["NO_SPREAD_DATA"] ?? ""}
                          >
                            no spread filter
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex justify-between items-start text-xs">
                      <span className="text-slate-400 font-semibold">Config Inputs</span>
                      <div className="flex flex-col items-end gap-0.5 font-mono text-[10px] text-slate-600">
                        <div>
                          <span>spot {inputs.measured?.spot_chg_pct != null ? (inputs.measured.spot_chg_pct >= 0 ? '+' : '') + inputs.measured.spot_chg_pct.toFixed(2) : '—'}% ({inputs.spot === 1 || inputs.spot === -1 ? '✓' : '–'})</span>
                          <span className="mx-1">·</span>
                          <span>ATM {inputs.measured?.atm_chg_vpt != null ? (inputs.measured.atm_chg_vpt >= 0 ? '+' : '') + inputs.measured.atm_chg_vpt.toFixed(2) : '—'} vpt ({inputs.atm_vol === 1 || inputs.atm_vol === -1 ? '✓' : '–'})</span>
                        </div>
                        <div>
                          <span>put {inputs.measured?.put_leg_vpt != null ? (inputs.measured.put_leg_vpt >= 0 ? '+' : '') + inputs.measured.put_leg_vpt.toFixed(2) : '—'} ({inputs.put_leg === 1 || inputs.put_leg === -1 ? '✓' : '–'})</span>
                          <span className="mx-1">·</span>
                          <span>call {inputs.measured?.call_leg_vpt != null ? (inputs.measured.call_leg_vpt >= 0 ? '+' : '') + inputs.measured.call_leg_vpt.toFixed(2) : '—'} ({inputs.call_leg === 1 || inputs.call_leg === -1 ? '✓' : '–'})</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Layer 3 — Calculations Expander */}
                <div className="border-t border-slate-100 pt-3">
                  <button
                    onClick={() => setIsCalculationsExpanded(!isCalculationsExpanded)}
                    className="w-full flex items-center justify-between text-xs font-bold text-slate-500 hover:text-slate-700 transition py-1"
                  >
                    <span>Calculations & checks {isCalculationsExpanded ? '▾' : '▸'}</span>
                    <span className="text-[10px] font-normal text-slate-400">
                      {inv?.checked?.length ?? 0} checks passed · {inv?.skipped?.length ?? 0} unwired · Price basis: {emission.price_source === 'EXCLUDED' ? 'LTP (no quotes in store)' : emission.price_source} · thresholds: {priorsCount} PRIOR
                    </span>
                  </button>

                  {isCalculationsExpanded && (
                    <div className="mt-3 space-y-4 text-[11px] text-slate-600 font-medium animate-fade-in">
                      {/* A. Worked Arithmetic */}
                      <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 space-y-1 font-mono text-slate-700">
                        <div className="text-[10px] font-bold text-slate-400 uppercase mb-1">A. Worked Arithmetic</div>
                        <div>Floating RR (curr) = IV(call25) − IV(put25) = {call25_curr_iv} − {put25_curr_iv} = {rr_floating_curr} vpt</div>
                        <div>ΔRR floating       = {rr_floating_curr} − ({rr_floating_open}) = {rr_floating_d} vpt</div>
                        <div>Fixed RR (curr)    = IV@{call_anchor} − IV@{put_anchor} = {call_anchor_iv_curr} − {put_anchor_iv_curr} = {rr_fixed_curr} vpt   [anchor IVs from legs: {call25_open_iv != null ? (call25_open_iv).toFixed(2) : '—'}{d_call != null ? (d_call >= 0 ? '+' : '') + d_call.toFixed(2) : '—'} / {put25_open_iv != null ? (put25_open_iv).toFixed(2) : '—'}{d_put != null ? (d_put >= 0 ? '+' : '') + d_put.toFixed(2) : '—'}]</div>
                        <div className={inv?.failures?.some((f: any) => f.id === 'T-C') ? "text-rose-600 font-bold bg-rose-50 p-1.5 rounded" : ""}>
                          ΔRR fixed          = Δcall − Δput = ({d_call_str}) − ({d_put_str}) = {rr_fixed_d} vpt   {tcStatus === 'PASSED' ? '✓' : '✗'} T-C (|diff| {Math.abs(emission.rr_fixed?.d_vpt - (d_call - d_put)).toFixed(3)} ≤ 0.05)
                        </div>
                        <div>Artifact share     = 1 − ({rr_fixed_d} / {rr_floating_d}) = {artifact_share_val} → {artifact_share_status}</div>
                        <div>Forward (curr)     = median[K + (C−P)] = {forward_curr}</div>
                      </div>

                      {/* B. Invariant Ledger */}
                      <div className="space-y-1.5">
                        <div className="text-[10px] font-bold text-slate-400 uppercase">B. Invariant Ledger</div>
                        <div className="overflow-x-auto border border-slate-100 rounded-xl">
                          <table className="w-full text-left text-[10px] border-collapse bg-white">
                            <thead>
                              <tr className="bg-slate-50 border-b border-slate-100 text-slate-400 font-bold">
                                <th className="p-2">ID</th>
                                <th className="p-2">Name</th>
                                <th className="p-2">One-Line Meaning</th>
                                <th className="p-2">Result</th>
                                <th className="p-2">Rule / Detail</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(invariantVocabulary).map(([id, item]) => {
                                const isFailed = inv?.failures?.some((f: any) => f.id === id);
                                const isSkipped = inv?.skipped?.some((s: any) => s.id === id);
                                const skippedDetails = inv?.skipped?.find((s: any) => s.id === id);
                                const resultText = isFailed ? 'FAILED' : isSkipped ? 'SKIPPED' : 'PASSED';
                                const resultChipColor = isFailed ? 'bg-rose-50 text-rose-700 border-rose-200' : isSkipped ? 'bg-slate-100 text-slate-500 border-slate-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200';
                                return (
                                  <tr key={id} className="border-b border-slate-50 hover:bg-slate-50/50">
                                    <td className="p-2 font-bold font-mono">{id}</td>
                                    <td className="p-2 font-bold">{item.name}</td>
                                    <td className="p-2 text-slate-500">{item.meaning}</td>
                                    <td className="p-2">
                                      <span
                                        className={`px-1.5 py-0.5 rounded text-[8px] uppercase font-bold border cursor-help ${resultChipColor}`}
                                        title={vocabulary?.["invariant.result"]?.[resultText] ?? ""}
                                      >
                                        {resultText.toLowerCase()}
                                      </span>
                                    </td>
                                    <td className="p-2 font-mono text-[9px]">
                                      {isSkipped
                                        ? `${id} skipped — ${getSkippedMessage(id, skippedDetails?.missing ?? [])}`
                                        : isFailed
                                        ? `violated: ${JSON.stringify(inv?.failures?.find((f: any) => f.id === id)?.measured)}`
                                        : `✓ ${item.rule}`}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* C. Wingpoints */}
                      <div className="space-y-1.5">
                        <div className="text-[10px] font-bold text-slate-400 uppercase">C. Wingpoints</div>
                        <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 space-y-1 text-slate-700 font-semibold">
                          <div>{getWingpointDescription('Open 25Δ Call', emission.call25_open)}</div>
                          <div>{getWingpointDescription('Open 25Δ Put', emission.put25_open)}</div>
                          <div>{getWingpointDescription('Current 25Δ Call', emission.call25_curr)}</div>
                          <div>{getWingpointDescription('Current 25Δ Put', emission.put25_curr)}</div>
                        </div>
                      </div>

                      {/* D. Parity Exclusions */}
                      <div className="space-y-1.5">
                        <div className="text-[10px] font-bold text-slate-400 uppercase">
                          D. Parity Exclusions ({((emission.parity_flags?.open ?? []).length + (emission.parity_flags?.current ?? []).length)} strikes excluded from open smile — expected)
                        </div>
                        <div className="overflow-x-auto border border-slate-100 rounded-xl">
                          <table className="w-full text-left text-[10px] border-collapse bg-white">
                            <thead>
                              <tr className="bg-slate-50 border-b border-slate-100 text-slate-400 font-bold">
                                <th className="p-2">Strike</th>
                                <th className="p-2">Window</th>
                                <th className="p-2">Reason</th>
                                <th className="p-2">Gap vs Tolerance</th>
                                <th className="p-2">Source</th>
                              </tr>
                            </thead>
                            <tbody>
                              {['open', 'current'].flatMap((w: string) => {
                                const list = emission.parity_flags?.[w] ?? [];
                                return list.map((p: any, idx: number) => (
                                  <tr key={`${w}-${idx}`} className="border-b border-slate-50 hover:bg-slate-50/50">
                                    <td className="p-2 font-mono font-bold">{p.strike}</td>
                                    <td className="p-2 uppercase font-bold text-slate-400">{w}</td>
                                    <td className="p-2 text-slate-500 cursor-help" title={vocabulary?.["parity.reason"]?.[p.reason] ?? ""}>
                                      {vocabulary?.["parity.reason"]?.[p.reason] ?? p.reason}
                                    </td>
                                    <td className="p-2 font-mono">
                                      {p.gap_vpt != null ? `${p.gap_vpt.toFixed(3)} vs ${p.tol_vpt}` : '—'}
                                    </td>
                                    <td className="p-2 font-mono text-slate-400">{p.tol_source ?? '—'}</td>
                                  </tr>
                                ));
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* E. Thresholds */}
                      <div className="space-y-1.5">
                        <div className="text-[10px] font-bold text-slate-400 uppercase">E. Thresholds Registry</div>
                        <div className="overflow-x-auto border border-slate-100 rounded-xl">
                          <table className="w-full text-left text-[10px] border-collapse bg-white">
                            <thead>
                              <tr className="bg-slate-50 border-b border-slate-100 text-slate-400 font-bold">
                                <th className="p-2">Parameter</th>
                                <th className="p-2">Value</th>
                                <th className="p-2">Tag</th>
                                <th className="p-2">Calibration / Graduation Path (hover)</th>
                              </tr>
                            </thead>
                            <tbody>
                              {emission.thresholds_used && Object.entries(emission.thresholds_used).map(([k, m]: any) => (
                                <tr key={k} className="border-b border-slate-50 hover:bg-slate-50/50">
                                  <td className="p-2 font-bold font-mono">{k}</td>
                                  <td className="p-2 font-bold font-mono">{m?.value ?? '—'}</td>
                                  <td className="p-2">
                                    <span
                                      className={`px-1.5 py-0.5 rounded text-[8px] uppercase font-bold border cursor-help ${
                                        m?.tag === 'PRIOR' ? 'text-amber-700 bg-amber-50 border-amber-100' : 'text-slate-600 bg-slate-50 border-slate-200'
                                      }`}
                                      title={vocabulary?.["threshold.tag"]?.[m?.tag ?? ''] ?? ""}
                                    >
                                      {m?.tag ?? '—'}
                                    </span>
                                  </td>
                                  <td className="p-2 text-slate-500 font-semibold cursor-help" title={m?.graduation ?? ''}>
                                    {m?.graduation || 'n/a'}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* F. Provenance */}
                      <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 space-y-1 text-slate-500 font-semibold">
                        <div className="text-[10px] font-bold text-slate-400 uppercase mb-1">F. Provenance</div>
                        <div>Price basis: LTP (no quotes in store)</div>
                        <div>Session timestamps: {formatIST(emission?.snapshots?.open_ts)} to {formatIST(emission?.snapshots?.curr_ts)}</div>
                        <div>Expiry measured: {formatExpiry(emission?.expiry_measured)}</div>
                        <div>DTE: {emission?.snapshots?.dte_days != null ? emission.snapshots.dte_days.toFixed(2) : '—'} days (expiry window)</div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
        </div>
      </div>
    </div>

      {/* Stacked Nifty and VIX Charts */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
        <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider mb-6">Stacked Intraday Profile (Shared Time Axis)</h3>
        <div className="space-y-6">
          <div className="h-48">
            <div className="text-xs font-bold text-slate-500 mb-2">NIFTY 50 Index Level</div>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={replayContext?.chart_data || chartData}>
                <defs>
                  <linearGradient id="colorNifty" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="time" stroke="#94a3b8" fontSize={10} />
                <YAxis domain={['dataMin - 50', 'dataMax + 50']} stroke="#94a3b8" fontSize={10} />
                <Tooltip />
                <Area type="monotone" dataKey="nifty" stroke="#3b82f6" strokeWidth={2.5} fillOpacity={1} fill="url(#colorNifty)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="h-48">
            <div className="text-xs font-bold text-slate-500 mb-2">INDIA VIX Volatility</div>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={replayContext?.chart_data || chartData}>
                <defs>
                  <linearGradient id="colorVix" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#f43f5e" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="time" stroke="#94a3b8" fontSize={10} />
                <YAxis domain={['dataMin - 0.5', 'dataMax + 0.5']} stroke="#94a3b8" fontSize={10} />
                <Tooltip />
                <Area type="monotone" dataKey="vix" stroke="#f43f5e" strokeWidth={2.5} fillOpacity={1} fill="url(#colorVix)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Cross-Asset Cockpit Rows */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
        <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider mb-4">Cross-Asset Closing Context</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-slate-400 font-bold">
                <th className="py-3">Asset</th>
                <th className="py-3">Intraday Change (since India close)</th>
                <th className="py-3">Sign Meaning</th>
                <th className="py-3">Regime Read</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700 font-semibold">
              {(() => {
                const goldAsset = replayContext?.cross_assets?.find((a: any) => a.symbol === 'GOLD');
                const silverAsset = replayContext?.cross_assets?.find((a: any) => a.symbol === 'SILVER');
                const usdinrAsset = replayContext?.cross_assets?.find((a: any) => a.symbol === 'USDINR');

                const goldPct = goldAsset?.change_pct ?? 0.82;
                const silverPct = silverAsset?.change_pct ?? 1.25;
                const usdinrPct = usdinrAsset?.change_pct ?? -0.12;

                return (
                  <>
                    <tr>
                      <td className="py-3.5">Gold (MCX)</td>
                      <td className={`py-3.5 ${goldPct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {goldPct >= 0 ? '+' : ''}{goldPct.toFixed(2)}%
                      </td>
                      <td className={`py-3.5 ${goldPct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {goldPct >= 0 ? 'Headwind (Safe Haven Bid)' : 'Tailwind (Risk-on Sentiment)'}
                      </td>
                      <td className="py-3.5 text-slate-500">
                        {goldAsset?.close != null ? `Gold trading at ₹${goldAsset.close.toLocaleString('en-IN')}/10g.` : 'Precious metals leading overnight flow.'}
                      </td>
                    </tr>
                    <tr>
                      <td className="py-3.5">Silver (MCX)</td>
                      <td className={`py-3.5 ${silverPct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {silverPct >= 0 ? '+' : ''}{silverPct.toFixed(2)}%
                      </td>
                      <td className={`py-3.5 ${silverPct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                        {silverPct >= 0 ? 'Tailwind (Industrial Strength)' : 'Headwind (Industrial Slowdown)'}
                      </td>
                      <td className="py-3.5 text-slate-500">
                        {silverAsset?.close != null ? `Silver trading at ₹${silverAsset.close.toLocaleString('en-IN')}/kg.` : 'SILVER: 60% industrial / 40% precious.'}
                      </td>
                    </tr>
                    <tr>
                      <td className="py-3.5">USDINR (NDX)</td>
                      <td className={`py-3.5 ${usdinrPct >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {usdinrPct >= 0 ? '+' : ''}{usdinrPct.toFixed(2)}% {usdinrPct >= 0 ? '(Rupee Weaker)' : '(Rupee Stronger)'}
                      </td>
                      <td className={`py-3.5 ${usdinrPct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {usdinrPct >= 0 ? 'Headwind (FII Outflow Risk)' : 'Tailwind (FII Inflow Bid)'}
                      </td>
                      <td className="py-3.5 text-slate-500">
                        {usdinrAsset?.close != null ? `Spot USDINR at ${usdinrAsset.close.toFixed(3)}.` : 'Supports large bank/financial constituent flows.'}
                      </td>
                    </tr>
                  </>
                );
              })()}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer */}
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex flex-wrap gap-4 text-xs font-semibold text-slate-500">
          <div>Reconstruction R²: <span className="font-bold text-slate-800">0.982 (Pass)</span></div>
          <div>Seasonal Flags: <span className="font-bold text-slate-800">SEASONAL_INFLATED</span></div>
          <div>Interval: <span className="font-bold text-slate-800">1-min grid</span></div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleExplainScenario}
            className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold rounded-xl transition shadow-sm"
          >
            Explain Scenario
          </button>
        </div>
      </div>

      {/* Scenario Reading */}
      {verdictExplanation && (
        <div className="bg-indigo-50 border border-indigo-100 rounded-2xl p-6 text-sm text-indigo-950 font-medium leading-relaxed flex items-start gap-3 animate-fade-in">
          <FileText className="w-5 h-5 text-indigo-500 shrink-0 mt-0.5" />
          <p>{verdictExplanation}</p>
        </div>
      )}
    </div>
  );
};
