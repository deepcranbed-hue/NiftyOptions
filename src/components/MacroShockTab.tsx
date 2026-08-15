import React, { useEffect, useState, useCallback } from 'react';
import { Zap, RefreshCw, AlertTriangle, ArrowRight, TrendingDown, TrendingUp, ChevronRight, ChevronDown } from 'lucide-react';

/*
 * MacroShockTab
 * -------------
 * Comprehensive cause-and-effect read of any session: the trigger (e.g. oil up),
 * the cross-asset reaction (gold haven-failure, the USD magnet, copper as a growth
 * gauge), the transmission chain into Indian equities, and how each sector reacted
 * vs how the shock PREDICTS it should — including the energy producer/refiner split.
 * Data-driven from /api/macro-shock; runs on any date.
 */

const ROLE_COLOR: Record<string, string> = {
  'TRIGGER': 'bg-rose-100 text-rose-700', 'HAVEN FAILED': 'bg-amber-100 text-amber-700',
  'USD MAGNET': 'bg-blue-100 text-blue-700', 'growth gauge': 'bg-emerald-100 text-emerald-700',
};
const VERDICT_COLOR = (v: string) =>
  v.includes('capitulated') || v.includes('surprise') || v.includes('bucked') ? 'text-rose-600'
    : v.includes('cushioned') || v.includes('led the move') ? 'text-emerald-600'
    : v.includes('as expected') || v.includes('led the fall') ? 'text-slate-500'
    : 'text-slate-400';

export const MacroShockTab: React.FC = () => {
  const [days, setDays] = useState<string[]>([]);
  const [date, setDate] = useState<string>('');
  const [res, setRes] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [openSec, setOpenSec] = useState<string | null>(null);
  const [earn, setEarn] = useState<any>(null);

  useEffect(() => {
    fetch('/api/intraday-dates').then(r => r.json()).then(j => {
      const d = j.dates || j || [];
      if (Array.isArray(d) && d.length) { setDays(d); setDate((prev) => prev || d[d.length - 1]); }
    }).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const q = date ? `?date=${encodeURIComponent(date)}` : '';
      const r = await fetch(`/api/macro-shock${q}`);
      const j = await r.json();
      if (!j.success) { setErr(j.detail || 'no data'); setRes(null); } else setRes(j);
    } catch (e) { setErr('Cannot reach /api.'); }
    finally { setLoading(false); }
  }, [date]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const q = date ? `?date=${encodeURIComponent(date)}` : '';
    fetch(`/api/earnings-headlines${q}`).then(r => r.json()).then(setEarn).catch(() => setEarn(null));
  }, [date]);

  const n = res?.nifty;
  const pos = (n?.day_pts ?? 0) >= 0;

  return (
    <div className="space-y-4">
      {/* header + date picker */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-500" />
            <h2 className="text-base font-bold text-slate-800">Macro Shock — Cause &amp; Effect</h2>
          </div>
          <div className="flex items-center gap-2">
            <select value={date} onChange={(e) => setDate(e.target.value)} className="text-xs border border-slate-200 rounded-lg px-2 py-1 font-semibold">
              {days.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <button onClick={load} className="p-1 rounded hover:bg-slate-100"><RefreshCw className={`w-3.5 h-3.5 text-slate-400 ${loading ? 'animate-spin' : ''}`} /></button>
          </div>
        </div>

        {err && <div className="flex items-center gap-2 text-xs text-rose-600 bg-rose-50 rounded-lg px-3 py-2"><AlertTriangle className="w-3.5 h-3.5" /> {err}</div>}

        {n && (<>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div>
              <div className="text-[10px] uppercase font-black text-slate-400">NIFTY {res.date}</div>
              <div className={`text-2xl font-black ${pos ? 'text-emerald-600' : 'text-rose-600'}`}>{pos ? '+' : ''}{n.day_pts} pts <span className="text-base">({n.day_pct}%)</span></div>
            </div>
            <div className="text-[11px] text-slate-500">
              vs prev close <span className="font-mono">{n.prev_close}</span> → <span className="font-mono">{n.close}</span><br />
              gap <span className={`font-mono ${n.gap_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{n.gap_pts >= 0 ? '+' : ''}{n.gap_pts}</span> + intraday <span className={`font-mono ${n.intraday_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{n.intraday_pts >= 0 ? '+' : ''}{n.intraday_pts}</span>
            </div>
            {res.trigger && (
              <div className="ml-auto bg-slate-900 text-white rounded-xl px-4 py-2 max-w-md">
                <div className="text-[10px] uppercase font-black text-amber-300">Trigger · {res.trigger.magnitude}</div>
                <div className="text-sm font-bold">{res.trigger.label}</div>
                <div className="text-[10px] text-slate-300 mt-0.5">{res.trigger.detail}</div>
              </div>
            )}
          </div>
          {n?.shock_timing && (
            <div className={`mt-3 rounded-xl px-4 py-2 border ${n.shock_timing.kind === 'overnight_gap' ? 'bg-rose-50 border-rose-200' : n.shock_timing.kind === 'intraday' ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-200'}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] uppercase font-black text-slate-400">When</span>
                <span className={`text-sm font-bold ${n.shock_timing.kind === 'overnight_gap' ? 'text-rose-700' : n.shock_timing.kind === 'intraday' ? 'text-amber-700' : 'text-slate-600'}`}>{n.shock_timing.label}</span>
                <span className="text-[11px] text-slate-500">at open <span className={`font-mono ${n.shock_timing.gap_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{n.shock_timing.gap_pts >= 0 ? '+' : ''}{n.shock_timing.gap_pts}</span> · during session <span className={`font-mono ${n.shock_timing.intraday_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{n.shock_timing.intraday_pts >= 0 ? '+' : ''}{n.shock_timing.intraday_pts}</span> pts</span>
              </div>
              {pos && (
                <div className="mt-1.5 text-[11px] text-slate-500">▲ Up / non-falling session — the pre-open hedge &amp; foreseeability check applies to <b>down</b> moves, so there's nothing to hedge here.</div>
              )}
              {!pos && n.shock_timing.preopen?.status === 'ok' && (
                <div className="mt-1.5 text-[11px] flex items-center gap-2 flex-wrap">
                  <span className="text-[10px] uppercase font-black text-slate-400">Was it foreseeable?</span>
                  {n.shock_timing.preopen.data_quality === 'insufficient' ? (
                    <span className="font-bold text-amber-700">OVERNIGHT DATA UNAVAILABLE — {n.shock_timing.preopen.data_quality_reason || 'crude/GIFT overnight bars missing or stale'}. Foreseeability can't be scored — fix the overnight sync{n.shock_timing.preopen.last_data_ts ? ` (last synced: ${n.shock_timing.preopen.last_data_ts})` : ''}.</span>
                  ) : n.shock_timing.preopen.armed ? (
                    <span className="font-bold text-rose-700">ARMED — the overnight tape flagged risk before the open, giving a <u>pre-open window</u> to act (see below).</span>
                  ) : (
                    <span className="font-bold text-emerald-700">CLEAR — no overnight warning; it only developed after the open, so it was <u>manage-live</u>, not pre-hedgeable.</span>
                  )}
                  <span className="text-[10px] text-slate-400">warning strength {n.shock_timing.preopen.intensity} <span className="text-slate-300">(0–1; higher = stronger overnight tell)</span></span>
                  {n.shock_timing.preopen.reads && (
                    <span className="text-slate-400 font-mono">overnight: crude {n.shock_timing.preopen.reads.crude_overnight_pct != null ? Number(n.shock_timing.preopen.reads.crude_overnight_pct).toFixed(2) : 'n/a'}% · GIFT {n.shock_timing.preopen.reads.giftnifty_overnight_pct != null ? Number(n.shock_timing.preopen.reads.giftnifty_overnight_pct).toFixed(2) : 'n/a'}%</span>
                  )}
                  {n.shock_timing.preopen.tell_timing && (
                    <span className={`basis-full text-[11px] font-bold mt-0.5 ${
                      n.shock_timing.preopen.tell_timing === 'overnight' ? 'text-amber-700'
                      : n.shock_timing.preopen.tell_timing === 'at_close' ? 'text-rose-700'
                      : n.shock_timing.preopen.tell_timing === 'both' ? 'text-rose-700' : 'text-slate-500'}`}>
                      Tell appeared: {n.shock_timing.preopen.tell_timing === 'overnight' ? 'OVERNIGHT (after the prior close)' : n.shock_timing.preopen.tell_timing === 'at_close' ? 'BY THE PRIOR CLOSE' : n.shock_timing.preopen.tell_timing === 'both' ? 'BUILDING AT CLOSE + OVERNIGHT' : 'no clear tell'} — {n.shock_timing.preopen.tell_timing_note}
                      {n.shock_timing.preopen.window_split?.crude && (
                        <span className="font-mono font-normal text-slate-400"> · crude: by close {n.shock_timing.preopen.window_split.crude.at_close_pct ?? 'n/a'}% / overnight {n.shock_timing.preopen.window_split.crude.overnight_pct ?? 'n/a'}%</span>
                      )}
                    </span>
                  )}
                  <span className="text-[10px] text-slate-500 basis-full mt-0.5">
                    How to act: you can't trade the cash gap at the open. <b>If the risk was already visible by the prior 3:30&nbsp;PM close</b>, carry OTM NIFTY puts over. <b>If it emerged overnight</b> (e.g. a post-close oil spike — no signal existed at the 3:30 close), the only routes are GIFT&nbsp;Nifty (trades ~24h) or a standing tail hedge already held through the risk period. If crude flagged but GIFT was flat, damage tends to come intraday — hold protection through the session.
                  </span>
                </div>
              )}
              {n.shock_timing.preopen?.status === 'no_data' && (
                <div className="mt-1 text-[10px] text-slate-400">Pre-open derisk: overnight cross-asset series not in this DB copy — can't score foreseeability.</div>
              )}
              <div className="text-[11px] text-slate-600 mt-0.5">{n.shock_timing.note}</div>
            </div>
          )}
        </>)}
      </div>

      {/* Who drove it — intraday futures price × ΔOI (conviction vs intraday/leverage) */}
      {res?.intraday_oi && (
        <div className="bg-white rounded-2xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <div className="text-[11px] font-black uppercase text-slate-400">Who drove it · futures OI</div>
            <div className="flex items-center gap-2">
              {res.intraday_oi.intraday_churn_share_pct != null && (
                <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-600" title="Non-delivery share of NIFTY-50 volume = 100 − delivery %">Intraday churn {res.intraday_oi.intraday_churn_share_pct}%</span>
              )}
              {res.intraday_oi.available && (
                <span className={`text-[10px] font-black px-2 py-0.5 rounded ${res.intraday_oi.verdict?.startsWith('Intraday') ? 'bg-amber-100 text-amber-700' : res.intraday_oi.verdict?.startsWith('Conviction') ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-500'}`}>{res.intraday_oi.verdict}</span>
              )}
            </div>
          </div>
          {!res.intraday_oi.available ? (
            <div className="text-[11px] text-slate-400">{res.intraday_oi.note}</div>
          ) : (
            <>
              <div className="grid md:grid-cols-2 gap-2">
                {['morning', 'midday', 'afternoon', 'full_day'].map((k) => {
                  const lg = res.intraday_oi.legs?.[k];
                  if (!lg) return null;
                  const color = (lg.kind === 'short_buildup' || lg.kind === 'long_unwinding') ? 'text-rose-600'
                    : (lg.kind === 'long_buildup' || lg.kind === 'short_covering') ? 'text-emerald-600'
                    : lg.kind === 'coiled' ? 'text-amber-600' : 'text-slate-500';
                  return (
                    <div key={k} className="border border-slate-100 rounded-lg px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-black uppercase text-slate-400">{k.replace('_', ' ')}</span>
                        <span className={`text-[10px] font-black uppercase ${color}`}>{lg.kind.replace(/_/g, ' ')}</span>
                      </div>
                      <div className="text-[11px] font-mono mt-0.5">
                        ΔP <span className={lg.d_price_pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}>{lg.d_price_pts >= 0 ? '+' : ''}{lg.d_price_pts}</span> ({lg.d_price_pct}%)
                        {' · '}ΔOI <span className={lg.d_oi == null ? 'text-slate-400' : (lg.d_oi >= 0 ? 'text-emerald-600' : 'text-rose-600')}>{lg.d_oi == null ? 'n/a' : `${lg.d_oi >= 0 ? '+' : ''}${lg.d_oi.toLocaleString('en-IN')}`}</span>{lg.d_oi_pct != null ? ` (${lg.d_oi_pct}%)` : ''}
                      </div>
                      <div className="text-[11px] text-slate-500 mt-0.5">{lg.read}</div>
                    </div>
                  );
                })}
              </div>
              <div className="text-[10px] text-slate-500 mt-3 space-y-0.5 border-t border-slate-100 pt-2">
                <div><b>How to read:</b> OI = open futures contracts. <b>↑ OI</b> = new bets being opened (conviction); <b>↓ OI</b> = bets being closed (exiting); <b>flat OI</b> = intraday churn.</div>
                <div>
                  <span className="text-rose-600 font-semibold">Fall + OI↑ = fresh new shorts (real selling pressure)</span> ·
                  <span className="text-slate-500"> Fall + OI↓ = longs just exiting (bounce-prone)</span> ·
                  <span className="text-slate-500"> flat price + flat OI = day-trader churn (noise)</span>
                </div>
                <div><span className="text-amber-600 font-semibold">Coiled</span> = flat price but OI building heavily — big money committing on both sides with no resolution yet (a spring, not noise). Watch for a break.</div>
              </div>
            </>
          )}
        </div>
      )}

      {/* earnings-season overlay — the idiosyncratic driver, esp. on no-macro days */}
      {earn?.headlines?.length > 0 && (
        <div className="bg-white rounded-2xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[11px] font-black uppercase text-slate-400">Earnings season — today's results</div>
            <span className="text-[10px] text-slate-400">{earn.tagged_count} NIFTY names · live RSS</span>
          </div>
          <div className="text-[11px] text-slate-500 mb-2">A stock leading the tape on a no-macro day is often an earnings reaction. Company-tagged headlines from live feeds.</div>
          <div className="grid md:grid-cols-2 gap-2">
            {earn.headlines.filter((h: any) => h.symbol).slice(0, 10).map((h: any, i: number) => (
              <a key={i} href={h.link || undefined} target="_blank" rel="noreferrer"
                className="flex items-start gap-2 border border-slate-100 rounded-lg px-2.5 py-1.5 hover:bg-slate-50 transition">
                <span className={`shrink-0 mt-0.5 text-[9px] font-bold px-1.5 py-0.5 rounded ${h.sentiment === 'positive' ? 'bg-emerald-100 text-emerald-700' : h.sentiment === 'negative' ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-500'}`}>{h.symbol}</span>
                <div className="min-w-0">
                  <div className="text-[11px] text-slate-700 leading-tight">{h.title}</div>
                  <div className="text-[9px] text-slate-400">{h.source}{h.on_date ? ' · today' : ''}</div>
                </div>
              </a>
            ))}
          </div>
          {earn.headlines.filter((h: any) => h.symbol).length === 0 && (
            <div className="text-[11px] text-slate-400">No constituent earnings in the current feed{date ? ` for ${date}` : ''}.</div>
          )}
        </div>
      )}

      {res && (
        <>
          {/* cross-asset reaction */}
          {res.cross_assets?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="text-[11px] font-black uppercase text-slate-400 mb-2">Cross-asset reaction</div>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {res.cross_assets.map((c: any) => (
                  <div key={c.symbol} className="border border-slate-100 rounded-xl p-2.5">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-slate-700 text-sm">{c.symbol}</span>
                      <span className={`font-mono font-bold ${c.pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{c.pct > 0 ? '+' : ''}{c.pct}%</span>
                    </div>
                    {c.role && <span className={`inline-block mt-1 text-[9px] font-bold px-1.5 py-0.5 rounded ${ROLE_COLOR[c.role] || 'bg-slate-100 text-slate-500'}`}>{c.role}</span>}
                    <div className="text-[10px] text-slate-500 mt-1 leading-tight">{c.note}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* transmission chain */}
          {res.transmission?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="text-[11px] font-black uppercase text-slate-400 mb-3">Transmission chain</div>
              <div className="space-y-2">
                {res.transmission.map((t: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-5 h-5 shrink-0 rounded-full bg-slate-900 text-white text-[10px] font-bold flex items-center justify-center">{i + 1}</span>
                    <span className="font-semibold text-slate-700 bg-slate-50 rounded px-2 py-1">{t.cause}</span>
                    <ArrowRight className="w-3.5 h-3.5 text-slate-300 shrink-0" />
                    <span className="text-slate-600">{t.effect}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* energy producer/refiner split */}
          {res.energy_split?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="text-[11px] font-black uppercase text-slate-400 mb-1">Energy complex{res.crude_spike ? ' — producer vs refiner split' : ''}</div>
              <div className="text-[11px] text-slate-500 mb-2">{res.crude_spike
                ? 'A crude spike is not uniform: producers gain on higher realisations, refiners get squeezed on input cost.'
                : 'No crude spike today — energy moved with the tape, so the producer/refiner split is not the story. Shown for reference.'}</div>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {res.energy_split.map((e: any) => (
                  <div key={e.sym} className="flex items-center justify-between border border-slate-100 rounded-xl px-3 py-2">
                    <div>
                      <div className="font-bold text-slate-700 text-sm flex items-center gap-1">
                        {e.pct >= 0 ? <TrendingUp className="w-3.5 h-3.5 text-emerald-500" /> : <TrendingDown className="w-3.5 h-3.5 text-rose-500" />}
                        {e.sym}
                      </div>
                      <div className="text-[10px] text-slate-400">{e.role}</div>
                    </div>
                    <div className="text-right">
                      <div className={`font-mono font-bold ${e.pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{e.pct > 0 ? '+' : ''}{e.pct}%</div>
                      <div className="text-[10px] font-mono text-slate-400">{e.pts > 0 ? '+' : ''}{e.pts} pt</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* sector cause-effect */}
          {res.sectors?.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="text-[11px] font-black uppercase text-slate-400 mb-1">
                {res.has_causal_model ? 'Sector cause & effect — expected vs observed' : 'Sector leaders & laggards'}
              </div>
              <div className="text-[11px] text-slate-500 mb-3">
                Market avg {res.context?.mkt_avg_pct}% ·{' '}
                {res.has_causal_model
                  ? "each sector's expected reaction to the shock vs how it actually moved."
                  : 'no macro trigger detected — showing who led vs lagged the move (no causal model applied, so no "expected" column).'}
              </div>
              <div className="overflow-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-slate-400 border-b border-slate-100">
                      <th className="text-left font-normal py-1">Sector</th>
                      <th className="text-right font-normal">Index pts</th>
                      <th className="text-right font-normal">% of move</th>
                      <th className="text-right font-normal">Avg Δ%</th>
                      {res.has_causal_model && <th className="text-left font-normal pl-3">Expected effect</th>}
                      <th className="text-left font-normal pl-3">{res.has_causal_model ? 'Verdict' : 'Role today'}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.sectors.map((s: any) => {
                      const open = openSec === s.sector;
                      const ncols = res.has_causal_model ? 6 : 5;
                      return (
                        <React.Fragment key={s.sector}>
                          <tr className="border-b border-slate-50 hover:bg-slate-50 cursor-pointer" onClick={() => setOpenSec(open ? null : s.sector)}>
                            <td className="py-1.5 font-semibold text-slate-700">
                              <span className="inline-flex items-center gap-1">
                                {open ? <ChevronDown className="w-3 h-3 text-slate-400" /> : <ChevronRight className="w-3 h-3 text-slate-400" />}
                                {s.sector} <span className="text-slate-300 font-normal">{s.weight}%</span>
                                <span className="text-slate-300 font-normal">· {s.n}</span>
                              </span>
                            </td>
                            <td className={`text-right font-mono font-semibold ${s.pts >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{s.pts >= 0 ? '+' : ''}{s.pts}</td>
                            <td className="text-right font-mono text-slate-400">{Math.abs(s.share_pct)}%</td>
                            <td className={`text-right font-mono ${s.avg_pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{s.avg_pct > 0 ? '+' : ''}{s.avg_pct}%</td>
                            {res.has_causal_model && <td className="pl-3 text-slate-500 max-w-xs"><span className="font-semibold text-slate-600">{s.expected}</span> — {s.why}</td>}
                            <td className={`pl-3 font-semibold ${VERDICT_COLOR(s.verdict)}`}>{s.verdict}</td>
                          </tr>
                          {open && s.members && (
                            <tr className="bg-slate-50/60">
                              <td colSpan={ncols} className="px-3 py-2">
                                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1">
                                  {s.members.map((m: any) => (
                                    <div key={m.sym} className="flex items-center justify-between text-[11px]">
                                      <span className="text-slate-600">{m.sym} <span className="text-slate-300">{m.weight}%</span></span>
                                      <span className="flex items-center gap-2 font-mono">
                                        {m.close != null && <span className="text-slate-400">{m.close.toLocaleString('en-IN')}</span>}
                                        <span className={`font-semibold ${m.pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{m.pct > 0 ? '+' : ''}{m.pct}%</span>
                                        <span className={`w-14 text-right ${m.pts >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>{m.pts > 0 ? '+' : ''}{m.pts} pt</span>
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {res.breadth && (
                <div className="text-[11px] text-slate-400 mt-3">
                  Breadth: <span className="font-semibold text-slate-600">{res.breadth.decliners}↓ / {res.breadth.advancers}↑</span> of {res.breadth.total} · {(res.breadth.frac_big * 100).toFixed(0)}% moving &gt;1% · {res.breadth.decliners > res.breadth.advancers * 3 ? 'broad-based (capitulation)' : 'mixed'}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
};
