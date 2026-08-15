import React, { useState, useEffect } from 'react';
import { Activity, DownloadCloud, Database, RefreshCcw, AlertTriangle } from 'lucide-react';

interface DailyPrice {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export const NiftyIndexPanel: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<DailyPrice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [sessionToken, setSessionToken] = useState<string>(() => localStorage.getItem('breezeSessionToken') || '');
  const [interval, setInterval] = useState<string>('1day');

  // Load from DB on mount
  useEffect(() => {
    loadFromDB();
  }, []);

  const loadFromDB = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/nifty-history-db?limit=365');
      const json = await res.json();
      if (json.success) {
        setData(json.data);
        if (json.data.length === 0) {
          setError("Local database is empty. Please fetch from ICICI.");
        }
      } else {
        setError(json.detail || "Failed to load from DB");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchFromICICI = async () => {
    if (!sessionToken) {
      setError("Please provide a Session Token (apisession).");
      return;
    }
    
    localStorage.setItem('breezeSessionToken', sessionToken);
    setLoading(true);
    setError(null);
    setSuccess(null);
    
    // Fetch last 30 days
    const now = new Date();
    // Breeze API requires strict format like 2026-07-03T06:00:00.000Z
    // The standard toISOString() has ms like .123Z which causes "The string did not match the expected pattern"
    const pad = (n: number) => n.toString().padStart(2, '0');
    const formatBreezeDate = (d: Date) => 
      `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T06:00:00.000Z`;

    const toDate = formatBreezeDate(now);
    
    const fromDateObj = new Date(now);
    fromDateObj.setDate(fromDateObj.getDate() - 30);
    const fromDate = formatBreezeDate(fromDateObj);
    
    try {
      const res = await fetch(`/api/nifty-history?session_token=${encodeURIComponent(sessionToken)}&from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}&interval=${interval}`);
      const json = await res.json();
      if (json.success) {
        // Merge with existing data uniquely by date
        const newData = json.data as DailyPrice[];
        const merged = [...newData];
        
        // Add old data that isn't in new data
        const newDates = new Set(newData.map(d => d.date.split(' ')[0]));
        for (const old of data) {
          if (!newDates.has(old.date.split(' ')[0])) {
            merged.push(old);
          }
        }
        
        // Sort descending by date for display
        merged.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
        setData(merged);
        setSuccess(`Fetched ${newData.length} days from ICICI.`);
      } else {
        setError(json.detail || "Failed to fetch from ICICI");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const saveToDB = async () => {
    if (data.length === 0) return;
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/save-nifty-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ records: data })
      });
      const json = await res.json();
      if (json.success) {
        setSuccess("Successfully saved daily prices to the database!");
      } else {
        setError(json.detail || "Failed to save to database");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  
  // Calculate max/min for the sparkline chart
  const closes = data.map(d => d.close).reverse(); // chronological for chart
  const minC = Math.min(...closes) * 0.99;
  const maxC = Math.max(...closes) * 1.01;
  const range = maxC - minC || 1;

  return (
    <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm mt-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-500" /> Nifty Index Time Series
          </h3>
          <p className="text-sm text-slate-500 mt-1">Historical Daily OHLC data for the NIFTY index.</p>
        </div>
      </div>
      
      <div className="flex flex-col gap-4 mb-6">
        <div className="flex items-center gap-4">
          <div className="w-64">
            <label className="block text-sm font-semibold text-slate-700 mb-1">ICICI Session Token</label>
            <input 
              type="text" 
              className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
              placeholder="e.g. 56191246"
              value={sessionToken}
              onChange={(e) => setSessionToken(e.target.value)}
            />
          </div>
          <div className="w-48">
            <label className="block text-sm font-semibold text-slate-700 mb-1">Frequency</label>
            <select 
              className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm bg-white"
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
            >
              <option value="1day">1 Day</option>
              <option value="1minute">1 Minute</option>
              <option value="15minute">15 Minutes</option>
            </select>
          </div>
          <div className="flex gap-2 self-end mb-0.5">
            <button
              onClick={fetchFromICICI}
              disabled={loading}
              className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 transition flex items-center gap-2"
            >
              <DownloadCloud className="w-4 h-4" /> Fetch ICICI
            </button>
            <button
              onClick={saveToDB}
              disabled={loading || data.length === 0}
              className="px-4 py-2 rounded-lg text-sm font-bold text-emerald-700 bg-emerald-100 hover:bg-emerald-200 transition flex items-center gap-2"
            >
              <Database className="w-4 h-4" /> Save to DB
            </button>
            <button
              onClick={loadFromDB}
              disabled={loading}
              className="px-4 py-2 rounded-lg text-sm font-bold text-slate-700 bg-slate-100 hover:bg-slate-200 transition flex items-center gap-2"
            >
              <RefreshCcw className="w-4 h-4" /> Reload DB
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 text-red-700 p-4 rounded-xl flex items-start gap-3 mt-2 text-sm font-medium border border-red-100">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <p>{error}</p>
          </div>
        )}
        
        {success && (
          <div className="bg-emerald-50 text-emerald-700 p-4 rounded-xl flex items-start gap-3 mt-2 text-sm font-medium border border-emerald-100">
            <p>{success}</p>
          </div>
        )}
      </div>

      {data.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <div className="border rounded-xl overflow-hidden shadow-sm">
              <table className="w-full text-sm text-left">
                <thead className="bg-slate-50 border-b text-slate-600">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Date</th>
                    <th className="px-4 py-3 font-semibold text-right">Open</th>
                    <th className="px-4 py-3 font-semibold text-right">High</th>
                    <th className="px-4 py-3 font-semibold text-right">Low</th>
                    <th className="px-4 py-3 font-semibold text-right">Close</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.slice(0, 15).map((row, i) => (
                    <tr key={i} className="hover:bg-slate-50">
                      <td className="px-4 py-2.5 text-slate-900 font-medium whitespace-nowrap">
                        {row.date.split(' ')[0]}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-600">{row.open.toFixed(2)}</td>
                      <td className="px-4 py-2.5 text-right text-emerald-600">{row.high.toFixed(2)}</td>
                      <td className="px-4 py-2.5 text-right text-red-600">{row.low.toFixed(2)}</td>
                      <td className="px-4 py-2.5 text-right font-bold text-slate-800">{row.close.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.length > 15 && (
                <div className="px-4 py-2 bg-slate-50 text-xs text-center text-slate-500 font-medium">
                  Showing 15 of {data.length} records
                </div>
              )}
            </div>
          </div>
          
          <div className="bg-slate-50 rounded-xl p-6 border flex flex-col items-center justify-center">
            <h4 className="text-sm font-bold text-slate-600 mb-6 text-center w-full">30-Day Trend (Close)</h4>
            {closes.length > 1 ? (
              <svg viewBox="0 0 100 40" className="w-full h-32 overflow-visible">
                <polyline
                  fill="none"
                  stroke="#3b82f6"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  points={closes.map((c, i) => `${(i / (closes.length - 1)) * 100},${40 - ((c - minC) / range) * 40}`).join(' ')}
                />
              </svg>
            ) : (
              <p className="text-sm text-slate-400">Not enough data to plot</p>
            )}
            <div className="flex justify-between w-full text-xs text-slate-400 font-medium mt-4">
              <span>{data[data.length-1]?.date.split(' ')[0]}</span>
              <span>{data[0]?.date.split(' ')[0]}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
