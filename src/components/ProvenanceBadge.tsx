import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, Clock, XCircle, Info } from 'lucide-react';

interface ProvenanceRecord {
  component: string;
  quality: 'PRIMARY' | 'PARTIAL' | 'FALLBACK' | 'STALE' | 'UNAVAILABLE';
  method: string;
  reason: string;
  detail: any;
}

interface Props {
  record?: ProvenanceRecord;
  className?: string;
}

export const ProvenanceBadge: React.FC<Props> = ({ record, className = "" }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!record) return null;

  const getStyle = () => {
    switch (record.quality) {
      case 'PRIMARY': return 'bg-emerald-100 text-emerald-700 border-emerald-200';
      case 'PARTIAL': return 'bg-amber-100 text-amber-700 border-amber-200';
      case 'STALE': return 'bg-orange-100 text-orange-700 border-orange-200';
      case 'FALLBACK': return 'bg-rose-100 text-rose-700 border-rose-200';
      case 'UNAVAILABLE': return 'bg-slate-100 text-slate-700 border-slate-200';
      default: return 'bg-slate-100 text-slate-700 border-slate-200';
    }
  };

  const getIcon = () => {
    switch (record.quality) {
      case 'PRIMARY': return <ShieldCheck className="w-3 h-3" />;
      case 'PARTIAL': return <AlertTriangle className="w-3 h-3" />;
      case 'STALE': return <Clock className="w-3 h-3" />;
      case 'FALLBACK': return <XCircle className="w-3 h-3" />;
      case 'UNAVAILABLE': return <Info className="w-3 h-3" />;
      default: return <Info className="w-3 h-3" />;
    }
  };

  return (
    <div 
      className={`relative inline-block ${className}`}
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
    >
      <div className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border cursor-help ${getStyle()}`}>
        {getIcon()}
        <span>{record.quality}</span>
      </div>

      {isOpen && record.quality !== 'PRIMARY' && (
        <div className="absolute z-50 left-1/2 -translate-x-1/2 bottom-full mb-2 w-64 p-3 bg-slate-800 text-white text-xs rounded shadow-lg border border-slate-700">
          <div className="font-bold text-slate-300 mb-1 flex justify-between items-center">
            <span>{record.component.toUpperCase()} LAYER</span>
            <span className={`px-1.5 py-0.5 rounded text-[9px] ${getStyle().replace('bg-', 'bg-opacity-20 bg-')}`}>{record.method}</span>
          </div>
          <div className="text-slate-200 leading-tight">
            {record.reason}
          </div>
          <div className="absolute left-1/2 -bottom-1 -translate-x-1/2 w-2 h-2 bg-slate-800 border-b border-r border-slate-700 transform rotate-45"></div>
        </div>
      )}
    </div>
  );
};
