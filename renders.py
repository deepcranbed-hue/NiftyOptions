#!/usr/bin/env python3
"""
renders.py — chart + digest builders for the MCP `render` tool.

Two outputs per view, deliberately separated:
  chart   an HTML/SVG file — for the human. Shows SHAPE.
  digest  ~15 reduced features — for the model. Cheaper than raw rows and strictly
          more useful: max-pain, PCR, centre-of-gravity are not in the rows at all.

Nothing here is model-generated. Python computes and draws from the store, so the
picture cannot contain invented data points.

PROVENANCE IS PART OF THE SCHEMA. Every digest reports where its numbers came from
and what could not be computed. Today's defects were all fabricated values posing as
measurements — a 12.0 VIX placeholder, a 0.0 IV on a stale strike, a 0.4% expected
move with no label. A digest that returns 0 for "absent" recreates them.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from db_config import connect                       # noqa: E402  single DB source
from strategy_framework.bs import implied_vol       # noqa: E402  the ONE inverter

SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)           # 0.797885
SIGMA_PER_STRADDLE = 1.0 / SQRT_2_OVER_PI           # 1.253314


# ------------------------------------------------------------------ store access
def _latest_capture(con, expiry=None, ts=None, capture_id=None):
    if capture_id:
        row = con.execute("SELECT * FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
        if row is None:
            raise ValueError(f"no capture {capture_id}")
        return row
    q = "SELECT * FROM captures WHERE 1=1"
    a: list = []
    if ts:
        q += " AND captured_at <= ?"; a.append(ts)
    if expiry:
        q += (" AND capture_id IN (SELECT DISTINCT capture_id FROM chain_rows WHERE expiry=?)")
        a.append(expiry)
    q += " ORDER BY captured_at DESC LIMIT 1"
    row = con.execute(q, a).fetchone()
    if row is None:
        raise ValueError("no capture matches")
    return row


def _rows(con, capture_id, expiry=None):
    q = ("SELECT expiry, strike, call_ltp, put_ltp, call_oi, put_oi, call_oi_chg, "
         "put_oi_chg, call_volume, put_volume, call_bid, call_ask, put_bid, put_ask "
         "FROM chain_rows WHERE capture_id=?")
    a = [capture_id]
    if expiry:
        q += " AND expiry=?"; a.append(expiry)
    q += " ORDER BY expiry, strike"
    return [dict(r) for r in con.execute(q, a)]


def _vix_as_of(ts):
    """Real INDIAVIX, no-lookahead, via the one resolver (D-SC-05)."""
    try:
        from bar_store import get_latest_vix
        from db_config import DB_PATH
        return get_latest_vix(before_ts=ts, db=DB_PATH, with_source=True)
    except Exception:
        return (None, None)


def _dte_days(captured_at: str, expiry: str) -> float:
    from datetime import datetime
    f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00").replace(".000+00:00", "+00:00"))
    return max((f(expiry) - f(captured_at)).total_seconds() / 86400.0, 1e-4)


# ------------------------------------------------------------------ oi_profile
def _oi_profile(con, cap, expiry, outdir):
    rows = [r for r in _rows(con, cap["capture_id"], expiry) if r["strike"] is not None]
    if not rows:
        raise ValueError("no chain rows for that capture/expiry")
    expiry = expiry or rows[0]["expiry"]
    rows = [r for r in rows if r["expiry"] == expiry]
    spot = cap["spot"]
    dte = _dte_days(cap["captured_at"], expiry)
    T = max(dte / 365.0, 1e-5)

    g = lambda r, k: (r.get(k) or 0.0)
    tot_c = sum(g(r, "call_oi") for r in rows)
    tot_p = sum(g(r, "put_oi") for r in rows)
    max_c = max(rows, key=lambda r: g(r, "call_oi"))
    max_p = max(rows, key=lambda r: g(r, "put_oi"))
    atm = min(rows, key=lambda r: abs(r["strike"] - spot))

    def pain(k):
        return (sum(g(r, "call_oi") * max(0.0, k - r["strike"]) for r in rows)
                + sum(g(r, "put_oi") * max(0.0, r["strike"] - k) for r in rows))
    max_pain = min((r["strike"] for r in rows), key=pain)
    cog = (sum(r["strike"] * (g(r, "call_oi") + g(r, "put_oi")) for r in rows)
           / (tot_c + tot_p)) if (tot_c + tot_p) else None

    # --- straddle and its 1-sigma equivalent. BOTH emitted, always (D-SC-01/02).
    c, p = atm.get("call_ltp"), atm.get("put_ltp")
    straddle = (c + p) if (c and p and c > 0 and p > 0) else None
    if straddle:
        em, em_source = straddle * SIGMA_PER_STRADDLE, "atm_straddle"
    else:
        ivs = [v for v in (implied_vol(c, spot, atm["strike"], T, call=True) if c else None,
                           implied_vol(p, spot, atm["strike"], T, call=False) if p else None)
               if v is not None]
        if ivs:
            em, em_source = spot * (sum(ivs) / len(ivs)) * math.sqrt(T), "atm_iv"
        else:
            em, em_source = None, None

    vix, vix_source = _vix_as_of(cap["captured_at"])
    quoted = sum(1 for r in rows if (r.get("call_bid") or 0) > 0)

    digest: dict[str, Any] = {
        "view": "oi_profile",
        "capture_id": cap["capture_id"], "captured_at": cap["captured_at"],
        "expiry": expiry, "dte_days": round(dte, 3),
        "spot": spot, "atm_strike": atm["strike"], "n_strikes": len(rows),
        "total_call_oi": int(tot_c), "total_put_oi": int(tot_p),
        "pcr_oi": round(tot_p / tot_c, 3) if tot_c else None,
        "max_call_oi_strike": max_c["strike"], "max_call_oi": int(g(max_c, "call_oi")),
        "max_put_oi_strike": max_p["strike"], "max_put_oi": int(g(max_p, "put_oi")),
        "oi_centre_of_gravity": round(cog, 1) if cog else None,
        "max_pain": max_pain,
        "spot_vs_max_pain_pct": round((spot - max_pain) / max_pain * 100, 2),
        # both figures, so the x1.2533 confusion cannot recur (D-SC-02)
        "atm_straddle_pts": round(straddle, 2) if straddle else None,
        "straddle_1sigma_pts": round(em, 1) if (em and em_source == "atm_straddle") else None,
        "expected_move_1sigma_pts": round(em, 1) if em else None,
        "em_source": em_source,                       # atm_straddle | atm_iv | None (D-SC-04)
        "vix": vix, "vix_source": vix_source,         # 1m | 1d | None (D-SC-05)
        "provenance": {
            "iv_inverter": "strategy_framework.bs (LTP inversion)",
            "quoted_strikes": f"{quoted}/{len(rows)}",
            "price_source": "MID_2S" if quoted else "LTP_ONLY",
            "note": None if quoted else
                    "no bid/ask on this capture (api_json backfill era, D-MA-09a) — "
                    "IV is LTP-based; a mid-based skew is not computable here",
        },
    }
    chart = _draw_oi(rows, digest, outdir)
    return {"chart": str(chart), "digest": digest}


def _draw_oi(rows, d, outdir) -> Path:
    W, H, PL, PT, PB = 980, 460, 62, 28, 64
    PW, PH = W - PL - 20, H - PT - PB
    g = lambda r, k: (r.get(k) or 0.0)
    ymax = max(max(g(r, "call_oi") for r in rows), max(g(r, "put_oi") for r in rows)) or 1
    ymax = (int(ymax / 2_000_000) + 1) * 2_000_000
    n = len(rows); slot = PW / n; bw = min(13.0, slot / 2 - 2.5)
    x = lambda i: PL + slot * (i + 0.5)
    y = lambda v: PT + PH - (v / ymax) * PH

    bars, ticks = [], []
    for i, r in enumerate(rows):
        xc = x(i)
        for off, key, cls in ((-bw - 1, "call_oi", "s1"), (1, "put_oi", "s2")):
            v = g(r, key); h = max((v / ymax) * PH, 1)
            bars.append(f'<rect class="{cls}" x="{xc+off:.1f}" y="{PT+PH-h:.1f}" width="{bw:.1f}" '
                        f'height="{h:.1f}" rx="4"><title>{r["strike"]:.0f}  '
                        f'{"call" if key=="call_oi" else "put"} OI {v:,.0f}</title></rect>')
        if r["strike"] % 100 == 0:
            ticks.append(f'<text class="ax" x="{xc:.1f}" y="{PT+PH+18:.1f}" text-anchor="middle">{r["strike"]:.0f}</text>')

    grid = []
    v = 0
    while v <= ymax:
        yy = y(v)
        grid.append(f'<line class="gl" x1="{PL}" y1="{yy:.1f}" x2="{PL+PW}" y2="{yy:.1f}"/>'
                    f'<text class="ax" x="{PL-10}" y="{yy+4:.1f}" text-anchor="end">{v/1e6:.0f}M</text>')
        v += 2_000_000

    spot = d["spot"]; si = 0
    for i, r in enumerate(rows):
        if r["strike"] >= spot:
            prev = rows[i-1]["strike"] if i else r["strike"]
            si = (i - 1) + ((spot - prev) / (r["strike"] - prev) if r["strike"] != prev else 0)
            break
    sx = PL + slot * (si + 0.5)

    em = d.get("expected_move_1sigma_pts")
    em_marks = ""
    if em:
        for sign in (-1, 1):
            k = spot + sign * em
            frac = (k - rows[0]["strike"]) / (rows[-1]["strike"] - rows[0]["strike"]) if len(rows) > 1 else 0
            ex = PL + max(0.0, min(1.0, frac)) * PW
            em_marks += (f'<line class="em" x1="{ex:.1f}" y1="{PT+10}" x2="{ex:.1f}" y2="{PT+PH}"/>'
                         f'<text class="emlab" x="{ex:.1f}" y="{PT+6}" text-anchor="middle">1σ</text>')

    prov = d["provenance"]
    warn = f'<div class="warn">{prov["note"]}</div>' if prov.get("note") else ""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>OI profile {d['expiry'][:10]}</title><style>
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:var(--page);color:var(--tp);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
.viz-root{{color-scheme:light;--page:#f9f9f7;--surface-1:#fcfcfb;--tp:#0b0b0b;--ts:#52514e;--muted:#898781;
 --grid:#e1e0d9;--base:#c3c2b7;--ring:rgba(11,11,11,.10);--series-1:#2a78d6;--series-2:#eb6834}}
@media(prefers-color-scheme:dark){{:root:where(:not([data-theme="light"])) .viz-root{{color-scheme:dark;
 --page:#0d0d0d;--surface-1:#1a1a19;--tp:#fff;--ts:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--base:#383835;
 --ring:rgba(255,255,255,.10);--series-1:#3987e5;--series-2:#d95926}}}}
.card{{background:var(--surface-1);border:1px solid var(--ring);border-radius:12px;padding:20px 22px;max-width:1040px;margin:0 auto}}
h1{{font-size:17px;margin:0 0 2px;font-weight:650}} .sub{{font-size:12.5px;color:var(--ts);margin:0 0 14px}}
.legend{{display:flex;gap:18px;font-size:12.5px;color:var(--ts);margin-bottom:6px}}
.sw{{width:11px;height:11px;border-radius:3px;display:inline-block;margin-right:6px;vertical-align:-1px}}
svg{{display:block;width:100%;height:auto;overflow:visible}}
.s1{{fill:var(--series-1)}} .s2{{fill:var(--series-2)}}
.s1:hover,.s2:hover{{stroke:var(--surface-1);stroke-width:2}}
.gl{{stroke:var(--grid);stroke-width:1}} .ax{{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}}
.spot{{stroke:var(--tp);stroke-width:1.5;stroke-dasharray:4 3}}
.spotlab{{fill:var(--tp);font-size:11.5px;font-weight:600}}
.em{{stroke:var(--muted);stroke-width:1;stroke-dasharray:2 3}} .emlab{{fill:var(--muted);font-size:10px}}
.stats{{display:flex;flex-wrap:wrap;gap:26px;margin-top:16px;padding-top:14px;border-top:1px solid var(--grid)}}
.stat .l{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}}
.stat .v{{font-size:19px;font-weight:640;font-variant-numeric:tabular-nums}}
.warn{{margin-top:14px;font-size:12px;color:var(--ts);border-left:3px solid #fab219;padding:6px 0 6px 10px}}
.prov{{margin-top:12px;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}}
</style></head><body class="viz-root"><div class="card">
<h1>NIFTY open interest by strike — {d['expiry'][:10]} expiry</h1>
<p class="sub">capture {d['capture_id']} · {d['captured_at']} · spot {d['spot']:,.1f} ·
{d['n_strikes']} strikes · {d['dte_days']:.2f} DTE · VIX {d['vix'] if d['vix'] else '—'} ({d['vix_source'] or 'absent'})</p>
<div class="legend"><span><i class="sw" style="background:var(--series-1)"></i>Call OI</span>
<span><i class="sw" style="background:var(--series-2)"></i>Put OI</span>
<span style="color:var(--muted)">dashed = spot · dotted = ±1σ</span></div>
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Call and put open interest by strike">
{''.join(grid)}<line class="gl" x1="{PL}" y1="{PT+PH}" x2="{PL+PW}" y2="{PT+PH}" style="stroke:var(--base)"/>
{''.join(bars)}{em_marks}
<line class="spot" x1="{sx:.1f}" y1="{PT+2}" x2="{sx:.1f}" y2="{PT+PH}"/>
<text class="spotlab" x="{sx+7:.1f}" y="{PT-8}" text-anchor="start">spot {d['spot']:,.0f}</text>
{''.join(ticks)}<text class="ax" x="{PL+PW/2}" y="{H-16}" text-anchor="middle">strike</text></svg>
<div class="stats">
 <div class="stat"><div class="l">PCR (OI)</div><div class="v">{d['pcr_oi'] if d['pcr_oi'] else '—'}</div></div>
 <div class="stat"><div class="l">Max pain</div><div class="v">{d['max_pain']:,.0f}</div></div>
 <div class="stat"><div class="l">Straddle</div><div class="v">{d['atm_straddle_pts'] or '—'}</div></div>
 <div class="stat"><div class="l">1σ move</div><div class="v">{d['expected_move_1sigma_pts'] or '—'}</div></div>
 <div class="stat"><div class="l">OI centre</div><div class="v">{d['oi_centre_of_gravity'] or '—'}</div></div>
</div>{warn}
<div class="prov">σ from <b>{d['em_source'] or 'unavailable'}</b> · IV via {prov['iv_inverter']} ·
quotes {prov['quoted_strikes']} ({prov['price_source']})
{'· 1σ = straddle × 1.2533, NOT × 0.8 (D-SC-01)' if d['em_source']=='atm_straddle' else ''}</div>
</div></body></html>"""
    out = Path(outdir) / f"oi_profile_{d['capture_id']}_{d['expiry'][:10]}.html"
    out.write_text(html)
    return out


# ------------------------------------------------------------------ entry point
def build(view: str, capture_id=None, expiry=None, ts=None, outdir=None) -> dict:
    outdir = Path(outdir or (ROOT / ".state" / "mcp_artifacts"))
    outdir.mkdir(parents=True, exist_ok=True)
    con = connect(readonly=True)
    try:
        cap = _latest_capture(con, expiry=expiry, ts=ts, capture_id=capture_id)
        if view == "oi_profile":
            return _oi_profile(con, cap, expiry, outdir)
        if view in ("skew", "vrp", "term_structure"):
            raise NotImplementedError(
                f"'{view}' not built yet — oi_profile is the v1 view. "
                "skew binds to backend/quant/skew/adapter.py (D-SC-03); vrp needs "
                "ATM IV vs realized vs INDIAVIX; term_structure needs 2+ expiries per capture.")
        raise NotImplementedError(view)
    finally:
        con.close()
