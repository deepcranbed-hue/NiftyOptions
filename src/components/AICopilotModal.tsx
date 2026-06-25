import React, { useState } from 'react';
import { Bot, Sparkles, X, Loader2, FileText, CheckCircle2, Copy, AlertCircle } from 'lucide-react';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  dashboardState: any;
}

export const AICopilotModal: React.FC<Props> = ({ isOpen, onClose, dashboardState }) => {
  const [loading, setLoading] = useState(false);
  const [memo, setMemo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    setMemo(null);

    try {
      const res = await fetch('/api/analyze-desk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dashboardState),
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || 'Failed to connect to Quant Desk Copilot service.');
      }

      setMemo(data.analysis);
    } catch (err: any) {
      setError(err.message || 'Error executing AI analysis.');
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = () => {
    if (!memo) return;
    navigator.clipboard.writeText(memo);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl bg-slate-900 text-slate-100 rounded-3xl border border-slate-800 shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="p-6 bg-gradient-to-r from-indigo-900 via-slate-900 to-slate-900 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-2xl bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
              <Bot className="w-6 h-6 animate-pulse" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                Chief Quant Desk Copilot <span className="px-2 py-0.5 rounded-full bg-indigo-500/30 text-indigo-300 text-[10px] uppercase font-mono">Gemini 2.5 Flash</span>
              </h3>
              <p className="text-xs text-slate-400">
                Institutional Derivatives Desk Trading Memo &amp; Vol Risk Assessment
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl bg-slate-800/80 text-slate-400 hover:text-white hover:bg-slate-700 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 flex-1 overflow-y-auto space-y-6">
          {!memo && !loading && !error && (
            <div className="py-12 text-center space-y-4 max-w-md mx-auto">
              <div className="w-16 h-16 rounded-full bg-indigo-950 text-indigo-400 flex items-center justify-center mx-auto border border-indigo-800/60 shadow-inner">
                <Sparkles className="w-8 h-8" />
              </div>
              <h4 className="text-lg font-bold text-white">Generate Institutional Strategy Memo</h4>
              <p className="text-xs text-slate-400 leading-relaxed">
                Synthesizes live OI resistance/support ceilings, PCR tilt, complacency gauge compression, overnight global cues, and domestic sector headlines into an actionable desk directive.
              </p>
              <button
                onClick={handleGenerate}
                className="w-full py-3 px-6 rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white font-bold text-sm shadow-lg shadow-indigo-600/30 transition flex items-center justify-center gap-2 cursor-pointer"
              >
                <Sparkles className="w-4 h-4" /> Synthesize Live Desk Analysis
              </button>
            </div>
          )}

          {loading && (
            <div className="py-20 text-center space-y-4">
              <Loader2 className="w-10 h-10 animate-spin text-indigo-500 mx-auto" />
              <div className="text-sm font-semibold text-slate-300">
                Running Quantitative Derivatives Engine &amp; Vol Pricing Models...
              </div>
              <p className="text-xs text-slate-500">
                Evaluating tail risk asymmetries and strike positioning...
              </p>
            </div>
          )}

          {error && (
            <div className="p-6 rounded-2xl bg-rose-950/60 border border-rose-800/80 text-rose-200 text-sm flex items-start gap-3">
              <AlertCircle className="w-6 h-6 text-rose-500 shrink-0" />
              <div className="space-y-2">
                <span className="font-bold block text-white">Analysis Generation Failed</span>
                <p>{error}</p>
                <button
                  onClick={handleGenerate}
                  className="px-4 py-1.5 rounded-lg bg-rose-800 text-white font-semibold text-xs hover:bg-rose-700 mt-2 inline-block"
                >
                  Retry Copilot Execution
                </button>
              </div>
            </div>
          )}

          {memo && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-1.5">
                  <FileText className="w-4 h-4" /> Institutional Trading Desk Memo
                </span>
                <button
                  onClick={handleCopy}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition"
                >
                  {copied ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  {copied ? 'Memo Copied to Clipboard' : 'Copy Directive Markdown'}
                </button>
              </div>
              
              <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 font-sans text-sm text-slate-200 leading-relaxed overflow-x-auto whitespace-pre-wrap">
                {memo}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 bg-slate-950 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-500">
          <span>Server-Side Secure Gemini Proxy API</span>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-slate-800 text-slate-300 font-bold hover:bg-slate-700 transition"
          >
            Close Copilot
          </button>
        </div>
      </div>
    </div>
  );
};
