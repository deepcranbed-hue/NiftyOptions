import re

with open("src/components/BreezeSyncPanel.tsx", "r") as f:
    code = f.read()

# Add states for historical data
state_injection = """
  const [histInterval, setHistInterval] = useState<string>("1minute");
  const [histFrom, setHistFrom] = useState<string>(() => {
    const d = new Date(); d.setDate(d.getDate() - 4); return d.toISOString();
  });
  const [histTo, setHistTo] = useState<string>(() => new Date().toISOString());
  const [histLoading, setHistLoading] = useState(false);
  const [histSuccess, setHistSuccess] = useState<string | null>(null);
  const [histError, setHistError] = useState<string | null>(null);

  const fetchHistorical = async () => {
    if (!sessionToken) {
      setHistError("Please provide Session Token.");
      return;
    }
    setHistLoading(true);
    setHistError(null);
    setHistSuccess(null);
    try {
      const res = await fetch(`/api/fetch-historical-bars?session_token=${encodeURIComponent(sessionToken)}&interval=${encodeURIComponent(histInterval)}&from_date=${encodeURIComponent(histFrom)}&to_date=${encodeURIComponent(histTo)}`);
      const json = await res.json();
      if (json.status === "success") {
        setHistSuccess(`Successfully saved ${json.count} bars to database!`);
      } else {
        setHistError(json.detail || json.error || "Unknown error occurred.");
      }
    } catch (err: any) {
      setHistError(err.message);
    } finally {
      setHistLoading(false);
    }
  };
"""

code = code.replace("const [saving, setSaving] = useState(false);", "const [saving, setSaving] = useState(false);\n" + state_injection)


# Add UI
ui_injection = """
      {/* Historical Data Sync */}
      <div className="mt-10 pt-6 border-t border-slate-200">
        <h3 className="text-md font-bold text-slate-800 flex items-center gap-2 mb-4">
          <DownloadCloud className="w-5 h-5 text-indigo-500" /> Historical Price Sync (Chart Data)
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">Interval</label>
            <select 
              value={histInterval}
              onChange={(e) => setHistInterval(e.target.value)}
              className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="1minute">1 Minute (Resampled to 5m/15m)</option>
              <option value="1day">1 Day</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">From Date (ISO)</label>
            <input 
              type="text" 
              className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={histFrom}
              onChange={(e) => setHistFrom(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">To Date (ISO)</label>
            <input 
              type="text" 
              className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={histTo}
              onChange={(e) => setHistTo(e.target.value)}
            />
          </div>
        </div>

        <button
          onClick={fetchHistorical}
          disabled={histLoading}
          className={`px-6 py-2.5 rounded-xl text-sm font-bold text-white transition shadow-md flex items-center gap-2
            ${histLoading ? 'bg-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 hover:shadow-indigo-500/25'}`}
        >
          {histLoading ? 'Downloading...' : 'Download & Save Bars'}
        </button>

        {histError && (
          <div className="bg-red-50 text-red-700 p-4 rounded-xl flex items-start gap-3 mt-4 text-sm font-medium border border-red-100">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <p>{histError}</p>
          </div>
        )}
        
        {histSuccess && (
          <div className="bg-emerald-50 text-emerald-700 p-4 rounded-xl flex items-start gap-3 mt-4 text-sm font-medium border border-emerald-100">
            <p>{histSuccess}</p>
          </div>
        )}
      </div>
"""

code = code.replace("    </div>\n  );\n};", ui_injection + "\n    </div>\n  );\n};")

with open("src/components/BreezeSyncPanel.tsx", "w") as f:
    f.write(code)

print("done")
