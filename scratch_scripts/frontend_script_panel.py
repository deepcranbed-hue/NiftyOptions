import re

with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    content = f.read()

props_interface_injection = """
  uploadFile?: File | null;
  setUploadFile?: (f: File | null) => void;
  uploadSpot?: number;
  setUploadSpot?: (n: number) => void;
  uploadDays?: number;
  setUploadDays?: (n: number) => void;
  onUploadPipeline?: () => void;
"""
content = content.replace("onRunPipeline: () => void;", "onRunPipeline: () => void;" + props_interface_injection)

props_destructure_injection = """
  uploadFile,
  setUploadFile,
  uploadSpot,
  setUploadSpot,
  uploadDays,
  setUploadDays,
  onUploadPipeline,
"""
content = content.replace("  onRunPipeline,", "  onRunPipeline,\n" + props_destructure_injection)

csv_ui = """
      {/* CSV Upload Panel */}
      <div className="bg-slate-900 text-white p-6 rounded-2xl shadow-lg border border-slate-800 space-y-4">
        <div className="flex items-center gap-2 text-indigo-400 text-sm font-bold uppercase tracking-wider">
          <Database className="w-5 h-5" /> Offline Chain Injection (NSE CSV)
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div className="flex flex-col gap-1 md:col-span-2">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Option Chain CSV File</label>
            <input 
              type="file" 
              accept=".csv"
              onChange={(e) => setUploadFile && e.target.files && setUploadFile(e.target.files[0])}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm file:mr-4 file:py-1 file:px-3 file:rounded-full file:border-0 file:text-xs file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Spot Price</label>
            <input
              type="number"
              step="0.05"
              value={uploadSpot || ''}
              onChange={(e) => setUploadSpot && setUploadSpot(parseFloat(e.target.value) || 0)}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
              placeholder="e.g. 24050.5"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase font-bold text-slate-400 px-1">Days to Expiry</label>
            <input
              type="number"
              step="0.01"
              value={uploadDays || ''}
              onChange={(e) => setUploadDays && setUploadDays(parseFloat(e.target.value) || 0)}
              className="bg-slate-800 text-white p-2 rounded-lg border border-slate-700 text-sm focus:border-indigo-500 outline-none"
              placeholder="e.g. 2.0 or 0.3"
            />
          </div>
        </div>
        
        <button
          onClick={onUploadPipeline}
          className="w-full py-3 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-bold tracking-wide transition-colors shadow-lg flex items-center justify-center gap-2"
        >
          <Database className="w-4 h-4" /> Load CSV & Run Pipeline
        </button>
      </div>
"""

content = content.replace("    <div className=\"space-y-6\">", "    <div className=\"space-y-6\">\n" + csv_ui)

import_injection = "import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart, Sliders, ChevronDown, ChevronRight, Database } from 'lucide-react';"
content = re.sub(r"import \{ Sparkles.*\} from 'lucide-react';", import_injection, content)

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(content)
