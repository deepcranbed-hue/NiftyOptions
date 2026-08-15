import React, { useState, useEffect } from 'react';
import { Briefcase, Trash2, ArrowRight, TrendingUp, TrendingDown, RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

interface Props {
  captures: any[];
}

export const PortfolioPanel: React.FC<Props> = ({ captures }) => {
  const [positions, setPositions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCaptureId, setSelectedCaptureId] = useState<number>(captures[0]?.capture_id || 0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleExpand = (id: string) => {
    setExpanded(prev => ({ ...prev, [id]: !prev[id] }));
  };

  const fetchPositions = async () => {
    setLoading(true);
    try {
      let res;
      if (!selectedCaptureId) {
        res = await fetch(`http://127.0.0.1:8000/api/portfolio/list`);
      } else {
        res = await fetch(`http://127.0.0.1:8000/api/portfolio/value?capture_id=${selectedCaptureId}`);
      }
      const data = await res.json();
      if (data.success) {
        // If from list, valuation won't exist. Add dummy valuation.
        const positions = data.positions.map((p: any) => ({
          ...p,
          valuation: p.valuation || { pnl_rupees: 0, pnl_pts: 0, value_a: 0, value_b: 0, error: !selectedCaptureId ? "No snapshot selected" : undefined }
        }));
        setPositions(positions);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPositions();
  }, [selectedCaptureId]);

  const closePosition = async (posId: string) => {
    if (!confirm("Close this position?")) return;
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/portfolio/close/${posId}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) fetchPositions();
    } catch (e) {
      console.error(e);
    }
  };

  const totalPnl = positions.reduce((acc, p) => acc + (p.valuation?.pnl_rupees || 0), 0);

  return (
    <div className="space-y-6">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-lg">
        <div className="flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-3 bg-indigo-500/10 rounded-xl">
              <Briefcase className="w-6 h-6 text-indigo-400" />
            </div>
            <div>
              <h2 className="text-xl font-black text-slate-100">Live Portfolio</h2>
              <p className="text-sm text-slate-400">Track and manage your traded options structures.</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex flex-col">
              <label className="text-[10px] uppercase font-bold text-slate-500 px-1 mb-1">Value Against Snapshot</label>
              <select 
                value={selectedCaptureId} 
                onChange={(e) => setSelectedCaptureId(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 text-sm text-slate-200 rounded-lg px-3 py-2 outline-none focus:border-indigo-500"
              >
                {captures.map(c => (
                  <option key={c.capture_id} value={c.capture_id}>{c.expiry} — {new Date(c.captured_at).toLocaleString()}</option>
                ))}
              </select>
            </div>
            <button onClick={fetchPositions} className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors border border-slate-700">
              <RefreshCw className="w-5 h-5 text-slate-400" />
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
          <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-5">
            <span className="text-xs uppercase font-bold text-slate-500">Total Net P&L</span>
            <div className={`text-3xl font-black mt-2 ${totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              ₹{totalPnl.toLocaleString()}
            </div>
          </div>
          <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-5">
            <span className="text-xs uppercase font-bold text-slate-500">Open Positions</span>
            <div className="text-3xl font-black mt-2 text-slate-200">
              {positions.length}
            </div>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="text-center p-12 text-slate-500">Loading portfolio...</div>
      ) : positions.length === 0 ? (
        <div className="text-center p-12 bg-slate-900 border border-slate-800 rounded-2xl">
          <Briefcase className="w-12 h-12 text-slate-700 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-400">No Open Positions</h3>
          <p className="text-slate-500 text-sm mt-1">Trade a structure from the Strategy Suggester or Optimizer to track it here.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {positions.map(pos => {
            const val = pos.valuation;
            if (!val) return null;
            return (
              <div key={pos.id} className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden flex flex-col lg:flex-row">
                <div className="p-6 lg:w-1/3 border-b lg:border-b-0 lg:border-r border-slate-100 bg-slate-50/50 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-600 bg-indigo-100 px-2 py-0.5 rounded">
                        {pos.source}
                      </span>
                      <button onClick={() => closePosition(pos.id)} className="text-rose-500 hover:text-rose-600 hover:bg-rose-50 p-1.5 rounded transition">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                    <button 
                      onClick={() => toggleExpand(pos.id)}
                      className="w-full text-left group flex items-center justify-between"
                    >
                      <div>
                        <h3 className="text-lg font-black text-slate-900 capitalize group-hover:text-indigo-600 transition-colors">
                          {pos.lineage?.family?.replace(/_/g, ' ') || "Custom Structure"} <span className="text-sm font-semibold text-slate-500 normal-case">— {pos.expiry}</span>
                        </h3>
                        {(() => {
                           const entryCap = captures.find(c => c.capture_id === pos.entry_capture_id);
                           return entryCap ? (
                             <div className="text-xs text-slate-500 font-mono mt-1">
                               Bought: {new Date(entryCap.captured_at).toLocaleString()}
                             </div>
                           ) : (
                             <div className="text-xs text-slate-500 font-mono mt-1">
                               Entry Snapshot ID: {pos.entry_capture_id}
                             </div>
                           );
                        })()}
                      </div>
                      {expanded[pos.id] ? (
                        <ChevronDown className="w-5 h-5 text-slate-400 group-hover:text-indigo-500" />
                      ) : (
                        <ChevronRight className="w-5 h-5 text-slate-400 group-hover:text-indigo-500" />
                      )}
                    </button>
                    
                    {expanded[pos.id] && (
                      <>
                        <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
                          {pos.legs.map((l: any, i: number) => {
                             const priceA = val?.entry_prices?.[i] ?? val?.prices_a?.[i];
                             const priceB = val?.prices_b?.[i];
                             return (
                              <div key={i} className="flex justify-between items-center text-sm font-semibold">
                                <span className={l[2] > 0 ? 'text-emerald-600' : 'text-rose-600'}>
                                  {l[2] > 0 ? 'BUY' : 'SELL'} {l[0].toUpperCase()}
                                </span>
                                <div className="flex items-center gap-3">
                                  <span className="font-mono text-slate-700">{l[1]}</span>
                                  {(priceA !== undefined && priceA !== null) && (
                                    <div className="flex items-center gap-1 text-[10px] bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">
                                      <span className="text-slate-400">Entry:</span>
                                      <span className="font-mono text-slate-600">₹{priceA}</span>
                                      <ArrowRight className="w-3 h-3 text-slate-300" />
                                      <span className="text-slate-400">Now:</span>
                                      <span className={`font-mono font-bold ${(priceB !== undefined && priceB !== null) ? 'text-slate-800' : 'text-slate-400'}`}>
                                        {(priceB !== undefined && priceB !== null) ? `₹${priceB}` : 'N/A'}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                        
                        {pos.lineage?.rationale && (
                          <div className="mt-4 bg-white p-3 rounded-lg border border-slate-200 text-xs text-slate-600">
                            <span className="block text-[10px] font-bold uppercase text-slate-400 mb-1">Thesis</span>
                            {pos.lineage.rationale}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
                
                <div className="p-6 lg:w-2/3 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <h4 className="font-bold text-slate-800 flex items-center gap-2">
                        Mark-to-Market P&L
                      </h4>
                      {val.error ? (
                        <div className="text-sm font-bold text-amber-500">
                          {val.error}
                        </div>
                      ) : (
                        <div className={`text-2xl font-black ${val.pnl_rupees >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                          {val.pnl_rupees >= 0 ? '+' : ''}₹{val.pnl_rupees.toLocaleString()}
                        </div>
                      )}
                    </div>
                    
                    {!val.error && (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                        <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                          <span className="block text-[10px] uppercase font-bold text-slate-400">Value Then</span>
                          <span className="font-mono text-sm font-bold text-slate-700">{val.value_a} pts</span>
                        </div>
                        <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                          <span className="block text-[10px] uppercase font-bold text-slate-400">Value Now</span>
                          <span className="font-mono text-sm font-bold text-slate-700">{val.value_b} pts</span>
                        </div>
                        <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                          <span className="block text-[10px] uppercase font-bold text-slate-400">Net Gain</span>
                          <span className={`font-mono text-sm font-bold ${val.pnl_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                            {val.pnl_pts > 0 ? '+' : ''}{val.pnl_pts} pts
                          </span>
                        </div>
                        <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                          <span className="block text-[10px] uppercase font-bold text-slate-400">Lots</span>
                          <span className="font-mono text-sm font-bold text-slate-700">{pos.lots} ({pos.lot_size})</span>
                        </div>
                      </div>
                    )}
                    
                    <div className="space-y-2">
                      {val.read?.map((r: string, idx: number) => (
                        <div key={idx} className="flex gap-2 items-start text-xs text-slate-600">
                          <ArrowRight className="w-3.5 h-3.5 text-indigo-400 shrink-0 mt-0.5" />
                          <span>{r}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
