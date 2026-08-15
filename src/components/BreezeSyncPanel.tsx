import React, { useState, useEffect } from 'react';
import { DownloadCloud, AlertTriangle, FileDown, Database, Clock } from 'lucide-react';
import { OptionRow } from '../types';

interface BreezeSyncPanelProps {
  onBreezeDataLoaded: (rows: OptionRow[], spot: number) => void;
  onCaptureSaved?: () => void;
}

export const BreezeSyncPanel: React.FC<BreezeSyncPanelProps> = ({ onBreezeDataLoaded, onCaptureSaved }) => {
  const [loading, setLoading] = useState(false);
  const [unifiedLoading, setUnifiedLoading] = useState(false);
  const [unifiedLogs, setUnifiedLogs] = useState<string[]>([]);
  const [unifiedSuccess, setUnifiedSuccess] = useState<string | null>(null);
  const [unifiedError, setUnifiedError] = useState<string | null>(null);
  const [sessionToken, setSessionToken] = useState<string>(() => localStorage.getItem('breezeSessionToken') || '');
  const [expiryDate, setExpiryDate] = useState<string>(() => localStorage.getItem('breezeExpiryDate') || '2026-07-09T06:00:00.000Z');
  const [symbol, setSymbol] = useState<string>(() => localStorage.getItem('breezeSymbol') || 'NIFTY');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [fetchedData, setFetchedData] = useState<{rows: OptionRow[], spot: number} | null>(null);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<'chain' | 'historical'>('chain');
  const [backfillInterval, setBackfillInterval] = useState<string>('1minute');
  const [backfillStart, setBackfillStart] = useState<string>(() => {
    const d = new Date(); d.setDate(d.getDate() - 5); return d.toISOString().split('T')[0];
  });
  const [backfillEnd, setBackfillEnd] = useState<string>(() => new Date().toISOString().split('T')[0]);


  const toLocalISTString = (date: Date): string => {
    // Offset standard Date to local IST representation
    const localOffsetDate = new Date(date.getTime() + 5.5 * 60 * 60 * 1000);
    return localOffsetDate.toISOString().replace('Z', '').slice(0, 19);
  };

  const [histInterval, setHistInterval] = useState<string>("1minute");
  const [histFrom, setHistFrom] = useState<string>(() => {
    const d = new Date(); d.setDate(d.getDate() - 4); d.setHours(9, 15, 0, 0);
    return toLocalISTString(d);
  });
  const [histTo, setHistTo] = useState<string>(() => toLocalISTString(new Date()));
  const [histLoading, setHistLoading] = useState(false);
  const [histSuccess, setHistSuccess] = useState<string | null>(null);
  const [histError, setHistError] = useState<string | null>(null);
  const [constituentsSyncLoading, setConstituentsSyncLoading] = useState(false);

  // DB Stored Range Memory State
  const [dbRange, setDbRange] = useState<{ min_date: string | null, max_date: string | null, count: number } | null>(null);

  const [symbols, setSymbols] = useState<string[]>(['NIFTY', 'NIFTY_FUT_1', 'NIFTY_FUT_2']);

  const [expiryOptions, setExpiryOptions] = useState<string[]>([]);

  useEffect(() => {
    const fetchExpiries = async () => {
      try {
        // Expiries come from Breeze now (Kite removed), so this is an
        // authenticated call — pass the session token if we have one. The
        // backend falls back to today's cached session file when it is blank.
        const res = await fetch('/api/exchange-expiries?session_token='
          + encodeURIComponent(sessionToken || ''));
        const json = await res.json();
        if (json.success && json.expiries && json.expiries.length > 0) {
          setExpiryOptions(json.expiries);
          const stored = localStorage.getItem('breezeExpiryDate');
          // If no stored expiry or the stored expiry is the old hardcoded default,
          // auto-select the nearest active expiry from the exchange list.
          if (!stored || stored === '2026-07-09T06:00:00.000Z' || !json.expiries.includes(stored)) {
            setExpiryDate(json.expiries[0]);
            localStorage.setItem('breezeExpiryDate', json.expiries[0]);
          }
        }
      } catch (e) {
        console.error("Failed to load exchange expiries in sync panel", e);
      }
    };
    fetchExpiries();
  }, [sessionToken]);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/bars/symbols');
        const json = await res.json();
        if (json.success && json.symbols.length > 0) {
          const merged = Array.from(new Set(['NIFTY', 'NIFTY_FUT_1', 'NIFTY_FUT_2', ...json.symbols]));
          setSymbols(merged);
          // If the stored symbol is not in the list, we can add it to prevent it from resetting
          const stored = localStorage.getItem('breezeSymbol') || 'NIFTY';
          if (!merged.includes(stored)) {
            setSymbols(prev => [...prev, stored]);
          }
        }
      } catch (e) {
        console.error("Failed to load symbols in sync panel", e);
      }
    };
    fetchSymbols();
  }, []);

  // Auto-Sync Scheduler State
  const [scheduleInterval, setScheduleInterval] = useState<number>(5);
  const [scheduleActive, setScheduleActive] = useState<boolean>(false);
  const [scheduleStartedAt, setScheduleStartedAt] = useState<string | null>(null);
  const [scheduleLoading, setScheduleLoading] = useState<boolean>(false);

  const checkScheduleStatus = async () => {
    try {
      const res = await fetch(`/api/schedule/status?symbol=${encodeURIComponent(symbol)}`);
      const json = await res.json();
      if (json.active) {
        setScheduleActive(true);
        setScheduleInterval(json.interval);
        setScheduleStartedAt(json.started_at);
      } else {
        setScheduleActive(false);
        setScheduleStartedAt(null);
      }
    } catch (e) {
      console.error("Failed to check schedule status", e);
    }
  };

  const startScheduler = async () => {
    if (!sessionToken || !expiryDate) {
      setError("Please provide Session Token and Expiry Date.");
      return;
    }
    setScheduleLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/schedule/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_token: sessionToken,
          expiry_date: expiryDate,
          symbol: symbol,
          interval: scheduleInterval
        })
      });
      const json = await res.json();
      if (json.success) {
        setSuccess(json.message);
        checkScheduleStatus();
      } else {
        setError(json.detail || "Failed to start schedule");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setScheduleLoading(false);
    }
  };

  const stopScheduler = async () => {
    setScheduleLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/schedule/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol })
      });
      const json = await res.json();
      if (json.success) {
        setSuccess(json.message);
        checkScheduleStatus();
      } else {
        setError(json.detail || "Failed to stop schedule");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setScheduleLoading(false);
    }
  };

  const fetchDbRange = async () => {
    try {
      const res = await fetch(`/api/bars/range?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(histInterval)}`);
      const json = await res.json();
      if (json.success) {
        setDbRange({
          min_date: json.min_date,
          max_date: json.max_date,
          count: json.count
        });
      }
    } catch (e) {
      console.error("Failed to fetch DB range", e);
    }
  };

  useEffect(() => {
    if (activeTab === 'historical') {
      fetchDbRange();
    } else if (activeTab === 'chain') {
      checkScheduleStatus();
    }
  }, [symbol, histInterval, activeTab]);

  const fetchHistorical = async () => {
    if (!sessionToken) {
      setHistError("Please provide Session Token.");
      return;
    }
    setHistLoading(true);
    setHistError(null);
    setHistSuccess(null);
    try {
      const res = await fetch(`/api/fetch-historical-bars?session_token=${encodeURIComponent(sessionToken)}&interval=${encodeURIComponent(histInterval)}&from_date=${encodeURIComponent(histFrom)}&to_date=${encodeURIComponent(histTo)}&symbol=${encodeURIComponent(symbol)}`);
      const json = await res.json();
      if (json.status === "success") {
        setHistSuccess(`Successfully saved ${json.count} bars to database!`);
        // Refresh range memory after successful save
        fetchDbRange();
      } else {
        setHistError(json.detail || json.error || "Unknown error occurred.");
      }
    } catch (err: any) {
      setHistError(err.message);
    } finally {
      setHistLoading(false);
    }
  };

  const syncAllConstituents = async () => {
    if (!sessionToken) {
      setHistError("Please provide Session Token.");
      return;
    }
    setConstituentsSyncLoading(true);
    setHistError(null);
    setHistSuccess(null);
    try {
      const res = await fetch(`/api/sync-all-constituents?session_token=${encodeURIComponent(sessionToken)}`);
      const json = await res.json();
      if (json.success) {
        setHistSuccess(`Successfully synchronized ${json.total_saved} bars across Nifty 50 constituents!`);
        fetchDbRange();
      } else {
        setHistError(json.detail || "Failed to sync constituents");
      }
    } catch (err: any) {
      setHistError(err.message);
    } finally {
      setConstituentsSyncLoading(false);
    }
  };


  const downloadCSV = () => {
    if (!fetchedData) return;
    const { rows, spot } = fetchedData;
    
    // Create CSV headers mapping to our NSE format structure slightly
    const headers = ["CALL_OI", "CALL_OICHG", "CALL_LTP", "STRIKE", "PUT_LTP", "PUT_OICHG", "PUT_OI", "IV"];
    
    const csvContent = [
      headers.join(","),
      ...rows.map(r => [
        r.call_oi, r.call_oichg, r.call_ltp, r.strike, r.put_ltp, r.put_oichg, r.put_oi, r.iv
      ].join(","))
    ].join("\n");
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `breeze_chain_${spot}_${expiryDate.split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const saveToDatabase = async () => {
    if (!fetchedData) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/save-breeze-chain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rows: fetchedData.rows,
          spot: fetchedData.spot,
          expiry: expiryDate.split('T')[0]
        })
      });
      const json = await res.json();
      if (json.success) {
        setSuccess(`Successfully saved to database! (Capture ID: ${json.capture_id})`);
        onCaptureSaved?.();
      } else {
        setError(json.detail || "Failed to save to database");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const fetchBreeze = async () => {
    if (!sessionToken || !expiryDate) {
      setError("Please provide both Session Token and Expiry Date.");
      return;
    }
    
    // Save preferences so the user doesn't have to retype them
    localStorage.setItem('breezeSessionToken', sessionToken);
    localStorage.setItem('breezeExpiryDate', expiryDate);
    localStorage.setItem('breezeSymbol', symbol);
    
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch(`/api/fetch-breeze?session_token=${encodeURIComponent(sessionToken)}&expiry_date=${encodeURIComponent(expiryDate)}&symbol=${encodeURIComponent(symbol)}`);
      const json = await res.json();
      if (json.success) {
        setFetchedData({ rows: json.rows, spot: json.spot });
        onBreezeDataLoaded(json.rows, json.spot);
      } else {
        setError(json.detail || json.error || "Unknown error occurred.");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const backfillHistoricalChain = async () => {
    if (!sessionToken || !expiryDate) {
      setError("Please provide both Session Token and Expiry Date.");
      return;
    }
    
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch(`/api/backfill-breeze-historical?session_token=${encodeURIComponent(sessionToken)}&expiry_date=${encodeURIComponent(expiryDate)}&symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(backfillInterval)}&start_date=${encodeURIComponent(backfillStart)}&end_date=${encodeURIComponent(backfillEnd)}`);
      const json = await res.json();
      if (json.success) {
        setSuccess(`Successfully backfilled and saved ${json.snapshots_saved} option chain captures (frequency: ${backfillInterval}) from ${backfillStart} to ${backfillEnd}!`);
        onCaptureSaved?.();
      } else {
        setError(json.detail || json.error || "Unknown error occurred.");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const runUnifiedSync = async () => {
    if (!sessionToken) {
      setUnifiedError("Breeze Session Token is required.");
      return;
    }
    setUnifiedLoading(true);
    setUnifiedError(null);
    setUnifiedSuccess(null);
    setUnifiedLogs(["Initiating connection verification..."]);

    localStorage.setItem('breezeSessionToken', sessionToken);
    localStorage.setItem('breezeExpiryDate', expiryDate);
    localStorage.setItem('breezeSymbol', symbol);

    try {
      const res = await fetch('/api/sync-all-data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          breeze_session_token: sessionToken,
          expiry_date: expiryDate,
          symbol: symbol,
          interval: backfillInterval,
          start_date: backfillStart,
          end_date: backfillEnd
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setUnifiedLogs(data.logs || []);
        setUnifiedSuccess("All stock, commodity, and option chain data synced successfully! 🎉");
        onCaptureSaved?.();
      } else {
        setUnifiedError(data.detail || "Unified Sync failed. Check credentials and retry.");
        if (data.logs) {
          setUnifiedLogs(data.logs);
        }
      }
    } catch (e: any) {
      setUnifiedError(e.message);
    } finally {
      setUnifiedLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm mt-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-xl font-black text-slate-800 flex items-center gap-2">
            <DownloadCloud className="w-6 h-6 text-indigo-600" /> Unified Database Sync Control
          </h3>
          <p className="text-sm text-slate-500 mt-1">Configure credentials and sync all stocks, commodities, and option chain snapshots sequentially.</p>
        </div>
      </div>
      
      {/* Global Connection Settings */}
      <div className="bg-slate-50 border border-slate-100 rounded-2xl p-6 mb-8">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-4">API Connections & Credentials</h4>
        <div className="grid grid-cols-1 gap-6">
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Breeze Session Token (apisession)</label>
            <input 
              type="text" 
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
              placeholder="e.g. 56225492"
              value={sessionToken}
              onChange={(e) => setSessionToken(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* Sync configuration settings */}
      <div className="border border-slate-200 rounded-2xl p-6 mb-8 space-y-6">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">Sync Configurations</h4>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Market Symbol</label>
            <select
              value={symbol}
              onChange={(e) => {
                setSymbol(e.target.value);
                localStorage.setItem('breezeSymbol', e.target.value);
              }}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white text-slate-700 font-bold"
            >
              {symbols.map(sym => (
                <option key={sym} value={sym}>{sym}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Expiry Date (Exchange)</label>
            <select
              value={expiryDate}
              onChange={(e) => setExpiryDate(e.target.value)}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white text-slate-700 font-bold"
            >
              {expiryOptions.map(exp => (
                <option key={exp} value={exp}>
                  {new Date(exp).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })} ({exp.split('T')[0]})
                </option>
              ))}
              {/* Fallback if list hasn't loaded yet */}
              {expiryOptions.length === 0 && (
                <option value={expiryDate}>{expiryDate}</option>
              )}
            </select>
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Backfill Frequency</label>
            <select 
              value={backfillInterval}
              onChange={(e) => setBackfillInterval(e.target.value)}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              <option value="1minute">1 Minute (Recommended)</option>
              <option value="5minute">5 Minutes</option>
              <option value="30minute">30 Minutes</option>
              <option value="1day">1 Day</option>
            </select>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t border-slate-100">
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Backfill Start Date</label>
            <input 
              type="date"
              value={backfillStart}
              onChange={(e) => setBackfillStart(e.target.value)}
              className="w-full px-4 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white text-xs"
            />
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1">Backfill End Date</label>
            <input 
              type="date"
              value={backfillEnd}
              onChange={(e) => setBackfillEnd(e.target.value)}
              className="w-full px-4 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white text-xs"
            />
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-4">
        <button
          onClick={runUnifiedSync}
          disabled={unifiedLoading}
          className={`w-full py-4 rounded-xl text-base font-black text-white transition shadow-lg flex items-center justify-center gap-2
            ${unifiedLoading ? 'bg-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 shadow-indigo-500/30'}`}
        >
          {unifiedLoading ? 'Synchronizing Database...' : 'Run Unified Database Sync'}
        </button>

        {unifiedLogs.length > 0 && (
          <div className="bg-slate-900 text-slate-300 font-mono text-xs p-4 rounded-xl space-y-1.5 max-h-60 overflow-y-auto mt-4 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-semibold mb-2 border-b border-slate-800 pb-1">Sync Process Logs</div>
            {unifiedLogs.map((log, idx) => (
              <div key={idx} className={log.startsWith("SUCCESS") ? "text-emerald-400" : log.startsWith("WARNING") ? "text-amber-400" : "text-slate-300"}>
                &gt; {log}
              </div>
            ))}
          </div>
        )}

        {unifiedError && (
          <div className="bg-rose-50 text-rose-700 p-4 rounded-xl flex items-start gap-3 mt-4 text-sm font-medium border border-rose-100">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <p>{unifiedError}</p>
          </div>
        )}

        {unifiedSuccess && (
          <div className="bg-emerald-50 text-emerald-700 p-4 rounded-xl flex items-start gap-3 mt-4 text-sm font-medium border border-emerald-100">
            <p>{unifiedSuccess}</p>
          </div>
        )}
      </div>

      {/* Periodic Capture Scheduling */}
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-6 mt-8">
        <h4 className="text-sm font-bold text-slate-800 flex items-center gap-2 mb-2">
          <Clock className="w-5 h-5 text-indigo-500" /> Periodic Live Captures (Background Daemon)
        </h4>
        <p className="text-xs text-slate-500 mb-4">
          Enable a background process to automatically capture and append live option chain snapshots to the database every N minutes during trading hours.
        </p>
        
        <div className="flex flex-col md:flex-row items-end gap-6">
          <div className="w-full md:w-1/3">
            <label className="block text-xs font-semibold text-slate-600 mb-1">Periodic Interval</label>
            <select
              value={scheduleInterval}
              onChange={(e) => setScheduleInterval(Number(e.target.value))}
              disabled={scheduleActive}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white text-sm font-semibold"
            >
              <option value={1}>Every 1 Minute</option>
              <option value={3}>Every 3 Minutes</option>
              <option value={5}>Every 5 Minutes</option>
              <option value={15}>Every 15 Minutes</option>
            </select>
          </div>

          <div className="w-full md:w-auto">
            {scheduleActive ? (
              <button
                onClick={stopScheduler}
                disabled={scheduleLoading}
                className="w-full px-6 py-2.5 bg-red-600 hover:bg-red-700 text-white text-sm font-bold rounded-xl transition shadow-sm"
              >
                {scheduleLoading ? "Stopping..." : "Stop Auto-Sync"}
              </button>
            ) : (
              <button
                onClick={startScheduler}
                disabled={scheduleLoading}
                className="w-full px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-xl transition shadow-sm"
              >
                {scheduleLoading ? "Start Auto-Sync" : "Start Auto-Sync"}
              </button>
            )}
          </div>
        </div>

        {scheduleActive && scheduleStartedAt && (
          <div className="mt-4 flex items-center gap-2 text-xs text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-xl p-3">
            <span className="w-2.5 h-2.5 bg-emerald-500 rounded-full animate-pulse"></span>
            <span>
              Background Daemon Active: capturing option chain every <strong>{scheduleInterval} minutes</strong>. (Started: {new Date(scheduleStartedAt).toLocaleTimeString()})
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
