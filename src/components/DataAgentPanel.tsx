import React, { useState, useCallback, useEffect } from 'react';
import { DownloadCloud, Play, MessageSquare, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';

/*
 * DataAgentPanel
 * --------------
 * Drives the data agent from the UI: a Start-collection button (pick broker + paste
 * token) AND a natural-language command box that routes through the backend's local
 * Qwen intent parser (/api/data-agent/command). Shows the data-health summary.
 * Tokens are sent per-request and never stored client-side.
 */

type Health = { level?: string; headline?: string; detail?: string; flagged?: any[] };

const Toggle: React.FC<{ label: string; hint?: string; on: boolean; onChange: (v: boolean) => void }> = ({ label, hint, on, onChange }) => (
  <label className="flex items-start gap-3 cursor-pointer select-none">
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={`mt-0.5 relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors ${on ? 'bg-emerald-500' : 'bg-slate-300'}`}>
      <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform mt-0.5 ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
    </button>
    <span className="flex flex-col">
      <span className="text-sm font-bold text-slate-800">{label}</span>
      {hint && <span className="text-[11px] text-slate-400 max-w-xs">{hint}</span>}
    </span>
  </label>
);

async function postJSON(url: string, body: any): Promise<{ ok: boolean; status: number; data: any; text: string }> {
  let r: Response;
  try {
    r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e: any) {
    return { ok: false, status: 0, data: null, text: `Cannot reach backend (${e?.message || e})` };
  }
  const text = await r.text();
  let data: any = null;
  try { data = JSON.parse(text); } catch { /* non-JSON */ }
  return { ok: r.ok, status: r.status, data, text };
}

export const DataAgentPanel: React.FC = () => {
  const [broker] = useState<'breeze'>('breeze');   // Kite removed 2026-08-08
  const [token, setToken] = useState('');
  const [mode, setMode] = useState<'cash' | 'all'>('cash');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [runResult, setRunResult] = useState<any>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [cmd, setCmd] = useState('');
  const [cmdResult, setCmdResult] = useState<any>(null);
  const [llmOn, setLlmOn] = useState(false);   // default OFF; loadSettings() sets the real value
  const [agentOn, setAgentOn] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const r = await fetch('/api/data-agent/settings');
      if (r.ok) { const s = JSON.parse(await r.text()); setLlmOn(!!s.local_llm_enabled); setAgentOn(!!s.agent_enabled); }
    } catch { /* leave defaults */ }
  }, []);
  useEffect(() => { loadSettings(); }, [loadSettings]);

  const saveSetting = async (patch: { local_llm_enabled?: boolean; agent_enabled?: boolean }) => {
    // optimistic
    if (patch.local_llm_enabled !== undefined) setLlmOn(patch.local_llm_enabled);
    if (patch.agent_enabled !== undefined) setAgentOn(patch.agent_enabled);
    const { ok } = await postJSON('/api/data-agent/settings', patch);
    if (!ok) loadSettings();   // revert to server truth on failure
  };

  const loadHealth = useCallback(async () => {
    try {
      const r = await fetch('/api/data-agent/health');
      if (r.ok) setHealth(JSON.parse(await r.text()));
    } catch { /* leave as-is */ }
  }, []);
  useEffect(() => { loadHealth(); }, [loadHealth]);

  const startCollection = async () => {
    setErr(''); setRunResult(null);
    if (!token) { setErr('Paste your broker token first.'); return; }
    setBusy(true);
    const { ok, status, data, text } = await postJSON('/api/data-agent/run', {
      broker, token, mode,
    });
    setBusy(false);
    if (!ok) { setErr(`Backend ${status || ''}: ${data?.detail || text.slice(0, 300)}`); return; }
    setRunResult(data); loadHealth();
  };

  const sendCommand = async () => {
    setErr(''); setCmdResult(null);
    if (!cmd.trim()) return;
    setBusy(true);
    const { ok, status, data, text } = await postJSON('/api/data-agent/command', {
      text: cmd,
      breeze_token: token || undefined,
    });
    setBusy(false);
    if (!ok) { setErr(`Backend ${status || ''}: ${data?.detail || text.slice(0, 300)}`); return; }
    setCmdResult(data); loadHealth();
  };

  const badge = (lvl?: string) =>
    lvl === 'ok' ? 'bg-emerald-100 text-emerald-800'
      : lvl === 'warn' ? 'bg-amber-100 text-amber-800'
        : lvl === 'alert' ? 'bg-rose-100 text-rose-800' : 'bg-slate-100 text-slate-600';

  return (
    <div className="space-y-6">
      {/* header + health badge */}
      <div className="bg-slate-950 text-white p-6 rounded-2xl border border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-black flex items-center gap-2"><DownloadCloud className="w-5 h-5 text-indigo-400" /> Data Agent</h2>
          <p className="text-xs text-slate-400 mt-1">Collect 1-minute data (cash + F&amp;O), keep it in sync, and monitor coverage.</p>
        </div>
        <div className="flex items-center gap-2">
          {health && (
            <span className={`text-xs font-bold px-3 py-1.5 rounded-full ${badge(health.level)}`}>
              {health.level === 'ok' ? <CheckCircle2 className="w-3.5 h-3.5 inline -mt-0.5 mr-1" /> : <AlertTriangle className="w-3.5 h-3.5 inline -mt-0.5 mr-1" />}
              {health.headline || 'Data status'}
            </span>
          )}
          <button onClick={loadHealth} className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300" title="Refresh health"><RefreshCw className="w-4 h-4" /></button>
        </div>
      </div>

      {/* power switches — stop local LLM (Mac heat) / idle the agent */}
      <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-sm flex flex-wrap items-center gap-6">
        <Toggle label="Local LLM (Qwen on this Mac)"
                hint={llmOn ? 'On-device inference active — uses CPU/GPU.' : 'Off — no local inference (no Mac heating). Commands use keyword parsing; tagger uses cloud/keywords.'}
                on={llmOn} onChange={(v) => saveSetting({ local_llm_enabled: v })} />
        <Toggle label="Data Agent (5 PM audit + collection)"
                hint={agentOn ? 'Running — audits at 17:00 IST.' : 'Off — no scheduled audit or collection.'}
                on={agentOn} onChange={(v) => saveSetting({ agent_enabled: v })} />
      </div>

      {/* credentials + Start button */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400">Broker</label>
            <div className="bg-slate-50 border border-slate-300 rounded-lg px-3 py-2 text-sm font-semibold text-slate-700">Breeze (ICICI)</div>
          </div>
          <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
            <label className="text-[10px] uppercase font-bold text-slate-400">Session token</label>
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="paste token (not stored)" className="border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400">Scope</label>
            <select value={mode} onChange={(e) => setMode(e.target.value as any)} className="bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm font-semibold">
              <option value="cash">Cash (50 stocks + index) → price_bars</option>
              <option value="all">Cash + F&amp;O (per-contract, backtest store)</option>
            </select>
          </div>
          <button onClick={startCollection} disabled={busy}
            className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white text-sm font-bold ${busy ? 'bg-slate-400' : 'bg-emerald-600 hover:bg-emerald-500'}`}>
            <Play className={`w-4 h-4 ${busy ? 'animate-spin' : ''}`} /> {busy ? 'Working…' : 'Start Collection'}
          </button>
        </div>
        {err && <div className="text-sm text-rose-600 font-semibold bg-rose-50 border border-rose-200 rounded-lg p-3">{err}</div>}
        {runResult && (
          <div className="text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 font-mono">
            saved {runResult.saved_total} bars · {runResult.ok} ok · {runResult.empty} empty · {runResult.errors} errors
            {runResult.warning && <div className="text-amber-700 mt-1">{runResult.warning}</div>}
            {runResult.health?.detail && <div className="text-slate-600 mt-1">{runResult.health.detail}</div>}
          </div>
        )}
      </div>

      {/* natural-language command box (routes through local Qwen) */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm space-y-3">
        <h3 className="text-sm font-bold uppercase tracking-wider text-indigo-500 flex items-center gap-2"><MessageSquare className="w-4 h-4" /> Ask the Data Agent (natural language)</h3>
        <div className="flex gap-2">
          <input value={cmd} onChange={(e) => setCmd(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendCommand()}
            placeholder='e.g. "is the data up to the mark?"  or  "start downloading with my breeze token"'
            className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <button onClick={sendCommand} disabled={busy} className={`px-4 py-2 rounded-lg text-white text-sm font-bold ${busy ? 'bg-slate-400' : 'bg-indigo-600 hover:bg-indigo-500'}`}>Send</button>
        </div>
        <p className="text-[11px] text-slate-400">Parsed locally by Qwen 2.5 7B → routed to health / collection. Token above is used if the command starts a download.</p>
        {cmdResult && (
          <div className="text-xs bg-slate-50 border border-slate-200 rounded-lg p-3 font-mono space-y-1">
            <div>intent: {JSON.stringify(cmdResult.intent)}</div>
            {cmdResult.message && <div className="text-slate-700">{cmdResult.message}</div>}
            {cmdResult.health?.detail && <div className="text-slate-700">{cmdResult.health.detail}</div>}
            {cmdResult.run && <div className="text-emerald-700">collected {cmdResult.run.saved_total} bars ({cmdResult.run.ok} ok)</div>}
          </div>
        )}
      </div>

      {/* flagged contracts / symbols */}
      {health?.flagged && health.flagged.length > 0 && (
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-3">Needs attention ({health.flagged.length})</h3>
          <div className="space-y-1 text-xs font-mono max-h-64 overflow-y-auto">
            {health.flagged.map((f: any, i: number) => (
              <div key={i} className="flex justify-between border-b border-slate-100 py-1">
                <span className="font-bold">{f.symbol} {f.date}</span>
                <span className="text-slate-500">{f.status}: {f.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
