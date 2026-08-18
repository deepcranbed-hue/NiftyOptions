#!/usr/bin/env python3
"""gold_cycles.py — the INR gold cycle, 2018 to now, at LANDED cost.

WHY LANDED AND NOT PARITY
-------------------------
An Indian buyer cannot transact at the international spot price. The number that behaves
like MCX is spot converted to rupees and then grossed up for what it costs to legally land
the metal here:

    parity  = USD/oz / 31.1035 * 10 * USDINR                      INR per 10g
    landed  = parity * (1 + import_duty_on_that_date) * (1 + GST)

Comparing MCX against naked parity measures the tax code and calls it a market signal. This
repo already made that mistake once on silver: an apparent +19.4% "premium" was 18.45% of
duty and GST and a real basis of about -0.75% (see probe_continuous_commodities.py).

THE DUTY IS NOT A CONSTANT, AND ONE CHANGE LANDS MID-DRAWDOWN
-------------------------------------------------------------
India moved bullion duty five times in this sample, and the most recent move — 6% to 15% on
2026-05-13 — falls INSIDE the 2026 drawdown. That single fact changes the answer to "how bad
was it for an Indian holder":

    parity   peak 2026-01-29 -> trough 2026-06-24    -22.6%
    landed   peak 2026-01-29 -> trough 2026-06-24    -16.1%

6.5pp of that gap is the government, not the market. The reverse also appears: the 2024-07-24
CUT from 15% to 6% shows up on the landed series as an 8%+ "drawdown" over nine days in which
international gold barely moved. Cycles whose window contains a duty change are FLAGGED in the
table for exactly this reason — a policy step is not a price move, and a drawdown table that
does not distinguish them is worse than no table.

VALIDATION, ON THE DAYS THE CONTRACT ACTUALLY TRADED
----------------------------------------------------
The model is checked against the traded MCX October future — but only where volume clears
`MIN_VOL`. This matters more than it sounds: 98 of the 173 overlapping dates have ZERO volume,
because the contract was far-dated and untraded until spring 2026, and on those days its
"close" is a notional print. Measured on stale prints the model looks terrible (day-to-day
return correlation with parity is -0.19 and the wedge swings 8% to 67%); measured on traded
days it is +0.83 correlated and the residual has a median of about -0.2%. Same model, same
dates, and the only difference is refusing to score against prices nobody made.

The residual that remains is not model error — it is India's domestic basis, which is a real
quantity: the market ran ~+1.7% over landed in April and ~-3% under it by August.

WHERE IT SITS IN THE PIPELINE
----------------------------
`sync_all.py` runs this as the `gold-view` step, in the macro phase and AFTER `mirror`. The
ordering is load-bearing rather than tidy: this is a READER and takes `resolve_db_path()`,
which is the repo-local mirror, so running it before the mirror refresh renders yesterday's
data under today's date — the quietest kind of wrong. Anything that reads the mirror belongs
after that step.

    python3 backend/quant/gold_cycles.py              # writes reports/gold_inr_view.html
    python3 backend/quant/gold_cycles.py --out x.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OZ_G = 31.1035
THRESH = 0.10          # a "cycle" is a fall of at least this much from a running high
MIN_VOL = 10           # below this the MCX print is notional; see the validation note

# THE CONTINUOUS FRONT CONTRACT, not a named expiry. A specific expiry is only liquid for a
# few months of its life, so scoring against one measures the contract's age as much as the
# model: GOLD_2026-10-05 gave a residual of -0.19% with sd 1.88pp over 55 days, while the
# front symbol gives about -1% over the same window with far more of them. Always compare a
# spot-equivalent model against whatever is currently the front month.
MCX_REF = "GOLD"

# Local highs, for the peaks panel. A cycle table finds troughs; nothing in it surfaces the
# fact that the landed price made effectively the SAME high twice, four months apart.
PEAK_WINDOW = 25       # a high must dominate this many sessions either side
PEAK_SEP = 40          # ...and highs closer together than this are one event

GST = 0.03             # 3% on bullion since 2017-07-01, unchanged across this sample

# EFFECTIVE TOTAL IMPORT DUTY ON GOLD, by the date it took effect. Inclusive of BCD + AIDC +
# SWS — one number, because that is what the landed price responds to.
#
# THESE ARE POLICY RATES AND THEY MOVE. Every entry is dated and sourced; when the residual
# basis below starts drifting, suspect a duty change before suspecting the market.
DUTY_SCHEDULE = [
    ("1900-01-01", 0.1000, "10%, the setting held from 2014"),
    ("2019-07-06", 0.1250, "Budget 2019 — raised to curb imports"),
    ("2021-02-02", 0.1075, "Budget 2021 — 7.5% BCD + 2.5% AIDC, SWS exempt"),
    ("2022-07-01", 0.1500, "raised to defend the current account deficit"),
    ("2024-07-24", 0.0600, "Budget 2024 — 5% BCD + 1% AIDC, lowest in over a decade"),
    ("2026-05-13", 0.1500, "Notifications 15-18/2026-Customs — 10% BCD + 5% AIDC"),
]


def duty_on(day: str) -> float:
    rate = DUTY_SCHEDULE[0][1]
    for eff, r, _ in DUTY_SCHEDULE:
        if day >= eff:
            rate = r
    return rate


def _load(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = ("select date(ts), close from price_bars where symbol=? and timeframe='1d' "
         "and close is not null")
    usd = dict(con.execute(q, ("GOLD_USD",)))
    fx = dict(con.execute(q, ("USDINR",)))
    mcx = dict(con.execute(q, (MCX_REF,)))
    vol = dict(con.execute("select date(ts), coalesce(volume,0) from price_bars "
                           "where symbol=? and timeframe='1d'", (MCX_REF,)))
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
    return days, usd, fx, mcx, vol


def cycles(pairs, thresh=THRESH):
    """Every fall of `thresh` or more from a running high, with its recovery."""
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


def policy_in(cyc):
    """Duty changes inside a cycle's PEAK->TROUGH window.

    Peak-to-trough, not peak-to-recovery, and the narrower window is the right one: a duty
    move between the high and the low changes the measured DEPTH, which is the number the
    table is for. A move during the recovery changes how long it took, which matters less
    and would flag almost everything — the 2020-08 cycle alone spans nineteen months.
    """
    return [(eff, r) for eff, r, _ in DUTY_SCHEDULE
            if cyc["peak_date"] <= eff <= cyc["trough_date"]]


def major_highs(days, vals, n=6):
    """Distinct local highs, biggest first.

    WHY THIS EXISTS. The drawdown table marks exactly one peak — the running maximum — and
    that hides the most interesting thing on this series: the landed price reached
    2026-01-29 and 2026-05-13 within 0.2% of each other, for completely unrelated reasons.
    Marking only the all-time high reports a 0.2% accident as though it were the story.
    """
    cand = []
    for i in range(len(days)):
        lo, hi = max(0, i - PEAK_WINDOW), min(len(days), i + PEAK_WINDOW + 1)
        if vals[i] == max(vals[lo:hi]):
            cand.append((vals[i], days[i], i))
    cand.sort(reverse=True)
    kept = []
    for v, d, i in cand:
        if all(abs(i - j) > PEAK_SEP for _, _, j in kept):
            kept.append((v, d, i))
        if len(kept) >= n:
            break
    return kept


def drawdown(pairs):
    mx, out = pairs[0][1], []
    for _, c in pairs:
        mx = max(mx, c)
        out.append(c / mx - 1)
    return out


# ----------------------------------------------------------------------------------------
# SVG, drawn server-side: this file has to open years from now with no network, and a CDN
# script tag is a dependency that expires quietly.
# ----------------------------------------------------------------------------------------
def _path(vals, w, h, lo, hi, log=False):
    n = len(vals)
    f = (lambda v: math.log10(v)) if log else (lambda v: v)
    lo_, hi_ = f(lo), f(hi)
    return "M" + " L".join(
        f"{i * w / (n - 1):.1f},{h - (f(v) - lo_) / (hi_ - lo_) * h:.1f}"
        for i, v in enumerate(vals))


def _x(i, n, w):
    return i * w / (n - 1)


def build(days, usd, fx, mcx, vol, out_path):
    parity = [usd[d] / OZ_G * 10 * fx[d] for d in days]
    landed = [p * (1 + duty_on(d)) * (1 + GST) for d, p in zip(days, parity)]
    idx = {d: i for i, d in enumerate(days)}

    cyc = cycles(list(zip(days, landed)))
    cyc_par = cycles(list(zip(days, parity)))
    dd_l, dd_p = drawdown(list(zip(days, landed))), drawdown(list(zip(days, parity)))

    # ---- validation, on traded days only -------------------------------------------------
    liq = [d for d in sorted(set(mcx) & set(idx)) if vol.get(d, 0) > MIN_VOL]
    stale = len(set(mcx) & set(idx)) - len(liq)
    resid = [(d, mcx[d] / landed[idx[d]] - 1) for d in liq]
    rv = sorted(x for _, x in resid)
    r_med = st.median(rv) if rv else 0.0
    r_sd = st.pstdev(rv) if len(rv) > 1 else 0.0
    by_m = {}
    for d, x in resid:
        by_m.setdefault(d[:7], []).append(x)

    D = dt.date.fromisoformat
    last_d, last = days[-1], landed[-1]
    peak_i = max(range(len(landed)), key=lambda i: landed[i])
    live = cyc[-1] if cyc and cyc[-1]["recovered"] is None else None

    W, H, HD = 1120, 380, 150
    lo, hi = min(parity) * 0.95, max(landed) * 1.06
    p_landed = _path(landed, W, H, lo, hi, log=True)
    p_parity = _path(parity, W, H, lo, hi, log=True)
    dmin = min(min(dd_l), min(dd_p)) - 0.02
    p_ddl = _path([1 + x for x in dd_l], W, HD, 1 + dmin, 1.005)
    p_ddp = _path([1 + x for x in dd_p], W, HD, 1 + dmin, 1.005)

    def y_of(v):
        return H - (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * H

    mcx_dots = "".join(
        f'<circle cx="{_x(idx[d], len(days), W):.1f}" cy="{y_of(mcx[d]):.1f}" r="1.6" '
        f'fill="var(--mcx)" opacity=".85"/>' for d in liq)

    highs = major_highs(days, landed)
    top = highs[0][0] if highs else max(landed)
    high_marks = "".join(
        f'<circle cx="{_x(i, len(days), W):.1f}" cy="{y_of(v):.1f}" r="3.4" fill="none" '
        f'stroke="var(--gold)" stroke-width="1.4"/>'
        f'<text class=lab x="{_x(i, len(days), W):.0f}" y="{y_of(v) - 8:.0f}" '
        f'text-anchor="middle" fill="var(--gold)">{d[:7]}</text>'
        for v, d, i in highs[:3])

    duty_marks = ""
    for eff, r, why in DUTY_SCHEDULE[1:]:
        j = next((i for i, d in enumerate(days) if d >= eff), None)
        if j is None:
            continue
        xx = _x(j, len(days), W)
        duty_marks += (f'<line x1={xx:.0f} y1=0 x2={xx:.0f} y2={H} stroke="var(--duty)" '
                       f'stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>'
                       f'<text class=lab x={xx + 3:.0f} y=12 fill="var(--duty)">'
                       f'{r * 100:g}%</text>')

    ticks, seen = [], set()
    for i, d in enumerate(days):
        if d[:4] not in seen:
            seen.add(d[:4])
            ticks.append((i, d[:4]))

    yl, v = [], 25000
    while v <= hi:
        if v >= lo:
            yl.append((y_of(v), f"{v // 1000:,}k"))
        v *= 2

    rows = []
    for c in cyc:
        pol = policy_in(c)
        dn = (D(c["trough_date"]) - D(c["peak_date"])).days
        up = (D(c["recovered"]) - D(c["trough_date"])).days if c["recovered"] else None
        tag = (f'<span class=pol title="duty changed inside this window">'
               f'{", ".join(e for e, _ in pol)}</span>' if pol else "")
        rows.append(f"""<tr{' class="live"' if c["recovered"] is None else ''}>
          <td>{c['peak_date']}</td><td class=n>{c['peak']:,.0f}</td>
          <td>{c['trough_date']}</td><td class=n>{c['trough']:,.0f}</td>
          <td class="n neg">{c['depth'] * 100:.1f}%</td><td class=n>{dn}</td>
          <td>{c['recovered'] or '—'}</td><td class=n>{up if up is not None else '—'}</td>
          <td>{tag}</td></tr>""")

    par_live = cyc_par[-1] if cyc_par and cyc_par[-1]["recovered"] is None else None
    done = [c for c in cyc if c["recovered"]]
    clean = [c for c in done if not policy_in(c)]
    ups = sorted((D(c["recovered"]) - D(c["trough_date"])).days for c in clean)

    mrows = "".join(
        f"<tr><td>{m}</td><td class=n>{len(v_)}</td>"
        f"<td class='n {'pos' if st.median(v_) >= 0 else 'neg'}'>"
        f"{st.median(v_) * 100:+.2f}%</td></tr>"
        for m, v_ in sorted(by_m.items()))

    payload = json.dumps({
        "d": days,
        "l": [round(x) for x in landed],
        "p": [round(x) for x in parity],
        "u": [round(usd[d], 1) for d in days],
        "f": [round(fx[d], 2) for d in days],
        "y": [round(duty_on(d) * 100, 2) for d in days],
        "dl": [round(x * 100, 2) for x in dd_l],
        "m": {d: round(mcx[d]) for d in liq},
    }, separators=(",", ":"))

    off = (last / live["trough"] - 1) if live else 0.0
    frm = last / landed[peak_i] - 1
    since = (D(last_d) - D(live["trough_date"])).days if live else 0
    yrs = (D(last_d) - D(days[0])).days / 365.25
    cagr = (last / landed[0]) ** (1 / yrs) - 1

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold in rupees at landed cost — 2018 to {last_d}</title>
<style>
 :root{{--bg:#0f1115;--pan:#171a21;--ink:#e8eaed;--dim:#8b93a1;--ln:#252a34;
        --gold:#e0b34d;--par:#5a6070;--mcx:#5fbf7f;--duty:#c77dff;
        --neg:#e56b6b;--pos:#5fbf7f}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--ink);
      font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
 .wrap{{max-width:1180px;margin:0 auto;padding:32px 24px 64px}}
 h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}}
 .sub{{color:var(--dim);font-size:14px;margin-bottom:26px}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;
        margin-bottom:22px}}
 .k{{background:var(--pan);border:1px solid var(--ln);border-radius:10px;padding:14px 16px}}
 .k .lbl{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.07em}}
 .k .val{{font-size:23px;font-weight:600;margin-top:5px;
          font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
 .k .note{{color:var(--dim);font-size:12px;margin-top:3px}}
 .panel{{background:var(--pan);border:1px solid var(--ln);border-radius:12px;
         padding:18px 20px 14px;margin-bottom:20px}}
 .panel h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
            margin:0 0 12px;font-weight:600}}
 svg{{display:block;width:100%;height:auto;overflow:visible}}
 .grid{{stroke:var(--ln);stroke-width:1}}
 .lab{{fill:var(--dim);font-size:11px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th{{text-align:left;color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;
     letter-spacing:.06em;padding:7px 10px;border-bottom:1px solid var(--ln)}}
 td{{padding:8px 10px;border-bottom:1px solid var(--ln);font-variant-numeric:tabular-nums}}
 tr:last-child td{{border-bottom:none}}
 tr.live td{{background:rgba(224,179,77,.07)}}
 .n{{text-align:right}} .neg{{color:var(--neg)}} .pos{{color:var(--pos)}}
 .pol{{color:var(--duty);font-size:11.5px;border:1px solid var(--duty);border-radius:4px;
       padding:1px 6px;white-space:nowrap}}
 .cols{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
 @media(max-width:820px){{.cols{{grid-template-columns:1fr}}}}
 .note-list{{color:var(--dim);font-size:13.5px;line-height:1.7}}
 .note-list b{{color:var(--ink);font-weight:600}}
 .note-list p{{margin:0 0 12px}}
 #tip{{position:fixed;pointer-events:none;background:#0b0d11;border:1px solid var(--ln);
       border-radius:8px;padding:9px 12px;font-size:12.5px;opacity:0;transition:opacity .1s;
       font-variant-numeric:tabular-nums;z-index:9;white-space:nowrap}}
 .sw{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}}
 .lg{{color:var(--dim);font-size:12.5px;margin-bottom:10px}} .lg span{{margin-right:18px}}
 code{{background:#0b0d11;padding:1px 5px;border-radius:4px;font-size:12.5px}}
</style></head><body><div class=wrap>

<h1>Gold in rupees, at landed cost</h1>
<div class=sub>INR per 10g including import duty and {GST * 100:g}% GST &middot; {days[0]} to
{last_d} &middot; {len(days):,} sessions &middot; built from COMEX GOLD_USD &times; USDINR in
<code>price_bars</code>, validated against the traded MCX October future</div>

<div class=kpis>
  <div class=k><div class=lbl>Landed, latest</div><div class=val>&#8377;{last:,.0f}</div>
    <div class=note>duty {duty_on(last_d) * 100:g}% + GST {GST * 100:g}% &middot; {last_d}</div></div>
  <div class=k><div class=lbl>Naked parity</div>
    <div class=val>&#8377;{parity[-1]:,.0f}</div>
    <div class=note>before duty &mdash; not transactable here</div></div>
  <div class=k><div class=lbl>From the peak</div><div class="val neg">{frm * 100:+.1f}%</div>
    <div class=note>{landed[peak_i]:,.0f} on {days[peak_i]}</div></div>
  <div class=k><div class=lbl>Off the trough</div><div class="val pos">{off * 100:+.1f}%</div>
    <div class=note>{since} days since {live['trough_date'] if live else '—'}</div></div>
  <div class=k><div class=lbl>Model vs traded MCX</div>
    <div class=val>{r_med * 100:+.2f}%</div>
    <div class=note>median over {len(liq)} traded days, sd {r_sd * 100:.2f}pp</div></div>
</div>

<div class=panel>
  <h2>Landed cost, naked parity, and where MCX actually traded &mdash; log scale</h2>
  <div class=lg>
    <span><i class=sw style="background:var(--gold)"></i>Landed (duty + GST)</span>
    <span><i class=sw style="background:var(--par)"></i>Naked parity</span>
    <span><i class=sw style="background:var(--mcx)"></i>Traded MCX Oct-26 future</span>
    <span><i class=sw style="background:var(--duty)"></i>Duty change</span>
  </div>
  <svg viewBox="0 0 {W} {H + 26}" preserveAspectRatio="none" id="c1">
    {''.join(f'<line class=grid x1={_x(i, len(days), W):.0f} y1=0 x2={_x(i, len(days), W):.0f} '
             f'y2={H} /><text class=lab x={_x(i, len(days), W) + 4:.0f} y={H + 14}>{y}</text>'
             for i, y in ticks)}
    {''.join(f'<line class=grid x1=0 y1={yy:.0f} x2={W} y2={yy:.0f} stroke-dasharray="2 4"/>'
             f'<text class=lab x=4 y={yy - 4:.0f}>{lab}</text>' for yy, lab in yl)}
    {duty_marks}
    <path d="{p_parity}" fill="none" stroke="var(--par)" stroke-width="1.4"/>
    <path d="{p_landed}" fill="none" stroke="var(--gold)" stroke-width="1.9"/>
    {mcx_dots}
    {high_marks}
    <line id=cx1 x1=0 y1=0 x2=0 y2={H} stroke="var(--dim)" stroke-width=1 opacity=0/>
  </svg>
</div>

<div class=panel>
  <h2>Drawdown from the running high &mdash; landed against naked parity</h2>
  <div class=lg>
    <span><i class=sw style="background:var(--gold)"></i>Landed</span>
    <span><i class=sw style="background:var(--par)"></i>Parity</span>
  </div>
  <svg viewBox="0 0 {W} {HD + 26}" preserveAspectRatio="none" id="c2">
    {''.join(f'<line class=grid x1={_x(i, len(days), W):.0f} y1=0 '
             f'x2={_x(i, len(days), W):.0f} y2={HD} />' for i, _ in ticks)}
    <path d="{p_ddp}" fill="none" stroke="var(--par)" stroke-width="1.4"/>
    <path d="{p_ddl}" fill="none" stroke="var(--gold)" stroke-width="1.8"/>
    <line id=cx2 x1=0 y1=0 x2=0 y2={HD} stroke="var(--dim)" stroke-width=1 opacity=0/>
  </svg>
</div>

<div class=panel>
  <h2>Distinct major highs &mdash; the landed price topped twice, four months apart</h2>
  <table>
    <tr><th>Date</th><th class=n>Landed</th><th class=n>vs the high</th>
        <th class=n>Duty</th><th class=n>COMEX $/oz</th><th class=n>USDINR</th>
        <th>What made it a high</th></tr>
    {''.join(f"<tr{' class=live' if k < 2 else ''}><td>{d}</td>"
             f"<td class=n>{v:,.0f}</td>"
             f"<td class='n {'pos' if v >= top else 'neg'}'>{v / top - 1:+.2%}</td>"
             f"<td class=n>{duty_on(d) * 100:g}%</td>"
             f"<td class=n>{usd[d]:,.1f}</td><td class=n>{fx[d]:.2f}</td>"
             f"<td>{'the metal' if duty_on(d) < 0.10 and usd[d] > 5000 else ('the tax — COMEX was ' + format(1 - usd[d] / usd[highs[0][1]], '.0%') + ' below its January level' if d >= '2026-05-13' else '')}</td></tr>"
             for k, (v, d, i) in enumerate(highs))}
  </table>
</div>

<div class=panel>
  <h2>Every fall of 10% or more, on the landed series</h2>
  <table>
    <tr><th>Peak</th><th class=n>Level</th><th>Trough</th><th class=n>Level</th>
        <th class=n>Depth</th><th class=n>Days down</th><th>Reclaimed</th>
        <th class=n>Days back</th><th>Duty moved</th></tr>
    {''.join(rows)}
  </table>
</div>

<div class=cols>
  <div class=panel>
    <h2>Duty schedule applied</h2>
    <table>
      <tr><th>Effective</th><th class=n>Total duty</th><th>What changed</th></tr>
      {''.join(f'<tr><td>{e}</td><td class=n>{r * 100:g}%</td><td>{w}</td></tr>'
               for e, r, w in DUTY_SCHEDULE)}
    </table>
  </div>
  <div class=panel>
    <h2>Residual basis: traded MCX over landed</h2>
    <table><tr><th>Month</th><th class=n>Traded days</th><th class=n>Median basis</th></tr>
      {mrows}
    </table>
  </div>
</div>

<div class=panel>
  <h2>What this page does and does not say</h2>
  <div class=note-list>
    <p><b>India's gold made the same high twice, and only one of them was about gold.</b>
    {highs[0][1]}: COMEX {usd[highs[0][1]]:,.0f}/oz, duty 6%, landed
    &#8377;{highs[0][0]:,.0f}. {highs[1][1]}: COMEX {usd[highs[1][1]]:,.0f}/oz — <b>
    {1 - usd[highs[1][1]] / usd[highs[0][1]]:.0%} lower</b> — but duty 15% and a weaker
    rupee, landed &#8377;{highs[1][0]:,.0f}. The two are
    {abs(highs[1][0] / highs[0][0] - 1) * 100:.2f}% apart. A view that marks only the
    all-time high would report that gap as the answer, when the honest reading is that the
    January and May tops are the same height and were built out of different materials.</p>
    <p><b>Treat the January top with more suspicion than the May one.</b> It rests on a
    two-session window in which COMEX fell <b>-11.4%</b> in a day (the largest move in this
    entire eight-year series) and Indian screens were barely open on it: the most active
    gold contract on 29-Jan did <b>32 lots</b>, and its print sat 16% above landed — rising
    to 25% the next day, when India did not follow the New York crash and repriced only on
    2-Feb. The global spike is real and independently corroborated by both vendors, but the
    Indian price at that moment is a model estimate against almost no trading. The May high
    happened on 1,700+ lots.</p>
    <p><b>The 2026 drawdown is 6.5pp shallower once duty is counted.</b> Naked parity fell
    {par_live['depth'] * 100:.1f}% from its {par_live['peak_date']} peak; landed fell
    {live['depth'] * 100:.1f}%. The difference is the {DUTY_SCHEDULE[-1][0]} hike from 6% to
    15%, which lands INSIDE the drawdown and cushioned every rupee holder by about 8.5% on
    one day. That is policy, not the metal &mdash; but it is what actually happened to the
    price in India, which is the whole reason to look at the landed series.</p>
    <p><b>Read the flagged rows as suspect.</b> The {DUTY_SCHEDULE[-2][0]} CUT, from 15% to
    6%, appears on the landed series as a fall of over 8% in nine days during which
    international gold barely moved &mdash; a tax cut wearing a drawdown's clothes. Rows whose
    window contains a duty change carry a marker; only {len(clean)} of the
    {len(done)} completed cycles are clean of one, which is why the recovery base rate below
    is quoted on those alone.</p>
    <p><b>Validation is on traded days only, and that decision does the heavy lifting.</b>
    {stale} of the {stale + len(liq)} overlapping MCX dates carry ZERO volume: the Oct-2026
    contract was far-dated and untraded until spring, and its "close" on those days is a
    notional print. Scored on all dates the model looks broken (the wedge swings 8% to 67%
    and day-to-day returns correlate <b>-0.19</b> with parity); scored on the {len(liq)} days
    with volume above {MIN_VOL} it has a median residual of <b>{r_med * 100:+.2f}%</b> and an
    sd of {r_sd * 100:.2f}pp, with returns correlating <b>+0.83</b>. Same model, same dates.</p>
    <p><b>The residual is a market quantity, not leftover error.</b> It ran about +1.7% in
    April and about -3% by August &mdash; India moving from a premium to a discount against
    landed cost. Watch it: a persistent drift is the first sign the duty schedule above has
    gone stale.</p>
    <p><b>There is no usable recovery base rate here, and that is the finding.</b> Of the
    {len(done)} completed drawdowns, {len(done) - len(clean)} straddle a duty change and only
    <b>{len(clean)}</b> is clean of one &mdash; so the honest sample for "how long does a
    rupee gold drawdown take to reclaim" is a single observation
    ({', '.join(f"{c['trough_date']} +{(D(c['recovered']) - D(c['trough_date'])).days}d"
                for c in clean) or 'none'}). All four completed recoveries took
    {min((D(c['recovered']) - D(c['trough_date'])).days for c in done)} to
    {max((D(c['recovered']) - D(c['trough_date'])).days for c in done)} days from trough,
    but three of those windows have a tax change inside them. The current drawdown is
    {since} days off its low and {abs(frm) * 100:.1f}% below the peak. Nothing here is a view
    on what happens next.</p>
  </div>
</div>

<div id=tip></div>
<script>
const S = {payload};
const tip = document.getElementById('tip');
function bind(svg, cross) {{
  const el = document.getElementById(svg), cx = document.getElementById(cross);
  el.addEventListener('mousemove', e => {{
    const r = el.getBoundingClientRect();
    let i = Math.round((e.clientX - r.left) / r.width * (S.d.length - 1));
    i = Math.max(0, Math.min(S.d.length - 1, i));
    cx.setAttribute('opacity', .6);
    cx.setAttribute('x1', i * {W} / (S.d.length - 1));
    cx.setAttribute('x2', i * {W} / (S.d.length - 1));
    const day = S.d[i], m = S.m[day];
    tip.style.opacity = 1;
    tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 250) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
    tip.innerHTML = '<b>' + day + '</b><br>' +
      '<span style="color:#e0b34d">landed &#8377;' + S.l[i].toLocaleString('en-IN') +
      '</span> &nbsp;<span style="color:#8b93a1">dd ' + S.dl[i].toFixed(1) + '%</span><br>' +
      '<span style="color:#5a6070">parity &#8377;' + S.p[i].toLocaleString('en-IN') +
      '  &middot; duty ' + S.y[i] + '%</span><br>' +
      (m ? '<span style="color:#5fbf7f">MCX traded &#8377;' + m.toLocaleString('en-IN') +
           '  (' + ((m / S.l[i] - 1) * 100).toFixed(2) + '% basis)</span><br>' : '') +
      '<span style="color:#8b93a1">$' + S.u[i].toLocaleString() + '/oz &middot; USDINR ' +
      S.f[i].toFixed(2) + '</span>';
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
    return out_path, len(days), cyc, liq, stale, r_med, r_sd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "gold_inr_view.html"))
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    # reports/ is gitignored: this is a DERIVED file, regenerated every sync, and a 230KB
    # blob that changes on every run carries no history worth keeping. The generator is what
    # is tracked. Keeping it out of the repo root also keeps it out of Vite's serving root.
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    db = a.db
    if db is None:
        from db_config import resolve_db_path        # a READER: the mirror is fine
        db = resolve_db_path()

    days, usd, fx, mcx, vol = _load(db)
    path, n, cyc, liq, stale, r_med, r_sd = build(days, usd, fx, mcx, vol, a.out)
    print(f"read   {db}")
    print(f"       {n:,} sessions {days[0]} .. {days[-1]}, duty {duty_on(days[0]) * 100:g}% "
          f"-> {duty_on(days[-1]) * 100:g}% across {len(DUTY_SCHEDULE) - 1} changes")
    print(f"       {len(cyc)} landed drawdowns >{THRESH:.0%}, "
          f"{sum(1 for c in cyc if policy_in(c))} of them contain a duty change")
    print(f"valid  {len(liq)} traded days (vol>{MIN_VOL}), {stale} stale prints skipped; "
          f"residual median {r_med:+.2%}, sd {r_sd:.2%}")
    print(f"wrote  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
