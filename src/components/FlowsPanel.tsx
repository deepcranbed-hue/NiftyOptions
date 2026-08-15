import React, { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, Activity, ShieldAlert, Clock } from 'lucide-react';
import { FormulaTooltip } from './FormulaTooltip';

export const FlowsPanel: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const [historyData, setHistoryData] = useState<any>(null);
  const [historyLoading, setHistoryLoading] = useState(true);

  useEffect(() => {
    fetch('/api/flows')
      .then(res => res.json())
      .then(json => {
        setData(json);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load flows", err);
        setLoading(false);
      });

    fetch('/api/flows-history?limit=30')
      .then(res => res.json())
      .then(json => {
        if (json.success) setHistoryData(json.data);
        setHistoryLoading(false);
      })
      .catch(err => {
        console.error("Failed to load flows history", err);
        setHistoryLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="p-8 text-center text-slate-500 animate-pulse font-bold text-sm">Loading Institutional Flows...</div>;
  }

  if (!data || !data.success) {
    return <div className="p-8 text-center text-rose-500 font-bold text-sm">Failed to load flow data.</div>;
  }

  const { bias, cash_stale, sip_stale, fpi_stale, sector_fpi, flow_tilt, formula_trace, bond_cues, fii_disambiguation } = data;
  const trend = bias.trend || bias;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Daily Cash Trend */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm relative">
          {cash_stale && <div className="absolute top-4 right-4 px-2 py-1 bg-amber-100 text-amber-700 text-[10px] font-black rounded uppercase flex items-center gap-1"><Clock className="w-3 h-3"/> Provisional</div>}
          <div className="flex items-center gap-2 mb-4">
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2">
              <Activity className="w-4 h-4" /> Daily Cash Trend
            </h3>
            <FormulaTooltip trace={formula_trace} />
          </div>
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Regime</span>
              <span className="text-sm font-black uppercase text-indigo-700 bg-indigo-50 px-2 py-1 rounded">{trend.regime?.replace(/_/g, ' ')}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Net FII (5d)</span>
              <span className={`text-base font-black ${trend.fii_cum_cr > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                ₹{trend.fii_cum_cr?.toLocaleString('en-IN')} cr
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Net DII (5d)</span>
              <span className={`text-base font-black ${trend.dii_cum_cr > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                ₹{trend.dii_cum_cr?.toLocaleString('en-IN')} cr
              </span>
            </div>
            
            {/* FII Disambiguation Cross-Check */}
            {fii_disambiguation && (
              <div className={`mt-2 p-3 rounded-lg border text-xs leading-snug font-medium ${
                fii_disambiguation.verdict === 'fii_exit_risk_off' ? 'bg-rose-50 border-rose-200 text-rose-800' :
                fii_disambiguation.verdict === 'fii_sell_rotation' ? 'bg-amber-50 border-amber-200 text-amber-800' :
                'bg-slate-50 border-slate-200 text-slate-700'
              }`}>
                <strong>Cross-check: </strong>{fii_disambiguation.read}
              </div>
            )}
            <div className="flex justify-between items-center border-t border-slate-100 pt-3 mt-3">
              <span className="text-xs font-bold text-slate-500 uppercase">Inst. Flow Tilt</span>
              <span className={`text-lg font-black ${flow_tilt > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                {flow_tilt > 0 ? '+' : ''}{flow_tilt?.toFixed(2)}
              </span>
            </div>
          </div>
        </div>

        {/* Structural SIP */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm relative">
          <div className="absolute top-4 right-4 px-2 py-1 bg-slate-100 text-slate-500 text-[10px] font-black rounded uppercase flex items-center gap-1"><Clock className="w-3 h-3"/> ~10d lag</div>
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-emerald-500" /> Structural SIP
          </h3>
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Latest Month</span>
              <span className="text-sm font-bold">{bias.sip?.latest_month}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Inflow</span>
              <span className="text-base font-black text-slate-800">
                ₹{bias.sip?.sip_inflow_cr?.toLocaleString('en-IN')} cr
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs font-bold text-slate-400">Trend</span>
              <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-1 rounded uppercase">{bias.sip?.trend}</span>
            </div>
          </div>
        </div>

        {/* Sector FPI */}
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm relative">
          <div className="absolute top-4 right-4 px-2 py-1 bg-slate-100 text-slate-500 text-[10px] font-black rounded uppercase flex items-center gap-1"><Clock className="w-3 h-3"/> ~3wk lag</div>
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
            <TrendingDown className="w-4 h-4 text-rose-500" /> Sector FPI (NSDL)
          </h3>
          <div className="space-y-2">
            {Object.entries(sector_fpi || {}).map(([sec, val]: any) => (
              <div key={sec} className="flex justify-between items-center">
                <span className="text-xs font-bold text-slate-600">{sec}</span>
                <span className={`text-xs font-black ${val > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  ₹{val.toLocaleString('en-IN')} cr
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Bond/Rates Panel */}
        {bond_cues && (
          <div className="bg-slate-900 rounded-2xl p-6 border border-slate-800 shadow-sm relative text-white">
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
              <Activity className="w-4 h-4 text-indigo-400" /> Bond & FX Market
            </h3>
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-slate-400">10Y G-Sec Yield</span>
                <span className={`text-base font-black ${bond_cues.change_bps < 0 ? 'text-emerald-400' : bond_cues.change_bps > 0 ? 'text-rose-400' : 'text-slate-300'}`}>
                  {bond_cues.yield_10y.toFixed(2)}% ({bond_cues.change_bps > 0 ? '+' : ''}{bond_cues.change_bps} bps)
                </span>
              </div>
              <div className={`text-xs p-2 rounded border ${
                bond_cues.equity_tilt === 'supportive' ? 'bg-emerald-500/20 border-emerald-500/30 text-emerald-300' :
                bond_cues.equity_tilt === 'headwind' ? 'bg-rose-500/20 border-rose-500/30 text-rose-300' :
                'bg-slate-800 border-slate-700 text-slate-300'
              }`}>
                {bond_cues.read}
              </div>
              {bond_cues.rupee_read && (
                <div className="text-xs text-slate-400 mt-2">
                  {bond_cues.rupee_read}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Historical Flows Table (Cash) */}
      {historyLoading ? (
        <div className="p-4 text-center text-slate-500 animate-pulse text-xs">Loading cash history...</div>
      ) : historyData && historyData.length > 0 ? (
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm mt-6">
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
            <Clock className="w-4 h-4 text-indigo-500" /> FII & DII Cash Flows (Upstox)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="bg-slate-50 border-y border-slate-200 text-slate-500 uppercase font-bold tracking-wider">
                <tr>
                  <th className="p-2">Date</th>
                  <th className="p-2 text-right">FII Cash Net (₹ Cr)</th>
                  <th className="p-2 text-right">DII Cash Net (₹ Cr)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-medium">
                {historyData.map((row: any) => (
                  <tr key={row.date} className="hover:bg-slate-50/50">
                    <td className="p-2 whitespace-nowrap text-slate-800 font-bold">{row.date}</td>
                    <td className={`p-2 text-right font-black ${row.fii_net > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                      {row.fii_net.toLocaleString('en-IN')}
                    </td>
                    <td className={`p-2 text-right font-black border-l border-slate-100 ${row.dii_net > 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                      {row.dii_net.toLocaleString('en-IN')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Flows vs index charts */}
      {!historyLoading && historyData && <FlowCharts rows={historyData} />}

    </div>
  );
};

/* ------------------------------------------------------------------ *
 * FlowCharts — FII/DII cash, NIFTY, and participant futures positioning
 *
 * Plain SVG, no chart library: this repo has none, and three small charts
 * do not justify adding one.
 *
 * NIFTY IS ITS OWN PANEL, NOT A SECOND Y-AXIS. Rupees-crore of flow and an
 * index level share no scale, and a dual axis lets you place the crossing
 * point anywhere you like — the two series can be made to look correlated
 * or not by choosing the zero. Stacked panels on a shared date axis say the
 * same thing honestly.
 * ------------------------------------------------------------------ */

type Row = { date: string; fii_net: number; dii_net: number; nifty_close: number | null };

const C = { fii: '#2a78d6', dii: '#eb6834', pro: '#1baf7a', client: '#eda100', ink: '#898781', grid: '#e1e0d9' };
const inr = (n: number) => (n < 0 ? '−' : '') + Math.abs(Math.round(n)).toLocaleString('en-IN');
const dm = (d: string) => d.slice(8) + '/' + d.slice(5, 7);

const Axis: React.FC<{ vals: number[]; y: (v: number) => number; x0: number; x1: number; pct?: boolean }> =
  ({ vals, y, x0, x1 }) => (
  <>
    {vals.map((v, i) => (
      <g key={i}>
        <line x1={x0} x2={x1} y1={y(v)} y2={y(v)} stroke={C.grid} strokeWidth={1} />
        <text x={x0 - 8} y={y(v) + 3.5} textAnchor="end" fontSize={10} fill={C.ink}
              style={{ fontVariantNumeric: 'tabular-nums' }}>{inr(v)}</text>
      </g>
    ))}
  </>
);

/** FII vs DII net cash, grouped bars around a zero baseline. */
const CashChart: React.FC<{ rows: Row[] }> = ({ rows }) => {
  const W = 900, H = 220, L = 62, R = 12, T = 10, B = 26;
  const iw = W - L - R, ih = H - T - B;
  const vs = rows.flatMap(r => [r.fii_net, r.dii_net]);
  const hi = Math.max(...vs) * 1.08, lo = Math.min(...vs) * 1.08;
  const y = (v: number) => T + ih * (hi - v) / (hi - lo);
  const bw = iw / rows.length, bar = Math.min(8, (bw - 5) / 2);
  const ticks = [lo, lo + (hi - lo) / 2, hi];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ overflow: 'visible' }}>
      <Axis vals={ticks} y={y} x0={L} x1={W - R} />
      <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke="#c3c2b7" strokeWidth={1} />
      {rows.map((r, i) => {
        const cx = L + bw * i + bw / 2;
        return (
          <g key={r.date}>
            {([[r.fii_net, C.fii, -1], [r.dii_net, C.dii, 1]] as [number, string, number][]).map(([v, col, side], k) => {
              const yv = y(v), y0 = y(0);
              return <rect key={k} x={cx + (side < 0 ? -bar - 1 : 1)} y={Math.min(y0, yv)}
                           width={bar} height={Math.max(1, Math.abs(yv - y0))} rx={3}
                           fill={col} stroke="#fff" strokeWidth={1} />;
            })}
            <title>{`${r.date}\nFII ${inr(r.fii_net)}\nDII ${inr(r.dii_net)}`}</title>
            <rect x={L + bw * i} y={T} width={bw} height={ih} fill="transparent" />
            {(i % 5 === 0 || i === rows.length - 1) &&
              <text x={cx} y={H - 8} textAnchor="middle" fontSize={10} fill={C.ink}>{dm(r.date)}</text>}
          </g>
        );
      })}
    </svg>
  );
};

/** NIFTY close on the same dates — its own scale, its own panel. */
const NiftyChart: React.FC<{ rows: Row[] }> = ({ rows }) => {
  const pts = rows.filter(r => r.nifty_close != null);
  if (pts.length < 2) return null;
  const W = 900, H = 130, L = 62, R = 12, T = 10, B = 26;
  const iw = W - L - R, ih = H - T - B;
  const vs = pts.map(r => r.nifty_close as number);
  const hi = Math.max(...vs), lo = Math.min(...vs), pad = (hi - lo) * 0.12 || 1;
  const y = (v: number) => T + ih * (hi + pad - v) / (hi - lo + 2 * pad);
  // x is indexed on the FULL row list so the panel lines up with the bars above
  const x = (i: number) => L + iw * i / (rows.length - 1) + (iw / rows.length) / 2;
  const d = pts.map(r => `${x(rows.indexOf(r))},${y(r.nifty_close as number)}`).join(' ');
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ overflow: 'visible' }}>
      {[lo, hi].map((v, i) => (
        <g key={i}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={C.grid} strokeWidth={1} />
          <text x={L - 8} y={y(v) + 3.5} textAnchor="end" fontSize={10} fill={C.ink}
                style={{ fontVariantNumeric: 'tabular-nums' }}>{Math.round(v).toLocaleString('en-IN')}</text>
        </g>
      ))}
      <polyline points={d} fill="none" stroke="#0b0b0b" strokeWidth={2} strokeLinejoin="round" />
      <circle cx={x(rows.indexOf(last))} cy={y(last.nifty_close as number)} r={3.5}
              fill="#0b0b0b" stroke="#fff" strokeWidth={2} />
      {rows.map((r, i) => (i % 5 === 0 || i === rows.length - 1) &&
        <text key={r.date} x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill={C.ink}>{dm(r.date)}</text>)}
    </svg>
  );
};

/** One measure, four participants. Rendered six times as small multiples.
 *
 * Small multiples rather than one chart with a measure dropdown: the six panels
 * are the same structure at the same scale-type, and the interesting reads are
 * CROSS-panel — FII short futures while long puts is a different story from FII
 * short futures alone. A dropdown hides exactly that comparison.
 *
 * Each panel keeps its own y-scale (a stock-futures net of 250,000 would flatten
 * the index-futures panel to a line), so compare SHAPES across panels, not heights.
 */
const MEASURES: [string, string][] = [
  ['idx_fut_net', 'Index futures'],
  ['idx_opt_call_net', 'Index calls'],
  ['idx_opt_put_net', 'Index puts'],
  ['stk_fut_net', 'Stock futures'],
  ['stk_opt_call_net', 'Stock calls'],
  ['stk_opt_put_net', 'Stock puts'],
];

const PKEYS: [string, string][] = [['FII', C.fii], ['Client', C.dii], ['Pro', C.pro], ['DII', C.client]];

const MiniChart: React.FC<{ data: any[]; measure: string; title: string }> = ({ data, measure, title }) => {
  const val = (d: any, k: string) => Number(d.participants?.[k]?.[measure] ?? 0);
  const W = 440, H = 150, L = 52, R = 8, T = 8, B = 20;
  const iw = W - L - R, ih = H - T - B;
  const all = data.flatMap(d => PKEYS.map(([k]) => val(d, k)));
  const rawHi = Math.max(...all), rawLo = Math.min(...all);
  const span = (rawHi - rawLo) || 1;
  const hi = rawHi + span * 0.1, lo = rawLo - span * 0.1;
  const y = (v: number) => T + ih * (hi - v) / (hi - lo);
  const x = (i: number) => L + iw * i / (data.length - 1);
  const compact = (v: number) => Math.abs(v) >= 1000
    ? (v / 1000).toFixed(0) + 'k' : String(Math.round(v));
  return (
    <div>
      <div className="text-[11px] font-bold text-slate-600 mb-1">{title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ overflow: 'visible' }}>
        {[lo, hi].map((v, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={C.grid} strokeWidth={1} />
            <text x={L - 6} y={y(v) + 3.5} textAnchor="end" fontSize={9} fill={C.ink}
                  style={{ fontVariantNumeric: 'tabular-nums' }}>{compact(v)}</text>
          </g>
        ))}
        {lo < 0 && hi > 0 &&
          <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke="#c3c2b7" strokeWidth={1} />}
        {PKEYS.map(([k, col]) => (
          <polyline key={k} points={data.map((d, i) => `${x(i)},${y(val(d, k))}`).join(' ')}
                    fill="none" stroke={col} strokeWidth={1.75} strokeLinejoin="round" />
        ))}
        {PKEYS.map(([k, col]) => (
          <circle key={k} cx={x(data.length - 1)} cy={y(val(data[data.length - 1], k))}
                  r={2.75} fill={col} stroke="#fff" strokeWidth={1.5} />
        ))}
        {data.map((d, i) => (i === 0 || i === data.length - 1) &&
          <text key={d.date} x={x(i)} y={H - 6} textAnchor={i === 0 ? 'start' : 'end'}
                fontSize={9} fill={C.ink}>{dm(d.date)}</text>)}
      </svg>
    </div>
  );
};

const Key: React.FC<{ c: string; label: string }> = ({ c, label }) => (
  <span className="inline-flex items-center gap-1.5">
    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: c }} />{label}
  </span>
);

export const FlowCharts: React.FC<{ rows: Row[] }> = ({ rows }) => {
  const [pdata, setPdata] = useState<any[]>([]);
  useEffect(() => {
    fetch('/api/participant-history?limit=30')
      .then(r => r.json())
      .then(j => { if (j.success) setPdata(j.data); })
      .catch(() => {});
  }, []);
  if (!rows || rows.length < 2) return null;
  return (
    <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm mt-6">
      <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-1 flex items-center gap-2">
        <Activity className="w-4 h-4 text-indigo-500" /> Flows vs Index
      </h3>
      <p className="text-xs text-slate-500 mb-4">
        Net cash by institution type, the index on the same sessions, and net index-futures
        positioning by participant. Separate panels rather than one dual-axis chart &mdash;
        crore and index points share no scale.
      </p>

      <div className="text-xs text-slate-500 flex gap-4 mb-1">
        <Key c={C.fii} label="FII" /><Key c={C.dii} label="DII" />
        <span className="text-slate-400">net cash, &#8377; crore</span>
      </div>
      <CashChart rows={rows} />

      <div className="text-xs text-slate-500 mt-4 mb-1">NIFTY close</div>
      <NiftyChart rows={rows} />

      {pdata.length > 1 && (
        <>
          <div className="text-xs text-slate-500 flex gap-4 mt-6 mb-2 items-center">
            <Key c={C.fii} label="FII" /><Key c={C.dii} label="Client" />
            <Key c={C.pro} label="Pro" /><Key c={C.client} label="DII" />
            <span className="text-slate-400">
              net contracts by participant &mdash; each panel has its own scale, so compare shapes
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-4">
            {MEASURES.map(([m, t]) => <MiniChart key={m} data={pdata} measure={m} title={t} />)}
          </div>
        </>
      )}
    </div>
  );
};
