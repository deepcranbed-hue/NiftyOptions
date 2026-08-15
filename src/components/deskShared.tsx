import React from 'react';

/* Shared helpers/components for the Strategy Desk views. */

export const fmtInr = (n: number | undefined) =>
  n === undefined || n === null ? '—' : `₹${Math.round(n).toLocaleString('en-IN')}`;

export const pnlColor = (n: number) =>
  n > 0 ? 'text-emerald-600' : n < 0 ? 'text-rose-600' : 'text-slate-500';

// DB timestamps are UTC ('...Z'); India is UTC+5:30. Show market-local IST time.
export const fmtIST = (utc?: string) => {
  if (!utc) return '—';
  const d = new Date(utc);
  if (isNaN(d.getTime())) return utc;
  const ist = new Date(d.getTime() + 5.5 * 3600 * 1000);
  return ist.toISOString().slice(5, 16).replace('T', ' ');   // "MM-DD HH:MM"
};

export const Tile: React.FC<{ label: string; value: any; icon?: React.ReactNode; cls?: string }> = ({ label, value, icon, cls }) => (
  <div className="bg-slate-50 rounded-lg px-3 py-2.5">
    <div className="text-xs text-slate-400">{label}</div>
    <div className={`text-lg font-semibold flex items-center gap-1 ${cls || 'text-slate-800'}`}>{icon}{value}</div>
  </div>
);

export const TypeBadge: React.FC<{ kind: string }> = ({ kind }) => {
  const m: Record<string, string> = {
    option_strategy: 'bg-indigo-50 text-indigo-600',
    future: 'bg-amber-50 text-amber-600',
    stock: 'bg-slate-100 text-slate-500',
  };
  const label = kind === 'option_strategy' ? 'Options' : kind.charAt(0).toUpperCase() + kind.slice(1);
  return <span className={`text-[11px] px-2 py-0.5 rounded-full ${m[kind] || 'bg-slate-100'}`}>{label}</span>;
};

/* A labeled control cell for the backtest control grid. */
export const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div className="flex flex-col gap-1">
    <span className="text-[11px] text-slate-400">{label}</span>
    {children}
    {hint && <span className="text-[10px] leading-tight text-slate-400">{hint}</span>}
  </div>
);
