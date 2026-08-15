import React, { useState } from 'react';
import { DownloadCloud, AlertTriangle } from 'lucide-react';

export const NSESyncPanel: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("NIFTY");

  const downloadChain = async () => {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await fetch(`/api/download-nse?symbol=${encodeURIComponent(symbol)}`);
      const json = await res.json();
      if (json.success) {
        setData(json.data);
      } else {
        setError(json.error || "Unknown error occurred.");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-black text-slate-800 flex items-center gap-2">
            <DownloadCloud className="w-5 h-5 text-indigo-500" /> NSE Option Chain Sync
          </h3>
          <p className="text-sm text-slate-500 mt-1">Download the live option chain directly from NSE servers.</p>
        </div>
        <div className="flex items-center gap-4">
          <input 
            type="text" 
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="e.g. NIFTY, RELIANCE"
            className="px-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
          <button
            onClick={downloadChain}
            disabled={loading}
            className={`px-6 py-2.5 rounded-xl text-sm font-bold text-white transition shadow-lg flex items-center gap-2
              ${loading ? 'bg-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 shadow-indigo-500/30'}`}
          >
            {loading ? 'Downloading...' : 'Fetch Live Chain'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-xl flex items-start gap-3 mt-4">
          <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" />
          <div>
            <p className="font-bold">Download Failed</p>
            <p className="text-sm mt-1">{error}</p>
            <p className="text-xs text-rose-500 mt-2">Note: If you see a 404 or 401 error, NSE is blocking automated requests from this environment's IP address. You will need to run the python script locally to fetch the CSV.</p>
          </div>
        </div>
      )}

      {data && data.records && (
        <div className="bg-emerald-50 border border-emerald-200 p-4 rounded-xl mt-4">
          <p className="font-bold text-emerald-800">Successfully fetched {data.records.data?.length} records!</p>
          <div className="mt-4 max-h-[300px] overflow-auto bg-white border border-emerald-100 rounded-lg p-2 text-xs">
            <pre className="text-emerald-900">{JSON.stringify(data.records.data.slice(0, 2), null, 2)}</pre>
            <p className="text-emerald-600 font-bold mt-2 text-center italic">... (showing first 2 records only) ...</p>
          </div>
        </div>
      )}
    </div>
  );
};
