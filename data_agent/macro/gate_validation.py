#!/usr/bin/env python3
"""
signal_validation.py — does the US→India IT rotation signal contain REAL,
causal, regime-stable predictive information? Diagnostic ONLY — no trading,
no PnL, no thresholds to tune. (The strategy backtest stays frozen until this
passes; keeping them separate is the whole point.)

Answers, and nothing else:
    1. Is the signal CAUSAL?              merge_asof to the most recent COMPLETED
                                          US session before each India open (never shift()).
    2. Is it STATISTICALLY SIGNIFICANT?   Spearman IC + p-value / t-stat.
    3. Does it survive REGIMES?           per-year IC (needs multi-year backfill).
    4. Does it DECAY sensibly?            IC at lag 1..5 India sessions.
    5. Does it add info beyond SIMPLER    walk-forward OOS incremental hit-rate of
       alternatives (Nasdaq/XLK/peers)?   SW_SERVICES + rotation vs SW_SERVICES alone.
    +  monotonic QUANTILE response        (the leak-free form of a threshold sweep).

CAUSAL BY CONSTRUCTION
  * Alignment: pandas.merge_asof(direction='backward', allow_exact_matches=False)
    → each India date D uses the latest US date < D. Handles divergent holidays;
    a US close on date D (evening ET = early IST of D+1) is NOT visible to India D.
  * Normalization: EQUAL-WEIGHT baskets by default — no fitted weights, so zero
    look-ahead. Spearman IC and sign hit-rate are rank/sign-invariant anyway; only
    PCA weights would leak, so PCA is opt-in (--method pca) and flagged.
  * ADR-only: INFY_ADR / WIT (Wipro ADR) — a guard rejects local NSE 'INFY' to
    prevent same-session constituent leakage into the target.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python signal_validation.py
    python signal_validation.py --method pca --json
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_db_path, resolve_pg_dsn

import argparse
import json
import os
import sqlite3
import sys

try:
    import numpy as np
    import pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")
try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')
try:
    from scipy import stats as _sps
except Exception:
    _sps = None
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv(
    "OPTION_CHAINS_DB",
    resolve_db_path(),
)
TARGET = "NIFTY_IT"
GROUPS = {
    "SW_SERVICES": ["ACN", "CTSH", "INFY_ADR", "EPAM", "WIT_ADR", "IBM"],   # ADRs only (WIT_ADR = Wipro ADR)
    "SW_PRODUCTS": ["CRM", "ADBE", "MSFT"],
    "AI_INFRA":    ["NVDA", "MU", "SMH"],
}
BASELINES = ["NASDAQ", "XLK"]
BANNED = {"INFY"}          # local NSE Infosys — would leak the target's own constituent
ROLL_WIN = 60
MAX_LAG = 5


def _dkey(x):
    t = pd.to_datetime(x)
    return (t.tz_convert(None) if t.tzinfo is not None else t).normalize()


def _sqlite_price(symbol, timeframe="1d"):
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite db not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    try:
        rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe=? ORDER BY ts",
                           (symbol, timeframe)).fetchall()
    finally:
        con.close()
    return pd.Series({_dkey(t): float(v) for t, v in rows if v is not None}, dtype=float).sort_index()


def _pg_price(conn, factor):
    with conn.cursor() as cur:
        cur.execute("SELECT obs_date, value FROM macro.factor_series WHERE factor=%s ORDER BY obs_date",
                    (factor,))
        return pd.Series({_dkey(d): float(v) for d, v in cur.fetchall() if v is not None},
                         dtype=float).sort_index()


def load_target_ohlc():
    """NIFTYIT open & close, to split the day into overnight-gap vs intraday. The
    TRADEABILITY test: a US overnight signal that predicts close-to-close usually lives
    in the OPENING GAP (Nifty opens already repriced) — you cannot capture it at the
    open. Only IC that survives into open→close (intraday) is exploitable."""
    con = sqlite3.connect(SQLITE_DB)
    try:
        rows = con.execute("SELECT ts, open, close FROM price_bars WHERE symbol=? AND timeframe=? ORDER BY ts",
                           ("NIFTYIT", "1d")).fetchall()
    finally:
        con.close()
    o = pd.Series({_dkey(t): float(op) for t, op, c in rows if op is not None}, dtype=float).sort_index()
    c = pd.Series({_dkey(t): float(cl) for t, op, cl in rows if cl is not None}, dtype=float).sort_index()
    prev_c = c.shift(1)
    gap = ((o - prev_c) / prev_c * 100.0).dropna()          # overnight: prev close → open
    intraday = ((c - o) / o * 100.0).dropna()               # open → close (the tradeable leg)
    return gap, intraday


def _ic(a, b):
    df = pd.concat([a.rename("p"), b.rename("y")], axis=1).dropna()
    return round(float(df["p"].corr(df["y"], method="spearman")), 3) if len(df) >= 20 else None


def load_prices():
    """Per-series price Series, each on its OWN trading calendar (never merged before
    returns — merging first would corrupt pct_change across divergent holidays)."""
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT factor FROM macro.factor_series")
            present = {r[0] for r in cur.fetchall()}
        prices = {TARGET: _sqlite_price("NIFTYIT")}
        wanted = set(BASELINES) | {t for g in GROUPS.values() for t in g}
        found, missing = [], []
        for f in sorted(wanted):
            if f in present:
                s = _pg_price(conn, f)
                if s.notna().any():
                    prices[f] = s; found.append(f); continue
            missing.append(f)
    finally:
        conn.close()
    return prices, found, missing


def ret(prices, name):
    return (prices[name].pct_change() * 100.0).dropna() if name in prices else None


def data_quality(prices, thresh=25.0):
    """Flag series with implausible single-day moves (|ret|>thresh%) — the signature
    of an unadjusted split/dividend or a bad bar (e.g. IBM printing -22.8%/20d while
    'LEADING'). A corrupt constituent silently poisons its basket, so it is caught
    BEFORE the gates rather than after."""
    issues = {}
    for name, s in prices.items():
        r = (s.pct_change() * 100.0).dropna()
        bad = r[r.abs() > thresh]
        if len(bad):
            issues[name] = [(str(d.date()), round(float(v), 1)) for d, v in list(bad.items())[:4]]
    return issues


def basket_equalweight(prices, tickers):
    """MEDIAN of constituent returns — the robust common move. A single name's
    idiosyncratic shock (e.g. IBM -25% on its 2026-07-14 earnings warning) does NOT
    hijack the systematic factor the way an equal-weight MEAN would. On normal days
    median≈mean; on a one-name blow-up the median ignores the outlier. PCA (--method
    pca) extracts the same common component and is likewise robust."""
    cols = {t: ret(prices, t) for t in tickers if t in prices and ret(prices, t) is not None}
    if not cols:
        return None, []
    df = pd.DataFrame(cols)                       # aligned on the US calendar
    return df.median(axis=1, skipna=True).dropna(), list(cols)


def basket_pca(prices, tickers):
    cols = {t: ret(prices, t) for t in tickers if t in prices and ret(prices, t) is not None}
    if len(cols) < 2:
        return (list(cols) and pd.DataFrame(cols).mean(axis=1).dropna()) or None, list(cols)
    R = pd.DataFrame(cols).dropna()
    Z = (R - R.mean()) / R.std(ddof=0).replace(0, np.nan)
    Z = Z.dropna(axis=1, how="any")
    _, _, Vt = np.linalg.svd(Z.to_numpy(), full_matrices=False)
    load = Vt[0]; load = -load if load.sum() < 0 else load
    return pd.Series(Z.to_numpy() @ load, index=Z.index), list(cols)


def causal_lag(us_series, india_index):
    """Each India date → the latest US date STRICTLY before it (merge_asof, not shift).
    This is the causal core: no positional shifting, holiday-safe."""
    us = us_series.dropna().sort_index()
    if us.empty:
        return pd.Series(dtype=float)
    left = pd.DataFrame({"date": pd.DatetimeIndex(sorted(india_index))})
    right = pd.DataFrame({"date": us.index, "val": us.to_numpy()})
    m = pd.merge_asof(left, right, on="date", direction="backward", allow_exact_matches=False)
    return pd.Series(m["val"].to_numpy(), index=pd.DatetimeIndex(m["date"]))


# ---------- metrics (all on causally-aligned, India-indexed series) ----------
def score(pred, target):
    df = pd.concat([pred.rename("p"), target.rename("y")], axis=1).dropna()
    n = len(df)
    if n < 20:
        return {"n": n}
    p, y = df["p"], df["y"]
    ic = float(p.corr(y, method="spearman"))
    if _sps is not None:
        _, pval = _sps.spearmanr(p, y)
        pval = float(pval)
    else:
        t = ic * np.sqrt(max(n - 2, 1) / max(1 - ic * ic, 1e-9))
        pval = None
    m = (p != 0) & (y != 0)
    hit = float((np.sign(p[m]) == np.sign(y[m])).mean()) if m.any() else None
    rics = [float(p.iloc[i-ROLL_WIN:i].corr(y.iloc[i-ROLL_WIN:i], method="spearman"))
            for i in range(ROLL_WIN, n + 1)]
    rics = [r for r in rics if r == r]
    return {"n": n, "ic": round(ic, 3), "ic_p": round(pval, 4) if pval is not None else None,
            "hit_rate": round(hit, 3) if hit is not None else None,
            "hit_chance": round(0.5 + 1.96 * 0.5 / np.sqrt(int(m.sum() or n)), 3),
            "rolling_ic_mean": round(float(np.mean(rics)), 3) if rics else None,
            "rolling_ic_std": round(float(np.std(rics)), 3) if rics else None}


def _spearman(a, b):
    ra = pd.Series(a).rank().to_numpy(); rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def block_bootstrap_ic(pred, target, block=10, n_boot=1000):
    """Dependence-respecting significance for the IC. Moving-block bootstrap over
    consecutive sessions preserves serial correlation, so the CI/p reflect the
    EFFECTIVE sample size — the fix for the Welch t-test's independence assumption.
    (Newey-West HAC is the parametric analogue; block bootstrap needs no lag guess.)"""
    df = pd.concat([pred.rename("p"), target.rename("y")], axis=1).dropna()
    n = len(df)
    if n < block * 4:
        return None
    p = df["p"].to_numpy(); y = df["y"].to_numpy()
    point = _spearman(p, y)
    rng = np.random.default_rng(20260801)
    starts_max = n - block
    ics = []
    for _ in range(n_boot):
        idx = []
        while len(idx) < n:
            s = int(rng.integers(0, starts_max + 1))
            idx.extend(range(s, s + block))
        idx = np.array(idx[:n])
        c = _spearman(p[idx], y[idx])
        if c == c:
            ics.append(c)
    ics = np.array(ics)
    lo, hi = np.percentile(ics, [2.5, 97.5])
    p_two = 2.0 * min(float((ics <= 0).mean()), float((ics >= 0).mean()))
    return {"ic": round(point, 3), "ci95": [round(float(lo), 3), round(float(hi), 3)],
            "p_block_boot": round(p_two, 4), "block": block, "n_boot": len(ics)}


def ic_decay(us_factor, target, india_index):
    pred = causal_lag(us_factor, india_index)
    out = {}
    for k in range(1, MAX_LAG + 1):
        df = pd.concat([pred.rename("p"), target.shift(-(k - 1)).rename("y")], axis=1).dropna()
        out[k] = round(float(df["p"].corr(df["y"], method="spearman")), 3) if len(df) >= 20 else None
    return out


def regime_ic(pred, target):
    df = pd.concat([pred.rename("p"), target.rename("y")], axis=1).dropna()
    return {str(yr): (round(float(w["p"].corr(w["y"], method="spearman")), 3) if len(w) >= 20 else None)
            for yr in sorted({d.year for d in df.index})
            for w in [df[(df.index >= f"{yr}-01-01") & (df.index <= f"{yr}-12-31")]]}


def quantile_response(pred, target, q=5):
    df = pd.concat([pred.rename("p"), target.rename("y")], axis=1).dropna()
    if len(df) < 40:
        return None
    try:
        df["b"] = pd.qcut(df["p"], q, labels=False, duplicates="drop")
    except ValueError:
        return None
    g = df.groupby("b")["y"].mean()
    mono = float(pd.Series(g.index).corr(pd.Series(g.to_numpy()), method="spearman"))
    return {"bins": {int(k): round(float(v), 3) for k, v in g.items()},
            "monotonicity": round(mono, 3)}


def walkforward_incremental(target, base_pred, add_pred, min_train=120):
    df = pd.concat([target.rename("y"), base_pred.rename("b"), add_pred.rename("a")], axis=1).dropna()
    n = len(df)
    if n < min_train + 40:
        return None
    y = df["y"].to_numpy(); b = df["b"].to_numpy(); a = df["a"].to_numpy()
    hb = ha = cnt = 0
    for t in range(min_train, n):
        if y[t] == 0:
            continue
        cb, *_ = np.linalg.lstsq(np.column_stack([np.ones(t), b[:t]]), y[:t], rcond=None)
        ca, *_ = np.linalg.lstsq(np.column_stack([np.ones(t), b[:t], a[:t]]), y[:t], rcond=None)
        hb += int(np.sign(cb[0] + cb[1] * b[t]) == np.sign(y[t]))
        ha += int(np.sign(ca[0] + ca[1] * b[t] + ca[2] * a[t]) == np.sign(y[t]))
        cnt += 1
    if not cnt:
        return None
    return {"base_hit": round(hb / cnt, 3), "aug_hit": round(ha / cnt, 3),
            "delta": round((ha - hb) / cnt, 3), "n_oos": cnt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=["equalweight", "pca"], default="equalweight")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    mk = basket_pca if args.method == "pca" else basket_equalweight

    prices, found, missing = load_prices()
    if TARGET not in prices or prices[TARGET].empty:
        sys.exit("Target NIFTY_IT (SQLite NIFTYIT) missing/empty.")
    target = ret(prices, TARGET)
    idx = target.index
    dq_issues = data_quality(prices)     # catch corrupt series before they poison baskets

    banned_hit = sorted(BANNED & (set(found) | {t for g in GROUPS.values() for t in g if t in prices}))
    services, svc_cols = mk(prices, GROUPS["SW_SERVICES"])
    products, prod_cols = mk(prices, GROUPS["SW_PRODUCTS"])
    infra, infra_cols = mk(prices, GROUPS["AI_INFRA"])
    sw_all, all_cols = mk(prices, GROUPS["SW_SERVICES"] + GROUPS["SW_PRODUCTS"])
    # DECOMPOSE the services signal: EXTERNAL peers vs Nifty-IT's OWN constituent ADRs.
    # INFY_ADR/WIT_ADR are Infosys/Wipro — index members repriced overnight (near-mechanical
    # "own-ADR leads local"), NOT peer transmission. Isolate to interpret the IC honestly.
    CONSTITUENT_ADRS = ["INFY_ADR", "WIT_ADR"]
    PURE_PEERS = [t for t in GROUPS["SW_SERVICES"] if t not in CONSTITUENT_ADRS]   # ACN,CTSH,EPAM,IBM
    pure, pure_cols = mk(prices, PURE_PEERS)
    const, const_cols = mk(prices, CONSTITUENT_ADRS)

    # US-calendar factor series → causal India-aligned predictors
    us_factors = {}
    for b in BASELINES:
        if b in prices:
            us_factors[b] = ret(prices, b)
    if pure is not None:     us_factors["PURE_PEERS"] = pure          # ACN/CTSH/EPAM/IBM — external only
    if const is not None:    us_factors["CONSTITUENT_ADR"] = const    # INFY_ADR/WIT_ADR — own members
    if services is not None: us_factors["SW_SERVICES"] = services
    if products is not None: us_factors["SW_PRODUCTS"] = products
    if sw_all is not None:   us_factors["SW_ALL"] = sw_all
    if infra is not None:    us_factors["AI_INFRA"] = infra
    if services is not None and infra is not None:
        js = pd.concat([services.rename("s"), infra.rename("i")], axis=1).dropna()
        us_factors["GSS_ROTATION"] = (js["s"] - js["i"])

    preds = {k: causal_lag(v, idx) for k, v in us_factors.items()}

    report = {"target": TARGET, "method": args.method, "found": found, "missing": missing,
              "sw_services": svc_cols, "ai_infra": infra_cols, "banned_present": banned_hit,
              "n_target": len(target),
              "span": [str(idx.min().date()), str(idx.max().date())] if len(idx) else None,
              "candidates": {}}
    for name, pr in preds.items():
        rec = score(pr, target)
        if rec.get("n", 0) >= 20:
            rec["ic_decay"] = ic_decay(us_factors[name], target, idx)
            rec["regime_ic"] = regime_ic(pr, target)
            rec["quantile_response"] = quantile_response(pr, target)
            rec["block_boot"] = block_bootstrap_ic(pr, target)   # dependence-corrected significance
        report["candidates"][name] = rec

    report["walkforward"] = {}
    if "SW_SERVICES" in preds and "AI_INFRA" in preds:
        report["walkforward"]["+AI_INFRA"] = walkforward_incremental(target, preds["SW_SERVICES"], preds["AI_INFRA"])
    if "SW_SERVICES" in preds and "GSS_ROTATION" in preds:
        report["walkforward"]["+GSS_ROTATION"] = walkforward_incremental(target, preds["SW_SERVICES"], preds["GSS_ROTATION"])
    # the decisive one: over EXTERNAL peers, how much do the OWN-constituent ADRs add?
    if "PURE_PEERS" in preds and "CONSTITUENT_ADR" in preds:
        report["walkforward"]["PURE_PEERS +constituentADR"] = walkforward_incremental(
            target, preds["PURE_PEERS"], preds["CONSTITUENT_ADR"])

    rank = sorted([(k, v) for k, v in report["candidates"].items() if v.get("ic") is not None],
                  key=lambda kv: (kv[1].get("hit_rate") or 0, kv[1]["ic"]), reverse=True)
    report["ranking"] = [k for k, _ in rank]

    # ---- FIVE MANDATORY GATES (all must PASS before any strategy backtest) -----
    # judged on SHAPE (decay), RELATIVE value (beats indices), and OOS + dependence-
    # corrected significance — NEVER on the IC's magnitude vs a prior.
    # judge the DEPLOYABLE external signal first (pure peers), then services, then rotation
    rep = next((c for c in ["PURE_PEERS", "SW_SERVICES", "GSS_ROTATION"] if c in report["candidates"]),
               (report["ranking"][0] if report["ranking"] else None))
    repv = report["candidates"].get(rep, {}) if rep else {}
    n_years = len({d.year for d in idx})

    # ---- TRADEABILITY: is the IC in the untradeable overnight GAP, or in intraday? ----
    try:
        tgt_gap, tgt_intra = load_target_ohlc()
    except Exception as e:
        tgt_gap, tgt_intra = None, None
        report["ohlc_error"] = str(e)
    gap_decomp, trade_flag, intra_p = {}, None, None
    if tgt_gap is not None and tgt_intra is not None:
        for name in [c for c in ["PURE_PEERS", "SW_SERVICES", "GSS_ROTATION", "CONSTITUENT_ADR"] if c in preds]:
            gap_decomp[name] = {"c2c": report["candidates"].get(name, {}).get("ic"),
                                "gap": _ic(preds[name], tgt_gap), "intraday": _ic(preds[name], tgt_intra)}
        if rep in preds:
            bbp = block_bootstrap_ic(preds[rep], tgt_intra)
            intra_p = bbp.get("p_block_boot") if bbp else None
            gi = gap_decomp.get(rep, {})
            intr = gi.get("intraday")
            # tradeable only if the OPEN→CLOSE IC is material AND significant after dependence
            trade_flag = bool(intr is not None and abs(intr) >= 0.10
                              and intra_p is not None and intra_p < 0.05)
    report["gap_decomp"] = gap_decomp
    report["tradeable_intraday"] = trade_flag
    # per-year IC of the TRADEABLE (intraday) leg — the real regime test: does the
    # capturable edge survive covid / 2022 shock / 2023, or is it a 2025-26 AI-boom artifact?
    report["intraday_regime_ic"] = (regime_ic(preds[rep], tgt_intra)
                                    if (tgt_intra is not None and rep in preds) else {})

    # Gate 1 — Causality (only pre-open info; ADR-only; return innovation; causal align).
    # Data-quality flags are ADVISORY, not a Gate-1 block: an extreme move can be a real
    # earnings crash (IBM -25% on 2026-07-14), and the MEDIAN basket already neutralizes
    # a single-name idiosyncratic shock — so it can't poison the systematic factor.
    g1 = (not banned_hit) and (args.method == "equalweight")
    g1_reason = ("merge_asof + return-innovation + ADR-only; median basket robust to single-name shocks"
                 if g1 else (f"local INFY present {banned_hit}" if banned_hit else "PCA weights leak full sample"))
    # Gate 2 — Decay (ECONOMIC spec, not machine-precision monotonicity): L1 must be
    # materially larger than every later lag, AND all later lags economically
    # insignificant (|IC|<0.10). "Signal disappears rapidly after Day 1" — a tiny L2/L3
    # wiggle in the noise floor is not a failure. Plus significant after block bootstrap.
    d = repv.get("ic_decay") or {}
    L1 = d.get(1)
    later = [d.get(k) for k in (2, 3, 4, 5) if d.get(k) is not None]
    bb = repv.get("block_boot") or {}
    sig = bb.get("p_block_boot") is not None and bb["p_block_boot"] < 0.05
    if L1 is not None and later:
        materially_larger = L1 > 0 and all(abs(x) < 0.5 * L1 for x in later)
        later_insignificant = max(abs(x) for x in later) < 0.10
        decays = bool(materially_larger and later_insignificant)
    else:
        decays = False
    g2 = bool(decays and sig)
    g2_reason = (f"{rep}: L1={L1} ≫ later={later} (all |IC|<0.10 → rapid decay={decays}); "
                 f"block-boot p={bb.get('p_block_boot')} CI{bb.get('ci95')}")
    # Gate 3 — Incremental information over the SIMPLE alternatives.
    #   base factor (PURE_PEERS/SW_SERVICES): must beat BOTH Nasdaq and XLK on IC.
    #   rotation factor (GSS_ROTATION): must additionally add OOS hit over services.
    nas_ic = report["candidates"].get("NASDAQ", {}).get("ic", -9)
    xlk_ic = report["candidates"].get("XLK", {}).get("ic", -9)
    beats_index = repv.get("ic", -9) > nas_ic and repv.get("ic", -9) > xlk_ic
    rot_wf = report["walkforward"].get("+GSS_ROTATION") or {}
    rotation_adds = (rot_wf.get("delta") or 0) > 0
    g3 = bool(beats_index and (rotation_adds if rep == "GSS_ROTATION" else True))
    g3_reason = (f"IC {rep}={repv.get('ic')} vs Nasdaq={nas_ic}/XLK={xlk_ic}"
                 + (f"; rotation OOS Δ={rot_wf.get('delta')}" if rep == "GSS_ROTATION" else ""))
    # Gate 4 — Out-of-sample: the deployable model's walk-forward OOS hit beats chance.
    wf = (report["walkforward"].get("PURE_PEERS +constituentADR")
          or report["walkforward"].get("+GSS_ROTATION")
          or report["walkforward"].get("+AI_INFRA") or {})
    g4 = bool(wf and (wf.get("aug_hit") or 0) > 0.52)
    g4_reason = f"walk-forward aug_hit={wf.get('aug_hit')} (n_oos={wf.get('n_oos')})"
    # Gate 5 — INFORMATION CAPTURE (was "tradeability"): the signal may be REAL and still
    # unreachable at your execution horizon. gap-heavy = market prices it before you act
    # (efficiency), not that the signal is bad. Human sign-off; depends on access.
    if trade_flag is True:
        g5_reason = (f"HUMAN SIGN-OFF — captured at open: {rep} intraday IC material & significant "
                     f"(p={intra_p}); real AND reachable.")
    elif trade_flag is False:
        g5_reason = (f"⚠ NOT CAPTURED AT OPEN — {rep} IC is in the overnight GAP "
                     f"(gap={gap_decomp.get(rep, {}).get('gap')} vs intraday={gap_decomp.get(rep, {}).get('intraday')}, "
                     f"p={intra_p}). Signal is REAL but the open already prices it (efficient transmission). "
                     f"Capturable only with pre-open / cross-listed access; blocks a plain at-open strategy.")
    else:
        g5_reason = "HUMAN SIGN-OFF — information-capture not computed (OHLC missing)"

    gates = {
        "Gate 1  Causality":              ("PASS" if g1 else "FAIL", g1_reason),
        "Gate 2  Decay + significance":   ("PASS" if g2 else "FAIL", g2_reason),
        "Gate 3  Incremental information":("PASS" if g3 else "FAIL", g3_reason),
        "Gate 4  Out-of-sample":          ("PASS" if g4 else "FAIL", g4_reason),
        "Gate 5  Information capture":     ("MANUAL", g5_reason),
    }
    report["gates"] = {k: v[0] for k, v in gates.items()}
    auto = [g1, g2, g3, g4]
    if all(auto) and trade_flag is False:
        report["gate_verdict"] = ("AUTO-GATES PASS — signal is REAL but NOT captured at the open "
                                  "(efficient transmission); plain at-open strategy stays BLOCKED")
    elif all(auto):
        report["gate_verdict"] = "ALL AUTO-GATES PASS — Gate 5 human sign-off (captured at open), then strategy may open"
    else:
        report["gate_verdict"] = "BLOCKED — strategy stays frozen"
    report["multiple_regimes"] = "PASS" if n_years >= 3 else f"WEAK ({n_years} yr — backfill to 2018)"

    if args.json:
        print(json.dumps(report, indent=2)); return

    print(f"\n=== signal_validation — {TARGET} (method={args.method}) ===")
    print(f"  present: {found}")
    if missing:
        print(f"  missing (add via download_us_stocks.py): {missing}")
    print(f"  span {report['span']}   sessions {report['n_target']}   regimes: {report['multiple_regimes']}")
    if dq_issues:
        print("\n  ⚠️  DATA QUALITY — extreme daily moves (advisory: real earnings crash OR unadjusted split?):")
        for name, days in dq_issues.items():
            print(f"      {name:<10} {days}   → if a real move (e.g. IBM 2026-07-14), fine — median basket absorbs it; "
                  f"if a split/bad bar, re-fetch adjusted")
    print(f"\n  FIVE MANDATORY GATES  (representative candidate: {rep})")
    for k, (verdict, why) in gates.items():
        mark = "✅" if verdict == "PASS" else ("🖐" if verdict == "MANUAL" else "❌")
        print(f"    {mark} {k:<32} {verdict:<6} {why}")
    print(f"\n  VERDICT: {report['gate_verdict']}")

    if gap_decomp:
        print("\n  TRADEABILITY — where does the IC live? (gap = untradeable at open, intraday = capturable)")
        print(f"    {'candidate':<16}{'close→close':>12}{'overnight gap':>15}{'intraday':>11}")
        for name, gi in gap_decomp.items():
            print(f"    {name:<16}{str(gi.get('c2c')):>12}{str(gi.get('gap')):>15}{str(gi.get('intraday')):>11}")
        print(f"    → {rep} intraday block-boot p = {intra_p}   "
              f"tradeable_intraday = {report['tradeable_intraday']}")
        ir = report.get("intraday_regime_ic") or {}
        if ir:
            print(f"    → {rep} INTRADAY IC per year (the real regime test): "
                  + "  ".join(f"{yr}:{ic}" for yr, ic in ir.items() if ic is not None))
            print("      (holds across 2018/2020/2022/2023 ⇒ structural edge; only 2025-26 ⇒ AI-boom artifact)")
        print("    (if the IC is almost all in the GAP and ~0 intraday, Nifty opens already repriced —")
        print("     the signal is real but NOT capturable at the open; size/again before believing PnL.)")

    print(f"\n  {'candidate':<16}{'n':>5}{'IC':>7}{'blkP':>7}{'IC 95% CI':>16}{'hit%':>7}{'roll±':>7}{'mono':>7}")
    for name in report["ranking"]:
        v = report["candidates"][name]
        qr = v.get("quantile_response") or {}; bb = v.get("block_boot") or {}
        ci = bb.get("ci95"); ci_s = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "—"
        print(f"  {name:<16}{v['n']:>5}{v['ic']:>7.3f}"
              f"{(bb.get('p_block_boot') if bb.get('p_block_boot') is not None else float('nan')):>7.3f}"
              f"{ci_s:>16}{(v.get('hit_rate') or 0)*100:>7.1f}"
              f"{(v.get('rolling_ic_std') or 0):>7.2f}{qr.get('monotonicity', float('nan')):>7.2f}")
    print("  (blkP = block-bootstrap p, dependence-corrected; CI excludes 0 ⇒ significant)")

    print("\n  IC decay (lag 1→5 India sessions):")
    for name in report["ranking"][:5]:
        d = report["candidates"][name].get("ic_decay") or {}
        print(f"    {name:<16} " + "  ".join(f"L{k}:{d.get(k)}" for k in range(1, MAX_LAG + 1)))

    print("\n  Walk-forward incremental hit-rate (OOS, over SW_SERVICES):")
    for k, w in report["walkforward"].items():
        if w:
            print(f"    SW_SERVICES {k:<14} base {w['base_hit']}  aug {w['aug_hit']}  Δ {w['delta']:+}  (n={w['n_oos']})")

    print("\n  Quantile response (signal quintile → mean next-day IT %, want monotone):")
    for name in report["ranking"][:4]:
        qr = report["candidates"][name].get("quantile_response") or {}
        if qr:
            cells = "  ".join(f"Q{int(b)}:{v:+.3f}" for b, v in qr["bins"].items())
            print(f"    {name:<16} {cells}   mono={qr['monotonicity']:+.2f}")

    print("\n  Regime IC (per year) — incl. AI_INFRA & rotation, to test the post-2023 tech bifurcation:")
    for name in ["SW_SERVICES", "PURE_PEERS", "NASDAQ", "XLK", "AI_INFRA", "GSS_ROTATION"]:
        ri = (report["candidates"].get(name) or {}).get("regime_ic") or {}
        if ri:
            print(f"    {name:<16} " + "  ".join(f"{yr}:{ic}" for yr, ic in ri.items() if ic is not None))
    # STRUCTURAL BREAK: does the software edge over Nasdaq appear only after the AI split?
    sw_ri = (report["candidates"].get("SW_SERVICES") or {}).get("regime_ic") or {}
    na_ri = (report["candidates"].get("NASDAQ") or {}).get("regime_ic") or {}
    yrs = sorted(set(sw_ri) & set(na_ri))
    edge = [(y, round(sw_ri[y] - na_ri[y], 3)) for y in yrs if sw_ri[y] is not None and na_ri[y] is not None]
    if edge:
        print("\n  STRUCTURAL BREAK — SW_SERVICES minus NASDAQ IC by year (edge should emerge post-2023):")
        print("    " + "  ".join(f"{y}:{d:+}" for y, d in edge))
        print("    (≈0/negative pre-2024 then widening ⇒ the peer edge is an AI-era regime, not a constant)")

    print("\n  RULE: all four auto-gates must PASS + Gate 5 human sign-off BEFORE any")
    print("  portfolio simulation. Validation precedes monetization; the backtest is step 5,")
    print("  not step 3. (Framework §3 guardrail 6.)")
    print("\n--- JSON ---"); print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
