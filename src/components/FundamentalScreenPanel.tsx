import React, { useState, useEffect } from 'react';
import { ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip as ChartTooltip, Cell } from 'recharts';
import { Activity, Shield, TrendingUp, Cpu, Globe, RefreshCw, X, ArrowUpDown, Info } from 'lucide-react';

interface FundamentalData {
  symbol: string;
  close_price: number;
  close_date: string;
  eps: number;
  book_value_ps: number;
  dividend_ps: number;
  roe: number;
  pe_ratio: number | null;
  pb_ratio: number | null;
  dividend_yield: number;
  iapm_category: string;
  as_of_date: string;
  staleness_days: number;
  volatility: number;
  volatility_status: string;
  momentum: number;
  momentum_status: string;
  source: string;
  pe_ratio_rank: number | null;
  pe_ratio_zscore: number | null;
  pb_ratio_rank: number | null;
  pb_ratio_zscore: number | null;
  dividend_yield_rank: number | null;
  dividend_yield_zscore: number | null;
  roe_rank: number | null;
  roe_zscore: number | null;
  value_z: number;
  quality_z: number;
  momentum_z: number;
  volatility_z: number;
}

export const FundamentalScreenPanel: React.FC = () => {
  const [data, setData] = useState<FundamentalData[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [updating, setUpdating] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [sortField, setSortField] = useState<keyof FundamentalData>('pe_ratio_rank');
  const [sortAsc, setSortAsc] = useState<boolean>(true);
  const [selectedStock, setSelectedStock] = useState<FundamentalData | null>(null);
  const [scatterPreset, setScatterPreset] = useState<'value-quality' | 'momentum-lowvol'>('value-quality');

  const fetchFundamentals = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/fundamentals');
      const json = await res.json();
      if (json.success) {
        setData(json.data);
      } else {
        setError(json.detail || 'Failed to fetch fundamentals.');
      }
    } catch (e) {
      setError('Connection to fundamentals API failed.');
    } finally {
      setLoading(false);
    }
  };

  const triggerUpdate = async () => {
    setUpdating(true);
    try {
      const res = await fetch('/api/fundamentals/update', { method: 'POST' });
      const json = await res.json();
      if (json.success) {
        setData(json.data);
      } else {
        alert(json.detail || 'Update failed.');
      }
    } catch (e) {
      alert('Update request failed.');
    } finally {
      setUpdating(false);
    }
  };

  useEffect(() => {
    fetchFundamentals();
  }, []);

  const handleSort = (field: keyof FundamentalData) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(true);
    }
  };

  // Sort rows, handling null values and negative EPS
  const sortedData = [...data].sort((a, b) => {
    const valA = a[sortField];
    const valB = b[sortField];
    
    if (valA === null || valA === undefined) return 1;
    if (valB === null || valB === undefined) return -1;
    
    if (typeof valA === 'string' && typeof valB === 'string') {
      return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    }
    
    return sortAsc 
      ? (valA as number) - (valB as number) 
      : (valB as number) - (valA as number);
  });

  // Calculate Zone 1 metrics
  const totalCovered = data.length;
  const staleCount = data.filter(d => d.staleness_days > 90).length;
  const oldestDate = data.reduce((oldest, current) => {
    if (!oldest) return current.as_of_date;
    return new Date(current.as_of_date) < new Date(oldest) ? current.as_of_date : oldest;
  }, '');

  // Category Configuration
  const categoriesConfig = [
    { key: 'defensive', label: 'Defensive', icon: <Shield className="w-4 h-4 text-emerald-500" />, desc: 'FMCG, Pharma, Energy' },
    { key: 'interest_sensitive', label: 'Interest-Sensitive', icon: <Activity className="w-4 h-4 text-blue-500" />, desc: 'Banks, Financials, Utilities' },
    { key: 'consumer_durables', label: 'Consumer Durables', icon: <TrendingUp className="w-4 h-4 text-amber-500" />, desc: 'Auto, Luxury Goods' },
    { key: 'capital_goods', label: 'Capital Goods', icon: <Cpu className="w-4 h-4 text-rose-500" />, desc: 'Cement, Construction, Mining' },
    { key: 'global_export', label: 'Global/Export', icon: <Globe className="w-4 h-4 text-violet-500" />, desc: 'IT Services' }
  ];

  // Group data by category
  const groupedData: Record<string, FundamentalData[]> = {};
  data.forEach(d => {
    groupedData[d.iapm_category] = groupedData[d.iapm_category] || [];
    groupedData[d.iapm_category].push(d);
  });

  const getCategoryMedians = (cat: string) => {
    const list = groupedData[cat] || [];
    if (list.length === 0) return { pe: 'N/A', roe: 'N/A' };
    
    const pes = list.map(d => d.pe_ratio).filter((pe): pe is number => pe !== null).sort((a, b) => a - b);
    const roes = list.map(d => d.roe).sort((a, b) => a - b);
    
    const medianPE = pes.length > 0 ? pes[Math.floor(pes.length / 2)].toFixed(1) : '—';
    const medianROE = roes.length > 0 ? roes[Math.floor(roes.length / 2)].toFixed(1) : '—';
    
    return { pe: medianPE, roe: `${medianROE}%` };
  };

  // Color coding category tiles
  const getCategoryColor = (cat: string) => {
    switch (cat) {
      case 'defensive': return 'bg-emerald-50 border-emerald-200 text-emerald-950';
      case 'interest_sensitive': return 'bg-blue-50 border-blue-200 text-blue-950';
      case 'consumer_durables': return 'bg-amber-50 border-amber-200 text-amber-950';
      case 'capital_goods': return 'bg-rose-50 border-rose-200 text-rose-950';
      case 'global_export': return 'bg-violet-50 border-violet-200 text-violet-950';
      default: return 'bg-slate-50 border-slate-200 text-slate-900';
    }
  };

  const getCategoryThemeColor = (cat: string) => {
    switch (cat) {
      case 'defensive': return '#10b981';
      case 'interest_sensitive': return '#3b82f6';
      case 'consumer_durables': return '#f59e0b';
      case 'capital_goods': return '#ef4444';
      case 'global_export': return '#8b5cf6';
      default: return '#64748b';
    }
  };

  // Scatter plot data formatting
  const scatterData = data.map(d => {
    if (scatterPreset === 'value-quality') {
      return {
        x: d.pe_ratio_zscore ?? 0.0,
        y: d.roe_zscore ?? 0.0,
        symbol: d.symbol,
        pe: d.pe_ratio,
        roe: d.roe,
        category: d.iapm_category,
        labelX: 'P/E Z-Score',
        labelY: 'ROE Z-Score'
      };
    } else {
      return {
        x: d.momentum_z,
        y: d.volatility_z,
        symbol: d.symbol,
        pe: d.pe_ratio,
        roe: d.roe,
        category: d.iapm_category,
        labelX: 'Momentum Z-Score',
        labelY: 'Volatility Z-Score'
      };
    }
  });

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 space-y-4">
        <RefreshCw className="w-8 h-8 text-indigo-600 animate-spin" />
        <p className="text-sm font-semibold text-slate-500">Loading fundamental metrics...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-rose-50 border border-rose-200 p-6 rounded-2xl text-rose-950">
        <h4 className="font-bold text-base mb-1">Failed to Load Fundamentals</h4>
        <p className="text-sm">{error}</p>
        <button onClick={fetchFundamentals} className="mt-4 px-4 py-2 bg-rose-100 hover:bg-rose-200 text-rose-800 text-xs font-bold rounded-xl transition">
          Retry Connection
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Zone 1 — Coverage & Freshness Strip */}
      <div className="bg-slate-900 text-white px-6 py-3 rounded-2xl flex flex-wrap items-center justify-between gap-4 shadow-sm border border-slate-800">
        <div className="flex items-center gap-3 text-xs font-bold uppercase tracking-wider text-slate-400">
          <Info className="w-4 h-4 text-slate-400" />
          <span>IAPM Core Coverage:</span>
          <span className="text-white font-mono text-sm">{totalCovered} / 50</span>
          
          <button
            onClick={triggerUpdate}
            disabled={updating}
            className="ml-4 px-3 py-1 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-800 text-white text-[10px] font-black rounded-xl transition flex items-center gap-1.5 uppercase tracking-wider"
          >
            <RefreshCw className={`w-3 h-3 ${updating ? 'animate-spin' : ''}`} />
            {updating ? 'Updating...' : 'Update Metrics'}
          </button>
        </div>
        <div className="flex items-center gap-6 text-xs text-slate-300">
          <div>
            Oldest Update: <span className="font-mono text-white font-bold">{oldestDate || 'N/A'}</span>
          </div>
          <div>
            Stale Entries (&gt;90d): <span className={`font-mono font-bold ${staleCount > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>{staleCount}</span>
          </div>
        </div>
      </div>

      {/* Zone 2 — Sector Rotation Map */}
      <div className="space-y-3">
        <h3 className="text-sm font-black uppercase tracking-wider text-slate-500">
          IAPM Business Cycle & Sector Rotation Map
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          {categoriesConfig.map(cfg => {
            const list = groupedData[cfg.key] || [];
            const medians = getCategoryMedians(cfg.key);
            return (
              <div key={cfg.key} className="bg-white rounded-2xl border border-slate-200 p-4 flex flex-col space-y-3 shadow-sm">
                <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                  <div className="flex items-center gap-2">
                    {cfg.icon}
                    <span className="text-sm font-bold text-slate-800 truncate" title={cfg.label}>{cfg.label}</span>
                  </div>
                </div>
                
                <div className="grid grid-cols-2 gap-2 py-1 bg-slate-50 rounded-lg text-[10px] text-center font-bold text-slate-600">
                  <div>Med P/E: <span className="text-slate-900 font-mono block text-xs">{medians.pe}</span></div>
                  <div>Med ROE: <span className="text-slate-900 font-mono block text-xs">{medians.roe}</span></div>
                </div>

                <div className="flex-1 space-y-2 overflow-y-auto max-h-[220px] pr-1">
                  {list.length > 0 ? (
                    list.map(stock => (
                      <div
                        key={stock.symbol}
                        onClick={() => setSelectedStock(stock)}
                        className={`p-2.5 rounded-xl border cursor-pointer hover:shadow-md transition text-left flex items-center justify-between gap-2 ${getCategoryColor(cfg.key)}`}
                      >
                        <span className="font-bold text-xs">{stock.symbol}</span>
                        <div className="text-[10px] text-right">
                          <span className="block font-mono font-bold">PE: {stock.pe_ratio ? stock.pe_ratio.toFixed(1) : '—'}</span>
                          <span className="text-slate-500 font-semibold">Rank #{stock.pe_ratio_rank || '—'}</span>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="text-[10px] text-slate-400 italic text-center py-6">
                      No constituents loaded
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Zone 4 — Valuation vs Quality Scatter Plot */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm space-y-4">
        <h3 className="text-sm font-black uppercase tracking-wider text-slate-500 flex items-center gap-2">
          IAPM Scatter Matrix (Valuation P/E vs Quality ROE z-scores)
        </h3>
        <div className="flex justify-between items-center bg-slate-50 p-2.5 rounded-xl border border-slate-100 mb-2">
          <p className="text-xs text-slate-500 leading-relaxed max-w-xl">
            Visualizing style factors. Select factor pair to view scatter quadrant alignment:
          </p>
          <div className="flex gap-1.5">
            <button
              onClick={() => setScatterPreset('value-quality')}
              className={`px-3 py-1.5 text-[10px] font-black rounded-lg uppercase tracking-wider transition ${scatterPreset === 'value-quality' ? 'bg-indigo-600 text-white shadow-sm' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}
            >
              Value vs Quality
            </button>
            <button
              onClick={() => setScatterPreset('momentum-lowvol')}
              className={`px-3 py-1.5 text-[10px] font-black rounded-lg uppercase tracking-wider transition ${scatterPreset === 'momentum-lowvol' ? 'bg-indigo-600 text-white shadow-sm' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'}`}
            >
              Momentum vs Low-Vol
            </button>
          </div>
        </div>

        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis 
                type="number" 
                dataKey="x" 
                name={scatterPreset === 'value-quality' ? "P/E z-score (inverted)" : "Momentum z-score"} 
                domain={[-3, 3]} 
                reversed
                stroke="#64748b"
                fontSize={10}
              />
              <YAxis 
                type="number" 
                dataKey="y" 
                name={scatterPreset === 'value-quality' ? "ROE z-score" : "Volatility z-score (inverted)"} 
                domain={[-3, 3]} 
                stroke="#64748b"
                fontSize={10}
              />
              <ChartTooltip 
                cursor={{ strokeDasharray: '3 3' }}
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const dataPoint = payload[0].payload;
                    return (
                      <div className="bg-slate-900 text-white p-3 rounded-xl shadow-xl text-xs border border-slate-800 space-y-1">
                        <div className="font-bold text-sm border-b border-slate-800 pb-1 mb-1">{dataPoint.symbol}</div>
                        <div>Category: <span className="capitalize text-slate-400 font-bold">{dataPoint.category.replace(/_/g, ' ')}</span></div>
                        {scatterPreset === 'value-quality' ? (
                          <>
                            <div>P/E: <span className="font-mono text-emerald-400 font-bold">{dataPoint.pe ? dataPoint.pe.toFixed(1) : '—'}</span></div>
                            <div>ROE: <span className="font-mono text-blue-400 font-bold">{dataPoint.roe.toFixed(1)}%</span></div>
                          </>
                        ) : (
                          <>
                            <div>Factor X: <span className="font-mono text-indigo-400 font-bold">{dataPoint.x.toFixed(2)}</span></div>
                            <div>Factor Y: <span className="font-mono text-indigo-400 font-bold">{dataPoint.y.toFixed(2)}</span></div>
                          </>
                        )}
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Scatter name="Constituents" data={scatterData}>
                {scatterData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={getCategoryThemeColor(entry.category)} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Zone 3 — Screening Table */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-black uppercase tracking-wider text-slate-500">
            Valuation Screen & Financial Ratios Grid
          </h3>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-slate-600 font-bold uppercase tracking-wider">
                <th className="px-6 py-3.5 cursor-pointer hover:bg-slate-100" onClick={() => handleSort('symbol')}>
                  Symbol <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 cursor-pointer hover:bg-slate-100" onClick={() => handleSort('iapm_category')}>
                  IAPM Category <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('pe_ratio')}>
                  P/E Ratio <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('pb_ratio')}>
                  P/B Ratio <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('dividend_yield')}>
                  Div Yield <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('roe')}>
                  ROE % <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('volatility')}>
                  Vol (Ann %) <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-right cursor-pointer hover:bg-slate-100" onClick={() => handleSort('pe_ratio_rank')}>
                  PE Sector Rank <ArrowUpDown className="inline w-3 h-3 ml-1 text-slate-400" />
                </th>
                <th className="px-6 py-3.5 text-center">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sortedData.map(row => (
                <tr key={row.symbol} className="hover:bg-slate-50 transition cursor-pointer" onClick={() => setSelectedStock(row)}>
                  <td className="px-6 py-3 font-bold text-slate-900">{row.symbol}</td>
                  <td className="px-6 py-3">
                    <span className="capitalize px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700">
                      {row.iapm_category.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="px-6 py-3 text-right font-mono font-bold">
                    {row.pe_ratio === null ? (
                      <span className="text-rose-600">—(loss)</span>
                    ) : (
                      row.pe_ratio.toFixed(2)
                    )}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-700">
                    {row.pb_ratio ? row.pb_ratio.toFixed(2) : '—'}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-700">
                    {row.dividend_yield ? `${row.dividend_yield.toFixed(2)}%` : '—'}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-700">{row.roe.toFixed(2)}%</td>
                  <td className="px-6 py-3 text-right font-mono text-slate-700">
                    {row.volatility > 0 ? `${row.volatility.toFixed(1)}%` : '—'}
                  </td>
                  <td className="px-6 py-3 text-right font-bold text-indigo-700 font-mono">
                    #{row.pe_ratio_rank || '—'}
                  </td>
                  <td className="px-6 py-3 text-center text-[10px] text-slate-400 font-medium">{row.as_of_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Stock Detail Side Drawer */}
      {selectedStock && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex justify-end">
          <div className="w-full max-w-md bg-white h-full p-6 shadow-2xl overflow-y-auto flex flex-col space-y-6 animate-in slide-in-from-right duration-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4">
              <div>
                <h2 className="text-xl font-black text-slate-900">{selectedStock.symbol}</h2>
                <span className="text-[10px] uppercase font-bold text-slate-400">Constituent Profile</span>
              </div>
              <button onClick={() => setSelectedStock(null)} className="p-2 hover:bg-slate-100 rounded-xl transition">
                <X className="w-5 h-5 text-slate-500" />
              </button>
            </div>

            <div className="space-y-4">
              <div className="bg-slate-50 p-4 rounded-2xl border border-slate-100 space-y-3">
                <h4 className="text-xs font-black uppercase text-slate-400">Financial Ratios</h4>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Computed P/E</span>
                    <span className="font-mono text-base font-bold text-slate-800">
                      {selectedStock.pe_ratio ? selectedStock.pe_ratio.toFixed(2) : '— (Loss)'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Computed P/B</span>
                    <span className="font-mono text-base font-bold text-slate-800">
                      {selectedStock.pb_ratio ? selectedStock.pb_ratio.toFixed(2) : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Dividend Yield</span>
                    <span className="font-mono text-base font-bold text-slate-800">
                      {selectedStock.dividend_yield ? `${selectedStock.dividend_yield.toFixed(2)}%` : '0.00%'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Return on Equity</span>
                    <span className="font-mono text-base font-bold text-slate-800">{selectedStock.roe.toFixed(2)}%</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Annualized Volatility</span>
                    <span className="font-mono text-base font-bold text-indigo-700">
                      {selectedStock.volatility > 0 ? `${selectedStock.volatility.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="bg-slate-50 p-4 rounded-2xl border border-slate-100 space-y-3">
                <h4 className="text-xs font-black uppercase text-slate-400">Within-Sector Rank & z-score</h4>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">PE Rank</span>
                    <span className="font-mono text-sm font-bold text-indigo-700">#{selectedStock.pe_ratio_rank || '—'}</span>
                    <span className="text-[9px] text-slate-400 block">z: {selectedStock.pe_ratio_zscore ? selectedStock.pe_ratio_zscore.toFixed(2) : '—'}</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">PB Rank</span>
                    <span className="font-mono text-sm font-bold text-indigo-700">#{selectedStock.pb_ratio_rank || '—'}</span>
                    <span className="text-[9px] text-slate-400 block">z: {selectedStock.pb_ratio_zscore ? selectedStock.pb_ratio_zscore.toFixed(2) : '—'}</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">Div Yield Rank</span>
                    <span className="font-mono text-sm font-bold text-indigo-700">#{selectedStock.dividend_yield_rank || '—'}</span>
                    <span className="text-[9px] text-slate-400 block">z: {selectedStock.dividend_yield_zscore ? selectedStock.dividend_yield_zscore.toFixed(2) : '—'}</span>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase text-slate-500 block">ROE Rank</span>
                    <span className="font-mono text-sm font-bold text-indigo-700">#{selectedStock.roe_rank || '—'}</span>
                    <span className="text-[9px] text-slate-400 block">z: {selectedStock.roe_zscore ? selectedStock.roe_zscore.toFixed(2) : '—'}</span>
                  </div>
                </div>
              </div>

              <div className="space-y-2 text-xs text-slate-500 p-2">
                <div className="flex justify-between">
                  <span>IAPM Category:</span>
                  <span className="font-bold text-slate-800 capitalize">{selectedStock.iapm_category.replace(/_/g, ' ')}</span>
                </div>
                <div className="flex justify-between">
                  <span>Last Close Price:</span>
                  <span className="font-mono text-slate-800 font-bold">₹{selectedStock.close_price.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Data As Of:</span>
                  <span className="font-bold text-slate-800">{selectedStock.as_of_date}</span>
                </div>
                <div className="flex justify-between">
                  <span>Data Source:</span>
                  <span className="font-semibold text-slate-800">{selectedStock.source}</span>
                </div>
                <div className="flex justify-between">
                  <span>Staleness:</span>
                  <span className="font-bold text-slate-800">{selectedStock.staleness_days} days</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
