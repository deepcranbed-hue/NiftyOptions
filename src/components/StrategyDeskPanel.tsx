import React, { useState, useEffect, useCallback } from 'react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid,
} from 'recharts';
import {
  Crosshair, RefreshCw, Plus, AlertTriangle, TrendingUp, TrendingDown, Minus,
  ChevronDown, ChevronRight, Check,
} from 'lucide-react';
import { fmtInr, Tile } from './deskShared';
import { useSignalRoster } from '../lib/signalRoster';

/*
 * StrategyDeskPanel
 * -----------------
 * The "find a strategy" side: live directional-momentum suggestion, the
 * quantitative signal breakdowns, the suggested payoff, and the ranked candidate
 * strategies. "Add" pushes a candidate into the shared Desk Book (backend
 * portfolio), which you then P&L-check and backtest in the Desk Book tab.
 */

/* Signal titles, method text and detail-field ordering are NOT declared here — they
 * come from /api/strategy/config, i.e. from signals/registry.py. A signal added to
 * the registry appears in this breakdown, with its explanation, automatically. */

interface SigRow { name: string; score: number; confidence: number; status: string; tag?: string; detail?: any; }

const fmtNum = (v: any): string =>
  typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v);

const DetailValue: React.FC<{ k: string; v: any }> = ({ k, v }) => {
  if (Array.isArray(v)) {
    return (
      <div className="col-span-2">
        <div className="text-slate-400 mb-0.5">{k}</div>
        {v.map((row: any, i: number) => (
          <div key={i} className="text-slate-600 pl-2">
            {typeof row === 'object' ? Object.entries(row).map(([kk, vv]) => `${kk}: ${fmtNum(vv)}`).join('  ·  ') : fmtNum(row)}
          </div>
        ))}
      </div>
    );
  }
  if (v && typeof v === 'object') {
    return (
      <div className="col-span-2">
        <div className="text-slate-400 mb-0.5">{k}</div>
        <div className="pl-2 text-slate-600">{Object.entries(v).map(([kk, vv]) => `${kk}: ${fmtNum(vv)}`).join('  ·  ')}</div>
      </div>
    );
  }
  return (
    <div className="flex justify-between gap-2">
      <span className="text-slate-400">{k}</span>
      <span className="text-slate-700 font-mono">{fmtNum(v)}</span>
    </div>
  );
};

const Metric: React.FC<{ label: string; value: any; cls?: string }> = ({ label, value, cls }) => (
  <div>
    <div className="text-xs text-slate-400">{label}</div>
    <div className={`text-sm font-semibold ${cls || 'text-slate-800'}`}>{value}</div>
  </div>
);

export const StrategyDeskPanel: React.FC = () => {
  const roster = useSignalRoster();
  const [sug, setSug] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [openSig, setOpenSig] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);   // flash confirmation

  const loadSuggestion = useCallback(async () => {
    setLoading(true); setErr('');
    let r: Response;
    try {
      r = await fetch('/api/strategy/suggest');
    } catch (e: any) {
      // ONLY a genuine network failure (fetch rejects) means the backend is unreachable.
      setErr('Cannot reach backend at /api (is uvicorn running on port 8000?)');
      setSug(null); setLoading(false); return;
    }
    try {
      // read as text first so a non-JSON error body (proxy 502/504 HTML, 500 page)
      // surfaces the REAL status instead of throwing into the "unreachable" path.
      const text = await r.text();
      let j: any = null;
      try { j = JSON.parse(text); } catch { /* non-JSON body */ }
      if (!r.ok) {
        setErr(`Backend ${r.status}: ${j?.detail || j?.error || text.slice(0, 300) || r.statusText}`);
        setSug(null);
      } else if (!j) {
        setErr(`Backend returned a non-JSON response (${r.status}): ${text.slice(0, 300)}`);
        setSug(null);
      } else if (j.error) {
        setErr(`${j.error}`); setSug(j);
      } else {
        setSug(j);
      }
    } catch (e: any) {
      setErr(`Failed to read backend response: ${e?.message || e}`); setSug(null);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { loadSuggestion(); }, [loadSuggestion]);

  const addCandidate = async (family: string) => {
    await fetch('/api/strategy/candidate/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family }),
    });
    setAdded(family);
    setTimeout(() => setAdded(null), 2500);
  };

  const dec = sug?.decision;
  const struct = sug?.structure;
  // Show the DIRECTIONAL signals only — gates and overlays are not votes and have
  // no score bar. Decided by `kind` in the registry, never by a name blacklist, so
  // a new gate is excluded automatically instead of leaking into the chart.
  const signals: SigRow[] = sug?.signals
    ? (Object.values(sug.signals) as SigRow[]).filter(
        (s) => (roster.byName[s.name]?.kind ?? 'directional') === 'directional')
    : [];

  const regimeBadge = (label?: string) => ({
    TREND_UP: 'bg-emerald-100 text-emerald-700', TREND_DOWN: 'bg-rose-100 text-rose-700',
    RANGE: 'bg-amber-100 text-amber-700', NO_TRADE: 'bg-slate-100 text-slate-500',
  }[label || 'NO_TRADE'] || 'bg-slate-100 text-slate-500');

  const payoffData = () => {
    if (!struct) return [];
    const strikes = struct.legs.map((l: any) => l.strike);
    const lo = Math.min(...strikes) - 300, hi = Math.max(...strikes) + 300;
    const pts = [];
    for (let s = lo; s <= hi; s += (hi - lo) / 60) {
      let pnl = 0;
      for (const leg of struct.legs) {
        const intr = leg.side === 'call' ? Math.max(s - leg.strike, 0) : Math.max(leg.strike - s, 0);
        pnl += leg.sign * intr;
      }
      pnl -= struct.net_debit_pts || 0;
      pts.push({ underlying: Math.round(s), pnl: Math.round(pnl) });
    }
    return pts;
  };

  const DirIcon = dec?.direction > 0 ? TrendingUp : dec?.direction < 0 ? TrendingDown : Minus;

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Crosshair className="w-5 h-5 text-indigo-600" />
          <h2 className="text-base font-bold text-slate-800">Strategy Desk</h2>
        </div>
        <div className="flex items-center gap-2">
          {dec && (
            <>
              <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${regimeBadge(dec.regime)}`}>{dec.regime}</span>
              <span className="text-xs px-2.5 py-1 rounded-full bg-slate-100 text-slate-500">{dec.phase}</span>
            </>
          )}
          <button onClick={loadSuggestion} className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white font-bold hover:bg-indigo-700">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> {loading ? 'Loading…' : 'Suggest'}
          </button>
        </div>
      </div>

      {added && (
        <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2 mb-3">
          <Check className="w-3.5 h-3.5" /> Added <span className="font-semibold">{added}</span> to the Desk Book — open the Desk Book tab to P&L-check or backtest it.
        </div>
      )}

      {err && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2 mb-3">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <div>
            <div className="font-semibold">{err}</div>
            {sug?.diag && <div className="text-rose-400 mt-0.5">db: {sug.diag.db_path} · expiries: {sug.diag.n_expiries} · captures: {sug.diag.captures_for_expiry ?? '—'}</div>}
          </div>
        </div>
      )}

      {!sug && !err && (
        <div className="text-sm text-slate-400 py-8 text-center">
          Click <span className="font-semibold">Suggest</span> to load a strategy read from your latest snapshot.
        </div>
      )}

      {sug && !dec && !err && (
        <div className="text-sm text-slate-500 py-6 text-center">
          <div>No suggestion could be built for the latest snapshot.</div>
          <div className="text-xs text-slate-400 mt-1">{sug.note}</div>
        </div>
      )}

      {/* Suggestion summary */}
      {dec && (
        <div className="grid grid-cols-4 gap-2.5 mb-4">
          <Tile label="Direction" value={dec.direction > 0 ? 'Bullish' : dec.direction < 0 ? 'Bearish' : 'None'}
                icon={<DirIcon className="w-4 h-4" />}
                cls={dec.direction > 0 ? 'text-emerald-600' : dec.direction < 0 ? 'text-rose-600' : 'text-slate-500'} />
          <Tile label="Net score" value={(dec.net_score >= 0 ? '+' : '') + dec.net_score.toFixed(2)} />
          <Tile label="Confidence" value={dec.net_confidence.toFixed(2)} />
          <Tile label="Exp. move" value={`±${Math.round(dec.expected_move_pts)}`} />
        </div>
      )}

      {/* Signal contributions */}
      {signals.length > 0 && (
        <div className="mb-5">
          <div className="text-xs text-slate-500 mb-2">Signal contributions <span className="text-slate-400">(click a signal for the math)</span></div>
          {signals.map((s) => {
            const meta = roster.byName[s.name];
            const open = openSig === s.name;
            return (
              <div key={s.name} className="border-b border-slate-50 last:border-0">
                <button onClick={() => setOpenSig(open ? null : s.name)} className="w-full grid grid-cols-[16px_142px_1fr_44px] items-center gap-2 py-1.5 text-left hover:bg-slate-50 rounded">
                  {open ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-300" />}
                  <span className="text-xs text-slate-600 truncate">{meta?.label || s.name}</span>
                  <div className="relative h-3.5 bg-slate-100 rounded">
                    <div className="absolute left-1/2 top-0 bottom-0 w-px bg-slate-300" />
                    <div className={`absolute top-0.5 bottom-0.5 rounded ${s.score >= 0 ? 'bg-emerald-500 left-1/2' : 'bg-rose-500 right-1/2'}`} style={{ width: `${Math.min(Math.abs(s.score), 1) * 50}%` }} />
                  </div>
                  <span className="text-xs text-right font-semibold text-slate-700">{(s.score >= 0 ? '+' : '') + s.score.toFixed(2)}</span>
                </button>
                {open && (
                  <div className="ml-6 mb-2 mt-1 p-3 bg-slate-50 rounded-lg text-[11px] leading-relaxed">
                    <div className="text-slate-500 mb-2">{meta?.method || 'Computed by the signal engine.'}</div>
                    <div className="grid grid-cols-3 gap-x-4 gap-y-1 mb-2 pb-2 border-b border-slate-200">
                      <div className="flex justify-between gap-2"><span className="text-slate-400">score</span><span className="font-mono text-slate-700">{s.score.toFixed(3)}</span></div>
                      <div className="flex justify-between gap-2"><span className="text-slate-400">confidence</span><span className="font-mono text-slate-700">{s.confidence.toFixed(3)}</span></div>
                      <div className="flex justify-between gap-2"><span className="text-slate-400">tag</span><span className="font-mono text-slate-700">{s.tag || '—'}</span></div>
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                      {s.detail && Object.keys(s.detail).length > 0
                        ? (meta?.detail_keys?.length ? meta.detail_keys : Object.keys(s.detail)).filter((k) => s.detail[k] !== undefined && s.detail[k] !== null).map((k) => <DetailValue key={k} k={k} v={s.detail[k]} />)
                        : <span className="text-slate-400">{s.status}</span>}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Suggested structure + payoff */}
      {struct && (
        <div className="border-t border-slate-100 pt-4 grid grid-cols-[1.2fr_1fr] gap-4 mb-4">
          <div>
            <div className="text-xs text-slate-500 mb-1">Suggested: <span className="font-semibold text-slate-700">{struct.family}</span></div>
            <ResponsiveContainer width="100%" height={140}>
              <AreaChart data={payoffData()}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                <XAxis dataKey="underlying" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={34} />
                <Tooltip formatter={(v: any) => `${v} pts`} />
                <ReferenceLine y={0} stroke="#94a3b8" />
                <Area type="monotone" dataKey="pnl" stroke="#4f46e5" fill="#e0e7ff" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="grid grid-cols-2 gap-2 content-start text-sm">
            <Metric label="Max profit" value={fmtInr(struct.rupees?.max_profit)} cls="text-emerald-600" />
            <Metric label="Max loss" value={fmtInr(struct.rupees?.max_loss)} cls="text-rose-600" />
            <Metric label="Breakeven" value={struct.breakevens?.map((b: number) => Math.round(b)).join(', ') || '—'} />
            <Metric label="Net debit" value={`${struct.net_debit_pts} pts`} />
            <button onClick={() => addCandidate(struct.family)} className="col-span-2 mt-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-xs font-bold hover:bg-indigo-700">
              <Plus className="w-3.5 h-3.5" /> Add to Desk Book
            </button>
          </div>
        </div>
      )}

      {dec && !sug?.tradeable && (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-600 mb-2">
          <AlertTriangle className="w-3 h-3" /> {sug?.note}
        </div>
      )}

      {/* Ranked candidates */}
      {sug?.candidates?.length > 0 && (
        <div className="mb-1">
          <div className="text-xs text-slate-500 mb-2">Candidate strategies (ranked)</div>
          <div className="space-y-1.5">
            {sug.candidates.map((c: any) => (
              <div key={c.family} className="flex items-center gap-2 px-2.5 py-2 rounded-lg border border-slate-100 hover:border-slate-200">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-slate-700">{c.family}</span>
                    {c.primary && <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">gate pick</span>}
                    {c.aligned && !c.primary && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600">aligned</span>}
                  </div>
                  <div className="text-[11px] text-slate-400 truncate">{c.rationale}</div>
                </div>
                <div className="text-[11px] text-right whitespace-nowrap">
                  <span className="text-emerald-600">+{fmtInr(c.structure?.rupees?.max_profit)}</span>
                  <span className="text-slate-300"> / </span>
                  <span className="text-rose-600">-{fmtInr(c.structure?.rupees?.max_loss)}</span>
                </div>
                <button onClick={() => addCandidate(c.family)} className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200" title="Add to Desk Book">
                  <Plus className="w-3.5 h-3.5 text-slate-600" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
