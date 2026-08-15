import React, { useEffect, useRef, useState } from 'react';
import { Zap, AlertTriangle, RefreshCw } from 'lucide-react';

const POLL_MS = 5 * 60 * 1000; // re-READ the stored state every 5 minutes

// IMPORTANT COST NOTE
// -------------------
// The 5-minute poll only calls GET /api/impact-monitor, which RE-READS a stored
// news_state. It does not fetch news. Fresh headlines only arrive when something
// calls POST /api/update-news — which runs fetch_rss() AND llm_tag_batch() over the
// batch. That LLM tagging call is the expensive part.
//
// So auto-refresh is OPT-IN and defaults OFF: at 5-minute cadence it would fire
// ~288 tagging batches a day versus a handful when the button is pressed by hand.
const AUTO_REFRESH_MS = 5 * 60 * 1000;

// UTC ISO ("...T09:11:00Z") -> "14:41 IST" (UTC + 5:30), per the app's IST-at-the-boundary rule
const toIST = (iso?: string): string => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const ist = new Date(d.getTime() + 5.5 * 60 * 60 * 1000);
  return `${String(ist.getUTCHours()).padStart(2, '0')}:${String(ist.getUTCMinutes()).padStart(2, '0')} IST`;
};

const tiltStyle = (label: string) =>
  label === 'BULLISH' ? 'bg-emerald-100 text-emerald-700'
  : label === 'BEARISH' ? 'bg-rose-100 text-rose-700'
  : label === 'MIXED' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600';

export const ImpactMonitorBanner: React.FC = () => {
  const [data, setData] = useState<any>(null);
  // LLM (local Qwen) is CPU-heavy — default OFF; rule-based read runs when off. Persisted.
  const [llmOn, setLlmOn] = useState<boolean>(() => {
    try { return localStorage.getItem('impactLLM') === '1'; } catch { return false; }
  });
  // Auto-refresh actually FETCHES news (costs an LLM tagging batch each time), so it
  // is opt-in and persisted. Default OFF.
  const [autoOn, setAutoOn] = useState<boolean>(() => {
    try { return localStorage.getItem('impactAuto') === '1'; } catch { return false; }
  });
  const [busy, setBusy] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<string>('');
  const timer = useRef<any>(null);
  const autoTimer = useRef<any>(null);

  const load = React.useCallback(() => {
    fetch(`/api/impact-monitor?use_llm=${llmOn}`)
      .then((r) => r.json())
      .then((j) => { if (j.success) setData(j); })
      .catch(() => {});
  }, [llmOn]);

  // Full refresh: refetch RSS + re-tag, then bypass the 90s impact cache so the
  // new articles show immediately rather than on the next poll.
  const refreshNews = React.useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      await fetch('/api/update-news', { method: 'POST' });
      const r = await fetch(`/api/impact-monitor?use_llm=${llmOn}&refresh=true`);
      const j = await r.json();
      if (j.success) setData(j);
      setLastRefresh(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }));
    } catch { /* leave the last good render in place */ }
    setBusy(false);
  }, [busy, llmOn]);

  useEffect(() => {
    load();
    timer.current = setInterval(load, POLL_MS);
    return () => clearInterval(timer.current);
  }, [load]);

  // Opt-in auto-refresh loop. Cleared whenever it is switched off or the tab unmounts,
  // so it can never keep firing tagging batches in the background.
  useEffect(() => {
    if (!autoOn) return;
    autoTimer.current = setInterval(refreshNews, AUTO_REFRESH_MS);
    return () => clearInterval(autoTimer.current);
  }, [autoOn, refreshNews]);

  const toggleLlm = () => {
    setLlmOn((v) => {
      const nv = !v;
      try { localStorage.setItem('impactLLM', nv ? '1' : '0'); } catch { /* ignore */ }
      return nv;
    });
  };

  const toggleAuto = () => {
    setAutoOn((v) => {
      const nv = !v;
      try { localStorage.setItem('impactAuto', nv ? '1' : '0'); } catch { /* ignore */ }
      return nv;
    });
  };

  if (!data) return null;
  const highs = (data.impact_items || []).filter((i: any) => i.flash);
  const hasHigh = highs.length > 0;

  return (
    <div className={`w-full rounded-xl border px-4 py-2 mb-4 flex items-center gap-3 flex-wrap ${hasHigh ? 'border-rose-300 bg-rose-50' : 'border-slate-200 bg-white'}`}>
      <span className="flex items-center gap-1.5 text-[10px] font-black uppercase text-slate-400">
        {hasHigh && <span className="relative flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75" /><span className="relative inline-flex rounded-full h-2 w-2 bg-rose-500" /></span>}
        <Zap className="w-3.5 h-3.5" /> Impact
      </span>
      <span className={`text-[11px] font-black px-2 py-0.5 rounded ${tiltStyle(data.tilt_label)}`}>
        {data.tilt_label}{typeof data.tilt === 'number' ? ` (${data.tilt > 0 ? '+' : ''}${data.tilt})` : ''}
      </span>
      <span className="text-[12px] text-slate-700 font-semibold flex-1 min-w-[220px]">{data.summary}</span>
      {hasHigh && (
        <span className="flex items-center gap-1 text-[11px] font-bold text-rose-700"><AlertTriangle className="w-3.5 h-3.5" /> {highs.length} high-impact</span>
      )}
      <button
        onClick={toggleLlm}
        title={llmOn ? 'LLM (local Qwen) ON — richer analysis, uses CPU. Click to turn OFF.' : 'LLM OFF — light rule-based read (no CPU load). Click to turn ON.'}
        className={`text-[9px] font-black px-2 py-0.5 rounded-full border transition ${llmOn ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-500 border-slate-300'}`}>
        LLM {llmOn ? 'ON' : 'OFF'}
      </button>

      {/* Manual refresh — the same thing the Update News button does, inline. */}
      <button
        onClick={refreshNews}
        disabled={busy}
        title="Refetch RSS + re-tag now (runs an LLM tagging batch), then bust the 90s cache."
        className={`flex items-center gap-1 text-[9px] font-black px-2 py-0.5 rounded-full border transition ${
          busy ? 'bg-slate-100 text-slate-400 border-slate-200 cursor-wait'
               : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}`}>
        <RefreshCw className={`w-3 h-3 ${busy ? 'animate-spin' : ''}`} />
        {busy ? 'FETCHING' : 'REFRESH'}
      </button>

      {/* Auto every 5 min — OFF by default because each tick costs an LLM batch. */}
      <button
        onClick={toggleAuto}
        title={autoOn
          ? 'AUTO ON — refetching news every 5 min. Each tick runs an LLM tagging batch (~288/day). Click to stop.'
          : 'AUTO OFF — news only refreshes when you press REFRESH. Click to auto-refresh every 5 min (costs an LLM batch per tick).'}
        className={`text-[9px] font-black px-2 py-0.5 rounded-full border transition ${
          autoOn ? 'bg-amber-500 text-white border-amber-500' : 'bg-white text-slate-500 border-slate-300'}`}>
        AUTO {autoOn ? '5m ON' : 'OFF'}
      </button>

      <span className="text-[9px] text-slate-400 font-mono uppercase">{data.engine || ''}</span>
      <span className="text-[9px] text-slate-400">
        updated {toIST(data.as_of)}
        {lastRefresh && <span className="text-slate-300"> · fetched {lastRefresh}</span>}
      </span>

      {hasHigh && (
        <div className="basis-full flex flex-wrap gap-1.5 mt-1">
          {highs.slice(0, 5).map((h: any, i: number) => (
            <span key={i} className={`text-[11px] px-2 py-0.5 rounded font-semibold ${h.direction === '-' ? 'bg-rose-100 text-rose-700' : h.direction === '+' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-600'}`}>
              {h.direction === '-' ? '▼' : h.direction === '+' ? '▲' : '•'} {h.headline}
            </span>
          ))}
        </div>
      )}

      {data.themes?.length > 0 && (
        <div className="basis-full flex flex-wrap items-center gap-1.5 mt-1.5">
          <span className="text-[9px] font-black uppercase text-slate-400">Market drivers now:</span>
          {data.themes.filter((t: any) => t.status !== 'QUIET').slice(0, 8).map((t: any, i: number) => (
            <span key={i}
              title={t.note || ''}
              className={`text-[10px] px-2 py-0.5 rounded font-bold border ${
                t.status === 'HOT' ? 'border-rose-300 bg-rose-50' : 'border-slate-200 bg-white'} ${
                t.sign > 0 ? 'text-emerald-700' : t.sign < 0 ? 'text-rose-700' : 'text-slate-600'}`}>
              {t.status === 'HOT' ? '🔥 ' : ''}{t.theme} {t.sign > 0 ? '▲' : t.sign < 0 ? '▼' : '—'}
            </span>
          ))}
        </div>
      )}

      {data.chains?.length > 0 && (
        <div className="basis-full text-[10px] text-slate-500 mt-0.5">
          {data.chains.map((c: string[], i: number) => (
            <span key={i} className="mr-4">⛓ {c.join(' → ')}</span>
          ))}
        </div>
      )}
    </div>
  );
};
