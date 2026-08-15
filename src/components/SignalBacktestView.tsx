import React, { useState, useEffect, useCallback } from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid, Cell,
  ComposedChart, Line, Scatter, ZAxis,
} from 'recharts';
import { Activity, Play, AlertTriangle } from 'lucide-react';
import { fmtInr, pnlColor, fmtIST, Tile, Field } from './deskShared';
import { useSignalRoster, rosterStatusText, setMomentumWindow } from '../lib/signalRoster';

/*
 * SignalBacktestView
 * ------------------
 * Validate each signal on its own: at every snapshot, compare the signal's score
 * to the actual NIFTY index move `horizon` hours later. Shows hit rate,
 * correlation, average forward move when bullish vs bearish, a score-bucket →
 * avg-forward-move chart (does a stronger signal mean a bigger move?), and the
 * biggest misses (high conviction, wrong direction — "what's missing").
 *
 * The signal roster is NOT declared here — it comes from /api/strategy/config,
 * i.e. straight from signals/registry.py. Add a SignalSpec row there and it
 * appears in every dropdown, label and table below automatically.
 */

export const SignalBacktestView: React.FC = () => {
  // Roster from the backend registry — directional only (gates/overlays carry no
  // score, so there is nothing to IC-test for them).
  const roster = useSignalRoster();
  const labelOf = roster.label;

  const [mode, setMode] = useState<'all' | 'single' | 'attribution' | 'correlation' | 'effectiveness' | 'phasegrid' | 'timeseries' | 'regimehorizon'>('all');
  const [rhMatrix, setRhMatrix] = useState<any>(null);
  const [rhSignal, setRhSignal] = useState('');
  const [rhMinN, setRhMinN] = useState(10);   // min INDEPENDENT (non-overlapping) windows
  const [rhRegimeBy, setRhRegimeBy] = useState<'tape_vol' | 'oi'>('tape_vol');
  const [rhHorizons, setRhHorizons] = useState('15,30,60');   // forward horizons, minutes
  const [phaseGrid, setPhaseGrid] = useState<any>(null);
  const [phaseDate, setPhaseDate] = useState('');
  const [oiSymbol, setOiSymbol] = useState('NIFTY');
  const [series, setSeries] = useState<any>(null);
  const [tsSignal, setTsSignal] = useState('');
  const [tsThr, setTsThr] = useState(0.15);
  const [tsFrom, setTsFrom] = useState('');   // date range for price+signal (YYYY-MM-DD)
  const [tsTo, setTsTo] = useState('');
  const [confirmN, setConfirmN] = useState(3);   // trade policy: confirm for N readings
  const [holdMin, setHoldMin] = useState(20);    // trade policy: minimum hold (minutes)
  const [regimeGate, setRegimeGate] = useState(true);          // follow/fade by tape regime
  const [chopAction, setChopAction] = useState<'fade' | 'flat'>('flat');  // what to do in chop
  const [signal, setSignal] = useState('');
  const [horizon, setHorizon] = useState('3');
  const [sampleMin, setSampleMin] = useState('auto');   // de-overlap sampling
  const [forceRebuild, setForceRebuild] = useState(false);
  const [windowDays, setWindowDays] = useState('all');
  const [expiry, setExpiry] = useState('');
  const [expiries, setExpiries] = useState<any[]>([]);
  const [res, setRes] = useState<any>(null);
  const [curve, setCurve] = useState<any>(null);
  const [allRes, setAllRes] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [testProg, setTestProg] = useState(0);   // creep bar for signal-test runs
  const [err, setErr] = useState('');
  // attribution mode
  const [predictor, setPredictor] = useState('');
  const [target, setTarget] = useState('fwd_ret_60m_pct');
  const [condition, setCondition] = useState('dte');
  const [featNames, setFeatNames] = useState<string[]>([]);
  const [attr, setAttr] = useState<any>(null);
  const [corr, setCorr] = useState<any>(null);
  const [eff, setEff] = useState<any>(null);
  const [effMetric, setEffMetric] = useState<'ic' | 'rank_ic' | 'spread' | 'sharpe' | 'hit'>('ic');
  const [vixRegime, setVixRegime] = useState('');   // '' = all regimes (Horizon map filter)
  // Return window — the GLOBAL shared price-return lookback. Not local state: the
  // value shown is whatever the backend config says, and changing it writes back
  // and fans out to every other view. See lib/signalRoster.setMomentumWindow.
  const momentumWindow = roster.config?.momentum_window;
  const retWindow = momentumWindow?.lookback_min ? String(momentumWindow.lookback_min) : '';
  // Selectable windows come from the backend — no hardcoded option list here. If the
  // backend is stale and omits them the control degrades to disabled + an explanation,
  // rather than silently rendering an empty select.
  const windowOptions: number[] = momentumWindow?.options || [];
  const activeScales = momentumWindow?.scales
    ? Object.entries(momentumWindow.scales).map(([k, v]) => `${k.split('_')[0]} ${v}`).join(', ')
    : '';
  const [winSaving, setWinSaving] = useState(false);
  const [winAudit, setWinAudit] = useState<any>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillMsg, setBackfillMsg] = useState('');
  const [bfProg, setBfProg] = useState<{ done: number; total: number; pct: number } | null>(null);

  const winQ = windowDays === 'all' ? '' : `&window_days=${windowDays}`;
  const expQ = expiry ? `&expiry=${encodeURIComponent(expiry)}` : '';

  /* Seed the dropdowns from the registry once it arrives — no hardcoded default
   * signal, so removing or renaming a SignalSpec can never leave a dead selection. */
  useEffect(() => {
    if (!roster.directional.length) return;
    // default = the highest-weighted signal, resolved from the registry rather than
    // named here, so re-weighting or renaming never leaves a dead default selection.
    const top = [...roster.directional].sort((a, b) => b.weight - a.weight)[0];
    setSignal((s) => s || top.name);
    setTsSignal((s) => s || top.name);
    setPredictor((p) => p || top.feature_key);
  }, [roster.directional.length]);   // eslint-disable-line react-hooks/exhaustive-deps

  const changeReturnWindow = async (minutes: string) => {
    setWinSaving(true); setErr('');
    try {
      const res = await setMomentumWindow(Number(minutes));   // persists + fans out
      setWinAudit(res?.feature_store ?? null);
    } catch (e: any) {
      setErr(`Could not set the return window: ${e?.message || e}`);
    } finally { setWinSaving(false); }
  };

  // Which window the STORED features were computed at (vs the one selected).
  const loadWindowAudit = useCallback(async () => {
    try {
      const r = await fetch(`/api/strategy/features/window-audit${expiry ? `?expiry=${encodeURIComponent(expiry)}` : ''}`);
      if (r.ok) setWinAudit(await r.json());
    } catch (e) { /* non-fatal */ }
  }, [expiry]);
  useEffect(() => { loadWindowAudit(); }, [loadWindowAudit]);

  // Stored rows were built at a different return window than the one selected →
  // the scores on screen are not the scores this setting describes.
  const windowStale = Boolean(
    retWindow && winAudit && winAudit.rows_by_window
    && Object.keys(winAudit.rows_by_window).some((k) => k !== String(retWindow)),
  );

  const loadPhaseGrid = useCallback(async () => {
    setLoading(true); setErr(''); setPhaseGrid(null);
    try {
      // date-first: no expiry param, so the Day dropdown lists every date incl. the
      // current expiry's (21–24 Jul), not just the last completed expiry.
      const q = `${phaseDate ? `date=${phaseDate}&` : ''}oi_symbol=${oiSymbol}`;
      const r = await fetch(`/api/strategy/signal-phase-grid?${q}`);
      const j = await r.json();
      if (!r.ok || j.error) setErr(j.error || `backend ${r.status}`);
      else { setPhaseGrid(j); if (!phaseDate && j.date) setPhaseDate(j.date); }
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setLoading(false); }
  }, [phaseDate, oiSymbol]);
  // NOTE: no auto-run — the heavy replay fires only when the user clicks Compute.

  const loadSeries = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const p = new URLSearchParams();
      // NOTE: no `expiry` param — this view is DATE-first and spans all expiries, so
      // recent dates under the current (unexpired) expiry are included. Passing an
      // expiry would pin it to one and hide those dates.
      if (tsFrom || tsTo) {                 // explicit range wins
        if (tsFrom) p.set('date_from', tsFrom);
        if (tsTo) p.set('date_to', tsTo);
      } else {
        p.set('window_days', '3');          // bounded default so the first open is fast
      }
      const r = await fetch(`/api/strategy/signal-timeseries?${p}`);
      const j = await r.json();
      if (!r.ok || j.error) { setErr(j.error || `backend ${r.status}`); if (j.session_dates) setSeries((s: any) => ({ ...(s || {}), session_dates: j.session_dates })); }
      else {
        setSeries(j);
        // seed the pickers to the loaded range once, so they reflect what's shown
        if (!tsFrom && j.date_from) setTsFrom(j.date_from);
        if (!tsTo && j.date_to) setTsTo(j.date_to);
      }
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setLoading(false); }
  }, [tsFrom, tsTo]);
  // Load once when you enter the tab (bounded default). After that it does NOT
  // auto-reload on date change — you set the range and click Plot, so a slow replay
  // fires once, not on every dropdown change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // no auto-run: the price+signal replay fires only on the Plot button.

  const loadMatrix = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const p = new URLSearchParams({ min_n: String(rhMinN), regime_by: rhRegimeBy, horizons: rhHorizons });
      if (tsFrom) p.set('date_from', tsFrom);
      if (tsTo) p.set('date_to', tsTo);
      const r = await fetch(`/api/strategy/signal-regime-horizon?${p}`);
      const j = await r.json();
      if (!r.ok || j.error) setErr(j.error || `backend ${r.status}`);
      else { setRhMatrix(j); if (!rhSignal && j.signals?.[0]) setRhSignal(j.signals[0].name); }
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setLoading(false); }
  }, [tsFrom, tsTo, rhMinN, rhRegimeBy, rhHorizons, rhSignal]);
  // no auto-run: the matrix replay fires only on the Compute button.

  const loadExpiries = useCallback(async () => {
    try {
      const r = await fetch('/api/strategy/expiries');
      const j = await r.json();
      if (j.expiries) setExpiries(j.expiries);
    } catch (e) { /* */ }
  }, []);
  const loadFeatNames = useCallback(async () => {
    try {
      const r = await fetch(`/api/strategy/feature-names${expiry ? `?expiry=${encodeURIComponent(expiry)}` : ''}`);
      const j = await r.json();
      if (j.names) setFeatNames(j.names);
    } catch (e) { /* */ }
  }, [expiry]);
  useEffect(() => { loadExpiries(); }, [loadExpiries]);
  useEffect(() => { if (mode === 'attribution') loadFeatNames(); }, [mode, loadFeatNames]);

  // Clear the previous mode's results when switching tabs — otherwise, e.g., the
  // Correlation matrix keeps rendering under Attribution (each result block is gated
  // on its own state, not the active mode).
  useEffect(() => {
    setRes(null); setCurve(null); setAllRes(null); setAttr(null); setCorr(null); setEff(null); setPhaseGrid(null); setSeries(null); setRhMatrix(null);
    setErr('');
  }, [mode]);

  // Attribution reads the pre-built feature store in one shot (fast), so it just
  // uses a short creep while `attrLoading` is true. The all/single runs report
  // REAL progress (evals done/total) via the signal-test background job below.
  const [attrLoading, setAttrLoading] = useState(false);
  useEffect(() => {
    if (attrLoading) {
      setTestProg(10);
      const t = setInterval(() => setTestProg((p) => (p < 90 ? p + Math.max(1, (90 - p) * 0.12) : p)), 200);
      return () => clearInterval(t);
    }
  }, [attrLoading]);

  const backfillFeatures = async () => {
    setBackfilling(true); setErr(''); setBackfillMsg(''); setBfProg({ done: 0, total: 0, pct: 0 });
    try {
      const p = new URLSearchParams();
      if (expiry) p.set('expiry', expiry);
      if (forceRebuild) p.set('force', 'true');
      // Rebuild at the selected return window (backend forces a full recompute if
      // it differs from the stored one — a partial pass would mix vintages).
      if (retWindow) p.set('lookback_min', retWindow);
      const s = await fetch(`/api/strategy/features/backfill/start?${p}`, { method: 'POST' });
      const sj = await s.json();
      if (sj.error) { setErr(sj.error); setBackfilling(false); setBfProg(null); return; }
      const poll = async () => {
        try {
          const r = await fetch('/api/strategy/features/backfill/status');
          const st = await r.json();
          setBfProg({ done: st.done, total: st.total, pct: st.pct });
          if (st.running) { setTimeout(poll, 800); return; }
          setBackfilling(false); setBfProg(null);
          if (st.error) setErr(st.error);
          else if (st.result) setBackfillMsg(`Computed ${st.result.written} · skipped ${st.result.skipped}${st.result.dates_written?.length ? ` · dates: ${st.result.dates_written.join(', ')}` : ''}`);
          await loadFeatNames();
          await loadWindowAudit();
        } catch (e) { setErr('Lost connection while backfilling.'); setBackfilling(false); setBfProg(null); }
      };
      setTimeout(poll, 500);
    } catch (e) { setErr('Cannot reach backend at /api.'); setBackfilling(false); setBfProg(null); }
  };
  const runCorrelation = async () => {
    setAttrLoading(true); setErr(''); setCorr(null);
    try {
      const r = await fetch(`/api/strategy/signal-correlation?${winQ.slice(1)}${expQ}`);
      const j = await r.json();
      if (!r.ok) setErr(`Backend ${r.status}: ${j.detail || 'error'} — restart uvicorn?`);
      else if (j.error) setErr(j.error);
      else setCorr(j);
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setAttrLoading(false); setTestProg(100); setTimeout(() => setTestProg(0), 500); }
  };
  const runAttribution = async () => {
    if (predictor === target) {
      setErr('Predictor and Target are the same field — that just correlates a number with itself (IC 1.00, 100% hit, always). Pick a signal/feature as the predictor and a forward return (or a different signal) as the target.');
      setAttr(null); return;
    }
    setAttrLoading(true); setErr(''); setAttr(null);
    try {
      const q = `predictor=${predictor}&target=${target}${condition ? `&condition=${condition}` : ''}${expQ}${winQ}`;
      const r = await fetch(`/api/strategy/attribution?${q}`);
      const j = await r.json();
      if (!r.ok) setErr(`Backend ${r.status}: ${j.detail || 'error'} — restart uvicorn?`);
      else if (j.error) setErr(j.error);
      else setAttr(j);
    } catch (e) { setErr('Cannot reach backend at /api.'); }
    finally { setAttrLoading(false); setTestProg(100); setTimeout(() => setTestProg(0), 500); }
  };

  // Kick off the signal-test background job and poll REAL progress (evals done /
  // total) until it finishes — so the bar reflects actual work, not a guess.
  const run = async () => {
    setLoading(true); setErr(''); setRes(null); setAllRes(null); setEff(null); setTestProg(1);
    try {
      const p = new URLSearchParams({ kind: mode, horizon_hours: horizon });
      if (expiry) p.set('expiry', expiry);
      if (windowDays !== 'all') p.set('window_days', windowDays);
      if ((mode === 'all' || mode === 'effectiveness') && sampleMin !== 'auto') p.set('sample_minutes', sampleMin);
      if (mode === 'effectiveness' && vixRegime) p.set('vix_regime', vixRegime);
      if (mode === 'single') p.set('signal', signal);
      const s = await fetch(`/api/strategy/signal-test/start?${p}`, { method: 'POST' });
      const sj = await s.json();
      if (!s.ok) { setErr(`Backend ${s.status}: ${sj.detail || 'error'} — restart uvicorn?`); setLoading(false); return; }
      if (sj.error) { setErr(sj.error); setLoading(false); return; }
      const poll = async () => {
        try {
          const r = await fetch('/api/strategy/signal-test/status');
          const st = await r.json();
          setTestProg(Math.max(1, st.pct || 0));
          if (st.running) { setTimeout(poll, 400); return; }
          setLoading(false); setTestProg(100);
          setTimeout(() => setTestProg(0), 600);
          if (st.error) { setErr(st.error); return; }
          const j = st.result || {};
          if (j.error) { setErr(j.error); return; }
          if (mode === 'effectiveness') setEff(j);
          else if (mode === 'all') setAllRes(j);
          else {
            setRes(j); setCurve(null);
            try {
              const cr = await fetch(`/api/strategy/signal-horizon-curve?signal=${signal}${expQ}${winQ}`);
              const cj = await cr.json();
              if (!cj.error) setCurve(cj);
            } catch (e) { /* */ }
          }
        } catch (e) { setErr('Lost connection while testing.'); setLoading(false); setTestProg(0); }
      };
      setTimeout(poll, 300);
    } catch (e) { setErr('Cannot reach backend at /api.'); setLoading(false); setTestProg(0); }
  };

  const sel = 'text-xs border border-slate-200 rounded-lg px-2 py-1.5 w-full';
  const hitGood = res && res.hit_rate != null && res.hit_rate >= 0.5;
  const corrGood = res && res.correlation > 0.1;

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      <div className="flex items-center gap-2 mb-1">
        <Activity className="w-5 h-5 text-indigo-600" />
        <h2 className="text-base font-bold text-slate-800">Signal Test</h2>
      </div>
      <div className="text-[11px] text-slate-400 mb-4">Does a signal actually predict the NIFTY move that follows? Score at each snapshot vs the index level N hours later.</div>

      <div className="flex rounded-lg overflow-hidden border border-slate-200 w-max mb-3">
        <button onClick={() => setMode('all')} className={`text-xs px-3 py-1.5 ${mode === 'all' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>All signals</button>
        <button onClick={() => setMode('single')} className={`text-xs px-3 py-1.5 ${mode === 'single' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Single signal</button>
        <button onClick={() => setMode('attribution')} className={`text-xs px-3 py-1.5 ${mode === 'attribution' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Attribution</button>
        <button onClick={() => setMode('correlation')} className={`text-xs px-3 py-1.5 ${mode === 'correlation' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Correlations</button>
        <button onClick={() => setMode('effectiveness')} className={`text-xs px-3 py-1.5 ${mode === 'effectiveness' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Horizon map</button>
        <button onClick={() => setMode('phasegrid')} className={`text-xs px-3 py-1.5 ${mode === 'phasegrid' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Phase grid</button>
        <button onClick={() => setMode('timeseries')} className={`text-xs px-3 py-1.5 ${mode === 'timeseries' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Price + signal</button>
        <button onClick={() => setMode('regimehorizon')} className={`text-xs px-3 py-1.5 ${mode === 'regimehorizon' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Regime × Horizon</button>
      </div>

      {/* RETURN WINDOW — a GLOBAL setting, so it sits outside the mode-specific
          controls and is visible in every mode. Changing it writes to the backend
          config and fans out to every view (lib/signalRoster.setMomentumWindow). */}
      <div className="flex flex-wrap items-center gap-2 mb-3 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg">
        <span className="text-xs font-semibold text-slate-600">Return window</span>
        <select
          value={retWindow}
          disabled={winSaving || !windowOptions.length}
          onChange={(e) => changeReturnWindow(e.target.value)}
          className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white disabled:opacity-50"
        >
          {!retWindow && <option value="">—</option>}
          {windowOptions.map((m: number) => (
            <option key={m} value={m}>{m === 60 ? '1 hour' : `${m} min`}</option>
          ))}
        </select>
        {winSaving && <span className="text-[11px] text-indigo-600">saving…</span>}
        <span className="text-[11px] text-slate-400">
          {windowOptions.length
            ? `applies to every price-return signal, everywhere${activeScales ? ` · scales ${activeScales}` : ''}`
            : 'unavailable — backend did not serve momentum_window (restart uvicorn)'}
        </span>
      </div>

      {rosterStatusText(roster) && (
        <div className="mb-3 flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{rosterStatusText(roster)}</span>
        </div>
      )}

      {windowStale && (
        <div className="mb-3 flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Stored features were computed at a different return window
            ({Object.keys(winAudit?.rows_by_window || {}).join(', ')} bars) than the
            selected {retWindow} min. Signal scores are a function of the window, so
            these results mix vintages — click <b>Backfill features</b> to rebuild at
            {' '}{retWindow} min before trusting the IC / correlation numbers.
          </span>
        </div>
      )}

      {(mode === 'all' || mode === 'single') && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
          {mode === 'single' && (
            <Field label="Signal">
              <select value={signal} onChange={(e) => setSignal(e.target.value)} className={sel}>
                {roster.directional.map((s) => <option key={s.name} value={s.name}>{s.label}</option>)}
              </select>
            </Field>
          )}
          <Field label="Forward horizon">
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)} className={sel}>
              {['1', '2', '3', '4', '6'].map((h) => <option key={h} value={h}>{h} hour{h !== '1' ? 's' : ''}</option>)}
            </select>
          </Field>
          {mode === 'all' && (
            <Field label="Sample every (de-overlap)">
              <select value={sampleMin} onChange={(e) => setSampleMin(e.target.value)} className={sel} title="Space evaluations so they don't overlap the horizon — set ≈ the horizon for honest stats">
                <option value="auto">Auto (dense)</option>
                {['15', '30', '60', '120'].map((m) => <option key={m} value={m}>{m} min</option>)}
              </select>
            </Field>
          )}
          <Field label="Session days before expiry">
            <select value={windowDays} onChange={(e) => setWindowDays(e.target.value)} className={sel}>
              <option value="all">All</option>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          <Field label="Expiry">
            <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={sel}>
              <option value="">Auto (latest completed)</option>
              {expiries.map((e) => <option key={e.expiry} value={e.expiry}>{e.expiry.slice(0, 10)} ({e.n_captures})</option>)}
            </select>
          </Field>
          <div className="flex items-end">
            <button onClick={run} disabled={loading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-slate-800 text-white font-bold hover:bg-slate-900 disabled:opacity-60">
              <Play className="w-3.5 h-3.5" /> {loading ? 'Testing…' : mode === 'all' ? 'Compare all' : 'Test signal'}
            </button>
          </div>
        </div>
      )}

      {mode === 'attribution' && (
        <>
          <div className="text-[11px] text-slate-400 mb-2">
            Relate any feature to a forward return, sliced by a condition — answers "does this work better when X?". Needs the feature store backfilled first.
            <button onClick={backfillFeatures} disabled={backfilling} className="ml-2 text-indigo-600 hover:underline disabled:opacity-50">{backfilling ? 'backfilling…' : 'Backfill features'}</button>
            <label className="ml-2 cursor-pointer"><input type="checkbox" checked={forceRebuild} onChange={(e) => setForceRebuild(e.target.checked)} className="rounded align-middle" /> <span className="align-middle">rebuild all (needed after new features are added)</span></label>
            {backfillMsg && <span className="ml-2 text-emerald-600">{backfillMsg}</span>}
          </div>
          {bfProg && (
            <div className="mb-3">
              <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
                <span>Backfilling features… {bfProg.total ? `${bfProg.done}/${bfProg.total} snapshots` : 'starting…'}</span>
                <span>{bfProg.pct}%</span>
              </div>
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full bg-indigo-500 rounded-full transition-all duration-300" style={{ width: `${bfProg.pct}%` }} />
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-2.5 mb-3">
            <Field label="Predictor (feature)" hint="Measured now — the thing you're testing (a signal score, or a feature like max_pain / pcr).">
              <select value={predictor} onChange={(e) => setPredictor(e.target.value)} className={sel}>
                {featNames.length === 0 && <option value={predictor}>{predictor}</option>}
                <optgroup label="Signals">
                  {roster.directional.map((s) => <option key={s.name} value={s.feature_key}>{s.label}</option>)}
                </optgroup>
                {featNames.filter((n) => !roster.byName[n.replace(/^sig_|_score$/g, '')]).length > 0 && (
                  <optgroup label="Other features">
                    {featNames.filter((n) => !roster.byName[n.replace(/^sig_|_score$/g, '')]).map((n) => <option key={n} value={n}>{n}</option>)}
                  </optgroup>
                )}
              </select>
            </Field>
            <Field label="Target (predict what)" hint="Measured later — the outcome. Usually a forward NIFTY return; must differ from the predictor.">

              <select value={target} onChange={(e) => setTarget(e.target.value)} className={sel} title="Usually a forward NIFTY return, but you can pick any feature — e.g. another signal's score — to do signal→signal attribution.">
                <optgroup label="Forward NIFTY return">
                  {['fwd_ret_5m_pct', 'fwd_ret_15m_pct', 'fwd_ret_30m_pct', 'fwd_ret_60m_pct', 'fwd_ret_eod_pct'].map((t) => <option key={t} value={t}>{t.replace('fwd_ret_', '').replace('_pct', '')}</option>)}
                </optgroup>
                <optgroup label="Signals (signal→signal)">
                  {roster.directional.map((s) => <option key={s.name} value={s.feature_key}>{s.label}</option>)}
                </optgroup>
                {featNames.filter((n) => !n.startsWith('fwd_ret_') && !roster.byName[n.replace(/^sig_|_score$/g, '')]).length > 0 && (
                  <optgroup label="Other features">
                    {featNames.filter((n) => !n.startsWith('fwd_ret_') && !roster.byName[n.replace(/^sig_|_score$/g, '')]).map((n) => <option key={n} value={n}>{n}</option>)}
                  </optgroup>
                )}
              </select>
            </Field>
            <Field label="Condition (slice by)" hint="Splits rows into buckets to see WHEN the predictor works — doesn't change the bet itself.">
              <select value={condition} onChange={(e) => setCondition(e.target.value)} className={sel}>
                <option value="">(none)</option>
                {['vix_regime', 'dte', 'vix', 'minutes_since_open', 'realized_vol_ann_pct', 'decomp_regime', 'decomp_primary', 'pcr', ...featNames.filter((n) => !['vix_regime', 'dte', 'vix', 'minutes_since_open', 'realized_vol_ann_pct', 'pcr'].includes(n))].filter((v, i, a) => a.indexOf(v) === i).map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
            <Field label="Session days">
              <select value={windowDays} onChange={(e) => setWindowDays(e.target.value)} className={sel}>
                <option value="all">All</option>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
            <Field label="Expiry" hint="Which expiry's snapshots to analyse. Blank = latest completed. Session days trims WITHIN this expiry.">
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={sel}>
                <option value="">Auto (latest completed)</option>
                {expiries.map((e) => <option key={e.expiry} value={e.expiry}>{e.expiry.slice(0, 10)} ({e.n_captures})</option>)}
              </select>
            </Field>
            <div className="flex items-end">
              <button onClick={runAttribution} disabled={attrLoading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-slate-800 text-white font-bold hover:bg-slate-900 disabled:opacity-60">
                <Play className="w-3.5 h-3.5" /> {attrLoading ? 'Running…' : 'Analyse'}
              </button>
            </div>
          </div>
        </>
      )}

      {mode === 'timeseries' && (
        <>
          <div className="text-[11px] text-slate-400 mb-3">
            NIFTY over time with the selected signal's calls. <span className="text-emerald-600">▲ buy</span> = score ≥ +threshold (signal expects the index up); <span className="text-rose-600">▼ sell</span> = score ≤ −threshold (expects down). Eyeball whether price actually followed each call.
          </div>
          <div className="flex flex-wrap items-end gap-3 mb-3">
            <Field label="Signal">
              <select value={tsSignal} onChange={(e) => setTsSignal(e.target.value)} className={sel} style={{ minWidth: 190 }}>
                {roster.directional.map((s) => <option key={s.name} value={s.name}>{s.label}</option>)}
              </select>
            </Field>
            <div>
              <div className="text-[11px] text-slate-500 mb-1">Threshold ±{tsThr.toFixed(2)}</div>
              <input type="range" min={0} max={0.6} step={0.05} value={tsThr} onChange={(e) => setTsThr(parseFloat(e.target.value))} />
            </div>
            <div>
              <div className="text-[11px] text-slate-500 mb-1">Confirm {confirmN} reading{confirmN !== 1 ? 's' : ''}</div>
              <input type="range" min={1} max={6} step={1} value={confirmN} onChange={(e) => setConfirmN(parseInt(e.target.value))} />
            </div>
            <div>
              <div className="text-[11px] text-slate-500 mb-1">Min hold {holdMin} min</div>
              <input type="range" min={0} max={90} step={5} value={holdMin} onChange={(e) => setHoldMin(parseInt(e.target.value))} />
            </div>
            <Field label="From">
              <select value={tsFrom} onChange={(e) => setTsFrom(e.target.value)} className={sel} style={{ width: 130 }}>
                {(series?.session_dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
            <Field label="To">
              <select value={tsTo} onChange={(e) => setTsTo(e.target.value)} className={sel} style={{ width: 130 }}>
                {(series?.session_dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
            <button onClick={loadSeries} disabled={loading}
              className="flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-indigo-600 text-white font-bold hover:bg-indigo-700 disabled:opacity-60 self-end">
              <Play className="w-3.5 h-3.5" /> {loading ? 'Plotting…' : 'Plot signals'}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 mb-2">
            <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <input type="checkbox" checked={regimeGate} onChange={(e) => setRegimeGate(e.target.checked)} />
              Regime gate <span className="text-slate-400">(follow in trend, {chopAction} in chop)</span>
            </label>
            {regimeGate && (
              <div className="flex rounded-lg overflow-hidden border border-slate-200 text-[11px]">
                <button onClick={() => setChopAction('flat')} className={`px-2.5 py-1 ${chopAction === 'flat' ? 'bg-slate-700 text-white' : 'text-slate-500'}`}>chop → stand aside</button>
                <button onClick={() => setChopAction('fade')} className={`px-2.5 py-1 ${chopAction === 'fade' ? 'bg-slate-700 text-white' : 'text-slate-500'}`}>chop → fade</button>
              </div>
            )}
          </div>
          <div className="text-[10px] text-slate-400 mb-2">
            Set the From/To range, then click <b>Plot signals</b>. It replays every signal across the range, so a wider range takes longer — it won't re-run until you click.
          </div>
          {!series && !loading && <div className="text-[11px] text-slate-400 mb-3">Set the range and click <b>Plot signals</b> — nothing computes until you click.</div>}
          {series && (() => {
            const sc: (number | null)[] = series.scores[tsSignal] || [];
            const tmin = series.times.map((t: string) => new Date(t).getTime() / 60000);
            // raw direction per bar (what the plain threshold would signal)
            const raw = sc.map((s) => (s == null ? 0 : s >= tsThr ? 1 : s <= -tsThr ? -1 : 0));
            const rawSignals = raw.filter((d: number) => d !== 0).length;
            // CONFIRMATION: only a directional read that held for the last N bars counts
            const confirmed = raw.map((d: number, i: number) => {
              if (d === 0) return 0;
              for (let k = i - confirmN + 1; k <= i; k++) { if (k < 0 || raw[k] !== d) return 0; }
              return d;
            });
            // REGIME GATE: follow the signal in a TREND, fade or stand aside in CHOP
            // (chop is where following buys high / sells low). Neutral/unknown → flat.
            const regimes: string[] = series.tape_regime || [];
            const gated = confirmed.map((c: number, i: number) => {
              if (c === 0 || !regimeGate) return c;
              const rg = regimes[i];
              if (rg === 'trend') return c;                       // follow
              if (rg === 'chop') return chopAction === 'fade' ? -c : 0;  // fade or flat
              return 0;                                           // neutral / unknown → flat
            });
            // POLICY: enter on the gated signal; once in, hold ≥ holdMin before an
            // opposite gated signal REVERSES the position. A reversal is just the next
            // entry — it implicitly closes the prior trade, so there's no separate exit
            // marker (that would always land on top of the entry).
            const entries: any[] = [];
            let pos = 0, entryT = 0;
            for (let i = 0; i < gated.length; i++) {
              const c = gated[i];
              if (c !== 0 && c !== pos) {
                if (pos === 0) { pos = c; entryT = tmin[i]; entries.push({ i, dir: c }); }
                else if (tmin[i] - entryT >= holdMin) { pos = c; entryT = tmin[i]; entries.push({ i, dir: c }); }
              }
            }
            const entrySet = new Map(entries.map((e) => [e.i, e.dir]));
            const data = series.times.map((t: string, i: number) => {
              const ed = entrySet.get(i);
              return {
                t: fmtIST(t), spot: series.spot[i], score: sc[i],
                buy: ed === 1 ? series.spot[i] : null,      // long entry
                sell: ed === -1 ? series.spot[i] : null,    // short entry
              };
            });
            // hit check on ACTUAL entries: did spot move the entry's way over the hold?
            let hit = 0, tot = 0;
            for (const e of entries) {
              const jt = tmin[e.i] + holdMin;
              let j = e.i; while (j < tmin.length - 1 && tmin[j] < jt) j++;
              if (j > e.i) { tot++; const d = series.spot[j] - series.spot[e.i]; if ((e.dir > 0) === (d > 0)) hit++; }
            }
            return (
              <>
                <div className="flex flex-wrap gap-4 text-[11px] text-slate-500 mb-2">
                  <span><b className="text-slate-700">{rawSignals}</b> raw signals → <b className="text-indigo-600">{entries.length}</b> actual trades <span className="text-slate-400">(confirm {confirmN}, hold {holdMin}m)</span></span>
                  {tot > 0 && <span>trade hit rate: <b className={hit / tot >= 0.5 ? 'text-emerald-600' : 'text-rose-600'}>{Math.round(100 * hit / tot)}%</b> <span className="text-slate-400">({tot} trades over the hold)</span></span>}
                </div>
                <ResponsiveContainer width="100%" height={340}>
                  <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 8, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                    <XAxis dataKey="t" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                    <YAxis domain={['dataMin - 20', 'dataMax + 20']} tick={{ fontSize: 10 }} width={60} />
                    <Tooltip formatter={(v: any, n: any) => [v, n]} labelStyle={{ fontSize: 11 }} contentStyle={{ fontSize: 11 }} />
                    <Line type="monotone" dataKey="spot" stroke="#3B82F6" dot={false} strokeWidth={1.6} name="NIFTY" isAnimationActive={false} />
                    <Scatter dataKey="buy" fill="#109E75" shape="triangle" name="long entry" isAnimationActive={false} />
                    <Scatter dataKey="sell" fill="#E24B4A" shape="triangle" name="short entry" isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
                {/* tape regime ribbon — trend (follow) vs chop (fade/stand aside) */}
                <div className="flex h-3 rounded overflow-hidden mt-1" title="tape regime: green=trend, amber=chop, grey=neutral">
                  {regimes.map((rg: string, i: number) => (
                    <div key={i} style={{ flex: 1, background: rg === 'trend' ? 'rgba(16,158,117,0.55)' : rg === 'chop' ? 'rgba(234,179,8,0.6)' : 'rgba(148,163,184,0.35)' }} />
                  ))}
                </div>
                <div className="flex justify-between text-[9px] text-slate-400 mt-0.5">
                  <span>tape regime · <span className="text-emerald-600">trend=follow</span> · <span className="text-amber-600">chop={chopAction}</span> · neutral=flat</span>
                  <span>{regimes.filter((r: string) => r === 'trend').length} trend · {regimes.filter((r: string) => r === 'chop').length} chop</span>
                </div>
                <div className="text-[10px] text-slate-400 mt-1">
                  Markers are ACTUAL trade entries after confirmation + regime gate + min-hold (green ▲ long, red ▼ short) — each entry runs until the next one flips it. The gate follows momentum only when the
                  tape trends and {chopAction === 'fade' ? 'fades it in chop' : 'stands aside in chop'} — that's the fix for buying high / selling low.
                  Descriptive; one-day samples can't prove the regime thresholds — validate out-of-sample.
                </div>
              </>
            );
          })()}
        </>
      )}

      {mode === 'regimehorizon' && (
        <>
          <div className="text-[11px] text-slate-400 mb-3">
            "<b>When</b> is a signal good?" — its IC split by regime (rows) × forward horizon (cols). Blue = predictive, red = anti-predictive (fade), grey = too few samples. <b>Regime by Futures OI</b> is the reliability-gate test: put a momentum signal in Signal and check if its IC is positive in <b>conviction</b> (fresh positioning) and negative in <b>hollow</b> (covering/unwinding).
          </div>
          <div className="flex flex-wrap items-end gap-3 mb-3">
            <Field label="Signal">
              <select value={rhSignal} onChange={(e) => setRhSignal(e.target.value)} className={sel} style={{ minWidth: 200 }}>
                {(rhMatrix?.signals || roster.directional).map((s: any) => <option key={s.name} value={s.name}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Regime by">
              <select value={rhRegimeBy} onChange={(e) => setRhRegimeBy(e.target.value as any)} className={sel} style={{ width: 150 }}>
                <option value="tape_vol">Tape × Vol</option>
                <option value="oi">Futures OI</option>
              </select>
            </Field>
            <Field label="Horizons (min)">
              <input value={rhHorizons} onChange={(e) => setRhHorizons(e.target.value)} className={sel} style={{ width: 120 }} placeholder="15,30,60" />
            </Field>
            <Field label="From">
              <select value={tsFrom} onChange={(e) => setTsFrom(e.target.value)} className={sel} style={{ width: 130 }}>
                {(rhMatrix?.session_dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
            <Field label="To">
              <select value={tsTo} onChange={(e) => setTsTo(e.target.value)} className={sel} style={{ width: 130 }}>
                {(rhMatrix?.session_dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
            <div>
              <div className="text-[11px] text-slate-500 mb-1">Min independent {rhMinN}</div>
              <input type="range" min={2} max={60} step={1} value={rhMinN} onChange={(e) => setRhMinN(parseInt(e.target.value))} title="minimum INDEPENDENT (non-overlapping) windows for a cell to count — not raw n" />
            </div>
            <button onClick={loadMatrix} disabled={loading}
              className="flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-indigo-600 text-white font-bold hover:bg-indigo-700 disabled:opacity-60 self-end">
              <Play className="w-3.5 h-3.5" /> {loading ? 'Computing…' : 'Compute'}
            </button>
          </div>
          {!rhMatrix && !loading && <div className="text-[11px] text-slate-400 mb-3">Pick a signal and range, then click <b>Compute</b> — nothing computes until you click.</div>}
          {rhMatrix?.matrix?.[rhSignal] && (() => {
            const m = rhMatrix.matrix[rhSignal];
            const cellBg = (ic: number | null, ok: boolean) => {
              if (ic == null || !ok) return 'rgba(148,163,184,0.12)';
              const a = Math.min(Math.abs(ic), 0.6) / 0.6 * 0.8 + 0.12;
              return ic >= 0 ? `rgba(55,138,221,${a})` : `rgba(226,75,74,${a})`;   // blue predictive, red fade
            };
            return (
              <div className="overflow-x-auto">
                <table className="text-[11px] border-collapse">
                  <thead>
                    <tr className="text-slate-500">
                      <th className="text-left p-2 font-medium">regime \ horizon</th>
                      {rhMatrix.horizons.map((h: number) => <th key={h} className="p-2 font-medium text-center w-20">{h}m</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {rhMatrix.regimes.map((reg: string) => (
                      <tr key={reg}>
                        <td className="p-2 font-medium text-slate-600 capitalize">{reg}</td>
                        {rhMatrix.horizons.map((h: number) => {
                          const c = m[reg]?.[h] || {};
                          return (
                            <td key={h} className="p-2 text-center" style={{ background: cellBg(c.ic, c.ok) }}
                                title={c.ok ? `IC ${c.ic} · n=${c.n} · independent=${c.eff_n}` : `${c.eff_n} independent windows < ${rhMatrix.min_n} — n=${c.n} raw but only ${c.eff_n} non-overlapping (not a real sample)`}>
                              {c.ic == null ? <span className="text-slate-300">·</span>
                                : <span className={c.ok ? 'font-semibold text-slate-800' : 'text-slate-400'}>{c.ic > 0 ? '+' : ''}{c.ic}</span>}
                              <div className="text-[9px] text-slate-500">n={c.n || 0}·ind={c.eff_n ?? 0}{c.ok ? '' : ' ⚠'}</div>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="text-[10px] text-slate-400 mt-2">
                  {rhMatrix.date_from}…{rhMatrix.date_to} · greyed = under-sampled (n &lt; {rhMatrix.min_n}). {rhMatrix.note}
                </div>
              </div>
            );
          })()}
        </>
      )}

      {mode === 'phasegrid' && (
        <>
          <div className="text-[11px] text-slate-400 mb-3">
            How each signal's directional read evolved through one session. Cell = mean score across that phase; green bull, red bear. Watch a row left→right: a signal that stays one colour <b>persisted</b>; one that flips <b>faded or reversed</b>.
          </div>
          <div className="flex flex-wrap items-center gap-3 mb-3">
            {phaseGrid?.days?.length > 1 && (
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-slate-500">Day</span>
                <select value={phaseDate} onChange={(e) => setPhaseDate(e.target.value)} className={sel} style={{ width: 140 }}>
                  {phaseGrid.days.map((d: string) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-slate-500">Positioning symbol</span>
              <select value={oiSymbol} onChange={(e) => setOiSymbol(e.target.value)} className={sel} style={{ width: 140 }}>
                {(phaseGrid?.oi_symbols || ['NIFTY']).map((s: string) => <option key={s} value={s}>{s}</option>)}
              </select>
              {phaseGrid?.oi_positioning?._proxy === 'volume' && (
                <span className="text-[10px] text-amber-600">volume proxy — no futures OI; weaker</span>
              )}
            </div>
            <button onClick={loadPhaseGrid} disabled={loading}
              className="flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-indigo-600 text-white font-bold hover:bg-indigo-700 disabled:opacity-60">
              <Play className="w-3.5 h-3.5" /> {loading ? 'Computing…' : 'Compute'}
            </button>
          </div>
          {!phaseGrid && !loading && <div className="text-[11px] text-slate-400 mb-3">Pick a day/symbol and click <b>Compute</b> to run — nothing computes until you click.</div>}
          {phaseGrid && (() => {
            const phases = phaseGrid.phases;
            const cellBg = (v: number | null) => {
              if (v === null || v === undefined) return 'transparent';
              const a = Math.min(Math.abs(v), 1) * 0.85 + 0.08;
              return v >= 0 ? `rgba(16,158,117,${a})` : `rgba(226,75,74,${a})`;
            };
            const txt = (v: number | null) => (v === null || v === undefined ? '·' : (v >= 0 ? '+' : '') + v.toFixed(2));
            const rows = phaseGrid.signals.filter((s: any) =>
              phases.some((p: any) => phaseGrid.grid[s.name]?.[p.key]?.score !== null));
            const pending = phaseGrid.signals.filter((s: any) =>
              !phases.some((p: any) => phaseGrid.grid[s.name]?.[p.key]?.score !== null));
            return (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] border-collapse">
                  <thead>
                    <tr className="text-slate-500">
                      <th className="text-left font-medium p-1.5">Signal</th>
                      <th className="text-right font-medium p-1.5 w-10">wt</th>
                      {phases.map((p: any) => (
                        <th key={p.key} className="text-center font-medium p-1.5">
                          <div>{p.label}</div><div className="text-[9px] text-slate-400 font-normal">{p.time}</div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-y border-slate-200 bg-slate-50">
                      <td className="p-1.5 font-semibold text-slate-600">NIFTY move (pt)</td><td />
                      {phases.map((p: any) => {
                        const m = phaseGrid.nifty_move[p.key];
                        return <td key={p.key} className={`p-1.5 text-center font-mono ${m >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{m === null ? '·' : (m >= 0 ? '+' : '') + m}</td>;
                      })}
                    </tr>
                    <tr>
                      <td className="p-1.5 font-semibold text-slate-700">Net blend</td><td />
                      {phases.map((p: any) => {
                        const v = phaseGrid.net[p.key];
                        return <td key={p.key} className="p-1.5 text-center font-mono font-semibold" style={{ background: cellBg(v), color: v === null ? '#94a3b8' : '#fff' }}>{txt(v)}</td>;
                      })}
                    </tr>
                    <tr className="border-b-2 border-slate-300">
                      <td className="p-1.5 font-semibold text-slate-700 whitespace-nowrap">OI positioning <span className="font-normal text-slate-400">(conviction)</span></td><td />
                      {phases.map((p: any) => {
                        const o = phaseGrid.oi_positioning?.[p.key];
                        if (!o || o.pending) return <td key={p.key} className="p-1.5 text-center text-[10px] text-slate-300 italic">pending</td>;
                        const SHORT: any = { 'long buildup': 'L build', 'short covering': 'S cover', 'short buildup': 'S build', 'long unwinding': 'L unwind', 'churn': 'churn' };
                        const bull = o.lean === 'bull';
                        const bg = o.lean === 'neutral' ? 'rgba(148,163,184,0.35)'
                          : (bull ? 'rgba(16,158,117,' : 'rgba(226,75,74,') + (o.conviction ? '0.85)' : '0.4)');
                        return (
                          <td key={p.key} className="p-1.5 text-center text-[10px] font-medium" style={{ background: bg, color: o.lean === 'neutral' ? '#475569' : '#fff' }} title={`${o.regime} — ${o.note}${o.dOI != null ? ` · ΔOI ${o.dOI}` : ''}`}>
                            {SHORT[o.regime] || o.regime}{o.conviction ? ' ★' : ''}
                          </td>
                        );
                      })}
                    </tr>
                    {rows.map((s: any) => (
                      <tr key={s.name} className="border-b border-slate-50">
                        <td className="p-1.5 text-slate-600 whitespace-nowrap">{s.label}</td>
                        <td className="p-1.5 text-right text-slate-400 font-mono">{s.weight > 0 ? s.weight.toFixed(2) : '—'}</td>
                        {phases.map((p: any) => {
                          const cell = phaseGrid.grid[s.name]?.[p.key] || {};
                          return (
                            <td key={p.key} className="p-1.5 text-center font-mono" style={{ background: cellBg(cell.score), color: cell.score === null || cell.score === undefined ? '#cbd5e1' : '#fff' }}
                                title={cell.n ? `conf ${(cell.conf * 100).toFixed(0)}% · n=${cell.n}` : 'no data'}>
                              {txt(cell.score)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                    {pending.length > 0 && (
                      <tr><td colSpan={phases.length + 2} className="p-1.5 text-[10px] text-slate-400">pending data: {pending.map((s: any) => s.label).join(' · ')}</td></tr>
                    )}
                  </tbody>
                </table>
                <div className="text-[10px] text-slate-400 mt-2">{phaseGrid.date} · {phaseGrid.note}</div>
              </div>
            );
          })()}
        </>
      )}

      {mode === 'effectiveness' && (
        <>
          <div className="text-[11px] text-slate-400 mb-2">
            Every signal scored against every forward horizon in one pass — a signal-discovery map. Reading a row tells you the signal's natural time scale; the ☆ cell is its best horizon. Colour by the metric of your choice.
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
            <Field label="Colour by" hint="Which metric fills the cells and picks each row's best horizon.">
              <select value={effMetric} onChange={(e) => setEffMetric(e.target.value as any)} className={sel}>
                <option value="ic">IC (correlation)</option>
                <option value="rank_ic">Rank IC (Spearman)</option>
                <option value="sharpe">Sharpe</option>
                <option value="spread">Spread (%)</option>
                <option value="hit">Hit rate</option>
              </select>
            </Field>
            <Field label="Sample every (de-overlap)" hint="Space evals so they don't overlap the horizon; ≈ horizon = honest stats.">
              <select value={sampleMin} onChange={(e) => setSampleMin(e.target.value)} className={sel}>
                <option value="auto">Auto (dense)</option>
                {['15', '30', '60', '120'].map((m) => <option key={m} value={m}>{m} min</option>)}
              </select>
            </Field>
            <Field label="VIX regime" hint="Slice to one volatility regime — which signals survive a risk-off tape.">
              <select value={vixRegime} onChange={(e) => setVixRegime(e.target.value)} className={sel}>
                <option value="">All regimes</option>
                <option value="calm">calm (&lt;13)</option>
                <option value="normal">normal (13–16)</option>
                <option value="elevated">elevated (16–20)</option>
                <option value="stressed">stressed (&gt;20)</option>
              </select>
            </Field>
            <Field label="Session days before expiry">
              <select value={windowDays} onChange={(e) => setWindowDays(e.target.value)} className={sel}>
                <option value="all">All</option>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
            <Field label="Expiry">
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={sel}>
                <option value="">Auto (latest completed)</option>
                {expiries.map((e) => <option key={e.expiry} value={e.expiry}>{e.expiry.slice(0, 10)} ({e.n_captures})</option>)}
              </select>
            </Field>
            <div className="flex items-end">
              <button onClick={run} disabled={loading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-slate-800 text-white font-bold hover:bg-slate-900 disabled:opacity-60">
                <Play className="w-3.5 h-3.5" /> {loading ? 'Mapping…' : 'Build map'}
              </button>
            </div>
          </div>
        </>
      )}

      {mode === 'correlation' && (
        <>
          <div className="text-[11px] text-slate-400 mb-2">
            How much do the six signals move together? Correlation of their scores across every snapshot — high |corr| means two signals are the same bet in disguise (double-counting), ≈0 means they're independent. Needs the feature store backfilled.
            <button onClick={backfillFeatures} disabled={backfilling} className="ml-2 text-indigo-600 hover:underline disabled:opacity-50">{backfilling ? 'backfilling…' : 'Backfill features'}</button>
          </div>
          {bfProg && (
            <div className="mb-3">
              <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
                <span>Backfilling features… {bfProg.total ? `${bfProg.done}/${bfProg.total} snapshots` : 'starting…'}</span>
                <span>{bfProg.pct}%</span>
              </div>
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full bg-indigo-500 rounded-full transition-all duration-300" style={{ width: `${bfProg.pct}%` }} />
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
            <Field label="Session days before expiry">
              <select value={windowDays} onChange={(e) => setWindowDays(e.target.value)} className={sel}>
                <option value="all">All</option>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
            <Field label="Expiry">
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={sel}>
                <option value="">Auto (latest completed)</option>
                {expiries.map((e) => <option key={e.expiry} value={e.expiry}>{e.expiry.slice(0, 10)} ({e.n_captures})</option>)}
              </select>
            </Field>
            <div className="flex items-end">
              <button onClick={runCorrelation} disabled={attrLoading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-slate-800 text-white font-bold hover:bg-slate-900 disabled:opacity-60">
                <Play className="w-3.5 h-3.5" /> {attrLoading ? 'Computing…' : 'Compute matrix'}
              </button>
            </div>
          </div>
        </>
      )}

      {testProg > 0 && (
        <div className="mb-3">
          <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
            <span>{(loading || attrLoading)
              ? (mode === 'all' ? 'Replaying all signals across the window…' : mode === 'single' ? 'Replaying signal across the window…' : 'Scoring feature vs forward returns…')
              : 'Done'}</span>
            <span>{Math.round(testProg)}%</span>
          </div>
          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 rounded-full transition-all duration-200" style={{ width: `${testProg}%` }} />
          </div>
        </div>
      )}

      {err && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mb-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}
        </div>
      )}

      {/* Attribution result */}
      {attr && (
        <div>
          <div className="text-[11px] text-slate-400 mb-2">
            <span className="font-semibold text-slate-600">{attr.predictor}</span> → {attr.target}
            {attr.condition ? <> · sliced by <span className="font-semibold text-slate-600">{attr.condition}</span></> : null} · {attr.n} rows
            {attr.expiry ? <> · expiry <span className="font-semibold text-slate-600">{String(attr.expiry).slice(0, 10)}</span></> : null}
          </div>
          <div className="border border-slate-100 rounded-lg overflow-hidden mb-2">
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr className="text-slate-400 text-left">
                  <th className="py-2 px-3 font-normal">{attr.condition ? 'Bucket' : 'All'}</th>
                  <th className="py-2 px-2 font-normal text-right">n</th>
                  <th className="py-2 px-2 font-normal text-right">IC (corr)</th>
                  <th className="py-2 px-2 font-normal text-right">Sharpe</th>
                  <th className="py-2 px-2 font-normal text-right">Hit rate</th>
                  <th className="py-2 px-2 font-normal text-right">Spread</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-slate-100 bg-slate-50/50">
                  <td className="py-2 px-3 font-semibold">Overall</td>
                  <td className="py-2 px-2 text-right">{attr.overall.n}</td>
                  <td className={`py-2 px-2 text-right font-mono ${(attr.overall.ic || 0) > 0.1 ? 'text-emerald-600' : (attr.overall.ic || 0) < -0.1 ? 'text-rose-600' : 'text-slate-500'}`}>{attr.overall.ic ?? '—'}</td>
                  <td className={`py-2 px-2 text-right font-mono font-semibold ${attr.overall.sharpe == null ? 'text-slate-300' : attr.overall.sharpe > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{attr.overall.sharpe ?? '—'}</td>
                  <td className={`py-2 px-2 text-right ${attr.overall.hit_rate == null ? 'text-slate-300' : attr.overall.hit_rate >= 0.5 ? 'text-emerald-600' : 'text-rose-600'}`}>{attr.overall.hit_rate != null ? `${Math.round(attr.overall.hit_rate * 100)}%` : '—'}</td>
                  <td className={`py-2 px-2 text-right font-mono ${pnlColor(attr.overall.spread || 0)}`}>{attr.overall.spread != null ? `${attr.overall.spread}%` : '—'}</td>
                </tr>
                {attr.buckets.map((b: any, i: number) => (
                  <tr key={i} className="border-t border-slate-50">
                    <td className="py-2 px-3">{b.label}</td>
                    <td className="py-2 px-2 text-right text-slate-400">{b.n}</td>
                    <td className={`py-2 px-2 text-right font-mono ${(b.ic || 0) > 0.1 ? 'text-emerald-600' : (b.ic || 0) < -0.1 ? 'text-rose-600' : 'text-slate-500'}`}>{b.ic ?? '—'}</td>
                    <td className={`py-2 px-2 text-right font-mono font-semibold ${b.sharpe == null ? 'text-slate-300' : b.sharpe > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{b.sharpe ?? '—'}</td>
                    <td className={`py-2 px-2 text-right ${b.hit_rate == null ? 'text-slate-300' : b.hit_rate >= 0.5 ? 'text-emerald-600' : 'text-rose-600'}`}>{b.hit_rate != null ? `${Math.round(b.hit_rate * 100)}%` : '—'}</td>
                    <td className={`py-2 px-2 text-right font-mono ${pnlColor(b.spread || 0)}`}>{b.spread != null ? `${b.spread}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-[11px] text-slate-500">IC = correlation of the feature with the forward return (predictive if &gt; 0). Sharpe = consistency of a median-split long/short (long when the feature is above its median, short below) — mean/σ of that per-snapshot return, the "can I rely on it" metric. Hit rate = sign agreement (for centered features). Spread = avg return of top-half minus bottom-half — the money metric.</div>
          <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-2">
            <AlertTriangle className="w-3 h-3" /> {attr.note}
          </div>
        </div>
      )}

      {/* Signal × horizon effectiveness heatmap */}
      {eff && eff.matrix && (() => {
        const scaleMax: any = { ic: 0.5, rank_ic: 0.5, sharpe: 1.0, spread: 0.5, hit: 1.0 };
        const cellVal = (c: any) => (c ? c[effMetric] : null);
        const fmt = (v: number | null) => v == null ? '—' : effMetric === 'hit' ? `${Math.round(v * 100)}` : v.toFixed(2);
        const color = (v: number | null) => {
          if (v == null) return { bg: '#f8fafc', fg: '#cbd5e1' };
          const signed = effMetric === 'hit' ? v - 0.5 : v;
          const a = Math.min(Math.abs(signed) / (effMetric === 'hit' ? 0.5 : scaleMax[effMetric]), 1);
          const bg = signed >= 0 ? `rgba(16,185,129,${0.1 + a * 0.75})` : `rgba(244,63,94,${0.1 + a * 0.75})`;
          return { bg, fg: a >= 0.55 ? '#fff' : '#334155' };
        };
        return (
          <div>
            <div className="text-[11px] text-slate-400 mb-2">
              {eff.n_evals} evals · sampled every {eff.sample_minutes} min{eff.vix_regime ? <> · <span className="font-semibold text-amber-600">VIX: {eff.vix_regime}</span></> : null} · colouring by <span className="font-semibold text-slate-600">{effMetric === 'ic' ? 'IC' : effMetric === 'rank_ic' ? 'Rank IC' : effMetric === 'hit' ? 'Hit rate' : effMetric[0].toUpperCase() + effMetric.slice(1)}</span> · green = predictive+, red = predictive−
            </div>
            <div className="overflow-auto">
              <table className="text-[11px] border-separate" style={{ borderSpacing: 2 }}>
                <thead>
                  <tr>
                    <th className="p-1"></th>
                    {eff.horizons.map((h: string) => <th key={h} className="p-1 text-slate-400 font-normal text-center w-14">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {eff.signals.map((s: string) => {
                    const best = eff.matrix[s].best_horizon;
                    return (
                      <tr key={s}>
                        <td className="p-1 text-right text-slate-500 whitespace-nowrap pr-2">{labelOf(s)}</td>
                        {eff.horizons.map((h: string) => {
                          const c = eff.matrix[s].cells[h];
                          const v = cellVal(c);
                          const col = color(v);
                          return (
                            <td key={h} title={c ? `${s} @ ${h}: IC ${c.ic ?? '—'} · RankIC ${c.rank_ic ?? '—'} · Sharpe ${c.sharpe ?? '—'} · Spread ${c.spread ?? '—'}% · Hit ${c.hit != null ? Math.round(c.hit * 100) + '%' : '—'} (n=${c.n})` : ''}
                              className="text-center font-mono w-14 h-9 rounded" style={{ background: col.bg, color: col.fg }}>
                              {best === h && v != null && <span title="best horizon (by |IC|)">☆</span>}{fmt(v)}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="text-[11px] text-slate-500 mt-2">Each row is one signal's edge decaying/building across horizons. A short-horizon signal (order-flow/OI) glows left; a slow one (RND/trend) glows right. ☆ = strongest |IC| in the row. Cells show {effMetric === 'hit' ? 'hit %' : effMetric === 'spread' ? 'spread %' : effMetric.toUpperCase()}; hover any cell for all five metrics.</div>
            <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-1">
              <AlertTriangle className="w-3 h-3" /> {eff.note}
            </div>
          </div>
        );
      })()}

      {/* Signal-correlation matrix */}
      {corr && corr.matrix && (
        <div>
          <div className="text-[11px] text-slate-400 mb-2">
            {corr.n_signals} signals · window {corr.expiry?.slice(0, 10)} ·{' '}
            <span className="font-semibold text-slate-600">≈{corr.effective_independent} truly independent bets</span> of {corr.n_signals}
          </div>
          <div className="overflow-auto">
            <table className="text-[11px] border-separate" style={{ borderSpacing: 2 }}>
              <thead>
                <tr>
                  <th className="p-1"></th>
                  {corr.signals.map((s: string) => (
                    <th key={s} className="p-1 text-slate-400 font-normal align-bottom">
                      <div className="whitespace-nowrap" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
                        {labelOf(s)}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {corr.matrix.map((row: any[], i: number) => (
                  <tr key={i}>
                    <td className="p-1 text-right text-slate-500 whitespace-nowrap pr-2">{labelOf(corr.signals[i])}</td>
                    {row.map((v: number | null, j: number) => {
                      const val = v == null ? null : v;
                      // red for positive (move together), blue for negative (opposed)
                      const a = val == null ? 0 : Math.min(Math.abs(val), 1);
                      const bg = val == null ? '#f1f5f9'
                        : val >= 0 ? `rgba(244,63,94,${0.12 + a * 0.7})` : `rgba(59,130,246,${0.12 + a * 0.7})`;
                      const strong = a >= 0.55;
                      return (
                        <td key={j} title={`${corr.signals[i]} vs ${corr.signals[j]}: ${val ?? '—'} (n=${corr.pair_n?.[i]?.[j] ?? '—'})`}
                          className="text-center font-mono w-11 h-9 rounded"
                          style={{ background: bg, color: strong ? '#fff' : '#334155' }}>
                          {val == null ? '—' : val.toFixed(2)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-slate-400 mt-2">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: 'rgba(244,63,94,0.75)' }} /> move together (redundant)</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: '#f1f5f9' }} /> independent</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded" style={{ background: 'rgba(59,130,246,0.75)' }} /> opposed</span>
          </div>
          {corr.redundancy?.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-slate-500 mb-1">Redundancy ranking <span className="text-slate-400">(avg |corr| to the other signals — high = carries little new information)</span></div>
              <div className="border border-slate-100 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50"><tr className="text-slate-400 text-left">
                    <th className="py-2 px-3 font-normal">Signal</th>
                    <th className="py-2 px-2 font-normal text-right">Avg |corr|</th>
                    <th className="py-2 px-2 font-normal text-right">n</th>
                  </tr></thead>
                  <tbody>
                    {corr.redundancy.map((r: any) => (
                      <tr key={r.signal} className="border-t border-slate-50">
                        <td className="py-2 px-3">{labelOf(r.signal)}</td>
                        <td className={`py-2 px-2 text-right font-mono ${r.avg_abs_corr == null ? 'text-slate-300' : r.avg_abs_corr >= 0.5 ? 'text-rose-600' : r.avg_abs_corr >= 0.3 ? 'text-amber-600' : 'text-emerald-600'}`}>{r.avg_abs_corr ?? '—'}</td>
                        <td className="py-2 px-2 text-right text-slate-400">{r.n_obs}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-2">
            <AlertTriangle className="w-3 h-3" /> {corr.note}
          </div>
        </div>
      )}

      {/* All-signals scoreboard */}
      {allRes && (
        <div>
          <div className="text-[11px] text-slate-400 mb-2">
            {allRes.n_snapshots} evals · sampled every {allRes.sample_minutes} min · {allRes.horizon_hours}h forward
            {allRes.best && <> · best: <span className="font-semibold text-emerald-600">{labelOf(allRes.best)}</span></>}
          </div>
          {allRes.overlap && (
            <div className="flex items-start gap-2 text-[11px] text-amber-700 bg-amber-50 rounded-lg px-3 py-2 mb-2">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              Sampling ({allRes.sample_minutes} min) is finer than the {allRes.horizon_hours}h horizon → observations overlap, so significance is inflated. Effective independent samples ≈ <span className="font-semibold">{allRes.effective_n}</span> (of {allRes.n_snapshots}). Set "Sample every" ≈ the horizon for honest stats.
            </div>
          )}
          <div className="border border-slate-100 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr className="text-slate-400 text-left">
                  <th className="py-2 px-3 font-normal">Signal (ranked by Sharpe)</th>
                  <th className="py-2 px-2 font-normal text-right">Sharpe</th>
                  <th className="py-2 px-2 font-normal text-right">Hit rate</th>
                  <th className="py-2 px-2 font-normal text-right">IC</th>
                  <th className="py-2 px-2 font-normal text-right">Spread</th>
                  <th className="py-2 px-2 font-normal text-right">n</th>
                </tr>
              </thead>
              <tbody>
                {allRes.table.map((row: any, i: number) => {
                  const label = labelOf(row.signal);
                  return (
                    <tr key={row.signal} className="border-t border-slate-50">
                      <td className="py-2 px-3">{i === 0 && row.sharpe != null && <span className="text-emerald-600 mr-1">★</span>}{label}</td>
                      <td className={`py-2 px-2 text-right font-semibold font-mono ${row.sharpe == null ? 'text-slate-300' : row.sharpe > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{row.sharpe ?? '—'}</td>
                      <td className={`py-2 px-2 text-right ${row.hit_rate == null ? 'text-slate-300' : row.hit_rate >= 0.5 ? 'text-emerald-600' : 'text-rose-600'}`}>{row.hit_rate != null ? `${Math.round(row.hit_rate * 100)}%` : '—'}</td>
                      <td className={`py-2 px-2 text-right font-mono ${(row.ic || 0) > 0.1 ? 'text-emerald-600' : (row.ic || 0) < -0.1 ? 'text-rose-600' : 'text-slate-500'}`}>{row.ic ?? '—'}</td>
                      <td className={`py-2 px-2 text-right font-mono ${pnlColor(row.spread || 0)}`}>{row.spread != null ? `${row.spread}%` : '—'}</td>
                      <td className="py-2 px-2 text-right text-slate-400">{row.n}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="text-[11px] text-slate-500 mt-2"><span className="font-semibold">Sharpe</span> = consistency of the directional call (mean/σ of sign×move) — the "can I count on it" metric. <span className="font-semibold">IC</span> = correlation (predictive if &gt; 0). <span className="font-semibold">Spread</span> = top-half − bottom-half forward return. Green Sharpe/IC/spread and hit &gt; 50% = a signal worth its weight.</div>
          <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-1">
            <AlertTriangle className="w-3 h-3" /> {allRes.note}
          </div>
        </div>
      )}

      {res && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5 mb-3">
            <Tile label="Hit rate" value={res.hit_rate != null ? `${Math.round(res.hit_rate * 100)}%` : '—'} cls={hitGood ? 'text-emerald-600' : 'text-rose-600'} />
            <Tile label="Correlation" value={res.correlation} cls={corrGood ? 'text-emerald-600' : res.correlation < -0.1 ? 'text-rose-600' : 'text-slate-500'} />
            <Tile label={`Move when bullish`} value={res.avg_move_when_bullish_pct != null ? `${res.avg_move_when_bullish_pct}%` : '—'} cls={pnlColor(res.avg_move_when_bullish_pct || 0)} />
            <Tile label={`Move when bearish`} value={res.avg_move_when_bearish_pct != null ? `${res.avg_move_when_bearish_pct}%` : '—'} cls={pnlColor(-(res.avg_move_when_bearish_pct || 0))} />
            <Tile label="Samples" value={res.n} />
          </div>

          {curve?.curve && (
            <div className="mb-3">
              <div className="text-xs text-slate-500 mb-1">Edge across horizons <span className="text-slate-400">(where the signal's predictive power lives)</span></div>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={curve.curve.filter((c: any) => c.ic != null).map((c: any) => ({ horizon: c.horizon, ic: c.ic, sharpe: c.sharpe }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                  <XAxis dataKey="horizon" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 9 }} width={38} />
                  <Tooltip formatter={(v: any, n: any) => [v, n]} />
                  <ReferenceLine y={0} stroke="#94a3b8" />
                  <Bar dataKey="ic" name="IC">
                    {curve.curve.filter((c: any) => c.ic != null).map((c: any, i: number) => (
                      <Cell key={i} fill={(c.ic || 0) >= 0 ? '#10b981' : '#f43f5e'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="text-[11px] text-slate-400">Bars = IC (correlation with the forward move) at each horizon. A fast signal peaks left (5–15 min); a slow one peaks right (60 min / EOD). Flat-near-zero = no edge at any horizon.</div>
            </div>
          )}

          <div className="text-xs text-slate-500 mb-1">Signal strength → avg forward NIFTY move ({res.horizon_hours}h)</div>
          <div className="text-[11px] text-slate-400 mb-1">A predictive signal slopes upward left→right (stronger bullish score → bigger positive move).</div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={res.buckets.filter((b: any) => b.n > 0)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
              <XAxis dataKey="bucket" tick={{ fontSize: 9 }} />
              <YAxis tick={{ fontSize: 9 }} width={38} tickFormatter={(v) => `${v}%`} />
              <Tooltip formatter={(v: any, _n: any, p: any) => [`${v}%  (n=${p.payload.n})`, 'avg fwd move']} />
              <ReferenceLine y={0} stroke="#94a3b8" />
              <Bar dataKey="avg_fwd_ret_pct">
                {res.buckets.filter((b: any) => b.n > 0).map((b: any, i: number) => (
                  <Cell key={i} fill={(b.avg_fwd_ret_pct || 0) >= 0 ? '#10b981' : '#f43f5e'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>

          <div className="mt-2 p-2.5 bg-slate-50 rounded-lg text-[11px] text-slate-600">
            <span className="font-semibold">Read: </span>
            {res.correlation > 0.15
              ? `favoured — a stronger ${labelOf(signal)} reading tended to precede a NIFTY move in the same direction (corr ${res.correlation}).`
              : res.correlation < -0.15
                ? `anti-predictive on this data — the signal tended to precede a move the OPPOSITE way (corr ${res.correlation}); it may be a fade/mean-reversion signal at this horizon, or the sample is too thin.`
                : `weak/no relationship at this horizon (corr ${res.correlation}) — the signal didn't lead the ${res.horizon_hours}h move here.`}
          </div>

          {res.misses?.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-slate-500 mb-1">Biggest misses <span className="text-slate-400">(high conviction, wrong direction — "what's missing")</span></div>
              <div className="max-h-52 overflow-auto border border-slate-100 rounded-lg">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr className="text-slate-400 text-left">
                      <th className="py-1.5 px-2 font-normal">Time (IST)</th>
                      <th className="py-1.5 px-2 font-normal text-right">Score</th>
                      <th className="py-1.5 px-2 font-normal text-right">Spot</th>
                      <th className="py-1.5 px-2 font-normal text-right">{res.horizon_hours}h move</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.misses.map((m: any, i: number) => (
                      <tr key={i} className="border-t border-slate-50">
                        <td className="py-1.5 px-2 text-slate-500 whitespace-nowrap">{fmtIST(m.ts)}</td>
                        <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(m.score)}`}>{m.score > 0 ? '+' : ''}{m.score}</td>
                        <td className="py-1.5 px-2 text-right">{Math.round(m.spot_now)}</td>
                        <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(m.fwd_move_pts)}`}>{m.fwd_move_pts > 0 ? '+' : ''}{m.fwd_move_pts} ({m.fwd_ret_pct}%)</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-3">
            <AlertTriangle className="w-3 h-3" /> {res.note}
          </div>
        </div>
      )}
    </div>
  );
};
