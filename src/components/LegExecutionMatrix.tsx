import React, { useState, useEffect, useMemo } from 'react';
import { Play, TrendingUp, TrendingDown, DollarSign } from 'lucide-react';

interface Leg {
  side: 'call' | 'put';
  strike: number;
  sign: number;
}

interface Props {
  family: string;
  capture: any;
  expectedMove: number;
  onTrade: (legs: Leg[], entryValue: number) => void;
  onCancel: () => void;
}

export const LegExecutionMatrix: React.FC<Props> = ({ family, capture, expectedMove, onTrade, onCancel }) => {
  const [legs, setLegs] = useState<Leg[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchDefaultStrikes = async () => {
      setIsLoading(true);
      try {
        const res = await fetch("http://127.0.0.1:8000/api/recommend-strikes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            family,
            capture_id: capture.capture_id,
            expected_move: expectedMove
          })
        });
        const data = await res.json();
        if (data.success && data.legs) {
          setLegs(data.legs.map((l: any) => ({ side: l[0], strike: l[1], sign: l[2] })));
        }
      } catch (e) {
        console.error(e);
      } finally {
        setIsLoading(false);
      }
    };
    if (family && capture) fetchDefaultStrikes();
  }, [family, capture, expectedMove]);

  const getPremium = (side: string, strike: number) => {
    if (!capture || !capture.strikes) return 0;
    const idx = capture.strikes.indexOf(strike);
    if (idx === -1) return 0;
    return side === 'call' ? (capture.call_ltp[idx] || 0) : (capture.put_ltp[idx] || 0);
  };

  const updateStrike = (idx: number, newStrike: number) => {
    const newLegs = [...legs];
    newLegs[idx].strike = newStrike;
    setLegs(newLegs);
  };

  const totalValue = useMemo(() => {
    return legs.reduce((acc, leg) => acc + (leg.sign * getPremium(leg.side, leg.strike)), 0);
  }, [legs, capture]);

  if (isLoading) return <div className="text-xs text-slate-400 p-4 bg-slate-900 border border-slate-700 rounded-xl mt-3 animate-pulse">Loading matrix...</div>;

  return (
    <div className="bg-slate-900 border border-indigo-500/30 rounded-xl p-4 mt-3">
      <h4 className="text-sm font-bold text-indigo-400 mb-3 flex items-center gap-2">
        <Play className="w-4 h-4" /> Leg Execution Matrix
      </h4>
      
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left text-slate-300">
          <thead className="text-xs text-slate-500 uppercase bg-slate-800/50">
            <tr>
              <th className="px-3 py-2 rounded-tl-lg">Action</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Strike (Editable)</th>
              <th className="px-3 py-2 rounded-tr-lg text-right">Est. Premium</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, i) => {
              const premium = getPremium(leg.side, leg.strike);
              return (
                <tr key={i} className="border-b border-slate-800/50">
                  <td className="px-3 py-2 font-bold">
                    {leg.sign > 0 ? (
                      <span className="text-emerald-400 flex items-center gap-1"><TrendingUp className="w-3 h-3"/> BUY</span>
                    ) : (
                      <span className="text-rose-400 flex items-center gap-1"><TrendingDown className="w-3 h-3"/> SELL</span>
                    )}
                  </td>
                  <td className="px-3 py-2 uppercase font-medium">{leg.side}</td>
                  <td className="px-3 py-2">
                    <select 
                      value={leg.strike}
                      onChange={(e) => updateStrike(i, Number(e.target.value))}
                      className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs outline-none focus:border-indigo-500"
                    >
                      {capture.strikes && capture.strikes.map((s: number) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    ₹{premium.toFixed(1)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      
      <div className="flex items-center justify-between mt-4 bg-slate-800/50 p-3 rounded-lg border border-slate-700">
        <div>
          <span className="text-xs text-slate-400 block uppercase font-bold">Net Entry Cost/Credit</span>
          <span className={`text-lg font-bold flex items-center gap-1 ${totalValue > 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
            <DollarSign className="w-4 h-4" /> 
            {totalValue > 0 ? 'Pay' : 'Collect'} {Math.abs(totalValue).toFixed(1)} pts
          </span>
        </div>
        <div className="flex gap-2">
          <button onClick={onCancel} className="px-4 py-2 text-xs font-bold text-slate-400 hover:text-white transition-colors border border-slate-700 hover:bg-slate-800 rounded-lg">
            Cancel
          </button>
          <button 
            onClick={() => onTrade(legs, totalValue)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded-lg text-sm font-bold shadow-lg shadow-indigo-900/50 transition-all flex items-center gap-2"
          >
            <Play className="w-4 h-4" /> Trade to Portfolio
          </button>
        </div>
      </div>
    </div>
  );
};
