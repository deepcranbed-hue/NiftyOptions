import React, { useState } from 'react';
import { X, CheckCircle2, RotateCcw, Database, AlertCircle, HelpCircle } from 'lucide-react';
import { SAMPLE_CHAIN } from '../lib/constants';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  rawChain: string;
  onSaveChain: (val: string, spotInput: number, dteInput: number) => void;
  currentSpotInput: number;
  currentDteInput: number;
}

export const ChainEditorDrawer: React.FC<Props> = ({
  isOpen,
  onClose,
  rawChain,
  onSaveChain,
  currentSpotInput,
  currentDteInput,
}) => {
  const [tempText, setTempText] = useState(rawChain);
  const [spotOverride, setSpotOverride] = useState(currentSpotInput);
  const [dteOverride, setDteOverride] = useState(currentDteInput);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleApply = () => {
    setError(null);
    if (!tempText.trim()) {
      setError("Option chain data cannot be empty.");
      return;
    }
    try {
      onSaveChain(tempText, spotOverride, dteOverride);
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to parse option chain data.");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-end bg-slate-950/70 backdrop-blur-xs animate-in fade-in duration-200">
      <div className="relative w-full max-w-2xl bg-slate-900 text-white h-full shadow-2xl border-l border-slate-800 flex flex-col overflow-hidden animate-in slide-in-from-right duration-300">
        {/* Header */}
        <div className="p-6 bg-slate-950 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-500/20 text-blue-400 border border-blue-500/30">
              <Database className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">Live Option Chain Data Stream</h3>
              <p className="text-xs text-slate-400">Paste 8-column tab/space separated NSE option chain feed</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 flex-1 overflow-y-auto space-y-6">
          <div className="bg-slate-800/60 p-4 rounded-xl border border-slate-700/60 text-xs text-slate-300 space-y-2">
            <div className="font-bold text-indigo-300 flex items-center gap-1.5">
              <HelpCircle className="w-4 h-4" /> Expected Column Structure (One Strike per Row)
            </div>
            <div className="font-mono bg-slate-950 p-2.5 rounded-lg text-emerald-400 overflow-x-auto text-[11px]">
              CallOIChg% | CallOI(lakh) | CallLTP | Strike | IV | PutLTP | PutOI(lakh) | PutOIChg%
            </div>
            <p className="text-[11px] text-slate-400">
              * Note: You can copy directly from Sensibull, NSE India, or brokerage option chain tables and paste directly here.
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-bold uppercase tracking-wider text-slate-300">
                Raw Chain Feed Text
              </label>
              <button
                onClick={() => setTempText(SAMPLE_CHAIN)}
                className="inline-flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
              >
                <RotateCcw className="w-3 h-3" /> Restore Default Paste
              </button>
            </div>
            <textarea
              rows={16}
              value={tempText}
              onChange={(e) => setTempText(e.target.value)}
              className="w-full p-4 bg-slate-950 text-slate-200 font-mono text-xs rounded-2xl border border-slate-800 focus:ring-2 focus:ring-indigo-500 outline-none leading-relaxed"
              placeholder="Paste 8-column option chain table..."
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800/40 p-4 rounded-xl border border-slate-800 space-y-2">
              <label className="text-xs font-bold text-slate-300 block">
                Manual Spot Price (₹) <span className="text-slate-500 font-normal">(0 = Auto)</span>
              </label>
              <input
                type="number"
                step="5"
                value={spotOverride}
                onChange={(e) => setSpotOverride(parseFloat(e.target.value) || 0)}
                className="w-full px-4 py-2 bg-slate-950 text-white font-mono font-bold text-sm rounded-xl border border-slate-800 focus:border-indigo-500 outline-none"
              />
            </div>
            
            <div className="bg-slate-800/40 p-4 rounded-xl border border-slate-800 space-y-2">
              <label className="text-xs font-bold text-slate-300 block">
                Days to Expiry (DTE)
              </label>
              <input
                type="number"
                step="0.5"
                value={dteOverride}
                onChange={(e) => setDteOverride(parseFloat(e.target.value) || 7)}
                className="w-full px-4 py-2 bg-slate-950 text-white font-mono font-bold text-sm rounded-xl border border-slate-800 focus:border-indigo-500 outline-none"
              />
            </div>
          </div>

          {error && (
            <div className="p-4 rounded-xl bg-rose-950/60 border border-rose-800 text-rose-300 text-xs flex items-center gap-2">
              <AlertCircle className="w-5 h-5 shrink-0 text-rose-500" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-6 bg-slate-950 border-t border-slate-800 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-5 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-xs transition"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            className="px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs transition shadow-lg shadow-indigo-600/30 flex items-center gap-2 cursor-pointer"
          >
            <CheckCircle2 className="w-4 h-4" /> Parse &amp; Refresh Engine
          </button>
        </div>
      </div>
    </div>
  );
};
