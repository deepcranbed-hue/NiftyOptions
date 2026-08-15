import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, RefreshCw, TrendingUp, AlertTriangle, ExternalLink } from 'lucide-react';
import {
  type Row, type View,
  BIAS_STYLE, VERDICT_STYLE, card, fmtPct, rupee, stockHref, readLine, historyLine,
  holdingSpans, months, Pct, RangeBar,
} from './nifty50Shared';

/**
 * The roomy single-stock detail, in two wrappers.
 *
 *   Nifty50StockDetail — just the content. Rendered by Nifty50Panel inside a per-stock
 *     tab in its own tab strip (alongside Today's Market / Macro Factors / Nifty
 *     Stocks). This is the normal path: a left-click on a symbol opens a tab.
 *   Nifty50StockPage — the same content behind a fetch + a page shell, mounted at
 *     /intel/nifty50/<SYMBOL>. Reached by cmd/ctrl/middle-click on the symbol, or by a
 *     shared or bookmarked link, and rendered with no app chrome at all.
 *
 * Both show the SAME payload as the panel's inline expand; the difference is room. The
 * inline version has to survive inside a table cell, so everything competes for one
 * column of width. Here each layer — returns, valuation, expectation, results
 * behaviour, curated drivers — gets its own block and can be read rather than decoded.
 */

const STAT = 'rounded-xl border border-slate-200 bg-white px-3 py-2';
const H = 'text-[10px] font-black uppercase tracking-wide text-slate-400';

// Spelled out, never built as `text-${tone}-500`: Tailwind scans source text for whole
// class names, so an interpolated one is simply never generated and the heading renders
// unstyled. Every value here appears literally.
const SECTION_TONE: Record<string, string> = {
  slate: 'text-slate-500',
  purple: 'text-purple-700',
  amber: 'text-amber-700',
  violet: 'text-violet-700',
  blue: 'text-blue-700',
  emerald: 'text-emerald-700',
  rose: 'text-rose-600',
};

/** `read` is the one-line "how it stands" under the number — the whole point of the
 *  page. It is generated from the scan's own rank/median context, so it is available
 *  for every name without anyone writing 50 sentences by hand. */
function Stat({ label, children, hint, read, extra }:
  { label: string; children: React.ReactNode; hint?: string; read?: string | null; extra?: string | null }) {
  return (
    <div className={STAT} title={hint}>
      <div className={H}>{label}</div>
      <div className="text-sm font-black text-slate-800 font-mono mt-0.5">{children}</div>
      {read && <div className="text-[10px] text-slate-400 leading-snug mt-1">{read}</div>}
      {extra && <div className="text-[10px] text-indigo-500 leading-snug mt-0.5">{extra}</div>}
    </div>
  );
}

const CR = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : '−'}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')} cr`;

function Section({ title, sub, tone = 'slate', children }:
  { title: string; sub?: string; tone?: string; children: React.ReactNode }) {
  return (
    <section className={card}>
      <div className="flex flex-wrap items-baseline gap-2 mb-3">
        <h2 className={`text-[11px] font-black uppercase tracking-wide ${SECTION_TONE[tone] ?? SECTION_TONE.slate}`}>{title}</h2>
        {sub && <span className="text-[10px] text-slate-400 font-normal">{sub}</span>}
      </div>
      {children}
    </section>
  );
}

/** The content. `embedded` = rendered inside the panel's tab strip rather than as a
 *  standalone page, which only changes the chrome around it. */
export function Nifty50StockDetail({ row, view, embedded = false }:
  { row: Row; view: View | null; embedded?: boolean }) {
  const v = row.verdict;
  const exp = row.expectation;
  const rx = row.reaction;
  const idxBasis = v?.basis === 'index';
  const ctx = row.context ?? {};
  const flows = view?.flows;
  const hold = row.fii_holding;
  // The sector FPI layer only ever renders when the backend vouched for it — the
  // fetcher falls back to a hardcoded placeholder, and that must never reach a screen.
  const sectorFpi = flows?.sector_fpi?.[row.sector] ?? null;

  return (
    <div className="space-y-4">
      {!embedded && view?.degraded && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] font-bold text-amber-800">
          Incomplete data — {view.degraded}
        </div>
      )}

      {/* ---- Identity + price ---- */}
      <header className={card}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-black text-slate-900">{row.symbol}</h1>
              {row.yahoo_symbol && row.yahoo_symbol !== row.symbol && (
                <span className="text-[10px] font-black px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                  title={row.symbol_note ?? undefined}>→ {row.yahoo_symbol}</span>
              )}
              {row.partial_history && (
                <span className="text-[10px] font-black px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-300"
                  title={row.history_note ?? undefined}>{row.bars ?? 0} sessions</span>
              )}
              {embedded && (
                <a href={stockHref(row.symbol)} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[10px] font-bold text-slate-400 hover:text-indigo-600"
                  title="Open this stock in its own browser tab">
                  <ExternalLink className="w-3 h-3" /> own window
                </a>
              )}
            </div>
            <p className="text-sm text-slate-500">{row.name}</p>
            <p className="text-[11px] text-slate-400 mt-0.5">
              {row.sector}{row.weight != null && <> · {row.weight}% of the index</>}
            </p>
          </div>
          <div className="text-right">
            <div className="text-3xl font-black text-slate-900 font-mono">{rupee(row.last)}</div>
            <div className="text-sm mt-0.5"><Pct v={row.d1_pct} /></div>
            <p className="text-[10px] text-slate-400 mt-0.5">
              {row.as_of ? `close ${row.as_of} · Yahoo, ~15-min delayed` : 'no price'}
            </p>
          </div>
        </div>
        {row.symbol_note && <p className="text-[11px] text-amber-700 mt-3">{row.symbol_note}</p>}
        {row.history_note && <p className="text-[11px] text-amber-700 mt-1">{row.history_note}</p>}
        {row.suspect_corporate_action && (
          <p className="text-[11px] font-bold text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2 mt-3">
            ⚠ The 1-day move of {row.d1_pct}% is too large to be a normal session for an index
            constituent. Almost certainly a corporate action — a bonus, split or demerger going
            ex — that the price series has not been adjusted for yet. Treat today's return, and
            anything derived from it, as unreliable until the source restates the history.
          </p>
        )}
      </header>

      {/* ---- Returns + where it sits in its range ---- */}
      <Section title="Returns" sub="vs the index over the same window — a blank means the history can't fill it">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {([['1 day', 'd1_pct', row.d1_pct, null], ['1 week', 'w1_pct', row.w1_pct, row.rel_1w],
             ['6 months', 'm6_pct', row.m6_pct, row.rel_6m], ['1 year', 'y1_pct', row.y1_pct, row.rel_1y]] as const)
            .map(([label, key, abs, rel]) => (
              <div key={label} className={STAT}>
                <div className={H}>{label}</div>
                <div className="text-base font-black mt-0.5"><Pct v={abs} /></div>
                {rel != null && (
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {rel >= 0 ? 'beat' : 'lagged'} Nifty by <b className="font-mono">{fmtPct(Math.abs(rel))}</b>
                  </div>
                )}
                {readLine(ctx[key]) && (
                  <div className="text-[10px] text-slate-400 leading-snug mt-0.5">{readLine(ctx[key])}</div>
                )}
                {abs == null && row.partial_history && (
                  <div className="text-[10px] text-slate-400 leading-snug mt-0.5">
                    not shown — only {row.bars ?? 0} sessions of history
                  </div>
                )}
              </div>
            ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <span className={H}>52-week range</span>
            <RangeBar v={row.pos_52w} width="w-40" />
          </div>
          <span className="text-[11px] text-slate-500 font-mono"
            title="Dividend-adjusted closes, so these sit slightly below NSE's published extremes — the gap widens with the yield.">
            {rupee(row.lo_52w)} – {rupee(row.hi_52w)}
            {row.pos_52w != null && <span className="text-slate-400"> · {Math.round(row.pos_52w * 100)}% up the range</span>}
          </span>
        </div>
      </Section>

      {/* ---- Valuation + the categorical verdict ---- */}
      <Section title="Valuation" sub="cross-sectional, not a fair value">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <Stat label="Trailing P/E" read={readLine(ctx.pe)}
            extra={historyLine(row.pe_history)}>{row.pe ?? '—'}</Stat>
          <Stat label="Forward P/E" read={readLine(ctx.fwd_pe)}>{row.fwd_pe ?? '—'}</Stat>
          <Stat label="P/B" read={readLine(ctx.pb)}>{row.pb ?? '—'}</Stat>
          <Stat label="Dividend yield" read={readLine(ctx.div_yield)}
            hint="Derived from dividendRate ÷ price where available; implausible values are withheld rather than shown.">
            {row.div_yield != null ? `${row.div_yield}%` : '—'}
          </Stat>
          <div className={STAT}>
            <div className={H}>Pricing</div>
            {v ? (
              <span className={`inline-block mt-1 text-[11px] font-black px-2 py-0.5 rounded-full border ${VERDICT_STYLE[v.label]} ${idxBasis ? 'border-dashed opacity-80' : ''}`}>
                {v.label} {v.vs_median_pct >= 0 ? '+' : ''}{v.vs_median_pct}%
              </span>
            ) : <span className="text-slate-300 font-mono">—</span>}
          </div>
        </div>

        {v ? (
          <div className="mt-3 text-[11px] text-slate-600 leading-relaxed">
            {v.metric === 'pe' ? 'Trailing P/E' : 'P/B (P/E unavailable or negative — book-value fallback)'}{' '}
            <b>{v.value ?? (v.metric === 'pe' ? row.pe : row.pb)}</b> is{' '}
            <b className={v.vs_median_pct >= 0 ? 'text-rose-600' : 'text-emerald-700'}>
              {v.vs_median_pct >= 0 ? '+' : ''}{v.vs_median_pct}%
            </b>{' '}
            vs {idxBasis
              ? <>the <b>whole-index</b> median of <b>{v.sector_median}</b></>
              : <>the {row.sector} peer median of <b>{v.sector_median}</b>{v.peers ? ` (${v.peers} names)` : ''}</>}
            {' '}→ <b>{v.label}</b> (thresholds: ≥+25% rich, ≤−25% cheap).
            {idxBasis && (
              <b className="text-amber-700"> {row.sector} has fewer than 3 valued peers, so this compares the
              stock to the index as a whole rather than to anything like it — weak evidence, not a peer verdict.</b>
            )}
            <span className="block mt-1 text-slate-400">
              A premium can be earned (growth, quality) and a discount deserved (weak fundamentals). This flags
              the question; it doesn't answer it.
            </span>
          </div>
        ) : (
          <p className="mt-3 text-[11px] text-slate-400">
            {row.fundamentals_ok === false
              ? 'No verdict — the fundamentals fetch failed for this name. That is a data gap, not a statement about the company; re-run the scan.'
              : 'No verdict — P/E and P/B unavailable from the data source.'}
          </p>
        )}
      </Section>

      {/* ---- Researched note — the layer no rank or median can supply ----
           Placed straight after Valuation because that is the number it exists to
           explain. Everything above it is computed; this is dated judgment with
           sources, and it says so. */}
      {row.valuation_note && (() => {
        const n = row.valuation_note;
        return (
          <section className="rounded-2xl border-2 border-indigo-200 bg-indigo-50/30 p-4 shadow-sm">
            <div className="flex flex-wrap items-baseline gap-2 mb-2">
              <h2 className="text-[11px] font-black uppercase tracking-wide text-indigo-700">
                Why the numbers look like this
              </h2>
              <span className="text-[10px] text-slate-400">
                researched{n.as_of ? ` · as of ${n.as_of}` : ''}{n.applies_to ? ` · ${n.applies_to}` : ''} · decays
              </span>
            </div>

            <p className="text-sm font-black text-slate-900 leading-snug">{n.headline}</p>
            {n.summary && <p className="text-[12px] text-slate-700 leading-relaxed mt-1.5">{n.summary}</p>}

            {!!n.better_lens?.metrics?.length && (
              <div className="mt-3">
                {n.better_lens.note && (
                  <p className="text-[11px] text-slate-600 mb-1.5">{n.better_lens.note}</p>
                )}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {n.better_lens.metrics.map((mx) => (
                    <div key={mx.label} className="rounded-xl border border-indigo-200 bg-white px-3 py-2">
                      <div className={H}>{mx.label}</div>
                      <div className="text-base font-black font-mono text-indigo-800 mt-0.5">{mx.value}</div>
                      {mx.detail && <div className="text-[10px] text-slate-400 leading-snug mt-1">{mx.detail}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid md:grid-cols-2 gap-4 mt-3">
              {!!n.why_the_multiple?.length && (
                <div>
                  <div className={H}>What the multiple is actually measuring</div>
                  <ul className="space-y-1.5 mt-1">
                    {n.why_the_multiple.map((t, i) => (
                      <li key={i} className="text-[12px] text-slate-700 leading-relaxed flex items-start gap-2">
                        <span className="font-black text-indigo-400 shrink-0">{i + 1}</span><span>{t}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {!!n.current_scenario?.length && (
                <div>
                  <div className={H}>Where it stands right now</div>
                  <ul className="space-y-1.5 mt-1">
                    {n.current_scenario.map((t, i) => (
                      <li key={i} className="text-[12px] text-slate-700 leading-relaxed flex items-start gap-2">
                        <span className="text-indigo-400 shrink-0">·</span><span>{t}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {!!n.what_would_change_it?.length && (
              <div className="mt-3">
                <div className="text-[10px] font-black uppercase tracking-wide text-amber-700">What would change it</div>
                <ul className="space-y-1 mt-1">
                  {n.what_would_change_it.map((t, i) => (
                    <li key={i} className="text-[12px] text-amber-800 leading-relaxed flex items-start gap-1.5">
                      <span className="font-black shrink-0">→</span><span>{t}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {n.caveats && <p className="text-[10px] text-slate-500 leading-snug mt-3 italic">{n.caveats}</p>}

            {!!n.sources?.length && (
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className={H}>Sources</span>
                {n.sources.map((s) => (
                  <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer"
                    className="text-[10px] text-indigo-600 hover:underline inline-flex items-center gap-0.5">
                    {s.title} <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                ))}
              </div>
            )}
          </section>
        );
      })()}

      {/* ---- Mechanical reference points ---- */}
      <Section title="Reference points" sub="where price HAS been and where valuation WOULD sit at peer parity — no probability attached">
        <div className="grid md:grid-cols-3 gap-2">
          <div className={STAT}>
            <div className={H}>To its 52-week high {row.hi_52w ? `(${rupee(row.hi_52w)})` : ''}</div>
            <div className="text-base font-black font-mono text-emerald-700 mt-0.5">
              {row.up_to_high_pct != null ? `+${row.up_to_high_pct}%` : '—'}
            </div>
          </div>
          <div className={STAT}>
            <div className={H}>To its 52-week low {row.lo_52w ? `(${rupee(row.lo_52w)})` : ''}</div>
            <div className="text-base font-black font-mono text-rose-600 mt-0.5">
              {row.down_to_low_pct != null ? `${row.down_to_low_pct}%` : '—'}
            </div>
          </div>
          <div className={STAT}>
            <div className={H}>If its {v?.metric === 'pb' ? 'P/B' : 'P/E'} reverted to the peer median</div>
            {v?.reversion_pct != null ? (
              <div className={`text-base font-black font-mono mt-0.5 ${v.reversion_pct >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                {v.reversion_pct >= 0 ? '+' : ''}{v.reversion_pct}%
              </div>
            ) : (
              <div className="text-[11px] text-slate-400 italic mt-1">{v?.reversion_note ?? '—'}</div>
            )}
          </div>
        </div>
      </Section>

      {/* ---- FII, three layers: this stock → its sector → the market ----
           Ordered most-specific first. Each layer answers a different question and
           moves at a different speed, so they are labelled rather than blended:
           a quarterly stake is not a daily flow and must not read like one. */}
      {(hold || sectorFpi != null || flows?.available || flows?.sector_fpi_note) && (
        <Section title="Foreign money" tone="blue"
          sub="a quarterly stake, a fortnightly sector flow and a daily market flow — three different clocks">
          <div className="grid md:grid-cols-3 gap-2">
            {/* Layer 3 — this company */}
            <div className={STAT}>
              <div className={H}>FII/FPI stake in {row.symbol}</div>
              {hold ? (
                <>
                  <div className="text-lg font-black font-mono text-slate-800 mt-0.5">{hold.latest_pct}%</div>
                  {(() => {
                    // Quote the gap the filings actually span, not an assumed quarter.
                    const s = holdingSpans(hold);
                    return (
                      <>
                        <div className={`text-[11px] font-bold ${hold.direction === 'adding' ? 'text-emerald-600'
                          : hold.direction === 'trimming' ? 'text-rose-600' : 'text-slate-500'}`}>
                          {hold.direction}
                          <span className="font-normal text-slate-400">
                            {' '}{hold.change_pp >= 0 ? '+' : ''}{hold.change_pp}pp vs {hold.prev_pct}%
                            {s.prevLabel ? ` in ${s.prevLabel}` : ' at the previous filing'}
                            {s.gapMonths ? ` (${months(s.gapMonths)})` : ''}
                          </span>
                        </div>
                        <div className="text-[10px] text-slate-400 leading-snug mt-1">
                          As filed for {hold.period}
                          {s.longChangePp != null && s.firstLabel && Math.abs(s.longChangePp) >= 0.3 && (
                            <> · {s.longChangePp >= 0 ? '+' : ''}{s.longChangePp}pp since {s.firstLabel}
                              {s.longMonths ? ` (${months(s.longMonths)})` : ''}</>
                          )}
                          . Percentage POINTS of the company, not a flow — a stake that is
                          restated a few times a year, so it is stale between filings by design.
                        </div>
                      </>
                    );
                  })()}
                  {hold.trend?.length > 1 && (
                    <div className="flex items-end gap-0.5 h-6 mt-1.5" title={hold.trend.map((t) => `${t.period}: ${t.pct}%`).join('\n')}>
                      {hold.trend.map((t) => {
                        const hi = Math.max(...hold.trend.map((x) => x.pct)) || 1;
                        return <div key={t.period} className="flex-1 bg-blue-300 rounded-sm"
                          style={{ height: `${Math.max(8, (t.pct / hi) * 100)}%` }} />;
                      })}
                    </div>
                  )}
                </>
              ) : (
                <div className="text-[10px] text-slate-400 leading-snug mt-1">
                  Not built yet. Run <b>data_agent/fundamentals/fii_holding_backfill.py</b> to
                  generate fii_holdings.json from the quarterly shareholding filings.
                </div>
              )}
            </div>

            {/* Layer 2 — its sector */}
            <div className={STAT}>
              <div className={H}>FPI flow into {row.sector}</div>
              {sectorFpi != null ? (
                <>
                  <div className={`text-lg font-black font-mono mt-0.5 ${sectorFpi >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                    {CR(sectorFpi)}
                  </div>
                  <div className="text-[10px] text-slate-400 leading-snug mt-1">
                    NSDL fortnightly, sector-wise{flows?.sector_fpi_as_of ? ` · ${String(flows.sector_fpi_as_of).slice(0, 10)}` : ''}.
                  </div>
                </>
              ) : (
                <div className="text-[10px] text-slate-400 leading-snug mt-1">
                  {flows?.sector_fpi_note ?? 'Sector-wise FPI unavailable.'}
                </div>
              )}
            </div>

            {/* Layer 1 — the market */}
            <div className={STAT}>
              <div className={H}>Market FII/DII cash</div>
              {flows?.available ? (
                <>
                  <div className={`text-lg font-black font-mono mt-0.5 ${(flows.fii_5d_cr ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                    {CR(flows.fii_5d_cr)}
                  </div>
                  <div className="text-[11px] text-slate-500">
                    FII 5-day · DII <b className="font-mono">{CR(flows.dii_5d_cr)}</b>
                  </div>
                  {flows.regime && (
                    <div className="text-[11px] font-bold text-slate-600 mt-0.5">{flows.regime}</div>
                  )}
                  <div className="text-[10px] text-slate-400 leading-snug mt-1">
                    20-day FII {CR(flows.fii_20d_cr)} · DII {CR(flows.dii_20d_cr)}
                    {!!flows.fii_streak_days && (
                      <> · {Math.abs(flows.fii_streak_days)} straight session
                        {Math.abs(flows.fii_streak_days) === 1 ? '' : 's'} of
                        {flows.fii_streak_days > 0 ? ' buying' : ' selling'}</>
                    )}
                    . To {flows.as_of}. Index-wide — says nothing about {row.symbol} on its own.
                  </div>
                </>
              ) : (
                <div className="text-[10px] text-slate-400 leading-snug mt-1">
                  {flows?.cash_note ?? 'FII/DII cash flow unavailable — run the flows job.'}
                </div>
              )}
            </div>
          </div>
        </Section>
      )}

      {/* ---- Expectation ---- */}
      {exp?.implied_eps_growth_pct != null && (
        <Section title="Priced for — the growth already in the price" tone="purple"
          sub={exp.as_of ? `analyst data as of ${String(exp.as_of).slice(0, 10)}` : undefined}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div className={STAT}>
              <div className={H}>Implied EPS growth</div>
              <div className="text-lg font-black font-mono text-purple-800 mt-0.5">
                {exp.implied_eps_growth_pct >= 0 ? '+' : ''}{Math.round(exp.implied_eps_growth_pct)}%
              </div>
              <div className="text-[10px] text-slate-400">{row.pe ?? '—'}× → {row.fwd_pe ?? '—'}×</div>
            </div>
            {exp.target_mean != null && (
              <Stat label="Mean analyst target">
                {rupee(exp.target_mean)}
                {exp.target_upside_pct != null && (
                  <span className="text-[10px] text-slate-400 font-normal"> ({fmtPct(exp.target_upside_pct)})</span>
                )}
              </Stat>
            )}
            {exp.dispersion_pct != null && (
              <Stat label="Target spread"
                hint="Highest minus lowest target as a share of the mean. Wide = low agreement — but only meaningful once corporate actions are normalised.">
                {exp.dispersion_pct}%
                {exp.target_low != null && exp.target_high != null && (
                  <span className="block text-[10px] text-slate-400 font-normal">
                    {rupee(exp.target_low)} – {rupee(exp.target_high)}
                  </span>
                )}
              </Stat>
            )}
            <Stat label="Coverage">
              {exp.analysts != null ? `${exp.analysts} analysts` : '—'}
              {exp.next_earnings && (
                <span className="block text-[10px] text-slate-400 font-normal">reports {exp.next_earnings}</span>
              )}
            </Stat>
          </div>
          <p className="text-[10px] text-slate-400 mt-2 leading-relaxed">
            Trailing P/E ÷ forward P/E − 1 — no weights set by hand, it is what the two multiples already imply.
            Yahoo's forward P/E is a next-fiscal-year consensus against a trailing-twelve-month denominator, so
            read this as growth embedded over the next one to two years, not a hurdle for the next quarter. It is
            why a company can report strong absolute growth and still fall: good in absolute terms, short of what
            was priced in.
          </p>
        </Section>
      )}

      {/* ---- Measured results behaviour ---- */}
      {rx && (
        <Section title="How it trades results — measured" tone="amber"
          sub={`${rx.n_events} announcements since 2018 · next-session move vs Nifty`}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div className={STAT}>
              <div className={H}>Usually</div>
              <div className="text-base font-black font-mono mt-0.5">{fmtPct(rx.full_mean_rel1d_pct)}</div>
              <span className={`inline-block mt-1 text-[10px] font-black px-2 py-0.5 rounded-full border ${BIAS_STYLE[rx.full_bias]}`}>
                {rx.full_bias}
              </span>
              <div className="text-[10px] text-slate-400 mt-1">{Math.round(rx.full_positive_share * 100)}% positive</div>
            </div>
            <div className={STAT}>
              <div className={H}>Last {rx.recent_n}</div>
              <div className="text-base font-black font-mono mt-0.5">{fmtPct(rx.recent_mean_rel1d_pct)}</div>
              <span className={`inline-block mt-1 text-[10px] font-black px-2 py-0.5 rounded-full border ${BIAS_STYLE[rx.recent_bias]}`}>
                {rx.recent_bias}
              </span>
              <div className="text-[10px] text-slate-400 mt-1">{Math.round(rx.recent_positive_share * 100)}% positive</div>
            </div>
            {rx.recent_mean_sect_rel1d_pct != null && (
              <Stat label="Recent, vs its own sector">{fmtPct(rx.recent_mean_sect_rel1d_pct)}</Stat>
            )}
            <Stat label={`Raw move (last ${rx.recent_n})`}>{fmtPct(rx.recent_mean_r1d_pct)}</Stat>
          </div>
          {rx.diverges && (
            <p className="text-[11px] font-bold text-amber-800 mt-2 flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              Recent results are being read differently from this stock's own history ({rx.full_bias} → {rx.recent_bias}) —
              the change is in the reaction, not necessarily the fundamentals.
            </p>
          )}
          <p className="text-[10px] text-slate-400 mt-2 leading-relaxed">
            Announcement days are detected by trading VOLUME only, never by the size of the move — selecting on the
            move would guarantee the result. Measured against Nifty, so a positive bias means it out-performed on
            results day, not merely that it rose.
          </p>
        </Section>
      )}

      {/* ---- Curated judgment layer ---- */}
      {row.drivers ? (
        <>
          <Section title="Position" sub={view?.drivers_meta ? `curated, as of ${view.drivers_meta.as_of}` : 'curated'}>
            <p className="text-sm text-slate-700 leading-relaxed">{row.drivers.position}</p>
          </Section>

          {row.drivers.recent_change && (
            <Section title="What changed" tone="violet" sub={`reviewed ${row.drivers.recent_change.as_of}`}>
              <ul className="space-y-1">
                {row.drivers.recent_change.points.map((p, i) => (
                  <li key={i} className="text-[12px] text-slate-600 flex items-start gap-2">
                    <span className="text-violet-400 shrink-0">·</span><span>{p}</span>
                  </li>
                ))}
              </ul>
              <p className="text-[12px] font-bold text-violet-800 mt-2">{row.drivers.recent_change.verdict}</p>
            </Section>
          )}

          {row.drivers.latest_quarter && (
            <Section title={`Latest quarter — ${row.drivers.latest_quarter.period}`} tone="blue"
              sub={`as of ${row.drivers.latest_quarter.as_of} · decays in ~a quarter`}>
              <ul className="space-y-1">
                {row.drivers.latest_quarter.points.map((p, i) => (
                  <li key={i} className="text-[12px] text-slate-600 flex items-start gap-2">
                    <span className="text-blue-400 shrink-0">·</span><span>{p}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <div className="grid md:grid-cols-2 gap-4">
            <Section title="Tailwinds — why investors hold" tone="emerald">
              <ul className="space-y-1.5">
                {row.drivers.tailwinds.map((t, i) => (
                  <li key={i} className="text-[12px] text-slate-600 flex items-start gap-2">
                    <span className="font-black text-emerald-600 shrink-0">✓</span><span>{t}</span>
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Headwinds — what can hurt" tone="rose">
              <ul className="space-y-1.5">
                {row.drivers.headwinds.map((h, i) => (
                  <li key={i} className="text-[12px] text-slate-600 flex items-start gap-2">
                    <span className="font-black text-rose-500 shrink-0">✗</span><span>{h}</span>
                  </li>
                ))}
              </ul>
            </Section>
          </div>
        </>
      ) : (
        <Section title="Drivers">
          <p className="text-[11px] text-slate-400">
            No curated drivers for this name yet — add it to nifty50_drivers.json.
          </p>
        </Section>
      )}

      {/* ---- Provenance ---- */}
      <div className="rounded-xl bg-white border border-slate-200 px-4 py-3 space-y-1.5">
        <p className="text-[10px] text-slate-500 leading-relaxed flex items-start gap-1.5">
          <TrendingUp className="w-3.5 h-3.5 shrink-0 mt-0.5 text-slate-400" />
          <span><b className="text-slate-600">Honesty note:</b> {view?.note}</span>
        </p>
        {view?.drivers_meta && (
          <p className="text-[10px] text-amber-700 leading-relaxed">
            <b>Drivers note (as of {view.drivers_meta.as_of}):</b> {view.drivers_meta.note}
          </p>
        )}
        <p className="text-[10px] text-slate-400">
          From the scan{view?.fetched_at ? ` computed ${new Date(view.fetched_at * 1000).toLocaleString('en-IN')}` : ''}.
          {' '}Re-run the scan to refresh.
        </p>
      </div>
    </div>
  );
}

/**
 * Standalone page at /intel/nifty50/<SYMBOL> — the cmd-click / bookmark path.
 *
 * Fetches with cached_only, because this route can be opened with no user gesture
 * behind it (a middle-click, a restored session, a shared link) and must never start a
 * 30-second 50-name scan on its own. Warm cache renders instantly; a cold one explains
 * itself and offers a button. That keeps the repo's no-auto-run convention intact on a
 * route that has no Run control of its own.
 */
export function Nifty50StockPage({ symbol }: { symbol: string }) {
  const [view, setView] = useState<View | null>(null);
  const [loading, setLoading] = useState(true);
  const [cold, setCold] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (compute = false) => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/nifty50-view${compute ? '' : '?cached_only=true'}`);
      const j = await r.json();
      if (!j.success) { setErr(j.detail || 'Failed to load the scan'); return; }
      if (j.cold || !j.view) { setCold(true); return; }
      setCold(false); setView(j.view);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(false); }, [load]);

  const wanted = symbol.trim().toUpperCase();
  const row: Row | null = useMemo(() => {
    if (!view) return null;
    // Match the CSV symbol first, then the ticker Yahoo actually resolved — a deep link
    // to /ZOMATO and one to /ETERNAL should land on the same page.
    return view.rows.find((r) => r.symbol.toUpperCase() === wanted)
        ?? view.rows.find((r) => (r.yahoo_symbol || '').toUpperCase() === wanted)
        ?? null;
  }, [view, wanted]);

  useEffect(() => {
    document.title = row ? `${row.symbol} · ${row.name} — Nifty 50` : `${wanted} — Nifty 50`;
  }, [row, wanted]);

  const shell = (body: React.ReactNode) => (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-6xl mx-auto px-6 py-6">
        <a href="/intel/nifty50"
          className="inline-flex items-center gap-1.5 text-[11px] font-bold text-slate-500 hover:text-slate-800 mb-4">
          <ArrowLeft className="w-3.5 h-3.5" /> Nifty 50 scan
        </a>
        {body}
      </div>
    </div>
  );

  if (loading && !view) return shell(<p className="text-xs text-slate-400">Loading {wanted}…</p>);

  if (err) return shell(<div className="text-xs text-rose-600">{err}. Is the backend running?</div>);

  if (cold) return shell(
    <div className={card}>
      <p className="text-sm font-bold text-slate-700">The Nifty 50 scan hasn't been run yet.</p>
      <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
        This page reads the scan's 30-minute cache rather than computing on its own, so a
        deep link never kicks off a fetch nobody asked for. Run it from the panel, or run
        it here — a cold scan takes about 15–30 seconds and covers all 50 names.
      </p>
      <button onClick={() => load(true)} disabled={loading}
        className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-600 text-white disabled:opacity-50">
        <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        {loading ? 'Computing…' : 'Run the scan'}
      </button>
    </div>
  );

  if (!row) return shell(
    <div className={card}>
      <p className="text-sm font-bold text-slate-700">No constituent called “{wanted}”.</p>
      <p className="text-[11px] text-slate-500 mt-1">
        The scan covers the {view?.rows.length ?? 0} names in nifty-50-stock-list.csv. Check the
        symbol, or open it from the table.
      </p>
    </div>
  );

  return shell(<Nifty50StockDetail row={row} view={view} />);
}
