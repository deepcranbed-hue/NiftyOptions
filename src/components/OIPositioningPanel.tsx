import React from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Cell } from 'recharts';
import { AutomatedRead, OptionRow, StructureContextItem } from '../types';
import { ShieldCheck, TrendingDown, TrendingUp, AlertTriangle, Info, Layers, BarChart2 } from 'lucide-react';

interface Props {
  rows: OptionRow[];
  spot: number;
  maxPain: number;
  pcr: number;
  reads: AutomatedRead[];
  structureContext: StructureContextItem[];
}

export const OIPositioningPanel: React.FC<Props> = ({
  rows,
  spot,
  maxPain,
  pcr,
  reads,
  structureContext,
}) => {
  // Format data for diverging horizontal bar chart
  const chartData = rows.map((r) => ({
    strike: r.strike,
    PutOI: r.put_oi || 0,
    CallOI: -(r.call_oi || 0), // Negative for left direction
    rawCallOI: r.call_oi || 0,
    isATM: Math.abs(r.strike - spot) <= 50,
  }));

  const getIcon = (tone: string) => {
    switch (tone) {
      case 'bull': return <TrendingUp className="w-5 h-5 text-emerald-600 shrink-0 mt-0.5" />;
      case 'bear': return <TrendingDown className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />;
      case 'caution': return <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />;
      default: return <Info className="w-5 h-5 text-blue-500 shrink-0 mt-0.5" />;
    }
  };

  const getToneBadge = (tone: string) => {
    switch (tone) {
      case 'bull': return 'bg-emerald-100 text-emerald-800 border-emerald-200';
      case 'bear': return 'bg-rose-100 text-rose-800 border-rose-200';
      case 'caution': return 'bg-amber-100 text-amber-800 border-amber-200';
      default: return 'bg-slate-100 text-slate-800 border-slate-200';
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Estimated Spot</div>
          <div className="text-2xl font-bold text-slate-900 mt-1">₹{spot.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</div>
          <div className="text-xs text-slate-400 mt-1 flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-full bg-blue-500"></span> Live Underlying Index
          </div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Max Pain Strike</div>
          <div className="text-2xl font-bold text-amber-600 mt-1">₹{maxPain.toLocaleString('en-IN')}</div>
          <div className="text-xs font-medium mt-1">
            {maxPain - spot > 0 ? (
              <span className="text-emerald-600">▲ +{Math.round(maxPain - spot)} pts Upward Magnet</span>
            ) : maxPain - spot < 0 ? (
              <span className="text-rose-600">▼ {Math.round(maxPain - spot)} pts Downward Pull</span>
            ) : (
              <span className="text-slate-500">Flat Equilibrium</span>
            )}
          </div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Put-Call Ratio (OI)</div>
          <div className="text-2xl font-bold text-slate-900 mt-1">{pcr.toFixed(2)}</div>
          <div className="text-xs font-medium mt-1">
            {pcr >= 1.3 ? (
              <span className="text-emerald-600 font-semibold">Put Heavy / Supportive Floor</span>
            ) : pcr <= 0.75 ? (
              <span className="text-rose-600 font-semibold">Call Heavy / Overhead Capped</span>
            ) : (
              <span className="text-slate-500">Balanced Structure</span>
            )}
          </div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
          <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Active Strikes</div>
          <div className="text-2xl font-bold text-slate-900 mt-1">{rows.length}</div>
          <div className="text-xs text-slate-400 mt-1">
            Step 50 Grid ({rows[0]?.strike} - {rows[rows.length - 1]?.strike})
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Automated Reads & Structure Context */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2 mb-4 pb-3 border-b border-slate-100">
              <Layers className="w-5 h-5 text-indigo-600" /> Automated OI Reads
            </h3>
            <div className="space-y-3">
              {reads.map((r, i) => (
                <div key={i} className={`p-3.5 rounded-lg border text-sm flex gap-3 ${getToneBadge(r.tone)}`}>
                  {getIcon(r.tone)}
                  <div className="leading-snug text-slate-800">{r.text}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-slate-900 text-white rounded-xl p-5 shadow-sm border border-slate-800">
            <h3 className="text-base font-bold flex items-center gap-2 mb-4 text-slate-100 pb-3 border-b border-slate-800">
              <ShieldCheck className="w-5 h-5 text-emerald-400" /> Structural Context
            </h3>
            <div className="space-y-4">
              {structureContext.map((item, idx) => (
                <div key={idx} className="bg-slate-800/80 p-3.5 rounded-lg border border-slate-700/60">
                  <div className="text-xs font-bold uppercase tracking-wider text-indigo-300 mb-1">
                    {item.label}
                  </div>
                  <div className="text-sm text-slate-200 font-mono leading-relaxed">
                    {item.text}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 text-xs text-slate-400 italic">
              * Note: OI/IV describe terrain positioning and tilt, not a guaranteed directional forecast.
            </div>
          </div>
        </div>

        {/* Right Column: Diverging OI Bar Chart */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5 shadow-sm flex flex-col">
          <div className="flex flex-wrap items-center justify-between gap-4 mb-6 pb-3 border-b border-slate-100">
            <div>
              <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                <BarChart2 className="w-5 h-5 text-blue-600" /> Open Interest Profile
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Put Writers (Support Floor) on Left ← | → Call Writers (Resistance Ceiling) on Right
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs font-semibold">
              <div className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded bg-emerald-600 inline-block"></span> Puts OI (Support)
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded bg-rose-600 inline-block"></span> Calls OI (Resistance)
              </div>
            </div>
          </div>

          <div className="flex-1 w-full min-h-[500px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={chartData}
                margin={{ top: 10, right: 30, left: 20, bottom: 10 }}
              >
                <XAxis
                  type="number"
                  tickFormatter={(v) => `${Math.abs(v).toFixed(0)}L`}
                  stroke="#94a3b8"
                  fontSize={11}
                />
                <YAxis
                  dataKey="strike"
                  type="category"
                  stroke="#64748b"
                  fontSize={11}
                  fontWeight="bold"
                  width={60}
                />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (!active || !payload || !payload.length) return null;
                    const putOI = payload.find((p) => p.dataKey === 'PutOI')?.value as number || 0;
                    const callOI = -(payload.find((p) => p.dataKey === 'CallOI')?.value as number || 0);
                    return (
                      <div className="bg-slate-900 text-white p-3 rounded-lg shadow-xl text-xs border border-slate-700 space-y-1">
                        <div className="font-bold text-sm border-b border-slate-700 pb-1 mb-1 text-indigo-300">
                          Strike ₹{label} {Math.abs(Number(label) - spot) <= 50 ? ' (Near ATM)' : ''}
                        </div>
                        <div className="flex justify-between gap-4 text-emerald-400">
                          <span>Put OI (Support):</span>
                          <span className="font-bold">{putOI.toFixed(1)} Lakh</span>
                        </div>
                        <div className="flex justify-between gap-4 text-rose-400">
                          <span>Call OI (Resistance):</span>
                          <span className="font-bold">{callOI.toFixed(1)} Lakh</span>
                        </div>
                        <div className="flex justify-between gap-4 text-slate-300 pt-1 border-t border-slate-800">
                          <span>Strike PCR:</span>
                          <span className="font-mono font-bold">
                            {callOI > 0 ? (putOI / callOI).toFixed(2) : '∞'}
                          </span>
                        </div>
                      </div>
                    );
                  }}
                />
                <ReferenceLine x={0} stroke="#cbd5e1" strokeWidth={1.5} />
                <Bar dataKey="PutOI" name="Puts" fill="#10B981" radius={[4, 0, 0, 4]}>
                  {chartData.map((entry, index) => (
                    <Cell
                      key={`cell-put-${index}`}
                      fill={entry.isATM ? '#059669' : '#10B981'}
                      fillOpacity={entry.isATM ? 1 : 0.8}
                    />
                  ))}
                </Bar>
                <Bar dataKey="CallOI" name="Calls" fill="#EF4444" radius={[0, 4, 4, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell
                      key={`cell-call-${index}`}
                      fill={entry.isATM ? '#DC2626' : '#EF4444'}
                      fillOpacity={entry.isATM ? 1 : 0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
};
