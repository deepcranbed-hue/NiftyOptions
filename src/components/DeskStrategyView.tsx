import React, { useState, useEffect, useCallback } from 'react';
import { CONFIG } from '../lib/constants';   // single source for lot size (frontend)
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid,
} from 'recharts';
import { Briefcase, Plus, Trash2, Play, AlertTriangle, RefreshCw } from 'lucide-react';
import { fmtInr, pnlColor, fmtIST, Tile, TypeBadge, Field } from './deskShared';

const DEFAULT_LOT = 75;   // NIFTY lot size

/* Shows the signal state that drove an adjustment — proves it's not spot-only. */
const SignalWhy: React.FC<{ sig: any }> = ({ sig }) => {
  if (!sig) return null;
  const dir = sig.direction > 0 ? 'bullish' : sig.direction < 0 ? 'bearish' : 'neutral';
  return (
    <div className="mb-1 text-[10px] text-slate-500 bg-slate-100 rounded px-1.5 py-1">
      <span className="font-semibold">signals:</span> {sig.regime} · {dir} · score {sig.net_score >= 0 ? '+' : ''}{sig.net_score} · conf {sig.confidence}
      {sig.signals && Object.keys(sig.signals).length > 0 && (
        <span className="text-slate-400"> · {Object.entries(sig.signals).map(([k, v]: any) => `${k.split('_')[0]} ${v >= 0 ? '+' : ''}${v}`).join(', ')}</span>
      )}
    </div>
  );
};

/*
 * DeskStrategyView
 * ----------------
 * The desk's portfolio + backtesting workspace. Strategies added from the
 * Strategy Desk (suggestions) land here (shared, backend-persisted book), along
 * with any futures/stocks you add. Check combined P&L and run the walk-forward
 * backtest with the controls laid out clearly. Self-contained (no props).
 */

export const DeskStrategyView: React.FC = () => {
  const [portfolio, setPortfolio] = useState<any>(null);
  const [bt, setBt] = useState<any>(null);
  const [btMode, setBtMode] = useState<'auto' | 'book'>('auto');
  const [exitMode, setExitMode] = useState('horizon');
  const [freq, setFreq] = useState('auto');
  const [rollDir, setRollDir] = useState(false);
  const [stopLoss, setStopLoss] = useState(false);
  const [stopAmt, setStopAmt] = useState('');           // user-set ₹ stop; '' = auto
  // adjustment-discipline knobs ('' = engine default: 15 / 2 / 1)
  const [cooldownMin, setCooldownMin] = useState('');
  const [maxRolls, setMaxRolls] = useState('');
  const [persistNear, setPersistNear] = useState('');
  const [harvest, setHarvest] = useState(false);        // opportunistic premium harvest
  const [minHarvest, setMinHarvest] = useState('100');
  const [takeProfit, setTakeProfit] = useState(false);  // book profit at % of max credit
  const [tpFrac, setTpFrac] = useState('60');           // percent
  const [maxManage, setMaxManage] = useState('');       // monitoring checks ('' = default)
  const [expiries, setExpiries] = useState<any[]>([]);
  const [liveExpList, setLiveExpList] = useState<string[]>([]);   // live exchange calendar (as the suggester)
  const [btExpiry, setBtExpiry] = useState('');
  const [windowDays, setWindowDays] = useState('all');
  const [edgeMult, setEdgeMult] = useState('0');        // cost-edge "do-nothing" gate (0 = off)
  const [mpsBench, setMpsBench] = useState('off');      // MPS0 %-of-max benchmark (off|gross|net)
  const [btLoading, setBtLoading] = useState(false);
  const [btErr, setBtErr] = useState('');
  const [btProgress, setBtProgress] = useState(0);
  const [openTrade, setOpenTrade] = useState<number | null>(null);
  const STRUCTURES = ['iron_condor', 'iron_butterfly', 'bull_put_spread', 'bear_call_spread', 'bull_call_spread', 'bear_put_spread', 'long_straddle', 'long_strangle'];
  const [addKind, setAddKind] = useState<string>('long_future');
  const [instMeta, setInstMeta] = useState<{ exchanges: string[]; lot_size: number; expiries: string[]; futures_expiries?: string[]; symbols?: string[] }>({ exchanges: ['NSE', 'NFO'], lot_size: CONFIG.lot_size, expiries: [], futures_expiries: [], symbols: [] });
  const [addExpiry, setAddExpiry] = useState('');    // futures contract expiry (from DB)
  const [addFutSymbol, setAddFutSymbol] = useState('NIFTY');   // selected futures series symbol
  const [form, setForm] = useState<any>({ symbol: 'NIFTY', entry_price: '', qty: '1', strike: '', side: 'long', exchange: 'NSE' });
  const [simEntry, setSimEntry] = useState('');       // datetime-local (IST)
  const [simFamily, setSimFamily] = useState('');     // '' = use suggestion
  const [sim, setSim] = useState<any>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simProgress, setSimProgress] = useState(0);
  const [simErr, setSimErr] = useState('');
  const [simChain, setSimChain] = useState<any>(null);      // chain as-of entry time
  const [simLegs, setSimLegs] = useState<any[]>([]);        // user-picked legs
  const [hideHolds, setHideHolds] = useState(false);        // decision-trace filter
  const [traceView, setTraceView] = useState<'cards' | 'table'>('cards');   // trace as cards or table
  // Proactive (advisory) forecast-driven action evaluator — user-tunable
  const [proactive, setProactive] = useState(false);
  const [proLambda, setProLambda] = useState(0.5);          // tail-aversion weight λ
  const [proHorizon, setProHorizon] = useState(100);        // touch-window, % of time-to-expiry
  const [proMinEdge, setProMinEdge] = useState(5);          // min pts to beat HOLD (churn guard)
  const [proRiskDrift, setProRiskDrift] = useState(100);    // % of drift used in the RISK tail (0=symmetric)
  const [proMaxHarvests, setProMaxHarvests] = useState(0);  // harvest budget: max/day (0 = no cap)
  const [proMinWidth, setProMinWidth] = useState(200);      // vertical spreads: min long↔short gap (pts)
  const [proOpen, setProOpen] = useState(false);            // settings section expanded?
  const [hc, setHc] = useState<any>(null);                  // A/B/C/D harvest experiment
  const [hcLoading, setHcLoading] = useState(false);
  const [hcErr, setHcErr] = useState('');
  const [hcTimeout, setHcTimeout] = useState(10);           // user-defined timeout (minutes)
  const [hcElapsed, setHcElapsed] = useState(0);            // live elapsed seconds
  const [harvestStrat, setHarvestStrat] = useState<'A' | 'B' | 'C' | 'D'>('A');   // which harvest strategy Simulate runs
  const [simCmp, setSimCmp] = useState<any>(null);          // single-entry A/B/C/D comparison
  const [simCmpLoading, setSimCmpLoading] = useState(false);
  const [cmpOpenTs, setCmpOpenTs] = useState<string | null>(null);   // expanded grid row

  const loadPortfolio = useCallback(async () => {
    try {
      const r = await fetch('/api/strategy/portfolio');
      if (r.ok) { try { setPortfolio(JSON.parse(await r.text())); } catch { /* non-JSON */ } }
    } catch (e) { /* network unreachable — leave portfolio as-is */ }
  }, []);
  const loadExpiries = useCallback(async () => {
    try {
      const r = await fetch('/api/strategy/expiries');
      if (!r.ok) return;
      let j: any = null; try { j = JSON.parse(await r.text()); } catch { /* non-JSON */ }
      if (j?.expiries) setExpiries(j.expiries);
    } catch (e) { /* network unreachable */ }
  }, []);
  // Same live exchange-expiry calendar the Directional Suggester uses.
  const loadLiveExpiries = useCallback(async () => {
    try {
      const r = await fetch('/api/exchange-expiries');
      if (!r.ok) return;
      const j = await r.json();
      if (j?.success && Array.isArray(j.expiries)) setLiveExpList(j.expiries);
    } catch (e) { /* network unreachable */ }
  }, []);
  useEffect(() => { loadPortfolio(); loadExpiries(); loadLiveExpiries(); }, [loadPortfolio, loadExpiries, loadLiveExpiries]);

  useEffect(() => {
    fetch('/api/strategy/instruments').then(r => r.json()).then((j) => {
      if (j?.exchanges) {
        setInstMeta(j);
        setForm((f: any) => ({ ...f, exchange: (j.exchanges || []).includes('NSE') ? 'NSE' : (j.exchanges || []).filter((x: string) => x !== 'NFO')[0] || 'NSE',
                               symbol: (j.symbols && j.symbols[0]) || f.symbol }));
        if (j.expiries?.length) setAddExpiry(j.expiries[j.expiries.length - 1]);
      }
    }).catch(() => {});
  }, []);

  const addInstrument = async () => {
    const t = addKind;                       // long_future|short_future|long_call|long_put|short_call|short_put|stock|<structure>
    // Structures are built + priced from the option chain at the chosen snapshot.
    if (STRUCTURES.includes(t)) {
      const now = simEntry ? new Date(simEntry).toISOString().slice(0, 19) + 'Z' : null;
      const r = await fetch('/api/strategy/candidate/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family: t, expiry: addExpiry || btExpiry || null, now, exchange: 'NFO' }),
      });
      const j = await r.json();
      if (!j.ok && j.error) setSimErr(`Couldn't build ${t.replace(/_/g, ' ')}: ${j.error}`);
      loadPortfolio();
      return;
    }
    const qty = parseInt(form.qty, 10) || 1;
    let body: any;
    // Entry price is derived from the data at the backtest entry time, so we don't
    // ask for it here — the book position defines the instrument, the backtest sets when.
    if (t === 'long_future' || t === 'short_future') {
      body = { kind: 'future', symbol: addFutSymbol || 'NIFTY', entry_price: 0, qty: t === 'long_future' ? qty : -qty,
               exchange: 'NFO', expiry: addExpiry || null, lot_size: instMeta.lot_size,
               label: `${t === 'long_future' ? 'Long' : 'Short'} ${addFutSymbol || 'NIFTY'} · NFO${addExpiry ? ` · exp ${addExpiry.slice(0, 10)}` : ''}` };
    } else if (t === 'stock') {
      body = { kind: 'stock', symbol: form.symbol.toUpperCase(), entry_price: 0,
               qty: (form as any).side === 'short' ? -qty : qty, exchange: (form as any).exchange || 'NSE' };
    } else { // option leg
      const side = t.includes('call') ? 'call' : 'put';
      const sign = t.startsWith('long') ? 1 : -1;
      const strike = parseFloat((form as any).strike) || 0;
      body = { kind: 'option_strategy', family: t, legs: [[side, strike, sign]],
               entry_prices: {}, exchange: 'NFO', expiry: addExpiry || null,
               label: `${t.replace('_', ' ')} ${strike}${addExpiry ? ` · exp ${addExpiry.slice(0, 10)}` : ''} · NFO` };
    }
    await fetch('/api/strategy/portfolio/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    setForm({ symbol: 'NIFTY', entry_price: '', qty: '1', strike: '', side: 'long', exchange: 'NSE' } as any);
    loadPortfolio();
  };
  const backtestBookPos = async (id: string) => {
    if (!simEntry) { setSimErr('Pick an entry date & time (in the Simulate box below) first.'); return; }
    setSimLoading(true); setSimErr(''); setSim(null);
    try {
      const utc = new Date(simEntry).toISOString().slice(0, 19) + 'Z';
      const r = await fetch('/api/strategy/book/backtest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pos_id: id, entry_ts: utc, expiry: btExpiry || null, exit_mode: exitMode,
          stop_loss: stopLoss, stop_loss_rupees: stopLoss && stopAmt ? Number(stopAmt) : null,
          take_profit: takeProfit, take_profit_frac: tpFrac ? Number(tpFrac) / 100 : 0.6,
        }),
      });
      const j = await r.json();
      if (!r.ok || j.error) setSimErr(j.error || j.detail || `Backend ${r.status}`);
      else setSim(j);
    } catch (e) { setSimErr('Cannot reach backend at /api.'); }
    finally { setSimLoading(false); }
  };
  const removePos = async (id: string) => {
    await fetch('/api/strategy/portfolio/remove', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    loadPortfolio();
  };

  const runBacktest = async () => {
    setBtLoading(true); setBtErr(''); setBt(null); setBtProgress(6);
    const tick = setInterval(() => setBtProgress((p) => (p < 92 ? p + Math.max(1, (92 - p) * 0.08) : p)), 400);
    const ctrl = new AbortController();
    const timeoutMs = (btMode === 'auto' && exitMode === 'manage') ? 300000 : 120000;
    const timeout = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch('/api/strategy/backtest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: btMode, exit_mode: exitMode,
          freq_minutes: freq === 'auto' ? null : Number(freq),
          roll_directional: rollDir, stop_loss: stopLoss,
          stop_loss_rupees: stopLoss && stopAmt ? Number(stopAmt) : null,
          cooldown_min: cooldownMin ? Number(cooldownMin) : null,
          max_rolls: maxRolls ? Number(maxRolls) : null,
          persist_near: persistNear ? Number(persistNear) : null,
          harvest, min_harvest_inr: minHarvest ? Number(minHarvest) : 100,
          take_profit: takeProfit, take_profit_frac: tpFrac ? Number(tpFrac) / 100 : 0.6,
          max_manage: maxManage ? Number(maxManage) : null,
          expiry: btExpiry || null,
          window_days: windowDays === 'all' ? null : Number(windowDays),
          min_edge_cost_mult: Number(edgeMult) || 0,
          mps_benchmark: mpsBench,
        }),
        signal: ctrl.signal,
      });
      const _t = await r.text();
      let j: any = null; try { j = JSON.parse(_t); } catch { /* non-JSON error body */ }
      if (!r.ok) setBtErr(`Backend ${r.status}: ${j?.detail || _t.slice(0, 300) || r.statusText} — did you restart uvicorn?`);
      else if (!j) setBtErr(`Backend returned a non-JSON response (${r.status}): ${_t.slice(0, 300)}`);
      else if (j.error) setBtErr(j.error);
      else if (btMode === 'book' && (!j.series || j.series.length === 0))
        setBtErr(j.metrics?.note || 'No positions in the book to backtest — add some first.');
      else setBt(j);
    } catch (e: any) {
      setBtErr(e.name === 'AbortError'
        ? `Backtest timed out (>${timeoutMs / 1000}s). Try a shorter window, coarser frequency, or horizon mode.`
        : 'Cannot reach backend at /api (is uvicorn running on port 8000?)');
    } finally {
      clearTimeout(timeout); clearInterval(tick); setBtProgress(100); setBtLoading(false);
      setTimeout(() => setBtProgress(0), 600);
    }
  };

  const toUtc = (local: string) => new Date(local).toISOString().slice(0, 19) + 'Z';

  const loadChain = useCallback(async () => {
    if (!simEntry) { setSimErr('Pick an entry date & time first.'); return; }
    setSimErr(''); setSimChain(null); setSimLegs([]);
    try {
      const fam = simFamily ? `&family=${encodeURIComponent(simFamily)}` : '';
      const r = await fetch(`/api/strategy/chain?at=${encodeURIComponent(toUtc(simEntry))}${btExpiry ? `&expiry=${encodeURIComponent(btExpiry)}` : ''}${fam}`);
      const _t = await r.text();
      let j: any = null; try { j = JSON.parse(_t); } catch { /* non-JSON error body */ }
      if (!r.ok) { setSimErr(`Backend ${r.status}: ${j?.detail || _t.slice(0, 300) || r.statusText} — did you restart uvicorn?`); return; }
      if (!j) { setSimErr(`Backend returned a non-JSON response (${r.status}): ${_t.slice(0, 300)}`); return; }
      if (j.error) { setSimErr(j.error); return; }
      setSimChain(j);
      if (j.template) setSimLegs(j.template.legs.map((l: any) => ({
        side: String(l.side || '').toLowerCase().startsWith('c') ? 'call' : 'put',
        strike: Number(l.strike), sign: Number(l.sign), price: l.premium,
      })));
      else if (j.template_error) setSimErr(j.template_error);
    } catch (e) { setSimErr('Cannot reach backend at /api (is uvicorn running on 8000?).'); }
  }, [simEntry, simFamily, btExpiry]);

  // auto-load the chain/template whenever the entry time or structure changes
  useEffect(() => { if (simEntry) loadChain(); }, [simEntry, simFamily, loadChain]);

  const priceOf = (side: string, strike: number) => {
    const row = simChain?.rows.find((r: any) => r.strike === strike);
    return (side === 'call' ? row?.call_ltp : row?.put_ltp) ?? 0;
  };
  const updateLeg = (i: number, patch: any) =>
    setSimLegs(simLegs.map((l, k) => {
      if (k !== i) return l;
      const nl = { ...l, ...patch };
      nl.price = priceOf(nl.side, nl.strike);
      return nl;
    }));
  const addBlankLeg = () => {
    const atm = simChain?.atm ?? simChain?.rows?.[0]?.strike;
    setSimLegs([...simLegs, { side: 'call', strike: atm, sign: -1, price: priceOf('call', atm) }]);
  };
  const removeLeg = (i: number) => setSimLegs(simLegs.filter((_, k) => k !== i));
  const netPremium = simLegs.reduce((s, l) => s + l.sign * (l.price || 0), 0);

  const runSim = async () => {
    if (!simEntry) { setSimErr('Pick an entry date & time first.'); return; }
    setSimLoading(true); setSimErr(''); setSim(null); setSimProgress(6);
    // walk-forward-to-expiry has no per-step callback, so an eased creep bar shows
    // it's alive (it re-evaluates the position at every snapshot to expiry).
    const tick = setInterval(() => setSimProgress((p) => (p < 92 ? p + Math.max(1, (92 - p) * 0.08) : p)), 400);
    // datetime-local is the user's local (IST) wall-clock; convert to UTC 'Z'.
    const utc = new Date(simEntry).toISOString().slice(0, 19) + 'Z';
    try {
      const r = await fetch('/api/strategy/simulate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expiry: btExpiry || null, entry_ts: utc,
          family: simFamily || null,
          legs: simLegs.length ? simLegs.map((l) => [l.side, l.strike, l.sign]) : null,
          exit_mode: exitMode,
          roll_directional: rollDir, stop_loss: stopLoss,
          stop_loss_rupees: stopLoss && stopAmt ? Number(stopAmt) : null,
          cooldown_min: cooldownMin ? Number(cooldownMin) : null,
          max_rolls: maxRolls ? Number(maxRolls) : null,
          persist_near: persistNear ? Number(persistNear) : null,
          harvest: HARVEST_STRAT[harvestStrat].harvest, harvest_gate: HARVEST_STRAT[harvestStrat].gate,
          min_harvest_inr: minHarvest ? Number(minHarvest) : 100,
          take_profit: takeProfit, take_profit_frac: tpFrac ? Number(tpFrac) / 100 : 0.6,
          max_manage: maxManage ? Number(maxManage) : null,
          proactive: proactive || HARVEST_STRAT[harvestStrat].gate !== 'off', proactive_lambda: proLambda,
          proactive_horizon_frac: (Number(proHorizon) || 100) / 100,
          proactive_min_edge: Number(proMinEdge) || 0,
          proactive_risk_drift: (Number(proRiskDrift) || 0) / 100,
          proactive_max_harvests: Number(proMaxHarvests) > 0 ? Number(proMaxHarvests) : null,
          proactive_min_width: Number(proMinWidth) > 0 ? Number(proMinWidth) : 200,
        }),
      });
      const _t = await r.text();
      let j: any = null; try { j = JSON.parse(_t); } catch { /* non-JSON error body */ }
      if (!r.ok) setSimErr(`Backend ${r.status}: ${j?.detail || _t.slice(0, 300) || r.statusText}`);
      else if (!j) setSimErr(`Backend returned a non-JSON response (${r.status}): ${_t.slice(0, 300)}`);
      else if (j.error) setSimErr(j.error + (j.note ? ` (${j.note})` : ''));
      else setSim(j);
    } catch (e) { setSimErr('Cannot reach backend at /api (is uvicorn running on port 8000?).'); }
    finally { clearInterval(tick); setSimProgress(100); setSimLoading(false); setTimeout(() => setSimProgress(0), 600); }
  };

  const runSimCompare = async () => {
    if (!simEntry) { setSimErr('Pick an entry date & time first.'); return; }
    setSimCmpLoading(true); setSimCmp(null); setSimErr('');
    try {
      const utc = new Date(simEntry).toISOString().slice(0, 19) + 'Z';
      const r = await fetch('/api/strategy/simulate-compare', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expiry: btExpiry || null, entry_ts: utc, family: simFamily || null,
          legs: simLegs.length ? simLegs.map((l) => [l.side, l.strike, l.sign]) : null,
          exit_mode: exitMode, stop_loss: stopLoss,
          stop_loss_rupees: stopLoss && stopAmt ? Number(stopAmt) : null,
          lam: proLambda, risk_drift: (Number(proRiskDrift) || 0) / 100,
          max_harvests: proMaxHarvests > 0 ? proMaxHarvests : 2, max_harvest_debt: 100,
          max_rolls: maxRolls ? Number(maxRolls) : null,
          cooldown_min: cooldownMin ? Number(cooldownMin) : null,
          persist_near: persistNear ? Number(persistNear) : null,
          min_harvest_inr: minHarvest ? Number(minHarvest) : 100,
        }),
      });
      const j = await r.json();
      if (!r.ok || j.error) setSimErr(j.error || j.detail || `Backend ${r.status}`);
      else setSimCmp(j);
    } catch (e) { setSimErr('Cannot reach backend at /api.'); }
    finally { setSimCmpLoading(false); }
  };

  const downloadSimCsv = () => {
    if (!sim?.decisions) return;
    const esc = (v: any) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const header = ['time', 'spot', 'mark_inr', 'return_pct', 'drawdown_pct', 'action', 'advisory_best', 'harvest_debt_pts', 'roll', 'reason'];
    const lines = sim.decisions.map((d: any) => [d.ts, d.spot, d.mark_pnl, d.return_pct ?? '', d.drawdown_pct ?? '', d.action, d.advisory?.best || '', d.advisory?.harvest_state?.debt_pts ?? '', d.roll || '', d.reason || ''].map(esc).join(','));
    const csv = [header.join(','), ...lines].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `strategy_${harvestStrat}_trace_${(sim.entry_ts || 'entry').slice(0, 16).replace(/[:T]/g, '-')}.csv`;
    a.click(); URL.revokeObjectURL(url);
  };

  const downloadCmpCsv = () => {
    if (!simCmp?.timeline) return;
    const order: string[] = simCmp.order;
    const esc = (v: any) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const header = ['time', 'spot', ...order.flatMap((n) => { const k = n.split('_')[0]; return [`${k}_action`, `${k}_mark`, `${k}_reason`]; })];
    const lines = simCmp.timeline.map((row: any) => {
      const cells: any[] = [row.ts, row.spot];
      order.forEach((n) => { const c = row[n]; cells.push(c?.action || '', c?.mark ?? '', c?.reason || ''); });
      return cells.map(esc).join(',');
    });
    const csv = [header.join(','), ...lines].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `abcd_compare_${(simCmp.entry_ts || 'entry').slice(0, 16).replace(/[:T]/g, '-')}.csv`;
    a.click(); URL.revokeObjectURL(url);
  };

  const HARVEST_STRAT: Record<string, { harvest: boolean; gate: string; label: string }> = {
    A: { harvest: true, gate: 'off', label: 'Always harvest (current)' },
    B: { harvest: false, gate: 'off', label: 'Never harvest' },
    C: { harvest: true, gate: 'optimizer', label: 'Optimizer-gated harvest' },
    D: { harvest: true, gate: 'both', label: 'Optimizer + budget' },
  };

  const runHarvestCompare = async () => {
    setHcLoading(true); setHcErr(''); setHc(null); setHcElapsed(0);
    const mins = Math.max(1, Number(hcTimeout) || 10);
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), mins * 60000);   // user-defined cap
    const tick = setInterval(() => setHcElapsed((s) => s + 1), 1000);
    try {
      const r = await fetch('/api/strategy/compare-harvest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
        body: JSON.stringify({
          expiry: btExpiry || null,
          window_days: windowDays === 'all' ? null : Number(windowDays),
          freq_minutes: freq === 'auto' ? null : Number(freq),
          stop_loss: stopLoss, stop_loss_rupees: stopLoss && stopAmt ? Number(stopAmt) : null,
          lam: proLambda, risk_drift: (Number(proRiskDrift) || 0) / 100,
          max_harvests: proMaxHarvests > 0 ? proMaxHarvests : 2, max_harvest_debt: 100,
        }),
      });
      const j = await r.json();
      if (!r.ok || j.error) setHcErr(j.error || j.detail || `Backend ${r.status}`);
      else setHc(j);
    } catch (e: any) {
      setHcErr(e.name === 'AbortError'
        ? `Timed out (>${mins} min). Raise the timeout, or pick a shorter window / coarser frequency and retry.`
        : 'Cannot reach backend at /api.');
    } finally { clearTimeout(timeout); clearInterval(tick); setHcLoading(false); }
  };

  const autoEquity = () => {
    if (!bt?.trades) return [];
    let cum = 0;
    return bt.trades.map((t: any, i: number) => { cum += t.pnl_rupees || 0; return { i: i + 1, pnl: Math.round(cum) }; });
  };

  const manage = btMode === 'auto' && exitMode === 'manage';
  const sel = 'text-xs border border-slate-200 rounded-lg px-2 py-1.5 w-full';
  // futures contracts = the actual futures SERIES in the DB (near/next month with
  // their own expiries); fall back to spot-tracked NIFTY with derived monthly expiries.
  const futuresContracts = (instMeta.futures_symbols && instMeta.futures_symbols.length)
    ? instMeta.futures_symbols.map((f: any) => ({ symbol: f.symbol, expiry: String(f.expiry || '').slice(0, 10) }))
    : (instMeta.futures_expiries || []).map((e: string) => ({ symbol: 'NIFTY', expiry: String(e).slice(0, 10) }));
  // Add-form expiries come from the SAME live exchange calendar the Directional
  // Suggester uses (/api/exchange-expiries) — so the list is the current, dynamic
  // set of tradable expiries, not the stale captured list. (The Backtest selector
  // below still uses `expiries`, which includes completed expiries you replay.)
  const liveExpiries = (liveExpList || []).map((e: string) => ({ expiry: String(e).slice(0, 10) }));

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Briefcase className="w-5 h-5 text-indigo-600" />
          <h2 className="text-base font-bold text-slate-800">Desk Book</h2>
        </div>
        <button onClick={loadPortfolio} className="p-1.5 rounded-lg hover:bg-slate-100" title="Refresh portfolio">
          <RefreshCw className="w-4 h-4 text-slate-400" />
        </button>
      </div>

      {/* ---- Portfolio ---- */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-500">Portfolio <span className="text-slate-400">(add strategies from the Strategy Desk; add futures/stocks here)</span></span>
        <div className="flex items-center gap-1.5">
          <select value={addKind} onChange={(e) => { setAddKind(e.target.value); setAddExpiry(''); }} className="text-xs border border-slate-200 rounded-lg px-2 py-1">
            <optgroup label="Future"><option value="long_future">Long future</option><option value="short_future">Short future</option></optgroup>
            <optgroup label="Option leg"><option value="long_call">Long call</option><option value="long_put">Long put</option><option value="short_call">Short call</option><option value="short_put">Short put</option></optgroup>
            <optgroup label="Structure (built from chain)"><option value="iron_condor">Iron condor</option><option value="iron_butterfly">Iron butterfly</option><option value="bull_put_spread">Bull put spread</option><option value="bear_call_spread">Bear call spread</option><option value="bull_call_spread">Bull call spread</option><option value="bear_put_spread">Bear put spread</option><option value="long_straddle">Long straddle</option><option value="long_strangle">Long strangle</option></optgroup>
            <optgroup label="Cash"><option value="stock">Stock</option></optgroup>
          </select>
          {STRUCTURES.includes(addKind) ? (
            <>
              {/* structures are multi-leg NFO option positions built from the chain */}
              <span className="text-[10px] px-1.5 py-1 rounded bg-slate-100 text-slate-500 font-semibold">NFO</span>
              <select value={addExpiry} onChange={(e) => setAddExpiry(e.target.value)} className="text-xs border border-slate-200 rounded-lg px-2 py-1" title="Option expiry the structure is built on (current/future only)">
                <option value="">expiry…</option>
                {liveExpiries.map((e: any) => <option key={e.expiry} value={e.expiry}>{String(e.expiry).slice(0, 10)}</option>)}
              </select>
              <span className="text-[10px] text-slate-400">strikes chosen from the chain at the Simulate entry-time</span>
            </>
          ) : (
            <>
              {/* stock: symbol dropdown from the DB */}
              {addKind === 'stock' && (
                <select value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-1 max-w-[110px]" title="Stock (from DB)">
                  {(instMeta.symbols && instMeta.symbols.length ? instMeta.symbols : ['RELIANCE']).map((x: string) => <option key={x} value={x}>{x}</option>)}
                </select>
              )}
              {/* exchange — NFO for F&O (future/option), cash exchange for stock */}
              {addKind === 'stock' ? (
                <select value={form.exchange} onChange={(e) => setForm({ ...form, exchange: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-1" title="Exchange (from DB)">
                  {instMeta.exchanges.filter(x => x !== 'NFO').map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              ) : (
                <span className="text-[10px] px-1.5 py-1 rounded bg-slate-100 text-slate-500 font-semibold">NFO</span>
              )}
              {/* futures contract — the actual near/next series with its own expiry */}
              {addKind.includes('future') && (
                <select value={addExpiry} onChange={(e) => { const c = futuresContracts.find((f: any) => f.expiry === e.target.value); setAddExpiry(e.target.value); setAddFutSymbol(c?.symbol || 'NIFTY'); }} className="text-xs border border-slate-200 rounded-lg px-2 py-1" title="Futures contract (series · expiry, from DB)">
                  <option value="">contract…</option>
                  {futuresContracts.map((f: any) => <option key={f.expiry + f.symbol} value={f.expiry}>{f.symbol !== 'NIFTY' ? `${f.symbol} · ` : ''}exp {f.expiry}</option>)}
                </select>
              )}
              {/* option expiry — weekly/monthly */}
              {(addKind.includes('call') || addKind.includes('put')) && (
                <select value={addExpiry} onChange={(e) => setAddExpiry(e.target.value)} className="text-xs border border-slate-200 rounded-lg px-2 py-1" title="Option expiry — weekly/monthly, current/future only">
                  <option value="">expiry…</option>
                  {liveExpiries.map((e: any) => <option key={e.expiry} value={e.expiry}>{String(e.expiry).slice(0, 10)}</option>)}
                </select>
              )}
              {addKind === 'stock' && <select value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-1"><option value="long">Long</option><option value="short">Short</option></select>}
              {(addKind.includes('call') || addKind.includes('put')) && <input placeholder="Strike" value={form.strike} onChange={(e) => setForm({ ...form, strike: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-1 w-16" />}
              <input placeholder={addKind === 'stock' ? 'Shares' : 'Lots'} value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-1 w-14" />
            </>
          )}
          <button onClick={addInstrument} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200" title="Add"><Plus className="w-3.5 h-3.5 text-slate-600" /></button>
        </div>
      </div>
      <table className="w-full text-sm table-fixed">
        <thead>
          <tr className="text-xs text-slate-400 text-left">
            <th className="py-1.5 font-normal">Instrument</th>
            <th className="py-1.5 font-normal w-16">Type</th>
            <th className="py-1.5 font-normal w-16 text-right">Qty</th>
            <th className="py-1.5 font-normal w-16 text-right">Entry</th>
            <th className="py-1.5 font-normal w-16 text-right">Current</th>
            <th className="py-1.5 font-normal w-20 text-right">P&L</th>
            <th className="w-14" />
          </tr>
        </thead>
        <tbody>
          {(portfolio?.valuation?.lines || []).map((l: any) => (
            <tr key={l.id} className="border-t border-slate-100">
              <td className="py-2 truncate">{l.label}</td>
              <td className="py-2"><TypeBadge kind={l.kind} /></td>
              <td className="py-2 text-right text-slate-500 text-xs">{l.qty ?? '—'}</td>
              <td className="py-2 text-right text-slate-500 font-mono text-xs">{l.entry ?? '—'}</td>
              <td className="py-2 text-right font-mono text-xs">
                {l.current ?? '—'}{l.marked_live === false && <span className="text-amber-500" title="marked to intrinsic — no live price">*</span>}
              </td>
              <td className={`py-2 text-right ${pnlColor(l.pnl_rupees)}`}>{fmtInr(l.pnl_rupees)}</td>
              <td className="text-center whitespace-nowrap">
                <button onClick={() => backtestBookPos(l.id)} title="Backtest this position (uses the entry time / expiry set in the Simulate box below)" className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 font-bold hover:bg-indigo-100 mr-1">BT</button>
                <button onClick={() => removePos(l.id)}><Trash2 className="w-3.5 h-3.5 text-slate-300 hover:text-rose-500" /></button>
              </td>
            </tr>
          ))}
          {portfolio?.valuation?.lines?.length > 0 && (
            <tr className="border-t-2 border-slate-200">
              <td className="py-2 font-bold">Combined</td>
              <td className="py-2 text-xs text-slate-400" colSpan={4}>net δ {portfolio.valuation.net_delta_rupees_per_point}/pt · spot {Math.round(portfolio.valuation.spot)}</td>
              <td className={`py-2 text-right font-bold ${pnlColor(portfolio.valuation.total_pnl_rupees)}`}>{fmtInr(portfolio.valuation.total_pnl_rupees)}</td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
      {portfolio?.valuation?.any_unmarked && (
        <div className="text-[11px] text-amber-600 mt-1">* marked to intrinsic (no live/last price for that symbol yet).</div>
      )}
      {(!portfolio?.valuation?.lines || portfolio.valuation.lines.length === 0) && (
        <div className="text-xs text-slate-400 py-3 text-center">No positions yet. Add strategies from the Strategy Desk tab, or a future/stock above.</div>
      )}

      {/* ---- Backtest controls ---- */}
      <div className="border-t border-slate-100 pt-4 mt-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-semibold text-slate-700">Backtest &amp; Simulate</span>
          {/* ONE shared Expiry — governs the Auto stream / My book backtests AND the
              Simulate-a-position flow below. FUTURES expire MONTHLY, so when the
              chosen structure is a future we list the futures contracts (30 Jul /
              27 Aug) instead of the weekly+monthly OPTION expiries. */}
          {(() => {
            const isFut = simFamily.includes('future');
            return (
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-slate-400 font-medium">{isFut ? 'Futures expiry' : 'Expiry'}</span>
                {isFut ? (
                  <select value={btExpiry} onChange={(e) => { const c = futuresContracts.find((f: any) => f.expiry === e.target.value); setBtExpiry(e.target.value); setAddFutSymbol(c?.symbol || 'NIFTY'); setSimChain(null); }} className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 font-semibold" title="Futures contract (monthly) to walk this position to">
                    <option value="">contract…</option>
                    {futuresContracts.map((f: any) => <option key={f.expiry + f.symbol} value={f.expiry}>{f.symbol !== 'NIFTY' ? `${f.symbol} · ` : ''}exp {f.expiry}</option>)}
                  </select>
                ) : (
                  <select value={btExpiry} onChange={(e) => { setBtExpiry(e.target.value); setSimChain(null); }} className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 font-semibold" title="Expiry cycle for all backtest & simulate runs below">
                    <option value="">Auto (latest completed)</option>
                    {expiries.map((e) => <option key={e.expiry} value={e.expiry}>{e.expiry.slice(0, 10)}{e.n_captures ? ` (${e.n_captures})` : ''}</option>)}
                  </select>
                )}
              </div>
            );
          })()}
        </div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[11px] text-slate-400">
            {btMode === 'auto'
              ? 'Walk-forward the framework’s own suggestions (enter → hold/manage → close).'
              : 'Mark the positions in your portfolio above forward through history as one book.'}
          </div>
          <div className="flex rounded-lg overflow-hidden border border-slate-200">
            <button onClick={() => setBtMode('auto')} className={`text-xs px-3 py-1.5 ${btMode === 'auto' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Auto stream</button>
            <button onClick={() => setBtMode('book')} className={`text-xs px-3 py-1.5 ${btMode === 'book' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>My book</button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-2.5 mb-3">
          <Field label="Session days before expiry">
            <select value={windowDays} onChange={(e) => setWindowDays(e.target.value)} className={sel}>
              <option value="all">All</option>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          {btMode === 'auto' && (
            <>
              <Field label="Entry frequency">
                <select value={freq} onChange={(e) => setFreq(e.target.value)} className={sel}>
                  <option value="auto">Auto</option>
                  <option value="15">Every 15 min</option>
                  <option value="30">Every 30 min</option>
                  <option value="60">Every 60 min</option>
                </select>
              </Field>
              <Field label="Exit mode">
                <select value={exitMode} onChange={(e) => setExitMode(e.target.value)} className={sel}>
                  <option value="horizon">Horizon (fast)</option>
                  <option value="expiry">Hold to expiry</option>
                  <option value="manage">Manage (roll/adjust)</option>
                </select>
              </Field>
              <Field label="Cost-edge gate (1σ ÷ round-trip cost)">
                <select value={edgeMult} onChange={(e) => setEdgeMult(e.target.value)} className={sel}>
                  <option value="0">Off</option>
                  <option value="1">1× cost</option>
                  <option value="1.5">1.5× cost</option>
                  <option value="2">2× cost (paper threshold)</option>
                  <option value="3">3× cost</option>
                </select>
              </Field>
              <Field label="MPS0 max-profit benchmark">
                <select value={mpsBench} onChange={(e) => setMpsBench(e.target.value)} className={sel}>
                  <option value="off">Off</option>
                  <option value="gross">Gross (zero-cost ceiling)</option>
                  <option value="net">Net (desk cost)</option>
                </select>
              </Field>
              {manage && (
                <Field label="On loss">
                  <select value={stopLoss ? 'stop' : 'hold'} onChange={(e) => setStopLoss(e.target.value === 'stop')} className={sel}>
                    <option value="hold">Hold to expiry</option>
                    <option value="stop">Stop-loss (cut early)</option>
                  </select>
                </Field>
              )}
              {manage && stopLoss && (
                <Field label="Stop-loss ₹ (blank = auto)">
                  <input value={stopAmt} onChange={(e) => setStopAmt(e.target.value)} placeholder="e.g. 2000"
                         className={sel} inputMode="numeric" />
                </Field>
              )}
            </>
          )}
        </div>

        {manage && (
          <label className="flex items-center gap-2 text-[11px] text-slate-500 mb-3 cursor-pointer">
            <input type="checkbox" checked={rollDir} onChange={(e) => setRollDir(e.target.checked)} className="rounded" />
            Also roll directional trades (spreads / long options), not just range structures
          </label>
        )}

        {manage && (
          <div className="mb-3 rounded-lg bg-slate-50 border border-slate-100 p-2.5">
            <div className="text-[11px] font-semibold text-slate-600 mb-1.5">Adjustment discipline <span className="font-normal text-slate-400">— how hard the engine defends before it stops chasing (blank = default)</span></div>
            <div className="grid grid-cols-3 gap-2.5">
              <Field label="Cooldown (min)" hint="Wait this long after an adjustment before adjusting again (a real breach overrides it). Higher = fewer, calmer adjustments. Default 15.">
                <input value={cooldownMin} onChange={(e) => setCooldownMin(e.target.value)} placeholder="15" className={sel} inputMode="numeric" />
              </Field>
              <Field label="Max adjustments" hint="After this many rolls/defends on one position, exit instead of adjusting again. Lower = cut losers sooner. Default 2.">
                <input value={maxRolls} onChange={(e) => setMaxRolls(e.target.value)} placeholder="2" className={sel} inputMode="numeric" />
              </Field>
              <Field label="Confirmation" hint="How many snapshots a strike must stay threatened before acting — filters one-tick wiggles. Higher = slower to react. Default 1.">
                <input value={persistNear} onChange={(e) => setPersistNear(e.target.value)} placeholder="1" className={sel} inputMode="numeric" />
              </Field>
              <Field label="Monitoring checks" hint="How many times to re-evaluate the position, spread evenly across the whole trade. Higher = finer monitoring (catches breaches / profit sooner) but slower; lower = coarser but faster. Raise it for multi-day expiries so every session is covered. Default 400.">
                <input value={maxManage} onChange={(e) => setMaxManage(e.target.value)} placeholder="400" className={sel} inputMode="numeric" />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-[11px] text-slate-600 mt-2.5 cursor-pointer">
              <input type="checkbox" checked={takeProfit} onChange={(e) => setTakeProfit(e.target.checked)} className="rounded" />
              Take profit (book the gain, don't hold a winner to expiry)
              <span className="text-slate-400">— close once the position has captured this much of its max credit.</span>
            </label>
            {takeProfit && (
              <div className="grid grid-cols-3 gap-2.5 mt-1.5">
                <Field label="Take profit at (% of credit)" hint="Book profit when the mark reaches this % of the max credit. Standard condor management is ~50–60%. Default 60.">
                  <input value={tpFrac} onChange={(e) => setTpFrac(e.target.value)} placeholder="60" className={sel} inputMode="numeric" />
                </Field>
              </div>
            )}
            <label className="flex items-center gap-2 text-[11px] text-slate-600 mt-2.5 cursor-pointer">
              <input type="checkbox" checked={harvest} onChange={(e) => setHarvest(e.target.checked)} className="rounded" />
              Harvest premium on the safe wing in a trend
              <span className="text-slate-400">— when trending, roll the over-safe wing toward spot to collect fresh premium (only if it clears cost). Off in a range.</span>
            </label>
            {harvest && (
              <div className="grid grid-cols-3 gap-2.5 mt-1.5">
                <Field label="Min net ₹/lot" hint="Only harvest if the fresh premium minus transaction cost is at least this much. Higher = only take clearly worthwhile trades. Default 100.">
                  <input value={minHarvest} onChange={(e) => setMinHarvest(e.target.value)} placeholder="100" className={sel} inputMode="numeric" />
                </Field>
              </div>
            )}
          </div>
        )}

        <button onClick={runBacktest} disabled={btLoading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-slate-800 text-white font-bold hover:bg-slate-900 disabled:opacity-60">
          <Play className="w-3.5 h-3.5" /> {btLoading ? 'Running…' : 'Run backtest'}
        </button>

        {/* Progress */}
        {(btLoading || btProgress > 0) && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
              <span>Running walk-forward{manage ? ' (manage — re-evaluates every snapshot)' : ''}…</span>
              <span>{Math.round(btProgress)}%</span>
            </div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-indigo-500 rounded-full transition-all duration-300" style={{ width: `${btProgress}%` }} />
            </div>
          </div>
        )}
        {btErr && (
          <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mt-3">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {btErr}
          </div>
        )}
      </div>

      {/* ---- Results: auto ---- */}
      {bt && bt.mode === 'auto' && bt.metrics && (
        <div className="mt-4">
          <div className="grid grid-cols-4 gap-2.5 mb-2">
            <Tile label="Net P&L" value={fmtInr(bt.metrics.total_pnl_rupees)} cls={pnlColor(bt.metrics.total_pnl_rupees || 0)} />
            <Tile label="Hit rate" value={bt.metrics.hit_rate != null ? `${Math.round(bt.metrics.hit_rate * 100)}%` : '—'} />
            <Tile label="Trades" value={bt.metrics.n_trades ?? '—'} />
            <Tile label="Max DD" value={`${bt.metrics.max_drawdown_pts ?? '—'} pts`} cls="text-rose-600" />
          </div>
          {bt.metrics.max_consecutive_loss > 0 && (
            <div className="text-[11px] text-slate-500 mb-2">
              Worst losing streak: <span className="font-mono text-rose-600">{bt.metrics.max_consecutive_loss}</span> trade{bt.metrics.max_consecutive_loss !== 1 ? 's' : ''} in a row
            </div>
          )}
          {/* Cost-edge gate — explain HOW it acted this run */}
          {bt.metrics.cost_edge_mult > 0 && (
            <div className="text-[11px] bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-2">
              <div className="flex items-center gap-2 font-semibold text-amber-700">
                <span className="text-[9px] font-bold bg-amber-200 text-amber-800 px-1.5 py-0.5 rounded">COST-EDGE {bt.metrics.cost_edge_mult}×</span>
                {bt.metrics.cost_gated_count > 0
                  ? <span>gated {bt.metrics.cost_gated_count} would-be entr{bt.metrics.cost_gated_count === 1 ? 'y' : 'ies'}</span>
                  : <span>gated nothing this run</span>}
                {bt.metrics.cost_edge_avg_ratio != null && <span className="text-amber-600 font-normal">· avg 1σ/cost ratio {bt.metrics.cost_edge_avg_ratio}×</span>}
              </div>
              <div className="text-amber-600/80 mt-0.5">{bt.metrics.cost_edge_note}</div>
            </div>
          )}
          {bt.metrics.capture_pct != null && (
            <div className="text-[11px] bg-indigo-50 rounded-lg px-3 py-2 mb-3">
              <div className="flex items-center justify-between">
                <span className="text-slate-600 font-medium">MPS0 capture ({bt.metrics.mps0_basis})</span>
                <span className="font-mono text-indigo-700 font-semibold">{bt.metrics.capture_pct}%</span>
                <span className="text-slate-500">of ceiling <span className="font-mono text-slate-700">{fmtInr(bt.metrics.mps0_max_rupees)}</span></span>
                <span className="text-slate-400" title={bt.metrics.mps0_note}>ⓘ perfect-hindsight, not a target</span>
              </div>
              {bt.metrics.mps_note_plain && <div className="text-slate-500 mt-1">{bt.metrics.mps_note_plain}</div>}
            </div>
          )}
          {bt.metrics.total_cost_inr != null && (
            <div className="flex items-center justify-between text-[11px] bg-slate-50 rounded-lg px-3 py-2 mb-3">
              <span className="text-slate-500">Gross <span className="font-mono text-slate-700">{fmtInr(bt.metrics.gross_pnl_rupees)}</span></span>
              <span className="text-slate-400">−</span>
              <span className="text-slate-500">Txn costs <span className="font-mono text-rose-600">{fmtInr(bt.metrics.total_cost_inr)}</span> <span className="text-slate-400">(~{fmtInr(bt.metrics.avg_cost_per_trade_inr)}/trade)</span></span>
              <span className="text-slate-400">=</span>
              <span className="text-slate-500">Net <span className={`font-mono ${pnlColor(bt.metrics.total_pnl_rupees || 0)}`}>{fmtInr(bt.metrics.total_pnl_rupees)}</span></span>
            </div>
          )}
          {/* P&L attribution — which exit rule / which structure earns or bleeds (descriptive) */}
          {(bt.metrics.by_exit_reason || bt.metrics.by_family) && (
            <div className="grid grid-cols-2 gap-2.5 mb-3">
              {[
                { title: 'By exit reason', data: bt.metrics.by_exit_reason, keyLabel: 'Exit' },
                { title: 'By structure', data: bt.metrics.by_family, keyLabel: 'Family' },
              ].map(({ title, data, keyLabel }) => (
                <div key={title} className="border border-slate-100 rounded-lg overflow-hidden">
                  <div className="text-[11px] font-medium text-slate-600 px-2.5 py-1.5 bg-slate-50">
                    {title} <span className="text-slate-400 font-normal">· worst net first</span>
                  </div>
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-slate-400 text-left">
                        <th className="py-1 px-2 font-normal">{keyLabel}</th>
                        <th className="py-1 px-2 font-normal text-center">n</th>
                        <th className="py-1 px-2 font-normal text-center">Hit</th>
                        <th className="py-1 px-2 font-normal text-right">Net</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data || {}).map(([k, v]: [string, any]) => (
                        <tr key={k} className="border-t border-slate-50">
                          <td className="py-1 px-2 text-slate-600 whitespace-nowrap">{k}</td>
                          <td className="py-1 px-2 text-center text-slate-500">{v.n}</td>
                          <td className="py-1 px-2 text-center text-slate-500">{Math.round((v.hit_rate || 0) * 100)}%</td>
                          <td className={`py-1 px-2 text-right font-mono ${pnlColor(v.net_rupees || 0)}`}>{fmtInr(v.net_rupees)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}
          {autoEquity().length > 0 && (
            <>
              <div className="text-xs text-slate-500 mb-1">Cumulative P&L across {bt.metrics.n_trades} trades</div>
              <ResponsiveContainer width="100%" height={110}>
                <LineChart data={autoEquity()}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                  <XAxis dataKey="i" tick={{ fontSize: 9 }} />
                  <YAxis tick={{ fontSize: 9 }} width={48} tickFormatter={(v) => fmtInr(v)} />
                  <Tooltip formatter={(v: any) => fmtInr(v)} labelFormatter={(l) => `Trade ${l}`} />
                  <ReferenceLine y={0} stroke="#94a3b8" />
                  <Line type="monotone" dataKey="pnl" stroke="#4f46e5" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
              <div className="text-xs text-slate-500 mt-3 mb-1">Trade log <span className="text-slate-400">(click a row for orders + adjustments)</span></div>
              <div className="max-h-64 overflow-auto border border-slate-100 rounded-lg">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr className="text-slate-400 text-left">
                      <th className="py-1.5 px-2 font-normal">Entry (IST)</th>
                      <th className="py-1.5 px-2 font-normal">Structure</th>
                      <th className="py-1.5 px-2 font-normal text-center">Dir</th>
                      <th className="py-1.5 px-2 font-normal text-center">Adj</th>
                      <th className="py-1.5 px-2 font-normal text-right">Cost</th>
                      <th className="py-1.5 px-2 font-normal text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bt.trades.map((t: any, i: number) => (
                      <React.Fragment key={i}>
                        <tr className="border-t border-slate-50 cursor-pointer hover:bg-slate-50" onClick={() => setOpenTrade(openTrade === i ? null : i)}>
                          <td className="py-1.5 px-2 text-slate-500 whitespace-nowrap">{fmtIST(t.entry_ts)}</td>
                          <td className="py-1.5 px-2 text-slate-600">
                            {t.final_family !== t.entry_family
                              ? <span>{t.entry_family} <span className="text-indigo-500">→ {t.final_family}</span></span>
                              : t.entry_family}
                          </td>
                          <td className="py-1.5 px-2 text-center">{t.direction > 0 ? '▲' : t.direction < 0 ? '▼' : '—'}</td>
                          <td className="py-1.5 px-2 text-center">{t.n_adjustments > 0 ? <span className="text-indigo-600 font-semibold">{t.n_adjustments}</span> : <span className="text-slate-300">0</span>}</td>
                          <td className="py-1.5 px-2 text-right font-mono text-rose-500">{fmtInr(t.cost_inr)}</td>
                          <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(t.pnl_rupees)}`}>{fmtInr(t.pnl_rupees)}</td>
                        </tr>
                        {openTrade === i && (
                          <tr className="bg-slate-50">
                            <td colSpan={6} className="px-3 py-2">
                              <div className="text-slate-400 mb-1">Entry orders</div>
                              <div className="flex flex-wrap gap-1 mb-2">
                                {(t.entry_legs || []).map((l: string, k: number) => (
                                  <span key={k} className={`px-1.5 py-0.5 rounded ${l.startsWith('Buy') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{l}</span>
                                ))}
                              </div>
                              {t.adjustments?.length > 0 ? (
                                <>
                                  <div className="text-slate-400 mb-1">Adjustments while held</div>
                                  {t.adjustments.map((a: any, k: number) => (
                                    <div key={k} className="mb-1.5 pl-2 border-l-2 border-indigo-200">
                                      <div className="text-indigo-600 font-semibold">{a.action}</div>
                                      <div className="text-slate-400 mb-0.5">{a.rationale}</div>
                                      <SignalWhy sig={a.signal} />
                                      <div className="flex flex-wrap gap-1">
                                        {(a.orders || []).map((o: string, m: number) => (
                                          <span key={m} className={`px-1.5 py-0.5 rounded ${o.includes('Buy') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{o}</span>
                                        ))}
                                      </div>
                                    </div>
                                  ))}
                                </>
                              ) : (
                                <div className="text-slate-400">No adjustments — held to exit. {!manage && 'Use manage mode for wing rolls/conversions.'}</div>
                              )}
                              <div className="text-slate-400 mt-2">
                                Held {fmtIST(t.entry_ts)} → {fmtIST(t.exit_ts)} IST · spot {Math.round(t.entry_spot)} → {Math.round(t.exit_spot)} · cost {fmtInr(t.cost_inr)} · net {fmtInr(t.pnl_rupees)}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {/* ---- Results: book ---- */}
      {bt && bt.mode === 'book' && bt.series?.length > 0 && (
        <div className="mt-4">
          <ResponsiveContainer width="100%" height={110}>
            <LineChart data={bt.series.map((s: any) => ({ ts: fmtIST(s.ts), pnl: s.pnl_rupees }))}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
              <XAxis dataKey="ts" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 9 }} width={44} />
              <Tooltip formatter={(v: any) => fmtInr(v)} />
              <ReferenceLine y={0} stroke="#94a3b8" />
              <Line type="monotone" dataKey="pnl" stroke="#2563eb" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
          <div className="grid grid-cols-4 gap-2.5 mt-2">
            <Tile label="Final P&L" value={fmtInr(bt.metrics.final_pnl)} cls={pnlColor(bt.metrics.final_pnl || 0)} />
            <Tile label="Best" value={fmtInr(bt.metrics.best_pnl)} cls="text-emerald-600" />
            <Tile label="Worst" value={fmtInr(bt.metrics.worst_pnl)} cls="text-rose-600" />
            <Tile label="Max DD" value={fmtInr(bt.metrics.max_drawdown_rupees)} cls="text-rose-600" />
          </div>
        </div>
      )}

      {bt && (
        <>
          {bt.metrics?.captures_total && (
            <div className="text-[11px] text-slate-400 mt-2">
              {bt.expiry && <>Expiry {bt.expiry.slice(0, 10)} · </>}
              {bt.metrics.session_dates?.length > 0 && (
                <>{bt.metrics.sessions_in_window} session{bt.metrics.sessions_in_window !== 1 ? 's' : ''} ({bt.metrics.session_dates.join(', ')}) · </>
              )}
              {bt.metrics.captures_total} snapshots{bt.metrics.freq_minutes_effective ? ` · entries ≈ every ${bt.metrics.freq_minutes_effective} min` : ''} in {bt.metrics.elapsed_sec}s.
            </div>
          )}
          <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mt-1">
            <AlertTriangle className="w-3 h-3" /> {bt.metrics?.note || 'Descriptive only — thin history (D-MA-04).'}
          </div>
        </>
      )}

      {/* ---- Simulate one position from a chosen entry ---- */}
      <div className="border-t border-slate-100 pt-4 mt-4">
        <div className="text-sm font-semibold text-slate-700 mb-1">Simulate a position</div>
        <div className="text-[11px] text-slate-400 mb-3">Open one structure at a past date/time and walk it forward to the <span className="font-medium text-slate-500">Expiry set above</span> (uses the Exit / roll / stop-loss settings above too).</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2.5 mb-3">
          <Field label="Entry date & time (IST)">
            <input type="datetime-local" value={simEntry} onChange={(e) => { setSimEntry(e.target.value); setSimChain(null); }} className={sel} />
          </Field>
          <Field label="Structure">
            <select value={simFamily} onChange={(e) => { setSimFamily(e.target.value); setSimChain(null); setSimLegs([]); }} className={sel}>
              <option value="">Use suggestion (free build)</option>
              {['iron_condor', 'iron_butterfly', 'bull_call_spread', 'bear_put_spread', 'bull_put_spread', 'bear_call_spread', 'long_call', 'long_put', 'long_straddle', 'long_strangle', 'long_future', 'short_future'].map((f) => <option key={f} value={f}>{f.replace(/_/g, ' ')}</option>)}
            </select>
          </Field>
        </div>

        {/* Unified dropdown-based leg editor */}
        {simChain && (
          <div className="mb-3 border border-slate-100 rounded-lg p-3">
            <div className="flex items-center gap-1.5 text-xs text-slate-600 mb-2">
              <span>NIFTY at <span className="font-semibold">{fmtIST(simChain.ts)} IST</span> = <span className="font-semibold">{Math.round(simChain.spot)}</span>{simChain.vix ? <span className="text-slate-400"> · VIX {simChain.vix}</span> : null}</span>
              <button onClick={loadChain} className="p-0.5 rounded hover:bg-slate-100" title="Reload chain at this time"><RefreshCw className="w-3 h-3 text-slate-400" /></button>
            </div>
            {simLegs.length > 0 && (
              <div className="text-[11px] text-slate-500 mb-2">
                {simFamily ? <span className="font-semibold text-slate-600">{simFamily.replace(/_/g, ' ')}: </span> : null}
                {simLegs.map((l, i) => (
                  <span key={i}>
                    {i > 0 ? ' · ' : ''}
                    <span className={l.sign > 0 ? 'text-emerald-600 font-semibold' : 'text-rose-600 font-semibold'}>{l.sign > 0 ? 'BUY' : 'SELL'}</span>
                    {' '}{Math.round(l.strike)} {l.side === 'call' ? 'CE' : 'PE'} <span className="text-slate-400 font-mono">@₹{l.price ?? '—'}</span>
                  </span>
                ))}
              </div>
            )}
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 text-left">
                  <th className="py-1 font-normal w-24">ACTION</th>
                  <th className="py-1 font-normal w-20">TYPE</th>
                  <th className="py-1 font-normal">STRIKE</th>
                  <th className="py-1 font-normal text-right">PREMIUM</th>
                  <th className="py-1 font-normal text-right">LOT</th>
                  <th className="w-6" />
                </tr>
              </thead>
              <tbody>
                {simLegs.map((l, i) => (
                  <tr key={i} className="border-t border-slate-50">
                    <td className="py-1.5">
                      <select value={l.sign > 0 ? 'buy' : 'sell'} onChange={(e) => updateLeg(i, { sign: e.target.value === 'buy' ? 1 : -1 })}
                              className={`text-xs border rounded px-1.5 py-1 font-semibold ${l.sign > 0 ? 'text-emerald-600 border-emerald-200' : 'text-rose-600 border-rose-200'}`}>
                        <option value="buy">BUY</option>
                        <option value="sell">SELL</option>
                      </select>
                    </td>
                    <td className="py-1.5">
                      <select value={l.side} onChange={(e) => updateLeg(i, { side: e.target.value })} className="text-xs border border-slate-200 rounded px-1.5 py-1">
                        <option value="call">CE</option>
                        <option value="put">PE</option>
                      </select>
                    </td>
                    <td className="py-1.5">
                      <select value={l.strike} onChange={(e) => updateLeg(i, { strike: Number(e.target.value) })} className="text-xs border border-slate-200 rounded px-1.5 py-1">
                        {(simChain.rows.some((r: any) => Number(r.strike) === Number(l.strike))
                          ? simChain.rows
                          : [{ strike: l.strike }, ...simChain.rows]
                        ).map((r: any) => <option key={r.strike} value={r.strike}>{Math.round(r.strike)}</option>)}
                      </select>
                    </td>
                    <td className="py-1.5 text-right font-mono">₹{l.price ?? '—'}</td>
                    <td className="py-1.5 text-right text-slate-400">1x</td>
                    <td className="text-center"><button onClick={() => removeLeg(i)}><Trash2 className="w-3.5 h-3.5 text-slate-300 hover:text-rose-500" /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between mt-2">
              <button onClick={addBlankLeg} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800"><Plus className="w-3 h-3" /> Add leg</button>
              {simLegs.length > 0 && (
                <span className="text-[11px] text-slate-500">
                  Net {netPremium < 0 ? 'credit' : 'debit'} <span className={`font-mono ${netPremium < 0 ? 'text-emerald-600' : 'text-rose-600'}`}>₹{Math.abs(Math.round(netPremium * DEFAULT_LOT))}</span>
                </span>
              )}
            </div>
          </div>
        )}

        {/* Proactive advisory — clean, collapsible, all user-defined */}
        <div className="mb-2 border border-violet-100 rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 bg-violet-50/60">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={proactive} onChange={(e) => setProactive(e.target.checked)} className="rounded accent-violet-600" />
              <span className="text-xs font-semibold text-violet-700">⚖ Proactive advisory</span>
              <span className="text-[10px] text-violet-400">forecast-driven action check (log-only)</span>
            </label>
            {proactive && (
              <button onClick={() => setProOpen((o) => !o)} className="text-[10px] text-violet-500 underline">
                {proOpen ? 'hide settings' : 'settings'}
              </button>
            )}
          </div>
          {proactive && proOpen && (
            <div className="px-3 py-2.5 space-y-3 border-t border-violet-100">
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Tail-aversion λ</span>
                  <span className="font-mono text-violet-700">{proLambda.toFixed(2)}</span>
                </div>
                <input type="range" min={0} max={1.5} step={0.05} value={proLambda}
                  onChange={(e) => setProLambda(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="flex justify-between text-[9px] text-slate-400">
                  <span>0 = max return</span><span>0.5 = balanced</span><span>1.5 = min tail</span>
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Touch window</span>
                  <span className="font-mono text-violet-700">{proHorizon}% of time-to-expiry</span>
                </div>
                <input type="range" min={10} max={100} step={5} value={proHorizon}
                  onChange={(e) => setProHorizon(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="text-[9px] text-slate-400">how far ahead to measure "will spot reach my strike?" (shorter = defend only imminent threats)</div>
              </div>
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Min edge (churn guard)</span>
                  <span className="font-mono text-violet-700">{proMinEdge} pts</span>
                </div>
                <input type="range" min={0} max={30} step={1} value={proMinEdge}
                  onChange={(e) => setProMinEdge(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="text-[9px] text-slate-400">an action must beat HOLD by this many points to be recommended (higher = fewer, higher-conviction adjustments)</div>
              </div>
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Risk uses trend</span>
                  <span className="font-mono text-violet-700">{proRiskDrift}%</span>
                </div>
                <input type="range" min={0} max={100} step={10} value={proRiskDrift}
                  onChange={(e) => setProRiskDrift(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="text-[9px] text-slate-400">how much the tail (CVaR) trusts the forecast direction. 100% = trend-centred; <span className="font-semibold">0% = symmetric</span> (sizes risk as if the trend could reverse — values the far wing as insurance, exposes over-harvesting)</div>
              </div>
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Harvest budget</span>
                  <span className="font-mono text-violet-700">{proMaxHarvests > 0 ? `${proMaxHarvests}/day` : 'off'}</span>
                </div>
                <input type="range" min={0} max={6} step={1} value={proMaxHarvests}
                  onChange={(e) => setProMaxHarvests(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="text-[9px] text-slate-400">max harvests/day (0 = no cap). Plus a running <span className="font-semibold">harvest-debt penalty</span> so the 4th harvest scores far worse than the 1st — the path-dependent guard a one-step score misses.</div>
              </div>
              <div>
                <div className="flex items-center justify-between text-[11px] mb-1">
                  <span className="text-slate-600 font-medium">Min spread width</span>
                  <span className="font-mono text-violet-700">{proMinWidth} pts</span>
                </div>
                <input type="range" min={50} max={600} step={50} value={proMinWidth}
                  onChange={(e) => setProMinWidth(Number(e.target.value))} className="w-full accent-violet-600" />
                <div className="text-[9px] text-slate-400">for <span className="font-semibold">bull/bear call &amp; put spreads</span>: the optimizer won't NARROW the long↔short gap below this — below the floor it prefers CLOSE or a full ROLL. Also floors any CONVERT to a different vertical.</div>
              </div>
              <div className="text-[10px] text-slate-500 bg-slate-50 rounded px-2 py-1.5">
                Objective: <span className="font-mono">score = E[P&amp;L] − λ·|CVaR10|</span>. Advisory runs alongside the rules and is logged per row — it does <span className="font-semibold">not</span> change what executes yet.
              </div>
            </div>
          )}
        </div>

        {/* A/B/C/D harvest experiment */}
        <div className="mb-2 border border-violet-100 rounded-lg p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-violet-700">Harvest experiment (A/B/C/D)</span>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-slate-400 flex items-center gap-1">
                timeout
                <input type="number" min={1} max={60} value={hcTimeout} disabled={hcLoading}
                  onChange={(e) => setHcTimeout(Number(e.target.value))}
                  className="w-11 text-[11px] border border-slate-200 rounded px-1 py-0.5 text-right" />
                min
              </label>
              <button onClick={runHarvestCompare} disabled={hcLoading}
                className="text-[11px] px-2.5 py-1 rounded-lg bg-violet-600 text-white font-bold hover:bg-violet-700 disabled:opacity-60 whitespace-nowrap">
                {hcLoading ? `Running… ${Math.floor(hcElapsed / 60)}:${String(hcElapsed % 60).padStart(2, '0')}` : 'Run comparison'}
              </button>
            </div>
          </div>
          <div className="text-[10px] text-slate-400 mt-1">Runs the same window 4 ways (always in <span className="font-semibold">Manage</span> mode — harvesting only happens when managing): A always-harvest · B never · C optimizer-gated · D optimizer+budget. This is an <span className="font-semibold">aggregate scoreboard</span> — the <span className="font-semibold">Harvests</span> &amp; <span className="font-semibold">Vetoes</span> columns are the decision summary. To see <span className="font-semibold">how</span> each bar was decided, use <span className="font-semibold">Simulate</span> below in Manage mode with ⚖ Proactive advisory ON. Shares the prediction timeline (≈1 backtest of work); for a quick first look set the window to 30 days.</div>
          {hcErr && <div className="text-[11px] text-rose-600 mt-1">{hcErr}</div>}
          {hc?.rows && (
            <table className="w-full text-[11px] mt-2">
              <thead>
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="text-left font-normal py-1">Strategy</th>
                  <th className="text-right font-normal">Net P&amp;L</th>
                  <th className="text-right font-normal">Max DD</th>
                  <th className="text-right font-normal">MPS0%</th>
                  <th className="text-right font-normal">Harv</th>
                  <th className="text-right font-normal">Veto</th>
                </tr>
              </thead>
              <tbody>
                {(() => { const best = Math.max(...hc.rows.map((r: any) => r.net_pnl_rupees ?? -1e9)); return hc.rows.map((r: any) => (
                  <tr key={r.strategy} className={`border-b border-slate-50 ${r.net_pnl_rupees === best ? 'bg-emerald-50' : ''}`}>
                    <td className="py-1 font-semibold text-slate-700">{r.strategy.replace(/_/g, ' ')}</td>
                    <td className={`text-right font-mono ${pnlColor(r.net_pnl_rupees || 0)}`}>{fmtInr(r.net_pnl_rupees)}</td>
                    <td className="text-right font-mono text-rose-600">{r.max_drawdown_pts ?? '—'}</td>
                    <td className="text-right font-mono text-slate-500">{r.mps0_capture_pct != null ? `${r.mps0_capture_pct}%` : '—'}</td>
                    <td className="text-right font-mono text-slate-500">{r.n_harvests ?? '—'}</td>
                    <td className="text-right font-mono text-amber-600">{r.n_harvest_vetoes ?? 0}</td>
                  </tr>
                )); })()}
              </tbody>
            </table>
          )}
        </div>

        {/* Harvest strategy selector — Simulate runs the one you pick, with full trace */}
        <div className="mb-2">
          <div className="text-[11px] font-semibold text-slate-600 mb-1">Harvest strategy for this replay</div>
          <div className="grid grid-cols-4 gap-1">
            {(['A', 'B', 'C', 'D'] as const).map((k) => (
              <button key={k} onClick={() => setHarvestStrat(k)}
                className={`px-2 py-1.5 rounded-lg text-[11px] font-bold border transition ${harvestStrat === k ? 'bg-violet-600 text-white border-violet-600' : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'}`}>
                {k}
              </button>
            ))}
          </div>
          <div className="text-[10px] text-slate-400 mt-1">{harvestStrat}: {HARVEST_STRAT[harvestStrat].label} — Simulate below runs this one; each bar's trace shows the harvest/veto decision. (Needs exit mode = Manage.)</div>
          <button onClick={runSimCompare} disabled={simCmpLoading}
            className="mt-2 w-full text-[11px] px-2 py-1.5 rounded-lg border border-violet-300 text-violet-700 font-bold hover:bg-violet-50 disabled:opacity-60">
            {simCmpLoading ? 'Running A/B/C/D on this entry…' : 'Compare all 4 on this entry →'}
          </button>
          {simCmp?.rows && (
            <div className="mt-2">
              <div className="flex justify-end mb-1">
                <button onClick={downloadCmpCsv} className="text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600 font-semibold hover:bg-slate-50">⬇ Download CSV</button>
              </div>
              {/* bottom-line per strategy */}
              <div className="grid grid-cols-4 gap-1 mb-2">
                {(() => { const best = Math.max(...simCmp.rows.map((r: any) => r.total_pnl_inr ?? -1e12)); return simCmp.rows.map((r: any) => (
                  <div key={r.strategy} className={`rounded-lg border p-1.5 text-center ${r.total_pnl_inr === best ? 'bg-emerald-50 border-emerald-200' : 'border-slate-200'}`}>
                    <div className="text-[9px] font-bold text-slate-500">{r.strategy.split('_')[0]}</div>
                    <div className={`text-[11px] font-mono font-bold ${pnlColor(r.total_pnl_inr || 0)}`}>{r.error ? '—' : fmtInr(r.total_pnl_inr)}</div>
                    <div className="text-[8px] text-slate-400">DD {r.max_drawdown_pct ?? '—'}% · H{r.n_harvests ?? 0}/V{r.n_vetoes ?? 0}</div>
                  </div>
                )); })()}
              </div>
              {/* time-aligned action grid — what each strategy did at each moment */}
              <div className="max-h-80 overflow-auto border border-slate-100 rounded-lg">
                <table className="w-full text-[10px]">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr className="text-slate-400">
                      <th className="text-left font-normal px-1.5 py-1">time</th>
                      <th className="text-right font-normal">spot</th>
                      {simCmp.order.map((n: string) => <th key={n} className="text-left font-normal px-2">{n.split('_')[0]}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {simCmp.timeline.filter((row: any) => !hideHolds || row.diverge).map((row: any, k: number) => {
                      const open = cmpOpenTs === row.ts;
                      return (
                      <React.Fragment key={k}>
                      <tr className={`border-t border-slate-50 cursor-pointer hover:bg-slate-50 ${row.diverge ? 'bg-amber-50' : ''}`} onClick={() => setCmpOpenTs(open ? null : row.ts)}>
                        <td className="px-1.5 py-0.5 text-slate-500 whitespace-nowrap font-mono">{open ? '▾' : '▸'} {fmtIST(row.ts)}</td>
                        <td className="text-right text-slate-500 font-mono">{row.spot}</td>
                        {simCmp.order.map((n: string) => {
                          const c = row[n];
                          const act = c?.action || '—';
                          const color = act === 'HARVEST_WING' ? 'text-indigo-700' : act === 'HARVEST_VETO' ? 'text-amber-600' : act.startsWith('DEFEND') || act === 'CLOSE' || act === 'STOP_LOSS' ? 'text-rose-600' : 'text-slate-400';
                          return (
                            <td key={n} className="px-2 whitespace-nowrap align-top" title={c?.roll || ''}>
                              <div className={`${color} ${act !== 'HOLD' && act !== '—' ? 'font-semibold' : ''}`}>{act === 'HOLD' ? '·' : act.replace('HARVEST_WING', 'HARVEST').replace('HARVEST_VETO', 'VETO').replace('_', ' ')}</div>
                              {c && <div className={`font-mono ${pnlColor(c.mark || 0)}`} style={{ fontSize: '8px' }}>{fmtInr(c.mark)}</div>}
                            </td>
                          );
                        })}
                      </tr>
                      {open && (
                        <tr className="bg-slate-50/70">
                          <td colSpan={2 + simCmp.order.length} className="px-3 py-2">
                            <div className="space-y-1.5">
                              {simCmp.order.map((n: string) => { const c = row[n]; if (!c) return null; return (
                                <div key={n} className="text-[10px]">
                                  <span className="font-bold text-slate-600">{n.split('_')[0]}</span>
                                  <span className={`ml-1 font-semibold ${c.action === 'HARVEST_WING' ? 'text-indigo-700' : c.action === 'HARVEST_VETO' ? 'text-amber-600' : c.action !== 'HOLD' ? 'text-rose-600' : 'text-slate-500'}`}>{c.action}</span>
                                  <span className={`ml-1 font-mono ${pnlColor(c.mark || 0)}`}>{fmtInr(c.mark)}</span>
                                  <span className="text-slate-500 ml-1">{c.reason}</span>
                                  {c.orders?.length > 0 && <span className="text-indigo-600 ml-1">[{c.orders.join(' · ')}]</span>}
                                </div>
                              ); })}
                            </div>
                          </td>
                        </tr>
                      )}
                      </React.Fragment>
                    ); })}
                  </tbody>
                </table>
              </div>
              <div className="text-[9px] text-slate-400 mt-1">Each row = one moment; each column = a strategy's action then (· = HOLD). <span className="text-amber-600 font-semibold">Amber rows</span> = strategies diverged. <span className="font-semibold">Click any row</span> to expand the full per-strategy rationale (mark, reason, signals, order tickets). Hover a cell for the roll / mark.</div>
            </div>
          )}
        </div>

        <button onClick={runSim} disabled={simLoading} className="flex items-center justify-center gap-1.5 w-full text-sm px-3 py-2 rounded-lg bg-indigo-600 text-white font-bold hover:bg-indigo-700 disabled:opacity-60 mb-2">
          <Play className="w-3.5 h-3.5" /> {simLoading ? 'Simulating…' : `Simulate strategy ${harvestStrat}${simLegs.length ? ` · your ${simLegs.length}-leg structure` : ''}`}
        </button>
        {(simLoading || simProgress > 0) && (
          <div className="mb-2">
            <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
              <span>Walking the position forward to expiry{rollDir ? ' (rolling directional too)' : ''}…</span>
              <span>{Math.round(simProgress)}%</span>
            </div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className="h-full bg-indigo-500 rounded-full transition-all duration-300" style={{ width: `${simProgress}%` }} />
            </div>
          </div>
        )}
        {simErr && (
          <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mb-2">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {simErr}
          </div>
        )}
        {sim && (
          <div>
            <div className="grid grid-cols-3 gap-2.5 mb-2">
              <Tile label="Net P&L" value={fmtInr(sim.pnl_rupees)} cls={pnlColor(sim.pnl_rupees || 0)} />
              <Tile label="Adjustments" value={sim.n_adjustments} />
              <Tile label="Cost" value={fmtInr(sim.cost_inr)} cls="text-rose-600" />
            </div>
            <div className="text-[11px] text-slate-500 mb-1">
              {sim.entry_family}{sim.final_family !== sim.entry_family && <span className="text-indigo-500"> → {sim.final_family}</span>} · held {fmtIST(sim.entry_ts)} → {fmtIST(sim.exit_ts)} IST · spot {Math.round(sim.entry_spot)} → {Math.round(sim.exit_spot)}
            </div>
            {sim.stats && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] bg-slate-50 border border-slate-100 rounded-lg px-3 py-2 mb-2">
                <span>Total return <span className={`font-mono font-semibold ${(sim.stats.total_return_pct || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{sim.stats.total_return_pct != null ? `${sim.stats.total_return_pct}%` : '—'}</span></span>
                <span>Max DD <span className="font-mono font-semibold text-rose-600">{sim.stats.max_drawdown_pct != null ? `${sim.stats.max_drawdown_pct}%` : '—'}</span> <span className="text-slate-400">({fmtInr(sim.stats.max_drawdown_inr)})</span></span>
                <span>Peak/Trough <span className="font-mono text-emerald-600">{fmtInr(sim.stats.peak_pnl_inr)}</span> / <span className="font-mono text-rose-600">{fmtInr(sim.stats.trough_pnl_inr)}</span></span>
                <span>Adjusts <span className="font-mono text-slate-600">{sim.stats.n_adjustments}</span></span>
                <span>Harvests <span className="font-mono text-slate-600">{sim.stats.n_harvests}</span></span>
                {sim.stats.n_vetoes > 0 && <span>Vetoes <span className="font-mono text-amber-600">{sim.stats.n_vetoes}</span></span>}
                <span className="text-slate-400">margin ≈ {fmtInr(sim.stats.capital_base_inr)}</span>
              </div>
            )}
            {sim.stats?.net_vega_inr_per_volpt != null && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] bg-violet-50 border border-violet-100 rounded-lg px-3 py-2 mb-2" title="Vega at entry, solved from LTPs. A short condor is short vega — an overnight IV spike hurts even if spot barely moves (the 06-30 lesson).">
                <span className="font-semibold text-violet-700">Overnight vega risk:</span>
                <span>net vega <span className="font-mono">{fmtInr(sim.stats.net_vega_inr_per_volpt)}</span>/vol-pt</span>
                <span>+3 vol-pt IV ≈ <span className={`font-mono ${(sim.stats.vega_3pt_inr || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{fmtInr(sim.stats.vega_3pt_inr)}</span></span>
                <span>+5 vol-pt ≈ <span className={`font-mono ${(sim.stats.vega_5pt_inr || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{fmtInr(sim.stats.vega_5pt_inr)}</span></span>
              </div>
            )}
            <div className="flex flex-wrap gap-1 mb-2 text-[11px]">
              {(sim.entry_legs || []).map((l: string, k: number) => (
                <span key={k} className={`px-1.5 py-0.5 rounded ${l.startsWith('Buy') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{l}</span>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={120}>
              <LineChart data={sim.series.map((s: any) => ({ ts: fmtIST(s.ts), pnl: s.pnl_rupees }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                <XAxis dataKey="ts" tick={{ fontSize: 9 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 9 }} width={48} tickFormatter={(v) => fmtInr(v)} />
                <Tooltip formatter={(v: any) => fmtInr(v)} />
                <ReferenceLine y={0} stroke="#94a3b8" />
                <Line type="monotone" dataKey="pnl" stroke="#4f46e5" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
            {sim.adjustments?.length > 0 && (
              <div className="mt-2 text-[11px]">
                <div className="text-slate-400 mb-1">Adjustments while held</div>
                {sim.adjustments.map((a: any, k: number) => (
                  <div key={k} className="mb-1.5 pl-2 border-l-2 border-indigo-200">
                    <div className="text-indigo-600 font-semibold">{a.action} <span className="text-slate-400 font-normal">@ {fmtIST(a.at)}</span></div>
                    <div className="text-slate-400 mb-0.5">{a.rationale}</div>
                    <SignalWhy sig={a.signal} />
                    <div className="flex flex-wrap gap-1">
                      {(a.orders || []).map((o: string, m: number) => (
                        <span key={m} className={`px-1.5 py-0.5 rounded ${o.includes('Buy') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{o}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Full decision trace — every step's reasoning */}
            {sim.decisions?.length > 0 && (
              <div className="mt-3 text-[11px]">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-slate-500 font-semibold">Decision trace ({sim.decisions.length} checks)</span>
                  <div className="flex items-center gap-2">
                    <div className="flex rounded-lg border border-slate-200 overflow-hidden">
                      {(['cards', 'table'] as const).map((v) => (
                        <button key={v} onClick={() => setTraceView(v)}
                          className={`px-2 py-0.5 text-[10px] font-semibold ${traceView === v ? 'bg-slate-700 text-white' : 'text-slate-500'}`}>{v}</button>
                      ))}
                    </div>
                    <button onClick={downloadSimCsv} className="text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600 font-semibold hover:bg-slate-50">⬇ CSV</button>
                    <label className="flex items-center gap-1 text-slate-400 cursor-pointer">
                      <input type="checkbox" checked={hideHolds} onChange={(e) => setHideHolds(e.target.checked)} className="rounded" /> hide holds
                    </label>
                  </div>
                </div>

                {traceView === 'table' && (
                  <div className="max-h-72 overflow-auto border border-slate-100 rounded-lg">
                    <table className="w-full text-[10px] font-mono">
                      <thead className="sticky top-0 bg-slate-50">
                        <tr className="text-slate-400">
                          <th className="text-left font-normal px-1.5 py-1">time</th>
                          <th className="text-right font-normal">spot</th>
                          <th className="text-right font-normal">P&amp;L ₹</th>
                          <th className="text-right font-normal">ret%</th>
                          <th className="text-right font-normal">DD%</th>
                          <th className="text-left font-normal px-2">action</th>
                          <th className="text-left font-normal">advisory</th>
                          <th className="text-right font-normal pr-1.5">debt</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sim.decisions.filter((d: any) => !hideHolds || d.action !== 'HOLD').map((d: any, k: number) => (
                          <tr key={k} className={`border-t border-slate-50 ${d.action === 'HARVEST_VETO' ? 'bg-amber-50' : d.action !== 'HOLD' ? 'bg-indigo-50/40' : ''}`}>
                            <td className="px-1.5 py-0.5 text-slate-500 whitespace-nowrap">{fmtIST(d.ts)}</td>
                            <td className="text-right text-slate-500">{d.spot}</td>
                            <td className={`text-right ${pnlColor(d.mark_pnl)}`}>{fmtInr(d.mark_pnl)}</td>
                            <td className={`text-right ${(d.return_pct || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{d.return_pct ?? '—'}</td>
                            <td className="text-right text-rose-500">{d.drawdown_pct ?? '—'}</td>
                            <td className="px-2 text-slate-700 font-semibold whitespace-nowrap">{d.action}{d.roll && <span className="text-indigo-600 font-normal"> · {d.roll}</span>}</td>
                            <td className={`whitespace-nowrap ${d.advisory && d.advisory.best !== d.action ? 'text-amber-600 font-semibold' : 'text-violet-500'}`}>{d.advisory?.best || '—'}</td>
                            <td className="text-right pr-1.5 text-amber-600">{d.advisory?.harvest_state?.debt_pts || 0}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {proactive && (
                  <div className="text-[10px] text-violet-500 mb-1 flex flex-wrap items-center gap-x-2">
                    <span>⚖ Advisory per row (log-only). Amber ≠ rule = optimizer disagreed.</span>
                    {sim.advisory_agreement && (
                      <span className={`font-semibold ${sim.advisory_agreement.pct >= 80 ? 'text-emerald-600' : sim.advisory_agreement.pct >= 50 ? 'text-amber-600' : 'text-rose-600'}`}>
                        Rule↔Optimizer agreement {sim.advisory_agreement.pct}% ({sim.advisory_agreement.matches}/{sim.advisory_agreement.total})
                      </span>
                    )}
                    {sim.advisory_agreement?.close_vs_hold && (
                      <span className="text-slate-500" title="Optimizer said CLOSE while rules HELD. Closing then would have locked the mark; holding produced the final P&L.">
                        · CLOSE-vs-HOLD validation: closing beat holding in {sim.advisory_agreement.close_vs_hold.close_better}/{sim.advisory_agreement.close_vs_hold.n} cases (avg {fmtInr(sim.advisory_agreement.close_vs_hold.avg_close_minus_hold_inr)} vs final {fmtInr(sim.advisory_agreement.close_vs_hold.final_pnl_inr)})
                      </span>
                    )}
                  </div>
                )}
                {traceView === 'cards' && (
                <div className="max-h-72 overflow-auto border border-slate-100 rounded-lg divide-y divide-slate-50">
                  {sim.decisions.filter((d: any) => !hideHolds || d.action !== 'HOLD').map((d: any, k: number) => {
                    const cls: any = { HOLD: 'bg-slate-100 text-slate-500', ROLL_UP: 'bg-indigo-50 text-indigo-700', ROLL_DOWN: 'bg-indigo-50 text-indigo-700', STOP_LOSS: 'bg-rose-50 text-rose-700', CLOSE: 'bg-rose-50 text-rose-700', SETTLE: 'bg-emerald-50 text-emerald-700' };
                    return (
                      <div key={k} className="px-2.5 py-1.5">
                        <div className="flex items-center gap-2">
                          <span className="text-slate-400 whitespace-nowrap">{fmtIST(d.ts)}</span>
                          <span className="text-slate-500">spot {d.spot}</span>
                          <span className={`font-mono ${pnlColor(d.mark_pnl)}`}>{fmtInr(d.mark_pnl)}</span>
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${cls[d.action] || 'bg-slate-100'}`}>{d.action}</span>
                        </div>
                        <div className="text-slate-400 mt-0.5">{d.reason}</div>
                        {d.orders?.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {d.roll && <span className="text-[10px] font-semibold text-indigo-700">{d.roll}</span>}
                            {d.orders.map((o: string, oi: number) => (
                              <span key={oi} className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${o.startsWith('Buy') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{o}</span>
                            ))}
                          </div>
                        )}
                        {d.advisory && !d.advisory.error && (
                          <div className="mt-1 text-[10px] bg-violet-50 border border-violet-100 rounded px-2 py-1">
                            <div className="flex flex-wrap items-center gap-x-2">
                              <span className="font-semibold text-violet-700">⚖ advisory: {d.advisory.best}</span>
                              {d.advisory.forecast?.confidence != null && (
                                <span className="text-violet-500">conf {Math.round(d.advisory.forecast.confidence * 100)}%</span>
                              )}
                              {d.advisory.best !== d.action && (
                                <span className="text-amber-600 font-semibold">≠ rule ({d.action})</span>
                              )}
                            </div>
                            <div className="flex flex-wrap gap-x-2 mt-0.5 text-violet-400">
                              <span>EM {d.advisory.forecast?.expected_move_pts}</span>
                              <span>σ {d.advisory.forecast?.std_dev_pts}</span>
                              <span>touch P{d.advisory.risk?.touch_put}/C{d.advisory.risk?.touch_call}</span>
                              <span>breach {d.advisory.risk?.threatened_side === 'put' ? d.advisory.risk?.breach_put : d.advisory.risk?.breach_call}</span>
                              {d.advisory.risk?.expected_loss_if_breach_inr != null && (
                                <span className="text-rose-500">loss-if-breach {fmtInr(d.advisory.risk.expected_loss_if_breach_inr)}</span>
                              )}
                              {d.advisory.harvest_state && (d.advisory.harvest_state.debt_pts > 0 || d.advisory.harvest_state.n_harvests > 0 || d.advisory.harvest_state.blocked) && (
                                <span className={d.advisory.harvest_state.blocked ? 'text-rose-600 font-semibold' : 'text-amber-600'}>
                                  harvest debt {d.advisory.harvest_state.debt_pts}pt/#{d.advisory.harvest_state.n_harvests}{d.advisory.harvest_state.blocked ? ` · ${d.advisory.harvest_state.block_why}` : ''}
                                </span>
                              )}
                            </div>
                            {!d.advisory.skipped && (
                              <div className="flex flex-wrap gap-x-3 mt-0.5 font-mono text-violet-500">
                                {d.advisory.table?.map((r: any) => (
                                  <span key={r.action} className={r.action === d.advisory.best ? 'text-violet-800 font-semibold' : ''}>
                                    {r.action}{' '}
                                    {r.kind === 'realized'
                                      ? <span title="Closing is flat — not an estimate. Locks the current mark.">realized{r.realized_inr != null ? ` (locks ${fmtInr(r.realized_inr)})` : ''}</span>
                                      : <>E{r.expected != null ? (r.expected >= 0 ? '+' : '') + r.expected : '—'}/CVaR{r.cvar10 ?? '—'}/σ{r.std ?? '—'}</>}
                                    {' '}<span className="text-violet-300" title="risk-adj EV (E−λ|CVaR10|) minus HOLD's">(util {r.score >= 0 ? '+' : ''}{r.score})</span>
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
