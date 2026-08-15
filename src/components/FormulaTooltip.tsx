import React, { useState } from 'react';
import { Info } from 'lucide-react';

interface FormulaTrace {
  formula: string;
  subbed: string;
  meaning: string;
}

interface FormulaTooltipProps {
  trace?: FormulaTrace;
  className?: string;
}

export const FormulaTooltip: React.FC<FormulaTooltipProps> = ({ trace, className = "" }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!trace) return null;

  return (
    <div className={`relative inline-block ${className}`}
         onMouseEnter={() => setIsOpen(true)}
         onMouseLeave={() => setIsOpen(false)}>
      <button className="text-slate-400 hover:text-slate-600 focus:outline-none ml-1">
        <Info className="h-3 w-3 inline-block" />
      </button>
      
      {isOpen && (
        <div className="absolute z-50 left-1/2 -translate-x-1/2 bottom-full mb-2 w-80 p-3 bg-slate-800 text-white text-xs rounded shadow-lg border border-slate-700">
          <div className="font-mono bg-slate-900 p-2 rounded mb-2 overflow-x-auto whitespace-nowrap">
            <span className="text-blue-300">Formula:</span> {trace.formula}
          </div>
          <div className="font-mono bg-slate-900 p-2 rounded mb-2 overflow-x-auto whitespace-nowrap">
            <span className="text-green-300">Trace:</span> {trace.subbed}
          </div>
          <div className="text-slate-300 leading-tight">
            {trace.meaning}
          </div>
          <div className="absolute left-1/2 -bottom-1 -translate-x-1/2 w-2 h-2 bg-slate-800 border-b border-r border-slate-700 transform rotate-45"></div>
        </div>
      )}
    </div>
  );
};
