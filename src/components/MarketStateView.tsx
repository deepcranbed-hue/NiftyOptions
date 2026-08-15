import { useCallback, useState } from 'react';

/**
 * MarketStateView — the three-layer market-state dashboard.
 *
 *   REGIME       (what kind of market)   — pin/vol strengths, NEVER a direction
 *   DIRECTIONAL  (which way is the edge)  — position votes + net score
 *   EXECUTION    (how to trade now)       — VWAP / volume / momentum confirmation
 *
 * Honest by design: the regime "market type" is a flagged PRIOR heuristic, and the net
 * score carries the same "no validated edge yet" caveat as the rest of the framework.
 * It surfaces the regime & execution layers the directional Signal window never showed;
 * it does NOT assert conditional reliability that hasn't been measured.
 */
type Sig = {
  name: string; label: string; family: string;
  score: number | null; confidence: number | null; status: string;
  detail: Record<string, any>;
};
type Contradiction = { a: string; b: string; note: string };
type Quality = {
  grade: string; agreement: number; completeness: number; n_ok: number; n_total: number;
  contradictions: Contradiction[]; n_contradictions: number; note: string;
};
type FactorMember = { name: string; role: string; score: number | null; ok: boolean; agrees: boolean };
type Factor = {
  name: string; label: string; kind: string;
  estimate: number | null; confidence: number | null; agreement: number | null;
  quality: number | null; quality_basis: string | null;
  members: FactorMember[];
};
type State = {
  ts: string; expiry: string; spot: number;
  regime: Sig[]; directional: Sig[]; execution: Sig[];
  factors: Factor[];
  net_directional_score: number | null;
  market_type: string; regime_note: string; regime_why: string[];
  market_quality: Quality; disclaimer: string;
  error?: string;
};

const card = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';
const hdr = 'text-[11px] font-black uppercase tracking-wide text-slate-400';

function StrengthBar({ v }: { v: number | null }) {
  // 0..1 strength (regime/execution magnitude) — neutral slate fill, no bull/bear colour.
  const pct = v == null ? 0 : Math.max(0, Math.min(1, v)) * 100;
  return (
    <div className="h-2 flex-1 rounded-full bg-slate-100 overflow-hidden">
      <div className="h-full rounded-full bg-slate-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

function ScoreBar({ v }: { v: number | null }) {
  // −1..+1 directional score — green right of centre (bull), rose left (bear).
  const s = v == null ? 0 : Math.max(-1, Math.min(1, v));
  const w = Math.abs(s) * 50;
  return (
    <div className="relative h-2 flex-1 rounded-full bg-slate-100 overflow-hidden">
      <div className="absolute top-0 bottom-0 left-1/2 w-px bg-slate-300" />
      <div className={`absolute top-0 bottom-0 ${s >= 0 ? 'bg-emerald-500' : 'bg-rose-500'}`}
        style={{ left: s >= 0 ? '50%' : `${50 - w}%`, width: `${w}%` }} />
    </div>
  );
}

export function MarketStateView() {
  const [st, setSt] = useState<State | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch('/api/strategy/market-state');
      const j = await r.json();
      if (j.error) { setErr(j.error); setSt(null); } else setSt(j);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);
  // NO auto-run — computes only when the user presses Run (don't drain the machine).

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-slate-800">Market State</h2>
          <p className="text-xs text-slate-500">
            Regime · Directional · Execution — the three layers, as of{' '}
            {st ? `${st.ts.slice(11, 16)} (spot ${st.spot})` : '—'}
          </p>
        </div>
        <button onClick={load} disabled={loading}
          className="px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-600 text-white disabled:opacity-50">
          {loading ? 'Running…' : st ? 'Refresh' : 'Run'}
        </button>
      </div>

      {err && <div className="text-xs text-rose-600">Couldn’t load market state — {err}. Is the backend running?</div>}

      {!st && !loading && !err && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
          <p className="text-sm font-semibold text-slate-600">Press <span className="text-indigo-600">Run</span> to compute the current market state.</p>
          <p className="text-[11px] text-slate-400 mt-1">Nothing runs automatically — it evaluates every signal on demand.</p>
        </div>
      )}

      {st && (
        <>
          {/* ---- MARKET QUALITY (coherence of the evidence, not a prediction) ---- */}
          {(() => {
            const q = st.market_quality;
            const gc = q.grade === 'HIGH' ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
              : q.grade === 'MEDIUM' ? 'bg-amber-50 text-amber-700 border-amber-200'
              : 'bg-rose-50 text-rose-700 border-rose-200';
            return (
              <div className={card}>
                <div className="flex items-center justify-between">
                  <span className={hdr}>Market Quality — can today’s signals be trusted?</span>
                  <span className={`text-[11px] font-black px-2.5 py-0.5 rounded-full border ${gc}`}>{q.grade}</span>
                </div>
                <div className="grid sm:grid-cols-3 gap-4 mt-3">
                  <div>
                    <div className="flex justify-between text-[11px]"><span className="text-slate-500">Signal agreement</span><span className="font-mono">{Math.round(q.agreement * 100)}%</span></div>
                    <div className="flex mt-0.5"><StrengthBar v={q.agreement} /></div>
                  </div>
                  <div>
                    <div className="flex justify-between text-[11px]"><span className="text-slate-500">Data completeness</span><span className="font-mono">{q.n_ok}/{q.n_total}</span></div>
                    <div className="flex mt-0.5"><StrengthBar v={q.completeness} /></div>
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-500">Contradictions</div>
                    <div className={`text-lg font-black ${q.n_contradictions ? 'text-rose-600' : 'text-emerald-600'}`}>{q.n_contradictions}</div>
                  </div>
                </div>
                {q.contradictions.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {q.contradictions.map((c, i) => (
                      <div key={i} className="text-[11px] text-rose-600 flex items-start gap-1">
                        <span className="font-black">⚠</span><span>{c.note}</span>
                      </div>
                    ))}
                  </div>
                )}
                <p className="text-[9px] text-slate-400 mt-2 leading-snug">{q.note}</p>
              </div>
            );
          })()}

          {/* ---- FACTOR BELIEFS (sensors → hidden market properties) ---- */}
          {st.factors && st.factors.length > 0 && (
            <div className={card}>
              <span className={hdr}>Factor beliefs — hidden market properties (prior aggregation)</span>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-3">
                {st.factors.map((f) => (
                  <div key={f.name} className="rounded-xl border border-slate-100 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold text-slate-700">{f.label}</span>
                      <span className="flex items-center gap-1.5">
                        {f.kind === 'slow' && <span className="text-[9px] font-bold px-1.5 rounded bg-slate-100 text-slate-500">slow</span>}
                        <span className={`text-[11px] font-black font-mono ${f.estimate == null ? 'text-slate-400' : f.estimate >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                          {f.estimate == null ? 'no data' : (f.estimate >= 0 ? '+' : '') + f.estimate}
                        </span>
                      </span>
                    </div>
                    <div className="flex items-center mt-1"><ScoreBar v={f.estimate} /></div>
                    <div className="text-[10px] text-slate-400 mt-1">
                      confidence {f.confidence == null ? '—' : f.confidence} · agreement {f.agreement == null ? '—' : Math.round((f.agreement || 0) * 100) + '%'} ·{' '}
                      <span className={f.quality != null && f.quality < 0.4 && (f.confidence ?? 0) > 0.6 ? 'text-amber-600 font-semibold' : ''}>
                        quality {f.quality == null ? 'unmeasured' : `${f.quality}${f.quality_basis === 'provisional' ? ' (prov.)' : ''}`}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {f.members.map((m) => (
                        <span key={m.name} title={`${m.role} · score ${m.score ?? 'n/a'}`}
                          className={`text-[9px] px-1.5 py-0.5 rounded-full border ${!m.ok ? 'border-slate-200 text-slate-300'
                            : m.agrees ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-600'}`}>
                          {m.ok ? (m.agrees ? '✓' : '✗') : '·'} {m.name.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[9px] text-slate-400 mt-2 leading-snug">
                Signals are sensors; each factor is a belief about one market property. Estimate =
                role-weighted member vote; ✓/✗ = member agrees/disagrees with the factor. PRIOR
                aggregation — roles and weights become evidence-based as sessions accumulate.
              </p>
            </div>
          )}

          <div className="grid lg:grid-cols-3 gap-4">
            {/* ---- REGIME ---- */}
            <div className={card}>
              <div className="flex items-center justify-between">
                <span className={hdr}>Market Regime</span>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                  {st.market_type}
                </span>
              </div>
              <p className="text-[11px] text-slate-500 mt-1 mb-3">{st.regime_note}</p>
              <div className="space-y-2">
                {st.regime.map((s) => (
                  <div key={s.name}>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-600">{s.label}</span>
                      <span className="font-mono text-slate-500">
                        {s.score == null ? '—' : Math.round(s.score * 100)}
                        {s.detail?.regime ? ` · ${s.detail.regime}` : ''}
                      </span>
                    </div>
                    <div className="flex items-center mt-0.5"><StrengthBar v={s.score} /></div>
                  </div>
                ))}
              </div>
              <p className="text-[9px] text-amber-600 mt-3 leading-snug">
                Regime signals never vote direction — they say what kind of day it is. The
                market-type label is a PRIOR heuristic, not a validated classifier.
              </p>
            </div>

            {/* ---- DIRECTIONAL ---- */}
            <div className={card}>
              <div className="flex items-center justify-between">
                <span className={hdr}>Directional Signals</span>
                <span className={`text-sm font-black font-mono ${(st.net_directional_score ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  net {st.net_directional_score == null ? '—' : (st.net_directional_score >= 0 ? '+' : '') + st.net_directional_score}
                </span>
              </div>
              <div className="space-y-2 mt-3">
                {st.directional.map((s) => (
                  <div key={s.name}>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-600">{s.label}</span>
                      <span className={`font-mono ${s.score == null ? 'text-slate-400' : s.score >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {s.score == null ? '—' : (s.score >= 0 ? '+' : '') + s.score}
                      </span>
                    </div>
                    <div className="flex items-center mt-0.5"><ScoreBar v={s.score} /></div>
                  </div>
                ))}
              </div>
            </div>

            {/* ---- EXECUTION ---- */}
            <div className={card}>
              <span className={hdr}>Execution Conditions</span>
              <div className="space-y-2 mt-3">
                {st.execution.map((s) => (
                  <div key={s.name}>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-600">{s.label}</span>
                      <span className={`font-mono ${s.score == null ? 'text-slate-400' : s.score >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {s.score == null ? '—' : (s.score >= 0 ? '+' : '') + s.score}
                      </span>
                    </div>
                    <div className="flex items-center mt-0.5"><ScoreBar v={s.score} /></div>
                  </div>
                ))}
              </div>
              <p className="text-[9px] text-slate-400 mt-3 leading-snug">
                Confirmation reads (VWAP, volume, momentum) — they don’t vote direction, they
                modulate whether positioning is being accepted.
              </p>
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3">
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <b className="text-slate-600">Honesty note:</b> {st.disclaimer}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
