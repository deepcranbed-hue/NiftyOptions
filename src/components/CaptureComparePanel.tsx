import React, { useState } from 'react';
import { RefreshCw, Database, Layers, ArrowRight, Plus, X, BarChart as BarChartIcon, Info, TrendingUp, DollarSign, Activity } from 'lucide-react';
import DatePicker from 'react-datepicker';
import 'react-datepicker/dist/react-datepicker.css';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, ReferenceLine, CartesianGrid, Legend } from 'recharts';

interface Props {
  captures: any[];
}

export const CaptureComparePanel: React.FC<Props> = ({ captures }) => {
  const [capA, setCapA] = useState<string>('');
  const [capB, setCapB] = useState<string>('');
  // New state for entry dates and expiry dates (Date objects)
  const [entryDateA, setEntryDateA] = useState<Date | null>(null);
  const [entryDateB, setEntryDateB] = useState<Date | null>(null);
  const [expiryDateA, setExpiryDateA] = useState<Date | null>(null);
  const [expiryDateB, setExpiryDateB] = useState<Date | null>(null);
  const [compareResult, setCompareResult] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Strategy Builder State
  const [legs, setLegs] = useState<[string, number, number][]>([]);
  const [legSide, setLegSide] = useState<'call'|'put'>('call');
  const [legStrike, setLegStrike] = useState<number | ''>('');
  const [legSign, setLegSign] = useState<number>(1);

  const handleAddLeg = () => {
    if (!legStrike) return;
    setLegs([...legs, [legSide, Number(legStrike), legSign]]);
    setLegStrike('');
  };

  const handleRemoveLeg = (idx: number) => {
    setLegs(legs.filter((_, i) => i !== idx));
  };

  const findSnapshot = (date: Date | null, expiry: Date | null) => {
    // If an entry datetime is provided, match up to minute precision (YYYY‑MM‑DDTHH:MM)
    if (date) {
      const target = date.toISOString().slice(0, 16);
      return captures.find(
        c => new Date(c.captured_at).toISOString().slice(0, 16) === target,
      );
    }
    // Otherwise, match on expiry date (day only)
    if (expiry) {
      const targetExpiry = expiry.toISOString().slice(0, 10);
      return captures.find(
        c => new Date(c.expiry).toISOString().slice(0, 10) === targetExpiry,
      );
    }
    return undefined;
  };

  const handleLoadTemplate = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    e.target.value = ""; // reset dropdown
    if (captures.length === 0) return;
    const captureToAnchor = captures[0];
    const spot = captureToAnchor?.spot;
    const atm = spot ? Math.round(spot / 50) * 50 : 0; // fallback ATM 0 if no spot
    
    let newLegs: [string, number, number][] = [];
    if (val === "straddle") {
      newLegs = [["call", atm, -1], ["put", atm, -1]];
    } else if (val === "strangle") {
      newLegs = [["call", atm + 100, -1], ["put", atm - 100, -1]];
    } else if (val === "iron_condor") {
      newLegs = [
        ["put", atm - 200, 1], ["put", atm - 100, -1],
        ["call", atm + 100, -1], ["call", atm + 200, 1]
      ];
    } else if (val === "bull_call_spread") {
      newLegs = [["call", atm, 1], ["call", atm + 100, -1]];
    } else if (val === "bear_put_spread") {
      newLegs = [["put", atm, 1], ["put", atm - 100, -1]];
    }
    
    setLegs(newLegs);
  };

  const handleCompare = async () => {
    setIsLoading(true);
    try {
      const matchedA = findSnapshot(entryDateA, expiryDateA) || captures[0];
      const matchedB = findSnapshot(entryDateB, expiryDateB) || captures[1];

      if (!matchedA || !matchedB) return;

      const reqBody: any = {
        capture_a: matchedA.capture_id,
        capture_b: matchedB.capture_id,
      };
      if (legs.length > 0) {
        reqBody.legs = legs;
        reqBody.expiry_date = matchedA.expiry;
      }
      const res = await fetch("/api/compare-captures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqBody),
      });
      const data = await res.json();
      if (data.success) {
        setCompareResult(data);
      } else {
        alert("Error comparing: " + data.detail);
      }
    } catch (e: any) {
      alert("Failed to compare: " + e.message);
    } finally {
      setIsLoading(false);
    }
  };

  // Safe checks for data rendering
  const ch = compareResult?.chain_comparison;
  const liq = compareResult?.liquidity_analysis;
  const price = compareResult?.price_comparison;
  const strat = compareResult?.strategy_comparison;

  return (
    <div className="space-y-6">
      <div className="bg-slate-900 rounded-2xl shadow-sm border border-slate-800 p-6">
        <h2 className="text-lg font-bold flex items-center gap-2 mb-4 text-white">
          <Layers className="w-5 h-5 text-indigo-500" /> Snapshot Comparison
        </h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-bold text-slate-400 mb-1 block">Snapshot A (Older) – Entry Date & Time</label>
            <DatePicker
              selected={entryDateA}
              onChange={(date) => setEntryDateA(date)}
              showTimeSelect
              timeIntervals={15}
              dateFormat="yyyy-MM-dd HH:mm"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            />
            <label className="text-xs font-bold text-slate-400 mb-1 block mt-2">Expiry (Date only)</label>
            <DatePicker
              selected={expiryDateA}
              onChange={(date) => setExpiryDateA(date)}
              dateFormat="yyyy-MM-dd"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            />
          </div>
          <div>
            <label className="text-xs font-bold text-slate-400 mb-1 block">Snapshot B (Newer) – Entry Date & Time</label>
            <DatePicker
              selected={entryDateB}
              onChange={(date) => setEntryDateB(date)}
              showTimeSelect
              timeIntervals={15}
              dateFormat="yyyy-MM-dd HH:mm"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            />
            <label className="text-xs font-bold text-slate-400 mb-1 block mt-2">Expiry (Date only)</label>
            <DatePicker
              selected={expiryDateB}
              onChange={(date) => setExpiryDateB(date)}
              dateFormat="yyyy-MM-dd"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            />
          </div>
        </div>

        {/* Strategy Builder */}
        <div className="mt-6 border-t border-slate-800 pt-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-2">
            <h3 className="text-sm font-bold text-slate-400 uppercase tracking-wider">Strategy P&L Attribution (Optional)</h3>
            <select
              onChange={handleLoadTemplate}
              defaultValue=""
              className="bg-indigo-900/50 border border-indigo-700 rounded-lg px-3 py-1.5 text-sm text-indigo-200 outline-none focus:border-indigo-400 font-bold"
            >
              <option value="" disabled>Load Template Strategy...</option>
              <option value="straddle">Short Straddle (ATM)</option>
              <option value="strangle">Short Strangle (±100)</option>
              <option value="iron_condor">Iron Condor (±100/200)</option>
              <option value="bull_call_spread">Bull Call Spread (+100)</option>
              <option value="bear_put_spread">Bear Put Spread (-100)</option>
            </select>
          </div>
          
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <select
              value={legSide}
              onChange={(e) => setLegSide(e.target.value as any)}
              className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            >
              <option value="call">CE (Call)</option>
              <option value="put">PE (Put)</option>
            </select>
            <input
              type="number"
              value={legStrike}
              onChange={(e) => setLegStrike(e.target.value ? Number(e.target.value) : '')}
              placeholder="Strike (e.g. 24000)"
              className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400 w-32"
            />
            <select
              value={legSign}
              onChange={(e) => setLegSign(Number(e.target.value))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-indigo-400"
            >
              <option value={1}>Buy (+1)</option>
              <option value={-1}>Sell (-1)</option>
            </select>
            <button
              onClick={handleAddLeg}
              disabled={!legStrike}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 text-white p-2 rounded-lg transition"
            >
              <Plus className="w-5 h-5" />
            </button>
          </div>

          {legs.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {legs.map((leg, idx) => (
                <div key={idx} className="flex items-center gap-2 bg-slate-800 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200">
                  <span className={leg[2] > 0 ? 'text-emerald-400' : 'text-rose-400'}>
                    {leg[2] > 0 ? 'Long' : 'Short'}
                  </span>
                  <span>{leg[1]}</span>
                  <span className="uppercase text-slate-400">{leg[0]}</span>
                  <button onClick={() => handleRemoveLeg(idx)} className="text-slate-500 hover:text-rose-400 ml-1">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <button
          onClick={handleCompare}
          disabled={isLoading}
          className="mt-2 w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2.5 text-sm font-bold shadow-md transition disabled:bg-slate-600 disabled:cursor-not-allowed flex items-center justify-center gap-2 cursor-pointer"
        >
          {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}
          Compare Chains {legs.length > 0 && '& Strategy'}
        </button>
      </div>

      {compareResult && (
        <div className="space-y-6">
          
          {/* 5a Positioning */}
          {ch && (
            <div className="bg-slate-900 rounded-2xl shadow-sm border border-slate-800 p-6">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
                <Activity className="w-4 h-4 text-indigo-400" /> Positioning Shifts
              </h3>
              
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="space-y-3">
                  {ch.read?.map((r: string, i: number) => (
                    <div key={i} className="flex gap-3 text-sm text-slate-200 bg-slate-800 p-3 rounded-lg border border-slate-700">
                      <ArrowRight className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                      <span>{r}</span>
                    </div>
                  ))}
                </div>
                
                {ch.fresh_writing?.flow && ch.fresh_writing.flow.length > 0 && (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={ch.fresh_writing.flow} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                        <XAxis dataKey="strike" stroke="#475569" fontSize={10} angle={-45} textAnchor="end" height={30} />
                        <YAxis stroke="#475569" fontSize={10} />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9' }}
                          itemStyle={{ fontSize: 12 }}
                          labelStyle={{ fontSize: 12, fontWeight: 'bold', marginBottom: 4 }}
                        />
                        <ReferenceLine y={0} stroke="#475569" />
                        <Legend wrapperStyle={{ fontSize: 11 }} />
                        <Bar dataKey="call_oi_delta" name="Call OI Shift" fill="#f43f5e" />
                        <Bar dataKey="put_oi_delta" name="Put OI Shift" fill="#10b981" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
              <p className="text-[11px] text-slate-500 mt-4 italic flex gap-1.5 items-center">
                <Info className="w-3 h-3" />
                {ch.caveat || "positioning SHIFTS, not predictions; OI delta = B−A."}
              </p>
            </div>
          )}

          {/* 5b Liquidity */}
          {liq && (
            <div className="bg-slate-900 rounded-2xl shadow-sm border border-slate-800 p-6">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
                <Layers className="w-4 h-4 text-amber-500" /> Liquidity & Volume
              </h3>
              
              <div className="space-y-4 mb-6">
                {liq.read?.map((r: string, i: number) => (
                  <div key={i} className="flex gap-3 text-sm text-slate-200 bg-amber-900/20 p-3 rounded-lg border border-amber-800/50">
                    <ArrowRight className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                    <span>{r}</span>
                  </div>
                ))}
              </div>

              {/* Heatmap Strip */}
              {liq.rows && (
                <div className="w-full pb-2">
                  <div className="flex flex-wrap gap-4 mb-4 text-xs font-semibold text-slate-300">
                    <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-emerald-500 rounded-sm"></div> Liquid</div>
                    <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-yellow-500 rounded-sm"></div> Wide/Quiet</div>
                    <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-rose-500 rounded-sm"></div> Avoid</div>
                    <div className="flex items-center gap-1.5 ml-4"><div className="w-3 h-3 bg-indigo-500 rounded-sm"></div> Volume (Height)</div>
                  </div>
                  
                  <div className="overflow-x-auto">
                    <div className="flex min-w-max gap-1 pb-6 pt-2">
                    {liq.rows.map((r: any, idx: number) => {
                      // liquidity class color
                      let bg = "bg-slate-800";
                      if (r.liquidity === "liquid") bg = "bg-emerald-500";
                      else if (r.liquidity === "active_wide" || r.liquidity === "tight_quiet") bg = "bg-yellow-500";
                      else if (r.liquidity === "avoid") bg = "bg-rose-500";
                      
                      const maxVol = Math.max(...liq.rows.map((x:any) => x.volume)) || 1;
                      const volHeight = Math.max(4, Math.floor((r.volume / maxVol) * 40));

                      return (
                        <div key={idx} className="flex flex-col items-center group relative w-10">
                          <div className={`w-full h-8 ${bg} opacity-80 hover:opacity-100 transition-opacity rounded-sm`} title={`${r.side} ${r.strike}: ${r.liquidity} (${r.spread_pct_now}%)`}></div>
                          <div className="h-10 flex items-end w-full px-1 mt-1">
                            <div className="w-full bg-indigo-500 rounded-t-sm" style={{ height: `${volHeight}px` }} title={`Vol: ${r.volume}`}></div>
                          </div>
                          <span className="text-[9px] text-slate-400 mt-2 uppercase leading-none">{r.side[0]}</span>
                          <div className="mt-6 mb-2">
                            <span className="text-[10px] text-slate-300 font-bold -rotate-90 inline-block w-8 text-right whitespace-nowrap">{r.strike}</span>
                          </div>
                          
                          {/* Tooltip on hover */}
                          <div className="absolute bottom-full mb-2 hidden group-hover:block z-10 w-32 bg-slate-800 text-xs text-white p-2 rounded shadow-xl border border-slate-700">
                            <p className="font-bold uppercase">{r.side} {r.strike}</p>
                            <p>Liq: {r.liquidity}</p>
                            <p>Spread: {r.spread_pct_now}%</p>
                            <p>Vol: {r.volume}</p>
                          </div>
                        </div>
                      )
                    })}
                    </div>
                  </div>
                </div>
              )}

              <p className="text-[11px] text-slate-500 mt-4 italic flex gap-1.5 items-center">
                <Info className="w-3 h-3" />
                {liq.caveat || "spread = execution cost; volume confirms OI; 'avoid' = keep legs out."}
              </p>
            </div>
          )}

          {/* 5c Price */}
          {price && (
            <div className="bg-slate-900 rounded-2xl shadow-sm border border-slate-800 p-6">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-blue-400" /> Price Movement (LTP Shift)
              </h3>
              
              <div className="flex flex-col md:flex-row gap-6">
                <div className="w-full md:w-1/3 space-y-3">
                  {price.read?.map((r: string, i: number) => (
                    <div key={i} className="flex gap-3 text-sm text-slate-200 bg-blue-900/10 p-3 rounded-lg border border-blue-900/40">
                      <ArrowRight className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" />
                      <span>{r}</span>
                    </div>
                  ))}
                </div>

                <div className="w-full md:w-2/3 h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={price.rows} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                      <XAxis dataKey="strike" stroke="#475569" fontSize={10} angle={-45} textAnchor="end" height={30} />
                      <YAxis stroke="#475569" fontSize={10} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9' }}
                        itemStyle={{ fontSize: 12 }}
                        labelStyle={{ fontSize: 12, fontWeight: 'bold', marginBottom: 4 }}
                      />
                      <ReferenceLine y={0} stroke="#475569" />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Bar dataKey="call_move" name="Call LTP Δ" fill="#3b82f6" />
                      <Bar dataKey="put_move" name="Put LTP Δ" fill="#a855f7" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <p className="text-[11px] text-slate-500 mt-4 italic flex gap-1.5 items-center">
                <Info className="w-3 h-3" />
                {price.caveat || "LTP change B−A; on a short leg, a falling price = decay in your favour."}
              </p>
            </div>
          )}

          {/* 5d Strategy */}
          {strat && !strat.error && (
            <div className="bg-slate-900 rounded-2xl shadow-sm border border-slate-800 p-6">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
                <BarChartIcon className="w-4 h-4 text-emerald-400" /> Strategy P&L Attribution
              </h3>
              
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
                  <p className="text-xs text-slate-400 font-bold mb-1">Total P&L</p>
                  <p className={`text-xl font-black ${strat.pnl_rupees >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    ₹ {strat.pnl_rupees.toLocaleString()}
                  </p>
                </div>
                <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
                  <p className="text-xs text-slate-400 font-bold mb-1">Value Then</p>
                  <p className="text-lg font-bold text-slate-200">
                    {strat.value_a} pts
                  </p>
                </div>
                <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
                  <p className="text-xs text-slate-400 font-bold mb-1">Value Now</p>
                  <p className="text-lg font-bold text-slate-200">
                    {strat.value_b} pts
                  </p>
                </div>
                <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
                  <p className="text-xs text-slate-400 font-bold mb-1">Net Gain (pts)</p>
                  <p className={`text-lg font-bold ${strat.pnl_pts >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {strat.pnl_pts > 0 ? '+' : ''}{strat.pnl_pts} pts
                  </p>
                </div>
              </div>

              <div className="flex flex-col lg:flex-row gap-6 mb-4">
                <div className="w-full lg:w-1/3 space-y-3">
                  {strat.read?.map((r: string, i: number) => (
                    <div key={i} className="flex gap-3 text-sm text-slate-200 bg-emerald-900/10 p-3 rounded-lg border border-emerald-900/30">
                      <ArrowRight className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                      <span>{r}</span>
                    </div>
                  ))}
                </div>
                
                {strat.payoff_curve && strat.payoff_curve.length > 0 && (
                  <div className="w-full lg:w-2/3 h-64 border border-slate-800 rounded-xl p-4 bg-slate-900/50">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={strat.payoff_curve} margin={{ top: 10, right: 20, left: 20, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis 
                          dataKey="underlying" 
                          type="number"
                          domain={['dataMin', 'dataMax']}
                          stroke="#475569" 
                          fontSize={11}
                          tickCount={10}
                        />
                        <YAxis stroke="#475569" fontSize={11} tickFormatter={(v) => `₹${v}`} />
                        <Tooltip 
                          contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9' }}
                          labelFormatter={(l) => `Spot: ${l}`}
                          formatter={(v: number) => [`₹${v.toLocaleString()}`, 'P&L']}
                        />
                        <ReferenceLine y={0} stroke="#475569" />
                        {strat.spot_a && <ReferenceLine x={strat.spot_a} stroke="#f43f5e" strokeDasharray="3 3" label={{ position: 'top', value: 'Spot Then', fill: '#f43f5e', fontSize: 10 }} />}
                        {strat.spot_b && <ReferenceLine x={strat.spot_b} stroke="#10b981" strokeDasharray="3 3" label={{ position: 'bottom', value: 'Spot Now', fill: '#10b981', fontSize: 10 }} />}
                        <Line type="monotone" dataKey="pnl" stroke="#6366f1" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>

              <p className="text-[11px] text-slate-500 mt-4 italic flex gap-1.5 items-center">
                <Info className="w-3 h-3" />
                {strat.caveat || "attribution is DIRECTIONAL not exact Greeks; P&L is mark-to-market, add costs separately."}
              </p>
            </div>
          )}
          
        </div>
      )}
    </div>
  );
};
