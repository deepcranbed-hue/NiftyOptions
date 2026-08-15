#!/usr/bin/env python3
"""
bank_validation.py — five-gate signal validation for Nifty BANK (same engine as IT/Energy).

Bank is DOMESTICALLY driven — no clean overnight foreign sector-peer. Predictors are the
INDIRECT risk / flow / rate channels, each lagged one India session (overnight → next open):
    SPY     global risk (S&P500)        — strongest candidate (FII risk-off sells financials)
    XLF     US financials               — global banking sentiment (expect weak; banks domestic)
    VIX     fear                        — expect NEGATIVE IC (fear up → banks down)
    US10Y   rates → India rates → NIM   — (yield change)
    USDINR  FX                          — expect NEGATIVE (rupee weak → FII outflow → banks down)
    RISK_ON z(SPY)+z(XLF)-z(VIX)        — composite risk-on factor
    NASDAQ  broad-risk baseline         — does the bank set beat generic risk?

PRE-REGISTERED EXPECTATION (before the run): MODERATE / weak — a domestic sector should NOT
show the sharp overnight lead IT had. A weak result CONFIRMS the taxonomy (medium chain,
no foreign peer); it does not indict the framework. Gap/intraday split still applies.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_validation.py
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_db_path, resolve_pg_dsn
import os, sqlite3, sys, json

try:
    import numpy as np, pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")
try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required')
try:
    from scipy import stats as _sps
except Exception:
    _sps = None
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = resolve_db_path()
TARGET = "BANKNIFTY"
# name -> (source, key, transform)
FACTORS = {
    "SPY":    ("pg", "SPY", "pct"), "XLF": ("pg", "XLF", "pct"), "VIX": ("pg", "VIX", "pct"),
    "US10Y":  ("pg", "US10Y", "diff"), "USDINR": ("sqlite", "USDINR", "pct"),
    "NASDAQ": ("pg", "NASDAQ", "pct"),
}
BASELINE = "NASDAQ"
ROLL, MAXLAG = 60, 5


def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()


def _sqlite(sym, col="close"):
    con = sqlite3.connect(SQLITE_DB)
    try:
        rows = con.execute(f"SELECT ts, {col} FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY ts",
                           (sym,)).fetchall()
    finally:
        con.close()
    return pd.Series({_dkey(t): float(v) for t, v in rows if v is not None}, dtype=float).sort_index()


def _pg(conn, factor):
    with conn.cursor() as cur:
        cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date", (factor,))
        return pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None}, dtype=float).sort_index()


def _z(s):
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd else s * 0.0


def _spear(a, b):
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    return float("nan") if ra.std() == 0 or rb.std() == 0 else float(np.corrcoef(ra, rb)[0, 1])


def _ic(a, b):
    d = pd.concat([a.rename("p"), b.rename("y")], axis=1).dropna()
    return round(float(d["p"].corr(d["y"], method="spearman")), 3) if len(d) >= 20 else None


def causal_lag(us, idx):
    us = us.dropna().sort_index()
    if us.empty:
        return pd.Series(dtype=float)
    L = pd.DataFrame({"date": pd.DatetimeIndex(sorted(idx))})
    R = pd.DataFrame({"date": us.index, "val": us.to_numpy()})
    m = pd.merge_asof(L, R, on="date", direction="backward", allow_exact_matches=False)
    return pd.Series(m["val"].to_numpy(), index=pd.DatetimeIndex(m["date"]))


def block_boot(pred, tgt, block=10, nb=800):
    d = pd.concat([pred.rename("p"), tgt.rename("y")], axis=1).dropna()
    n = len(d)
    if n < block * 4:
        return None
    p, y = d["p"].to_numpy(), d["y"].to_numpy()
    pt = _spear(p, y); rng = np.random.default_rng(20260801); sm = n - block; ics = []
    for _ in range(nb):
        ix = []
        while len(ix) < n:
            s = int(rng.integers(0, sm + 1)); ix.extend(range(s, s + block))
        ix = np.array(ix[:n]); c = _spear(p[ix], y[ix])
        if c == c:
            ics.append(c)
    ics = np.array(ics)
    return {"ic": round(pt, 3), "p": round(2 * min((ics <= 0).mean(), (ics >= 0).mean()), 4),
            "ci": [round(float(np.percentile(ics, 2.5)), 3), round(float(np.percentile(ics, 97.5)), 3)]}


def decay(us, tgt, idx):
    pr = causal_lag(us, idx)
    return {k: _ic(pr, tgt.shift(-(k - 1))) for k in range(1, MAXLAG + 1)}


def regime(pred, tgt):
    d = pd.concat([pred.rename("p"), tgt.rename("y")], axis=1).dropna()
    return {str(y): _ic(w["p"], w["y"]) for y in sorted({i.year for i in d.index})
            for w in [d[(d.index >= f"{y}-01-01") & (d.index <= f"{y}-12-31")]]}


def score(pred, tgt):
    d = pd.concat([pred.rename("p"), tgt.rename("y")], axis=1).dropna()
    n = len(d)
    if n < 20:
        return {"n": n}
    p, y = d["p"], d["y"]; m = (p != 0) & (y != 0)
    hit = float((np.sign(p[m]) == np.sign(y[m])).mean()) if m.any() else None
    rr = [float(p.iloc[i-ROLL:i].corr(y.iloc[i-ROLL:i], method="spearman")) for i in range(ROLL, n + 1)]
    rr = [r for r in rr if r == r]
    return {"n": n, "ic": round(float(p.corr(y, method="spearman")), 3),
            "hit": round(hit, 3) if hit is not None else None,
            "roll_sd": round(float(np.std(rr)), 3) if rr else None}


def wf_incr(tgt, base_lag, add_lag, mt=250):
    d = pd.concat([tgt.rename("y"), base_lag.rename("b"), add_lag.rename("a")], axis=1).dropna()
    n = len(d)
    if n < mt + 60:
        return None
    y, b, a = d["y"].to_numpy(), d["b"].to_numpy(), d["a"].to_numpy()
    hb = ha = c = 0
    for t in range(mt, n):
        if y[t] == 0:
            continue
        cb, *_ = np.linalg.lstsq(np.column_stack([np.ones(t), b[:t]]), y[:t], rcond=None)
        ca, *_ = np.linalg.lstsq(np.column_stack([np.ones(t), b[:t], a[:t]]), y[:t], rcond=None)
        hb += int(np.sign(cb[0] + cb[1]*b[t]) == np.sign(y[t]))
        ha += int(np.sign(ca[0] + ca[1]*b[t] + ca[2]*a[t]) == np.sign(y[t])); c += 1
    return None if not c else {"base": round(hb/c, 3), "aug": round(ha/c, 3), "delta": round((ha-hb)/c, 3), "n": c}


def build_returns(conn):
    series = {}
    for name, (src, key, tf) in FACTORS.items():
        s = _sqlite(key) if src == "sqlite" else _pg(conn, key)
        if s.notna().any():
            series[name] = s.diff() if tf == "diff" else s.pct_change() * 100.0
    # target OHLC for gap/intraday
    o, c = _sqlite(TARGET, "open"), _sqlite(TARGET, "close")
    tgt = (c.pct_change() * 100.0).dropna()
    prev = c.shift(1)
    gap = ((o - prev) / prev * 100).dropna(); intra = ((c - o) / o * 100).dropna()
    return {k: v.dropna() for k, v in series.items()}, tgt, gap, intra


def main():
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    rets, tgt, gap, intra = build_returns(conn); conn.close()
    if tgt.empty:
        sys.exit(f"No {TARGET} data.")
    idx = tgt.index

    # composite risk-on = z(SPY)+z(XLF)-z(VIX)
    if all(k in rets for k in ("SPY", "XLF", "VIX")):
        df = pd.concat([_z(rets["SPY"]).rename("s"), _z(rets["XLF"]).rename("x"), _z(rets["VIX"]).rename("v")],
                       axis=1).dropna()
        rets["RISK_ON"] = (df["s"] + df["x"] - df["v"])

    order = ["RISK_ON", "SPY", "XLF", "VIX", "US10Y", "USDINR", "NASDAQ"]
    preds = {k: causal_lag(rets[k], idx) for k in order if k in rets}

    rows = {}
    for k, pr in preds.items():
        r = score(pr, tgt)
        if r.get("n", 0) >= 20:
            r["bb"] = block_boot(pr, tgt); r["decay"] = decay(rets[k], tgt, idx); r["regime"] = regime(pr, tgt)
            r["gap"] = _ic(pr, gap); r["intra"] = _ic(pr, intra)
        rows[k] = r

    rep = "RISK_ON" if "RISK_ON" in rows else ("SPY" if "SPY" in rows else order[0])
    rv = rows.get(rep, {})
    # incremental: does the bank set (RISK_ON) beat generic broad risk (NASDAQ)?
    base_ic = rows.get(BASELINE, {}).get("ic", -9)
    wf = wf_incr(tgt, preds.get(BASELINE, pd.Series(dtype=float)), preds.get(rep, pd.Series(dtype=float))) \
        if BASELINE in preds and rep in preds else None

    print(f"\n=== NIFTY BANK signal validation (target {TARGET}, {len(idx)} sessions) ===")
    print("  PRE-REG: MODERATE/weak expected (domestic sector, no overnight foreign peer).")
    print(f"\n  {'predictor':<9}{'IC':>7}{'blkP':>7}{'hit%':>7}{'roll_sd':>9}{'gapIC':>8}{'intraIC':>9}")
    for k in order:
        r = rows.get(k) or {}
        if r.get("ic") is None:
            continue
        bb = r.get("bb") or {}
        print(f"  {k:<9}{r['ic']:>7.3f}{(bb.get('p') if bb.get('p') is not None else float('nan')):>7.3f}"
              f"{(r.get('hit') or 0)*100:>7.1f}{(r.get('roll_sd') or 0):>9.3f}"
              f"{(r.get('gap') if r.get('gap') is not None else float('nan')):>8.3f}"
              f"{(r.get('intra') if r.get('intra') is not None else float('nan')):>9.3f}")

    print(f"\n  Decay (lag1→5) — {rep}: " + "  ".join(f"L{k}:{(rv.get('decay') or {}).get(k)}" for k in range(1, MAXLAG+1)))
    print(f"  Regime IC per year — {rep}: " + "  ".join(f"{y}:{v}" for y, v in (rv.get('regime') or {}).items() if v is not None))
    if wf:
        print(f"  Incremental (walk-fwd): NASDAQ base {wf['base']} → +{rep} aug {wf['aug']}  Δ {wf['delta']:+}  (n={wf['n']})")

    # five gates (adapted)
    d = rv.get("decay") or {}; L1 = d.get(1); later = [d.get(k) for k in (2,3,4,5) if d.get(k) is not None]
    bb = rv.get("bb") or {}
    g2 = bool(L1 is not None and later and abs(L1) > 0 and max(abs(x) for x in later) < 0.10
              and bb.get("p") is not None and bb["p"] < 0.05)
    # Gate 3 — ECONOMIC threshold, not Δ>0: incremental edge must be MATERIAL, not just positive.
    ic_edge = abs(rv.get("ic", 0)) - abs(base_ic)
    g3 = bool(ic_edge >= 0.02 and wf and (wf.get("delta") or 0) >= 0.02)
    g3_note = f"IC edge {ic_edge:+.3f} (need ≥0.02); wfΔ {wf.get('delta') if wf else None} (need ≥0.02)"
    intra_ic = rv.get("intra"); gap_ic = rv.get("gap")
    captured = bool(intra_ic is not None and abs(intra_ic) >= 0.10)
    print("\n  FIVE GATES (rep = %s):" % rep)
    print(f"    Gate1 Causality      PASS  (merge_asof overnight lag, price/macro)")
    print(f"    Gate2 Decay+signif   {'PASS' if g2 else 'FAIL'}  (L1={L1} ≫ later {later}; blkP={bb.get('p')})")
    print(f"    Gate3 Incremental    {'PASS' if g3 else 'FAIL'}  ({g3_note}) — economic threshold, not Δ>0")
    print(f"    Gate4 Out-of-sample  {'PASS' if (wf and (wf.get('aug') or 0) > 0.52) else 'FAIL'}  (aug hit={wf.get('aug') if wf else None})")
    print(f"    Gate5 Info capture   {'CAPTURED' if captured else 'GAP-ONLY'}  (gap {gap_ic} vs intraday {intra_ic})")
    verdict = "signal present" if (g2 and g3) else "weak/absent — consistent with domestic sector"
    print(f"\n  READ: {verdict}. (Weak overnight IC here CONFIRMS the taxonomy — Bank's real")
    print("        driver is the domestic rate cycle, a thesis-engine/fundamental question.)")


if __name__ == "__main__":
    main()
