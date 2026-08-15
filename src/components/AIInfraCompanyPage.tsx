import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Server } from 'lucide-react';

/**
 * AIInfraCompanyPage — one AI-infra name, full window.
 *
 * WHAT THIS PAGE IS FOR, AND WHY IT IS NOT ANOTHER STOCK PAGE
 * -----------------------------------------------------------
 * Every aggregator already renders ratios, charts and peer tables, and does it with
 * more data than we hold. Competing there is a losing trade. This page answers three
 * questions none of them can, because the answers live in our own research record
 * rather than in a market-data feed:
 *
 *   1. HOW OLD IS EACH FACT?  Evidence is dated and decays. An order win stops being
 *      news in about three weeks; a capacity plan lasts years. Items past their
 *      half-life are dimmed, never deleted — "we knew this and it went stale" is a
 *      different statement from "we never knew it".
 *
 *   2. WHAT IS THIS NAME LOAD-BEARING FOR?  Each company is an edge into the theme's
 *      hypothesis set. Some names carry a hypothesis alone; some sit on BOTH sides of
 *      one. That is the most useful thing on the page and no ratio can express it.
 *
 *   3. WHAT DID WE SAY BEFORE, AND WAS IT RIGHT?  Every past stance and grade, scored
 *      over the window it was actually live. This is the section that makes the view
 *      falsifiable, which is the entire reason it exists.
 *
 * Route: /intel/ai-infra/<SYMBOL>, opened in a new tab from the theme table — the same
 * pattern as /intel/nifty50/<SYMBOL>. No app chrome; the window is for one company.
 *
 * NO AUTO-FETCH of market data beyond this page's own endpoint, per the panel
 * convention. The endpoint is read-only over JSON and price_bars.
 */

type Evidence = {
  date: string; note: string; source: string;
  decay_class: string; half_life_days: number;
  age_days: number | null; freshness: number | null; stale: boolean | null;
};
type Edge = {
  hypothesis: string; symbol: string; role: 'supporting' | 'contradicting';
  weight: 'load-bearing' | 'minor'; claim: string;
  hypothesis_text?: string; status?: string; note?: string;
  other_names_same_side: string[];
};
type Call = {
  kind: 'stance' | 'grade'; as_of: string; value: string;
  conviction?: string; rationale?: string; watch?: string;
  valid_till?: string; superseded_on: string | null; live: boolean;
  move_while_live_pct: number | null; move_to_date_pct: number | null;
  pe_at_call?: number | null; price_at_call?: number | null;
  evidence_strength?: string; priced_in?: string;
};
type Peer = {
  symbol: string; name: string; exposure: string; grade?: string;
  conviction?: string; evidence_strength?: string; priced_in?: string;
  pe_ttm?: number | null; is_self: boolean;
};
type Payload = {
  success: boolean; symbol: string; as_of: string;
  company: any; segment_label: string;
  evidence: Evidence[]; hypotheses: Edge[]; calls: Call[]; peers: Peer[];
  prices: { d: string; c: number }[];
  price_note: string | null; half_life_note: string; disclaimer: string;
};

const card = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';
const hdr = 'text-[11px] font-black uppercase tracking-wide text-slate-400';

const GRADE_CLS: Record<string, string> = {
  buy: 'bg-emerald-50 text-emerald-700 border-emerald-300',
  hold: 'bg-slate-50 text-slate-600 border-slate-300',
  sell: 'bg-rose-50 text-rose-700 border-rose-300',
};
const STANCE_CLS: Record<string, string> = {
  up: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  sideways: 'bg-amber-50 text-amber-700 border-amber-200',
  down: 'bg-rose-50 text-rose-600 border-rose-200',
};
const STANCE_GLYPH: Record<string, string> = { up: '▲', sideways: '→', down: '▼' };
const callCls = (c: Call) =>
  c.kind === 'grade' ? (GRADE_CLS[c.value] ?? GRADE_CLS.hold) : (STANCE_CLS[c.value] ?? STANCE_CLS.sideways);
/** Marker stroke for the price chart. Same hue meaning as the badges above, so the
 *  chart and the table are read as one system rather than two colour schemes. */
const callStroke = (c: Call) => {
  const v = c.value;
  if (v === 'buy' || v === 'up') return '#059669';
  if (v === 'sell' || v === 'down') return '#e11d48';
  return '#d97706';
};

const DECAY_HINT: Record<string, string> = {
  interpretation: 'reads and read-across — stops carrying information in about three weeks',
  order: 'an order win — informative for roughly a quarter',
  earnings: 'a reported quarter — superseded by the next one',
  structural: 'capacity, capex or policy — lasts years',
};

function Pct({ v, bold }: { v: number | null | undefined; bold?: boolean }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  return (
    <span className={`font-mono ${bold ? 'font-bold' : ''} ${v >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
      {v >= 0 ? '+' : ''}{v}%
    </span>
  );
}

function Section({ title, sub, children }:
  { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className={card}>
      <div className={hdr}>{title}</div>
      {sub && <p className="text-[11px] text-slate-500 mt-1 leading-snug">{sub}</p>}
      <div className="mt-3">{children}</div>
    </div>
  );
}

/**
 * Price line with a tick at every call we have ever made on this name.
 *
 * Deliberately one series on one axis. A second axis here (volume, a peer, an index)
 * would let the eye read a crossing as a relationship that the data does not contain,
 * and the only question this chart answers is "what did price do after each call".
 */
function CallChart({ prices, calls }: { prices: Payload['prices']; calls: Call[] }) {
  if (prices.length < 2) return null;
  const W = 900, H = 190, PAD_L = 46, PAD_R = 14, PAD_T = 12, PAD_B = 26;
  const cs = prices.map((p) => p.c);
  const lo = Math.min(...cs), hi = Math.max(...cs);
  const span = hi - lo || 1;
  const x = (i: number) => PAD_L + (i / (prices.length - 1)) * (W - PAD_L - PAD_R);
  const y = (c: number) => PAD_T + (1 - (c - lo) / span) * (H - PAD_T - PAD_B);
  const path = prices.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join(' ');

  // Snap each call to the first bar on or after its date — the same rule the backend
  // scores with, so the marker sits where the entry price actually was.
  const marks = calls
    .map((c) => {
      const i = prices.findIndex((p) => p.d >= c.as_of);
      return i < 0 ? null : { c, i };
    })
    .filter(Boolean) as { c: Call; i: number }[];

  const seen = new Set<string>();
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
      aria-label="Price with a marker at each recorded call">
      {[0, 0.5, 1].map((f) => (
        <line key={f} x1={PAD_L} x2={W - PAD_R} y1={PAD_T + f * (H - PAD_T - PAD_B)}
          y2={PAD_T + f * (H - PAD_T - PAD_B)} stroke="#e2e8f0" strokeWidth="1" />
      ))}
      {[hi, lo].map((v, k) => (
        <text key={k} x={PAD_L - 6} y={y(v) + 3} textAnchor="end"
          className="fill-slate-400" style={{ fontSize: 10, fontFamily: 'ui-monospace, monospace' }}>
          {Math.round(v).toLocaleString('en-IN')}
        </text>
      ))}
      <path d={path} fill="none" stroke="#475569" strokeWidth="1.6"
        strokeLinejoin="round" strokeLinecap="round" />
      {marks.map(({ c, i }, k) => {
        const key = `${c.as_of}-${c.kind}`;
        const dupDate = seen.has(c.as_of);
        seen.add(c.as_of);
        return (
          <g key={key}>
            <line x1={x(i)} x2={x(i)} y1={PAD_T} y2={H - PAD_B}
              stroke={callStroke(c)} strokeWidth="1" strokeDasharray="3 3" opacity="0.55" />
            <circle cx={x(i)} cy={y(prices[i].c)} r="3.5" fill="#fff"
              stroke={callStroke(c)} strokeWidth="2" />
            {!dupDate && (
              <text x={x(i)} y={H - PAD_B + 13} textAnchor="middle"
                className="fill-slate-500" style={{ fontSize: 9.5 }}>
                {c.as_of.slice(5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function AIInfraCompanyPage({ symbol, embedded = false }:
  { symbol: string; embedded?: boolean }) {
  const [d, setD] = useState<Payload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/ai-infra-company/${encodeURIComponent(symbol)}`);
      const j = await r.json();
      if (!r.ok || !j.success) throw new Error(j.detail || `HTTP ${r.status}`);
      setD(j);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => { load(); }, [load]);

  const stance = d?.company?.outlook_3m;
  const grade = d?.company?.grade_12m;
  const last = d?.prices?.length ? d.prices[d.prices.length - 1] : null;

  const { supporting, contradicting } = useMemo(() => ({
    supporting: (d?.hypotheses ?? []).filter((h) => h.role === 'supporting'),
    contradicting: (d?.hypotheses ?? []).filter((h) => h.role === 'contradicting'),
  }), [d]);

  const bothSides = useMemo(() => {
    const s = new Set(supporting.map((h) => h.hypothesis));
    return contradicting.filter((h) => s.has(h.hypothesis)).map((h) => h.hypothesis);
  }, [supporting, contradicting]);

  if (loading) return <Shell embedded={embedded}><div className="text-sm text-slate-400 py-20 text-center">Loading {symbol}…</div></Shell>;
  if (err || !d) {
    return (
      <Shell embedded={embedded}>
        <div className={`${card} border-rose-200 bg-rose-50/40`}>
          <div className="text-sm font-bold text-rose-700">Could not load {symbol}</div>
          <p className="text-xs text-rose-600 mt-1">{err}</p>
          <button onClick={load} className="mt-3 px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-900 text-white">
            Retry
          </button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell embedded={embedded}>
      {/* ---- Identity ---- */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-black text-slate-900">{d.symbol}</h1>
            {d.company.fno && (
              <span className="text-[10px] font-black px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 border border-violet-200">F&O</span>
            )}
            {stance && (
              <span className={`text-[11px] font-black px-2 py-0.5 rounded-full border ${STANCE_CLS[stance.stance]}`}>
                {STANCE_GLYPH[stance.stance]} 3M {stance.stance} · {stance.confidence?.[0]}
              </span>
            )}
            {grade && (
              <span className={`text-[11px] font-black uppercase px-2 py-0.5 rounded-full border ${GRADE_CLS[grade.grade]}`}>
                12M {grade.grade} · {grade.conviction?.[0]}
              </span>
            )}
          </div>
          <div className="text-sm text-slate-500 mt-0.5">
            {d.company.name} · {d.segment_label} · {d.company.exposure} exposure · {d.company.mcap_bucket} cap
          </div>
        </div>
        <div className="text-right">
          {last ? (
            <>
              <div className="text-2xl font-black font-mono text-slate-900">
                ₹{last.c.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
              </div>
              <div className="text-[10px] text-slate-400">close {last.d} · from price_bars</div>
            </>
          ) : (
            <div className="text-[11px] text-slate-400 max-w-[260px]">no stored bars</div>
          )}
        </div>
      </div>

      {/* ---- 1. CALL TRACK RECORD — the falsifiable bit, so it goes first ---- */}
      <Section
        title="What we said, and what happened next"
        sub="Every stance and grade ever recorded on this name, scored over the window it was actually live — not to today, because a call we abandoned should not be credited or blamed for what came after we abandoned it.">
        {d.price_note ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-2 text-[11px] text-amber-800">
            {d.price_note}
          </div>
        ) : (
          <CallChart prices={d.prices} calls={d.calls} />
        )}
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-100">
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Dated</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Call</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Held until</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase text-right">While live</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase text-right">To date</th>
                <th className="py-1.5 font-black text-[10px] uppercase">Reasoning at the time</th>
              </tr>
            </thead>
            <tbody>
              {d.calls.map((c) => (
                <tr key={`${c.kind}-${c.as_of}`} className="border-b border-slate-50 align-top">
                  <td className="py-2 pr-3 font-mono text-slate-500 whitespace-nowrap">{c.as_of}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full border ${callCls(c)}`}>
                      {c.kind === 'grade' ? '12M' : '3M'} {c.value}
                    </span>
                    {c.conviction && <span className="ml-1 text-[10px] text-slate-400">· {c.conviction}</span>}
                  </td>
                  <td className="py-2 pr-3 text-slate-500 whitespace-nowrap">
                    {c.live
                      ? <span className="text-emerald-700 font-bold">current</span>
                      : <span title="superseded by a later call of the same kind">revised {c.superseded_on}</span>}
                  </td>
                  <td className="py-2 pr-3 text-right"><Pct v={c.move_while_live_pct} bold /></td>
                  <td className="py-2 pr-3 text-right"><Pct v={c.move_to_date_pct} /></td>
                  <td className="py-2 text-slate-600 leading-snug min-w-[300px]">{c.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!d.calls.length && <p className="text-[11px] text-slate-400">No calls recorded yet.</p>}
        </div>
      </Section>

      {/* ---- 2. HYPOTHESIS LEDGER ---- */}
      <Section
        title="What this name is load-bearing for"
        sub="The company as an edge into the theme's hypothesis set. 'Load-bearing' means the hypothesis materially weakens if this claim fails; the names listed beside it carry the same side, so a short list is a thin hypothesis.">
        {bothSides.length > 0 && (
          <div className="rounded-xl border border-indigo-200 bg-indigo-50/50 px-3 py-2 text-[11px] text-indigo-800 mb-3">
            <b>Sits on both sides of {bothSides.join(', ')}.</b> That is not a contradiction to
            resolve — it is where the thesis is genuinely thin, and it is the first thing worth reading here.
          </div>
        )}
        {!d.hypotheses.length && (
          <p className="text-[11px] text-slate-400">
            This name is not cited in any hypothesis. It is in the theme by segment membership
            rather than by evidence — which is itself worth knowing.
          </p>
        )}
        <div className="grid md:grid-cols-2 gap-3">
          {[['supporting', supporting] as const, ['contradicting', contradicting] as const].map(([role, list]) => (
            <div key={role}>
              <div className={`text-[10px] font-black uppercase mb-1.5 ${role === 'supporting' ? 'text-emerald-700' : 'text-rose-600'}`}>
                {role} ({list.length})
              </div>
              <div className="space-y-2">
                {list.map((h) => (
                  <div key={`${h.hypothesis}-${h.role}-${h.claim.slice(0, 12)}`}
                    className={`rounded-xl border px-3 py-2 ${role === 'supporting' ? 'border-emerald-200 bg-emerald-50/40' : 'border-rose-200 bg-rose-50/40'}`}>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[11px] font-black text-slate-800">{h.hypothesis}</span>
                      <span className={`text-[9px] font-black uppercase px-1.5 py-0.5 rounded border ${h.weight === 'load-bearing' ? 'bg-white text-slate-700 border-slate-300' : 'bg-transparent text-slate-400 border-slate-200'}`}>
                        {h.weight}
                      </span>
                      {h.status && <span className="text-[9px] text-slate-400">{h.status}</span>}
                    </div>
                    <p className="text-[11px] text-slate-600 mt-1 leading-snug italic">{h.hypothesis_text}</p>
                    <p className="text-[11px] text-slate-700 mt-1.5 leading-snug">{h.claim}</p>
                    <p className="text-[10px] text-slate-400 mt-1.5">
                      {h.other_names_same_side.length
                        ? <>also on this side: {h.other_names_same_side.join(', ')}</>
                        : <b className="text-amber-700">this name carries this side alone</b>}
                    </p>
                  </div>
                ))}
                {!list.length && <p className="text-[11px] text-slate-300">none</p>}
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* ---- 3. EVIDENCE, AGED ---- */}
      <Section title="Evidence, with its age" sub={d.half_life_note}>
        <div className="space-y-1.5">
          {d.evidence.map((e, i) => {
            const f = e.freshness ?? 0;
            return (
              <div key={i}
                className={`flex items-start gap-3 rounded-lg px-2.5 py-2 border ${e.stale ? 'border-slate-100 bg-slate-50/60' : 'border-slate-200 bg-white'}`}>
                <div className="w-16 shrink-0">
                  <div className="font-mono text-[11px] text-slate-500">{e.date}</div>
                  <div className="text-[9px] text-slate-400">
                    {e.age_days == null ? '—' : `${e.age_days}d`}
                  </div>
                </div>
                <div className="w-14 shrink-0 pt-1" title={`${e.decay_class} — ${DECAY_HINT[e.decay_class] ?? ''} · half-life ${e.half_life_days}d`}>
                  <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
                    <div className="h-full rounded-full bg-slate-700" style={{ width: `${Math.round(f * 100)}%` }} />
                  </div>
                  <div className="text-[9px] text-slate-400 mt-0.5">{e.decay_class}</div>
                </div>
                <div className="min-w-0 flex-1">
                  <p className={`text-[12px] leading-snug ${e.stale ? 'text-slate-400' : 'text-slate-700'}`}>
                    {e.note}
                  </p>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {e.source}
                    {e.stale && <span className="ml-2 text-slate-400">· past its half-life — kept, not news</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* ---- Grade inputs, thesis, risk ---- */}
      <div className="grid md:grid-cols-2 gap-4">
        {grade && (
          <Section title="The three grade inputs" sub="Stored separately and never summed — read them and disagree with the weighting.">
            <div className="space-y-2 text-[12px]">
              <Row k="evidence" v={grade.evidence_strength} />
              <Row k="exposure" v={grade.exposure} />
              <Row k="priced in" v={grade.priced_in} />
              <div className="pt-2 border-t border-slate-100 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[12px]">
                <span>P/E {grade.valuation?.pe_ttm ?? 'n/m'}</span>
                <span>1Y <Pct v={grade.valuation?.y1_pct} /></span>
                <span>vs 52w hi <Pct v={grade.valuation?.from_52w_hi_pct} /></span>
              </div>
              <p className="text-[10px] text-slate-400 leading-snug">{grade.valuation?.pe_note}</p>
              <div className="pt-2 border-t border-slate-100">
                <div className={hdr}>What changes the grade</div>
                <p className="text-[11px] text-amber-700 leading-snug mt-1">{grade.changes_if}</p>
              </div>
            </div>
          </Section>
        )}
        <Section title="Thesis and key risk">
          <p className="text-[12px] text-slate-700 leading-snug">{d.company.thesis}</p>
          <div className={`${hdr} mt-3`}>Key risk</div>
          <p className="text-[12px] text-rose-600 leading-snug mt-1">{d.company.risk}</p>
          {stance?.watch && (
            <>
              <div className={`${hdr} mt-3`}>What changes the 3-month lean</div>
              <p className="text-[12px] text-amber-700 leading-snug mt-1">{stance.watch}</p>
            </>
          )}
        </Section>
      </div>

      {/* ---- Peers ---- */}
      <Section title={`Others in ${d.segment_label}`} sub="Same segment, so the difference between them is evidence and price rather than sector.">
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-100">
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Symbol</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">12M</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Evidence</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Priced in</th>
                <th className="py-1.5 pr-3 font-black text-[10px] uppercase">Exposure</th>
                <th className="py-1.5 font-black text-[10px] uppercase text-right">P/E</th>
              </tr>
            </thead>
            <tbody>
              {d.peers.map((p) => (
                <tr key={p.symbol} className={`border-b border-slate-50 ${p.is_self ? 'bg-slate-50' : ''}`}>
                  <td className="py-1.5 pr-3">
                    {p.is_self
                      ? <span className="font-black text-slate-900">{p.symbol}</span>
                      : <a href={`/intel/ai-infra/${p.symbol}`} className="font-bold text-indigo-600 hover:underline">{p.symbol}</a>}
                  </td>
                  <td className="py-1.5 pr-3">
                    {p.grade && (
                      <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full border ${GRADE_CLS[p.grade]}`}>
                        {p.grade}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-slate-500">{p.evidence_strength}</td>
                  <td className="py-1.5 pr-3 text-slate-500">{p.priced_in}</td>
                  <td className="py-1.5 pr-3 text-slate-500">{p.exposure}</td>
                  <td className="py-1.5 text-right font-mono text-slate-600">{p.pe_ttm ?? 'n/m'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3">
        <p className="text-[10px] text-slate-500 leading-relaxed">
          <b className="text-slate-600">Honesty note:</b> {d.disclaimer}
        </p>
      </div>
    </Shell>
  );
}

function Row({ k, v }: { k: string; v?: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[10px] font-black uppercase text-slate-400 w-20 shrink-0">{k}</span>
      <span className="text-slate-700">{v ?? '—'}</span>
    </div>
  );
}

function Shell({ children, embedded = false }: { children: React.ReactNode; embedded?: boolean }) {
  // Embedded inside AIInfraThemePanel the page already sits on a background, under a tab
  // strip, with the theme's own header above it. Re-wrapping it in min-h-screen chrome
  // and a second "back to theme" link nests a page inside a page.
  if (embedded) return <div className="space-y-4">{children}</div>;
  return (
    <div className="min-h-screen bg-slate-100 py-6">
      <div className="max-w-6xl mx-auto px-4 space-y-4">
        <a href="/intel/ai-infra" className="inline-flex items-center gap-1.5 text-[11px] font-bold text-slate-500 hover:text-slate-800">
          <ArrowLeft className="w-3.5 h-3.5" /> AI Infrastructure theme
        </a>
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <Server className="w-3.5 h-3.5 text-indigo-500" /> AI Infrastructure · India beneficiaries
        </div>
        {children}
      </div>
    </div>
  );
}
