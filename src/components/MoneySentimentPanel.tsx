import React, { useEffect, useState } from 'react';
import { TrendingDown, TrendingUp, Droplet, Layers, AlertTriangle, Info } from 'lucide-react';

const fmtCr = (v: number | null | undefined) => {
  if (v === null || v === undefined) return 'n/a';
  const a = Math.abs(v);
  if (a >= 100000) return `₹${(v / 100000).toFixed(2)} lakh cr`;
  return `₹${Math.round(v).toLocaleString('en-IN')} cr`;
};

const regimeStyle = (regime: string): string => {
  if (regime.startsWith('BUYABLE')) return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (regime.startsWith('REAL DISTRIBUTION')) return 'bg-rose-50 text-rose-700 border-rose-200';
  if (regime.startsWith('FROTH')) return 'bg-slate-50 text-slate-600 border-slate-200';
  if (regime.startsWith('CONVICTION')) return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (regime.startsWith('LOW-CONVICTION')) return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-indigo-50 text-indigo-700 border-indigo-200';
};

export const MoneySentimentPanel: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [marketCap, setMarketCap] = useState<number>(47000000);
  const [fetchDate, setFetchDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [fetching, setFetching] = useState(false);
  const [fetchMsg, setFetchMsg] = useState<string>('');

  const load = (mc: number, date?: string) => {
    setLoading(true);
    const url = `/api/money-sentiment?market_cap_cr=${mc}` + (date ? `&date=${date}` : '');
    fetch(url)
      .then((r) => r.json())
      .then((j) => { setData(j); setLoading(false); })
      .catch((e) => { console.error('money-sentiment load failed', e); setLoading(false); });
  };
  useEffect(() => { load(marketCap); }, []);   // initial = latest available

  const fetchDelivery = () => {
    setFetching(true);
    setFetchMsg('');
    fetch(`/api/money-sentiment/fetch-delivery?date=${fetchDate}&market_cap_cr=${marketCap}`, { method: 'POST' })
      .then((r) => r.json())
      .then((j) => {
        if (j.success) {
          const d = j.delivery || {};
          setFetchMsg(`✓ ${fetchDate}: NIFTY-50 delivery ${d.nifty50_index_weighted_pct ?? d.nifty50_traded_weighted_pct}% saved.`);
          if (j.view) setData({ success: true, view: j.view });
        } else {
          setFetchMsg(`✗ ${j.error || 'fetch failed'}`);
        }
        setFetching(false);
      })
      .catch((e) => { setFetchMsg(`✗ ${e}`); setFetching(false); });
  };

  if (loading) {
    return <div className="p-8 text-center text-slate-500 animate-pulse font-bold text-sm">Loading Money vs Sentiment…</div>;
  }
  if (!data || !data.success || !data.view) {
    return <div className="p-8 text-center text-rose-500 font-bold text-sm">Failed to load money-vs-sentiment view.</div>;
  }

  const v = data.view;
  const src = v.sources_ok || {};
  const asOf = v.as_of || {};
  const down = (v.move_pct ?? 0) < 0;

  return (
    <div className="space-y-6">
      {/* Regime banner */}
      <div className={`rounded-2xl p-6 border shadow-sm ${regimeStyle(v.regime)}`}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            {down ? <TrendingDown className="w-6 h-6" /> : <TrendingUp className="w-6 h-6" />}
            <div>
              <div className="text-[11px] font-black uppercase tracking-wider opacity-70">Today's regime</div>
              <div className="text-xl font-black">{v.regime}</div>
            </div>
          </div>
          <div className="text-right">
            <div className="text-[11px] font-black uppercase tracking-wider opacity-70">
              NIFTY move · {v.requested_date || asOf.delivery_date || 'latest'}
            </div>
            <div className="text-xl font-black">{v.move_pct > 0 ? '+' : ''}{v.move_pct}%</div>
          </div>
        </div>
        <p className="mt-3 text-sm font-semibold leading-snug">{v.posture}</p>
      </div>

      {/* Decomposition */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-3">
            <Layers className="w-4 h-4" /> Evaporated (notional)
          </h3>
          <div className="text-2xl font-black text-slate-800">{fmtCr(v.evaporated_cr)}</div>
          <p className="text-[11px] text-slate-400 mt-2 font-medium">|move| × market cap. Mark-to-market on the whole float — mostly repricing, not money removed.</p>
        </div>

        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-3">
            <TrendingDown className="w-4 h-4" /> Really withdrawn
          </h3>
          <div className={`text-2xl font-black ${v.money_came_in ? 'text-emerald-600' : 'text-rose-600'}`}>
            {v.money_came_in ? `${fmtCr(Math.abs(v.withdrawn_cr))} IN` : `${fmtCr(v.withdrawn_cr)} OUT`}
          </div>
          <p className="text-[11px] text-slate-400 mt-2 font-medium">
            −(net FII {fmtCr(v.net_fii_cr)} + net DII {fmtCr(v.net_dii_cr)}). {v.money_came_in ? 'Institutions net BOUGHT — money did not leave.' : 'Real capital left the market.'}
          </p>
        </div>

        <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-3">
            <Droplet className="w-4 h-4" /> Sentiment share
          </h3>
          <div className="text-2xl font-black text-indigo-700">{v.sentiment_share_pct ?? 'n/a'}%</div>
          <p className="text-[11px] text-slate-400 mt-2 font-medium">Of the evaporated value, the fraction that was pure repricing/fear (not real withdrawal).</p>
        </div>
      </div>

      {/* Delivery conviction */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-4">
          <Droplet className="w-4 h-4" /> Delivery conviction (NIFTY-50)
        </h3>
        <div className="flex items-center gap-6 flex-wrap">
          <div>
            <div className="text-[11px] font-bold text-slate-400">Delivery %</div>
            <div className="text-xl font-black text-slate-800">{v.delivery_pct != null ? `${v.delivery_pct}%` : 'n/a'}</div>
          </div>
          <div>
            <div className="text-[11px] font-bold text-slate-400">Baseline ({asOf.delivery_baseline_n || 0}d)</div>
            <div className="text-xl font-black text-slate-500">{v.delivery_baseline_pct != null ? `${v.delivery_baseline_pct}%` : 'n/a'}</div>
          </div>
          <div>
            <div className="text-[11px] font-bold text-slate-400">Read</div>
            <div className={`text-sm font-black uppercase px-2 py-1 rounded ${
              v.delivery_high === true ? 'bg-emerald-50 text-emerald-700' :
              v.delivery_high === false ? 'bg-amber-50 text-amber-700' : 'bg-slate-50 text-slate-500'}`}>
              {v.delivery_high === true ? 'High — real' : v.delivery_high === false ? 'Low — churn' : 'n/a'}
            </div>
          </div>
        </div>
        <p className="text-[11px] text-slate-400 mt-3 font-medium">
          Delivery is direction-neutral: pair with flow. High delivery + net buying = accumulation; high delivery + net selling = distribution.
        </p>
      </div>

      {/* Fetch delivery from NSE (server-side) */}
      <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-3">Fetch delivery data (NSE)</h3>
        <div className="flex items-center gap-3 flex-wrap text-xs">
          <input type="date" value={fetchDate} onChange={(e) => setFetchDate(e.target.value)}
            className="px-2 py-1 border border-slate-200 rounded font-mono" />
          <button onClick={fetchDelivery} disabled={fetching}
            className="px-4 py-1.5 bg-violet-600 text-white rounded font-bold disabled:opacity-50">
            {fetching ? 'Fetching…' : 'Fetch & store'}
          </button>
          <button onClick={() => load(marketCap, fetchDate)}
            className="px-4 py-1.5 bg-white border border-violet-300 text-violet-700 rounded font-bold">
            View this date
          </button>
          {fetchMsg && <span className={`font-semibold ${fetchMsg.startsWith('✓') ? 'text-emerald-600' : 'text-rose-600'}`}>{fetchMsg}</span>}
        </div>
        <p className="text-[11px] text-slate-400 mt-2 font-medium">
          Runs on the server (NSE must be reachable from the host). Publishes ~6–7pm IST; weekends/holidays have no file. Run a few recent dates to build the baseline.
        </p>
      </div>

      {/* Controls + provenance */}
      <div className="flex items-center gap-4 flex-wrap text-xs">
        <label className="flex items-center gap-2 font-bold text-slate-500">
          Market cap (₹ cr):
          <input type="number" value={marketCap} onChange={(e) => setMarketCap(Number(e.target.value))}
            className="w-32 px-2 py-1 border border-slate-200 rounded font-mono" />
          <button onClick={() => load(marketCap)} className="px-3 py-1 bg-indigo-600 text-white rounded font-bold">Recompute</button>
        </label>
        {v.basis_discount && (
          <span className="flex items-center gap-1 px-2 py-1 bg-rose-50 text-rose-700 rounded font-bold">
            <AlertTriangle className="w-3 h-3" /> Futures discount — forced deleveraging
          </span>
        )}
      </div>

      {/* Source freshness */}
      <div className="flex items-start gap-2 text-[11px] text-slate-400 font-medium">
        <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Delivery {src.delivery ? `(${asOf.delivery_date})` : '— missing: run download_nse_delivery.py'};
          {' '}Flows {src.flows ? `(${asOf.flows_date})` : '— missing: run update-flows'};
          {' '}Move {src.move ? 'from price_bars' : '— pass ?move_pct'}.
          Decision-support / PRIOR — delivery & flows are ~1-day-lagged EOD context.
        </span>
      </div>
    </div>
  );
};
