#!/usr/bin/env python3
"""gold_cycles.py — the INR gold cycle, 2018 to now, as one self-contained page.

WHAT THIS SERIES IS, AND WHY IT IS RECONSTRUCTED
-----------------------------------------------
An Indian holder's gold return is two things multiplied: the dollar price and the rupee.
`price_bars` holds each of them daily back to 2018-01-02 — GOLD_USD from COMEX and USDINR
from Upstox — so the INR series is built rather than downloaded:

    INR per 10g  =  USD per troy oz / 31.1035 * 10 * USDINR

Native MCX gold exists (`GOLD`, `GOLD_2026-10-05`, ...) and is the price that actually
trades, but its daily history starts 2025-10-16 — about ten months. Ten months cannot show
a cycle. So the long series is the reconstruction, and MCX is used only to say what the
reconstruction is NOT: the traded future sits a median 14.8% above parity, because import
duty, GST and carry are real and this formula contains none of them. LEVELS HERE ARE
PARITY LEVELS. Drawdowns, dates and ratios are unaffected — a constant multiplier cancels.

WHY BOTH CURRENCIES ARE PLOTTED
-------------------------------
Because they disagree, and the disagreement is the finding. The 2026 drawdown is 25.1% in
dollars and 22.6% in rupees, and the two bottomed THREE WEEKS APART — 16-Jul in USD,
24-Jun in INR — because the rupee kept weakening after gold stopped falling. A reader
looking only at global commentary gets the wrong trough date for their own holding.

A NOTE ON WHAT THIS DOES NOT DO
-------------------------------
It reports base rates: how deep past drawdowns went, how long they took to reclaim, and
where the current one sits against them. Three prior cycles is not a sample you can forecast
from, and the page says so on its face rather than in a footnote. No recommendation is
produced here about any position.

    python3 backend/quant/gold_cycles.py                 # writes gold_inr_view.html
    python3 backend/quant/gold_cycles.py --out other.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OZ_G = 31.1035
THRESH = 0.10          # a "cycle" is a fall of at least this much from a running high


def _load(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = ("select date(ts), close from price_bars where symbol=? and timeframe='1d' "
         "and close is not null")
    usd = dict(con.execute(q, ("GOLD_USD",)))
    fx = dict(con.execute(q, ("USDINR",)))
    mcx = dict(con.execute(q, ("GOLD_2026-10-05",)))
    con.close()

    # C39 guard, kept deliberately after the repair. Upstox stopped 10x-scaling USDINR on
    # 2026-08-16 and six daily bars were stored at 9.55 for a 95.5 rupee. Those were
    # repaired, but this page multiplies by FX, so a silent recurrence would move every
    # INR level by a factor of ten and still draw a perfectly smooth chart. Refuse instead.
    bad = sorted(d for d, v in fx.items() if v < 20 or v > 200)
    if bad:
        raise SystemExit(
            f"USDINR has {len(bad)} bar(s) outside 20-200: {bad[:6]}"
            f"{' ...' if len(bad) > 6 else ''}\n"
            f"That is the C39 scale defect. Repair before trusting any INR level:\n"
            f"  python3 data_agent/quality/fix_usdinr_scale.py --apply")

    days = sorted(set(usd) & set(fx))
    if not days:
        raise SystemExit("no overlapping GOLD_USD / USDINR dates")
    return days, usd, fx, mcx


def cycles(pairs, thresh=THRESH):
    """Every fall of `thresh` or more from a running high, with its recovery.

    Peak-to-trough AND trough-to-reclaim are both reported. Only the second answers "how
    long did holders wait", which is the question a chart of prices alone never shows.
    """
    out, cur = [], None
    mx, mxd = pairs[0][1], pairs[0][0]
    for d, c in pairs:
        if c >= mx:
            if cur and cur["depth"] <= -thresh:
                cur["recovered"] = d
                out.append(cur)
            cur, mx, mxd = None, c, d
            continue
        if cur is None:
            cur = {"peak_date": mxd, "peak": mx, "depth": 0.0,
                   "trough_date": d, "trough": c, "recovered": None}
        if c / mx - 1 < cur["depth"]:
            cur.update(depth=c / mx - 1, trough_date=d, trough=c)
    if cur and cur["depth"] <= -thresh:
        out.append(cur)
    return out


def drawdown(pairs):
    mx, out = pairs[0][1], []
    for _, c in pairs:
        mx = max(mx, c)
        out.append(c / mx - 1)
    return out


# ----------------------------------------------------------------------------------------
# SVG. Drawn server-side rather than by a charting library: this file has to open years
# from now with no network, and a CDN script tag is a dependency that expires quietly.
# ----------------------------------------------------------------------------------------
import math


def _path(vals, w, h, lo, hi, log=False):
    n = len(vals)
    f = (lambda v: math.log10(v)) if log else (lambda v: v)
    lo_, hi_ = f(lo), f(hi)
    pts = []
    for i, v in enumerate(vals):
        x = i * w / (n - 1)
        y = h - (f(v) - lo_) / (hi_ - lo_) * h
        pts.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(pts)


def _x_of(i, n, w):
    return i * w / (n - 1)


def build(days, usd, fx, mcx, out_path):
    inr = [usd[d] / OZ_G * 10 * fx[d] for d in days]
    pairs_i = list(zip(days, inr))
    pairs_u = [(d, usd[d]) for d in days]
    cyc_i, cyc_u = cycles(pairs_i), cycles(pairs_u)
    dd_i, dd_u = drawdown(pairs_i), drawdown(pairs_u)

    D = dt.date.fromisoformat
    last_d, last = days[-1], inr[-1]
    peak = max(range(len(inr)), key=lambda i: inr[i])
    live = cyc_i[-1] if cyc_i and cyc_i[-1]["recovered"] is None else None

    W, H, HD = 1120, 360, 150
    lo, hi = min(inr) * 0.95, max(inr) * 1.05
    price_path = _path(inr, W, H, lo, hi, log=True)
    dpi = _path([1 + x for x in dd_i], W, HD, 1 + min(min(dd_i), min(dd_u)) - 0.02, 1.005)
    dpu = _path([1 + x for x in dd_u], W, HD, 1 + min(min(dd_i), min(dd_u)) - 0.02, 1.005)

    # year gridlines
    ticks = []
    seen = set()
    for i, d in enumerate(days):
        y = d[:4]
        if y not in seen:
            seen.add(y)
            ticks.append((i, y))

    yl = []
    v = 25000
    while v <= hi:
        if v >= lo:
            yy = H - (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * H
            yl.append((yy, f"{v // 1000:,}k"))
        v *= 2

    mcx_days = sorted(set(mcx) & set(days))
    mcx_prem = None
    if len(mcx_days) > 20:
        idx = {d: i for i, d in enumerate(days)}
        r = sorted(mcx[d] / inr[idx[d]] - 1 for d in mcx_days)
        mcx_prem = r[len(r) // 2]

    rows = []
    for c in cyc_i:
        dn = (D(c["trough_date"]) - D(c["peak_date"])).days
        up = (D(c["recovered"]) - D(c["trough_date"])).days if c["recovered"] else None
        rows.append(f"""<tr{' class="live"' if c["recovered"] is None else ''}>
          <td>{c['peak_date']}</td><td class=n>{c['peak']:,.0f}</td>
          <td>{c['trough_date']}</td><td class=n>{c['trough']:,.0f}</td>
          <td class="n neg">{c['depth'] * 100:.1f}%</td>
          <td class=n>{dn}</td>
          <td>{c['recovered'] or '—'}</td>
          <td class=n>{up if up is not None else '—'}</td></tr>""")

    done = [c for c in cyc_i if c["recovered"]]
    ups = sorted((D(c["recovered"]) - D(c["trough_date"])).days for c in done)
    med_up = ups[len(ups) // 2] if ups else None

    payload = json.dumps({
        "d": days,
        "i": [round(x) for x in inr],
        "u": [round(usd[d], 1) for d in days],
        "f": [round(fx[d], 2) for d in days],
        "di": [round(x * 100, 2) for x in dd_i],
        "du": [round(x * 100, 2) for x in dd_u],
    }, separators=(",", ":"))

    off_trough = (last / live["trough"] - 1) if live else 0.0
    from_peak = last / inr[peak] - 1
    since = (D(last_d) - D(live["trough_date"])).days if live else 0
    yrs = (D(last_d) - D(days[0])).days / 365.25
    cagr_i = (last / inr[0]) ** (1 / yrs) - 1
    cagr_u = (usd[last_d] / usd[days[0]]) ** (1 / yrs) - 1

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold in rupees — the cycle, 2018 to {last_d}</title>
<style>
 :root{{--bg:#0f1115;--pan:#171a21;--ink:#e8eaed;--dim:#8b93a1;--ln:#252a34;
        --gold:#e0b34d;--usd:#6aa9ff;--neg:#e56b6b;--pos:#5fbf7f}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--ink);
      font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
 .wrap{{max-width:1180px;margin:0 auto;padding:32px 24px 64px}}
 h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}}
 .sub{{color:var(--dim);font-size:14px;margin-bottom:26px}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;
        margin-bottom:26px}}
 .k{{background:var(--pan);border:1px solid var(--ln);border-radius:10px;padding:14px 16px}}
 .k .lbl{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.07em}}
 .k .val{{font-size:23px;font-weight:600;margin-top:5px;
          font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
 .k .note{{color:var(--dim);font-size:12px;margin-top:3px}}
 .panel{{background:var(--pan);border:1px solid var(--ln);border-radius:12px;
         padding:18px 20px 12px;margin-bottom:20px;position:relative}}
 .panel h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
            margin:0 0 14px;font-weight:600}}
 svg{{display:block;width:100%;height:auto;overflow:visible}}
 .grid{{stroke:var(--ln);stroke-width:1}}
 .lab{{fill:var(--dim);font-size:11px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th{{text-align:left;color:var(--dim);font-weight:600;font-size:11px;
     text-transform:uppercase;letter-spacing:.06em;padding:7px 10px;
     border-bottom:1px solid var(--ln)}}
 td{{padding:8px 10px;border-bottom:1px solid var(--ln);font-variant-numeric:tabular-nums}}
 tr:last-child td{{border-bottom:none}}
 tr.live td{{background:rgba(224,179,77,.07)}}
 .n{{text-align:right}} .neg{{color:var(--neg)}} .pos{{color:var(--pos)}}
 .note-list{{color:var(--dim);font-size:13.5px;line-height:1.7}}
 .note-list b{{color:var(--ink);font-weight:600}}
 #tip{{position:fixed;pointer-events:none;background:#0b0d11;border:1px solid var(--ln);
       border-radius:8px;padding:9px 12px;font-size:12.5px;opacity:0;transition:opacity .1s;
       font-variant-numeric:tabular-nums;z-index:9;white-space:nowrap}}
 .sw{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}}
 .lg{{color:var(--dim);font-size:12.5px;margin-bottom:10px}}
 .lg span{{margin-right:18px}}
</style></head><body><div class=wrap>

<h1>Gold in rupees</h1>
<div class=sub>Parity INR per 10g, {days[0]} to {last_d} &middot; {len(days):,} sessions
&middot; built from COMEX GOLD_USD &times; USDINR, both from <code>price_bars</code></div>

<div class=kpis>
  <div class=k><div class=lbl>Latest</div><div class=val>&#8377;{last:,.0f}</div>
    <div class=note>per 10g, parity &middot; {last_d}</div></div>
  <div class=k><div class=lbl>From the {inr[peak]:,.0f} peak</div>
    <div class="val neg">{from_peak * 100:+.1f}%</div>
    <div class=note>peak {days[peak]}</div></div>
  <div class=k><div class=lbl>Off the trough</div>
    <div class="val pos">{off_trough * 100:+.1f}%</div>
    <div class=note>{since} days since {live['trough_date'] if live else '—'}</div></div>
  <div class=k><div class=lbl>This drawdown</div>
    <div class="val neg">{(live['depth'] if live else 0) * 100:.1f}%</div>
    <div class=note>deepest of the {len(cyc_i)} in sample</div></div>
  <div class=k><div class=lbl>CAGR since 2018</div>
    <div class="val pos">{cagr_i * 100:+.1f}%</div>
    <div class=note>in USD {cagr_u * 100:+.1f}% &middot; the gap is the rupee</div></div>
</div>

<div class=panel>
  <h2>Price &mdash; log scale, so equal vertical distance is equal percentage</h2>
  <svg viewBox="0 0 {W} {H + 26}" preserveAspectRatio="none" id="c1">
    {''.join(f'<line class=grid x1={_x_of(i, len(days), W):.0f} y1=0 '
             f'x2={_x_of(i, len(days), W):.0f} y2={H} />'
             f'<text class=lab x={_x_of(i, len(days), W) + 4:.0f} y={H + 14}>{y}</text>'
             for i, y in ticks)}
    {''.join(f'<line class=grid x1=0 y1={y:.0f} x2={W} y2={y:.0f} stroke-dasharray="2 4"/>'
             f'<text class=lab x=4 y={y - 4:.0f}>{lab}</text>' for y, lab in yl)}
    <path d="{price_path}" fill="none" stroke="var(--gold)" stroke-width="1.8"/>
    <line id=cx1 x1=0 y1=0 x2=0 y2={H} stroke="var(--dim)" stroke-width=1 opacity=0/>
  </svg>
</div>

<div class=panel>
  <h2>Drawdown from the running high &mdash; the same asset, two currencies</h2>
  <div class=lg>
    <span><i class=sw style="background:var(--gold)"></i>INR per 10g</span>
    <span><i class=sw style="background:var(--usd)"></i>USD per oz</span>
  </div>
  <svg viewBox="0 0 {W} {HD + 26}" preserveAspectRatio="none" id="c2">
    {''.join(f'<line class=grid x1={_x_of(i, len(days), W):.0f} y1=0 '
             f'x2={_x_of(i, len(days), W):.0f} y2={HD} />' for i, _ in ticks)}
    <path d="{dpu}" fill="none" stroke="var(--usd)" stroke-width="1.5" opacity=".85"/>
    <path d="{dpi}" fill="none" stroke="var(--gold)" stroke-width="1.7"/>
    <line id=cx2 x1=0 y1=0 x2=0 y2={HD} stroke="var(--dim)" stroke-width=1 opacity=0/>
  </svg>
</div>

<div class=panel>
  <h2>Every fall of 10% or more from a high, in rupees</h2>
  <table>
    <tr><th>Peak</th><th class=n>Level</th><th>Trough</th><th class=n>Level</th>
        <th class=n>Depth</th><th class=n>Days down</th><th>Reclaimed</th>
        <th class=n>Days back</th></tr>
    {''.join(rows)}
  </table>
</div>

<div class=panel>
  <h2>What the page does and does not say</h2>
  <div class=note-list>
    <p><b>The two currencies bottomed three weeks apart.</b> USD gold troughed
    {cyc_u[-1]['trough_date']} at {cyc_u[-1]['depth'] * 100:.1f}%; the rupee series troughed
    {live['trough_date'] if live else '—'} at {(live['depth'] if live else 0) * 100:.1f}%.
    The rupee kept weakening after the metal stopped falling, so an Indian holder's low came
    first and was shallower. Global commentary gives the wrong trough date for a rupee
    holding.</p>
    <p><b>These are parity levels, not traded prices.</b>
    {'The MCX October future sits a median ' + format(mcx_prem * 100, '.1f') + '% above this line'
     if mcx_prem else 'MCX futures sit well above this line'} &mdash; import duty, GST and
    carry, none of which the formula contains. Percentages, dates and drawdowns are
    unaffected, because a roughly constant multiplier cancels out of a ratio. Do not read a
    level here as what you would pay.</p>
    <p><b>Three completed cycles is not a forecast base.</b> Of the {len(cyc_i)} drawdowns
    over 10%, {len(done)} were reclaimed, taking {ups[0] if ups else '—'} to
    {ups[-1] if ups else '—'} days from trough to new high, median
    {med_up if med_up is not None else '—'}. The current one is {since} days off its low and
    {abs(from_peak) * 100:.1f}% below the peak. n = {len(done)}. That is an observation about
    three episodes, not a distribution, and nothing here is a view on what happens next.</p>
    <p><b>Why the series is reconstructed.</b> Native MCX daily history starts 2025-10-16 —
    about ten months, which cannot show a cycle. GOLD_USD and USDINR both run to
    2018-01-02, so the long series is built from them. It ends {last_d} rather than the
    latest COMEX session because USDINR has no bar after that date.</p>
  </div>
</div>

<div id=tip></div>
<script>
const S = {payload};
const tip = document.getElementById('tip');
function bind(svg, cross, extra) {{
  const el = document.getElementById(svg), cx = document.getElementById(cross);
  el.addEventListener('mousemove', e => {{
    const r = el.getBoundingClientRect();
    let i = Math.round((e.clientX - r.left) / r.width * (S.d.length - 1));
    i = Math.max(0, Math.min(S.d.length - 1, i));
    cx.setAttribute('opacity', .6);
    cx.setAttribute('x1', i * {W} / (S.d.length - 1));
    cx.setAttribute('x2', i * {W} / (S.d.length - 1));
    tip.style.opacity = 1;
    tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 230) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
    tip.innerHTML = '<b>' + S.d[i] + '</b><br>' +
      '&#8377;' + S.i[i].toLocaleString('en-IN') + ' / 10g' +
      ' &nbsp;<span style="color:#8b93a1">dd ' + S.di[i].toFixed(1) + '%</span><br>' +
      '<span style="color:#6aa9ff">$' + S.u[i].toLocaleString() + ' / oz' +
      ' &nbsp;dd ' + S.du[i].toFixed(1) + '%</span><br>' +
      '<span style="color:#8b93a1">USDINR ' + S.f[i].toFixed(2) + '</span>';
  }});
  el.addEventListener('mouseleave', () => {{
    tip.style.opacity = 0; cx.setAttribute('opacity', 0);
  }});
}}
bind('c1', 'cx1'); bind('c2', 'cx2');
</script>
</div></body></html>"""

    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path, len(days), cyc_i, cyc_u, mcx_prem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "gold_inr_view.html"))
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    db = a.db
    if db is None:
        from db_config import resolve_db_path        # a READER: the mirror is fine
        db = resolve_db_path()

    days, usd, fx, mcx = _load(db)
    path, n, ci, cu, prem = build(days, usd, fx, mcx, a.out)
    print(f"read   {db}")
    print(f"       {n:,} sessions {days[0]} .. {days[-1]}")
    print(f"       {len(ci)} INR drawdowns >10%, {len(cu)} in USD"
          + (f", MCX premium median {prem * 100:.1f}%" if prem else ""))
    print(f"wrote  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
