"""
strategy_framework/api.py
=========================
Thin facade the FastAPI layer calls. Keeps all framework wiring in one place so
backend/main.py only needs tiny route handlers that forward JSON in and out.

Functions
---------
  suggest(expiry, now=None)                      -> suggestion dict
  get_portfolio(expiry=None, live=...)           -> {positions, valuation}
  add_position(kind, **fields)                   -> {ok, position}
  remove_position(pos_id)                        -> {ok}
  backtest(mode, expiry, exit_mode, hold)        -> {mode, ...}

`mode` for backtest is "auto" (walk-forward the suggester) or "book" (mark the
assembled portfolio forward).
"""
from __future__ import annotations
from .config.settings import FrameworkConfig
from .signals.data_access import DataAccess
from .strategy import suggester, constructor
from .backtest import walkforward, portfolio_bt
from .portfolio.book import Book
from .portfolio import valuation, context

_CFG = FrameworkConfig()

# Bump when backtest/suggest behaviour changes so a running server can be verified.
VERSION = "desk-2025.07-bt-freq"


def _cfg_with(min_edge_cost_mult: float = 0.0):
    """Return the shared _CFG, or a per-request deep copy with the cost-edge gate
    ("do-nothing threshold") overridden — so a dropdown selection applies to just
    this call without mutating global config."""
    if not min_edge_cost_mult:
        return _CFG
    import copy
    cfg = copy.deepcopy(_CFG)
    cfg.gates.min_edge_cost_mult = float(min_edge_cost_mult)
    return cfg


def config_summary() -> dict:
    """Framework config + THE signal roster, straight from the registry.

    The roster is served here so the frontend never hardcodes a signal list, label
    map, or weight table (CLAUDE.md DRY rule / HARD RULE 13). Adding a SignalSpec
    row lights the signal up in every UI view with zero frontend edits."""
    from .signals import registry as _reg
    roster = _reg.roster()
    return {"version": VERSION, **_CFG.summary(),
            "signals": roster,
            "signal_families": _reg.families(),
            # convenience slices so the UI filters by meaning, not by hardcoded name
            "directional_signals": [s["name"] for s in roster if s["kind"] == "directional"],
            "blended_signals": [s["name"] for s in roster if s["blended"]],
            "pinned_zero_signals": _reg.pinned_zero_names()}


def market_health(as_of: str | None = None) -> dict:
    """The DAILY market-health / trend gauge (0-100) — see market_health/trend.py.
    Built from daily index (and, when synced, constituent) bars; Macro / Fundamentals
    / Flows layers omitted for want of a feed. `as_of` (ISO date) for a historical
    read; None = latest."""
    from .market_health.trend import market_health as _mh
    return _mh(_CFG.db_path, as_of=as_of)


def _parse_symbol_expiry(u: str) -> str | None:
    """Extract an expiry date (YYYY-MM-DD) from a futures symbol like NIFTY30JUL26,
    NIFTY-30-JUL-2026, NIFTY26JUL, etc. Returns None if no date is encoded."""
    import re
    mon = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
           "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}
    # DDMMMYY(YY): 30JUL26 / 30JUL2026
    m = re.search(r"(\d{1,2})[\-\s]?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\-\s]?(\d{2,4})", u)
    if m:
        d, mo, y = m.group(1), mon[m.group(2)], m.group(3)
        y = ("20" + y) if len(y) == 2 else y
        return f"{y}-{mo}-{int(d):02d}"
    # YYMMM / MMMYY (month only, no day): NIFTY26JUL -> month-end handled by caller
    m2 = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\-\s]?(\d{2,4})|(\d{2,4})[\-\s]?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", u)
    if m2:
        mo = mon[m2.group(1) or m2.group(4)]
        y = m2.group(2) or m2.group(3)
        y = ("20" + y) if len(y) == 2 else y
        return f"{y}-{mo}"          # month only; frontend/caller can show as the month
    return None


def _last_thursday(year: int, month: int) -> "str":
    """Last Thursday of a calendar month → 'YYYY-MM-DD' (NSE monthly-future expiry)."""
    import calendar, datetime as _dt
    last_day = calendar.monthrange(year, month)[1]
    d = _dt.date(year, month, last_day)
    d -= _dt.timedelta(days=(d.weekday() - 3) % 7)   # 3 = Thursday
    return d.isoformat()


def _rolling_future_expiry(rank: int, today: "str | None" = None) -> str:
    """Expiry for a ROLLING contract (rank 1 = near-month, 2 = next-month, ...),
    using the NSE last-Thursday calendar rule — matching the Breeze fetcher's
    fallback. If today is on/after this month's expiry the contract has already
    rolled, so FUT_1 points at next month. today defaults to the system date."""
    import datetime as _dt
    t = _dt.date.fromisoformat(today[:10]) if today else _dt.date.today()
    exp0 = _dt.date.fromisoformat(_last_thursday(t.year, t.month))
    base_m = t.month + (1 if t > exp0 else 0)          # roll if current expired
    y, m = t.year + (base_m - 1) // 12, (base_m - 1) % 12 + 1
    # advance (rank-1) further whole months
    tot = (y * 12 + (m - 1)) + (rank - 1)
    return _last_thursday(tot // 12, tot % 12 + 1)


def _future_rank(u: str) -> "int | None":
    """Infer a rolling contract's rank (near=1, next=2, far=3) from its symbol.
    Handles NIFTY_FUT_1 / NIFTYFUT1 / NIFTY-I / NIFTY-II / NIFTY-III forms."""
    import re
    m = re.search(r"FUT[\s_\-]?([123])\b", u)
    if m:
        return int(m.group(1))
    m = re.search(r"NIFTY[\s\-]?(I{1,3})\b", u)
    if m:
        return len(m.group(1))
    return None


def instruments_meta() -> dict:
    """Data-driven instrument metadata for the Desk Book dropdowns: the exchanges
    actually present in the DB, the NIFTY lot size from the instruments table (falls
    back to config), and the available expiries."""
    import sqlite3
    exchanges, lot_size = [], _CFG.lot_size
    try:
        c = sqlite3.connect(_CFG.db_path); cur = c.cursor()
        ex = set()
        for tbl, col in (("price_bars", "exchange"), ("instruments", "exchange_code")):
            try:
                ex |= {r[0] for r in cur.execute(f"SELECT DISTINCT {col} FROM {tbl}") if r[0]}
            except Exception:
                pass
        exchanges = sorted(ex)
        try:
            row = cur.execute("SELECT lot_size FROM instruments WHERE underlying='NIFTY' LIMIT 1").fetchone()
            if row and row[0]:
                lot_size = int(row[0])
        except Exception:
            pass
        c.close()
    except Exception:
        pass
    # tradable stock symbols = those with 1m bars (excl. the index & cross-assets)
    symbols, futures_symbols = [], []
    try:
        c = sqlite3.connect(_CFG.db_path); cur = c.cursor()
        _skip = {"NIFTY", "INDIAVIX", "USDINR", "GOLD", "SILVER", "COPPER", "CRUDEOIL", "GIFTNIFTY"}
        all_syms = sorted({r[0] for r in cur.execute(
            "SELECT DISTINCT symbol FROM price_bars") if r[0]})
        # NIFTY-futures-like series: contain NIFTY + a futures marker (FUT / -I / -II /
        # month code / digits), but not the plain index. Return with their date span so
        # the near/next contracts & their expiries can be shown.
        import re
        for s in all_syms:
            u = s.upper()
            if u == "NIFTY" or u in _skip:
                if u not in _skip:
                    continue
            if "NIFTY" in u and (("FUT" in u) or re.search(r"NIFTY[\s\-]?(I{1,3}|[0-9])", u)) and u != "NIFTY":
                last = cur.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=?", (s,)).fetchone()
                last_ts = last[0] if last else None
                rank = _future_rank(u)
                # expiry resolution, most-authoritative first:
                #   1. explicit date encoded in the symbol name (e.g. 30JUL26)
                #   2. ROLLING contract (NIFTY_FUT_1/_2, -I/-II): last-Thursday
                #      calendar rule by rank — correct even while data is still
                #      being collected (last bar ≈ today, not the true expiry)
                #   3. a dated series that has already ended → its last bar date
                exp = _parse_symbol_expiry(u)
                if not exp or len(exp) == 7:            # missing or month-only
                    if rank is not None:
                        exp = _rolling_future_expiry(rank)
                    elif last_ts:
                        exp = str(last_ts)[:10]
                futures_symbols.append({"symbol": s, "last_bar": last_ts,
                                        "rank": rank, "expiry": exp})
        futures_symbols.sort(key=lambda x: (x.get("expiry") or "9999"))
        symbols = [s for s in all_syms if s not in _skip and "FUT" not in s.upper()
                   and not (("NIFTY" in s.upper()) and s.upper() != "NIFTY") and s.upper() != "NIFTY"]
        c.close()
    except Exception:
        pass
    try:
        exps = [e["expiry"] for e in list_expiries().get("expiries", [])]
    except Exception:
        exps = []
    # FUTURES expire MONTHLY (last Thursday) while options are weekly+monthly. The
    # monthly = the LAST option expiry in each calendar month, so derive futures
    # expiries by taking the max expiry per YYYY-MM.
    by_month = {}
    for e in sorted(exps):
        by_month[str(e)[:7]] = e            # sorted asc → last wins = month-end
    fut_exps = list(by_month.values())
    return {"exchanges": exchanges or ["NSE", "NFO"], "lot_size": lot_size,
            "expiries": exps, "futures_expiries": fut_exps, "symbols": symbols,
            "futures_symbols": futures_symbols}


def list_expiries() -> dict:
    """Expiries with capture counts + spans, so the UI can pick which to backtest
    (a completed expiry) vs. trade live (the current one)."""
    da = DataAccess(_CFG.db_path)
    out = []
    for e in da.expiries():
        caps = da.list_captures(expiry=e)
        out.append({"expiry": e, "n_captures": len(caps),
                    "first": caps[0]["captured_at"] if caps else None,
                    "last": caps[-1]["captured_at"] if caps else None})
    return {"expiries": out}


def _latest_expiry(expiry: str | None) -> str | None:
    if expiry:
        return expiry
    da = DataAccess(_CFG.db_path)
    exps = da.expiries()
    # pick the most recent expiry that actually has captures (not just the last).
    for e in reversed(exps):
        if da.list_captures(expiry=e):
            return e
    return exps[-1] if exps else None


def features_backfill(expiry: str | None = None, force: bool = False) -> dict:
    """Incrementally compute + persist features for an expiry (only new/incomplete
    snapshots unless force=True). Synchronous — use the async start/status pair
    below for a progress bar on long runs."""
    from .features import store as _fs
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    return _fs.backfill(_CFG.db_path, expiry, force=force)


# ---- background backfill with live progress ------------------------------
_BF = {"running": False, "done": 0, "total": 0, "result": None, "error": None, "expiry": None}


def features_backfill_start(expiry: str | None = None, force: bool = False,
                            lookback_min: int | None = None) -> dict:
    """`lookback_min` rebuilds the store at a chosen RETURN WINDOW (the Signal
    view's "Return window" setting). Changing it implies a full recompute, since
    every sig_*_score is a function of the window."""
    import threading
    if _BF["running"]:
        return {"error": "a backfill is already running", "running": True}
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}

    def _run():
        _BF.update(running=True, done=0, total=0, result=None, error=None, expiry=expiry)
        try:
            from .features import store as _fs
            def cb(done, total):
                _BF["done"] = done; _BF["total"] = total
            _BF["result"] = _fs.backfill(_CFG.db_path, expiry, force=force, progress_cb=cb,
                                         lookback_min=lookback_min)
        except Exception as e:
            _BF["error"] = str(e)[:200]
        finally:
            _BF["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "expiry": expiry}


def features_backfill_status() -> dict:
    d = dict(_BF)
    d["pct"] = round(100 * d["done"] / d["total"], 1) if d["total"] else 0
    return d


def set_momentum_window(lookback_min: int) -> dict:
    """Set THE shared return window for every price-return signal, persistently.

    One setting, one place. Takes effect immediately in already-constructed configs
    (MomentumWindow reads config/runtime.py at call time) and survives a restart.
    Returns the new config plus a feature-store staleness audit, because changing
    the window invalidates every cached sig_*_score."""
    from .config import runtime
    from .config.settings import MomentumWindow
    allowed = list(MomentumWindow().options)
    if int(lookback_min) not in allowed:
        return {"error": f"lookback_min must be one of {allowed}", "given": lookback_min}
    runtime.set_lookback_min(int(lookback_min))
    return {"ok": True, "momentum_window": _CFG.momentum.as_dict(),
            "feature_store": feature_window_audit()}


def feature_window_audit(expiry: str | None = None) -> dict:
    """Which RETURN WINDOW the stored feature rows were computed at, vs the active
    config. Backs the Signal view's staleness warning: if the store was built at a
    different window than the one selected, every IC / correlation number would be
    computed across two different signals, so the view must say so."""
    from .features import store as _fs
    return _fs.window_audit(_CFG.db_path, expiry or _backtest_default_expiry())


# ---- signal-test job (real progress for the Signal Test view) --------------
# The scoreboard / single-signal runs replay the whole signal bundle at every
# strided snapshot, which takes seconds-to-tens-of-seconds. Run it in a thread
# and report ACTUAL evals done/total so the progress bar is truthful, not a creep.
_ST = {"running": False, "done": 0, "total": 0, "result": None, "error": None, "kind": None}


def signal_test_start(kind: str = "all", **params) -> dict:
    import threading
    if _ST["running"]:
        return {"error": "a signal test is already running", "running": True}

    def _run():
        _ST.update(running=True, done=0, total=0, result=None, error=None, kind=kind)
        try:
            def cb(done, total):
                _ST["done"] = done; _ST["total"] = total
            if kind == "single":
                _ST["result"] = signal_backtest(progress_cb=cb, **params)
            elif kind == "effectiveness":
                _ST["result"] = signal_effectiveness(progress_cb=cb, **params)
            else:
                _ST["result"] = signal_backtest_all(progress_cb=cb, **params)
        except Exception as e:
            _ST["error"] = str(e)[:200]
        finally:
            _ST["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "kind": kind}


def signal_test_status() -> dict:
    d = dict(_ST)
    d["pct"] = round(100 * d["done"] / d["total"], 1) if d["total"] else 0
    return d


def features_clear(expiry: str | None = None) -> dict:
    from .features import store as _fs
    expiry = expiry or _backtest_default_expiry()
    return {"expiry": expiry, "deleted": _fs.clear(_CFG.db_path, expiry)}


def features_view(expiry: str | None = None, limit: int = 500) -> dict:
    """Return the stored feature rows for an expiry (for inspection / analysis)."""
    from .features import store as _fs
    expiry = expiry or _backtest_default_expiry()
    rows = _fs.query(_CFG.db_path, expiry, limit=limit)
    return {"expiry": expiry, "n": len(rows),
            "feature_names": _fs.feature_names(_CFG.db_path, expiry), "rows": rows}


def feature_names_list(expiry: str | None = None) -> dict:
    from .features import store as _fs
    expiry = expiry or _backtest_default_expiry()
    return {"expiry": expiry, "names": _fs.feature_names(_CFG.db_path, expiry)}


def attribution(predictor: str, target: str = "fwd_ret_60m_pct",
                condition: str | None = None, expiry: str | None = None,
                window_days=None, n_buckets: int = 3) -> dict:
    """Conditional attribution over the feature store: relate a `predictor`
    (any signal/feature column) to a forward-return `target`, overall and sliced
    by a `condition` feature (e.g. dte, vix, decomp_regime). Reports per bucket:
    n, information coefficient (correlation), directional hit rate, and the
    top-vs-bottom-half forward-return spread — the "does this feature actually
    separate future returns, and when" question."""
    import numpy as np
    from .features import store as _fs
    if predictor == target:
        return {"error": "predictor and target are the same field — that trivially "
                "gives IC 1.00 / 100% hit. Pick a signal/feature to predict a "
                "forward return (or a different signal)."}
    expiry = expiry or _backtest_default_expiry()
    rows = _fs.query(_CFG.db_path, expiry, limit=10000)
    if window_days:
        dates = sorted({r["ts"][:10] for r in rows if r.get("ts")})
        keep = set(dates[-int(window_days):])
        rows = [r for r in rows if r.get("ts", "")[:10] in keep]

    data = [(r.get(predictor), r.get(target), r.get(condition))
            for r in rows]
    data = [(p, t, c) for p, t, c in data
            if isinstance(p, (int, float)) and isinstance(t, (int, float))]
    if len(data) < 5:
        return {"error": f"not enough rows with both '{predictor}' and '{target}' "
                "(backfill the feature store first, or pick populated fields)",
                "n": len(data)}
    P = np.array([d[0] for d in data], float)
    T = np.array([d[1] for d in data], float)
    C = [d[2] for d in data]

    def _stats(mask):
        p, t = P[mask], T[mask]
        if len(p) < 3:
            return {"n": int(len(p)), "ic": None, "hit_rate": None, "spread": None, "sharpe": None}
        ic = float(np.corrcoef(p, t)[0, 1]) if p.std() > 0 else 0.0
        act = np.abs(p) >= 0.1
        hit = float(np.mean(np.sign(p[act]) == np.sign(t[act]))) if act.any() else None
        med = np.median(p); hi = t[p >= med]; lo = t[p < med]
        spread = (float(hi.mean()) - float(lo.mean())) if (len(hi) and len(lo)) else None
        # Sharpe of the median-split long/short: long target when predictor is above
        # its median, short when below — return per obs = sign(p−med)·t. mean/σ of
        # that series = consistency of the edge. Works for any predictor (centred
        # signals OR raw features like max_pain), and is sign-aligned with spread.
        pnl = np.where(p >= med, 1.0, -1.0) * t
        sharpe = float(pnl.mean() / (pnl.std() + 1e-9)) if len(pnl) > 1 and pnl.std() > 0 else None
        return {"n": int(len(p)), "ic": round(ic, 3),
                "hit_rate": round(hit, 3) if hit is not None else None,
                "avg_hi": round(float(hi.mean()), 3) if len(hi) else None,
                "avg_lo": round(float(lo.mean()), 3) if len(lo) else None,
                "spread": round(spread, 3) if spread is not None else None,
                "sharpe": round(sharpe, 3) if sharpe is not None else None}

    overall = _stats(np.ones(len(P), bool))
    buckets = []
    if condition:
        numeric = all(isinstance(c, (int, float)) for c in C if c is not None)
        if numeric:
            cv = np.array([c if isinstance(c, (int, float)) else np.nan for c in C])
            valid = ~np.isnan(cv)
            if valid.sum() >= n_buckets:
                qs = np.unique(np.quantile(cv[valid], np.linspace(0, 1, n_buckets + 1)))
                for bi in range(len(qs) - 1):
                    lo_q, hi_q = qs[bi], qs[bi + 1]
                    last = bi == len(qs) - 2
                    m = valid & (cv >= lo_q) & (cv <= hi_q if last else cv < hi_q)
                    st = _stats(m); st["label"] = f"{condition} {round(float(lo_q),2)}–{round(float(hi_q),2)}"
                    buckets.append(st)
        else:
            for val in sorted({c for c in C if c is not None}, key=str):
                m = np.array([c == val for c in C])
                st = _stats(m); st["label"] = f"{condition} = {val}"
                buckets.append(st)

    return {"predictor": predictor, "target": target, "condition": condition,
            "expiry": expiry, "n": len(data), "overall": overall, "buckets": buckets,
            "note": "IC = corr(predictor, forward return); hit rate = sign agreement "
                    "(meaningful for centered predictors); spread = top-half − bottom-half "
                    "forward return. Descriptive only on thin history (D-MA-04)."}


def signal_correlation(expiry: str | None = None, window_days=None) -> dict:
    """Pairwise correlation of the directional signals' *scores* across snapshots
    (from the feature store). Answers: are these six really independent bets, or
    are some of them the same trade wearing different hats? Two signals with corr
    ≈ +0.9 are near-duplicates (double-counting conviction); corr ≈ 0 are
    independent; corr ≈ −0.9 are systematically opposed. Also returns each
    signal's average |corr| to the others as a redundancy score."""
    import numpy as np
    from .features import store as _fs
    names = _DIR_SIGNAL_NAMES
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    rows = _fs.query(_CFG.db_path, expiry, limit=10000)
    if window_days:
        dates = sorted({r["ts"][:10] for r in rows if r.get("ts")})
        keep = set(dates[-int(window_days):])
        rows = [r for r in rows if r.get("ts", "")[:10] in keep]
    if len(rows) < 5:
        return {"error": "not enough feature-store rows — backfill features first",
                "n": len(rows)}

    # Correlation matrix from the ONE canonical primitive (HARD RULE 12) — no
    # duplicate corrcoef loop. Round to 2dp to preserve this endpoint's contract.
    from .analysis.signal_ensemble import corr_matrix_full
    cols = {s: [r.get(f"sig_{s}_score") for r in rows] for s in names}
    _, mat_raw, pair_n = corr_matrix_full(cols)
    n = len(names)
    mat = [[(round(v, 2) if v is not None else None) for v in row] for row in mat_raw]
    redundancy = []
    for i in range(n):
        offs = [abs(mat[i][j]) for j in range(n) if j != i and mat[i][j] is not None]
        redundancy.append({"signal": names[i],
                           "avg_abs_corr": round(float(np.mean(offs)), 3) if offs else None,
                           "n_obs": max(pair_n[i]) if any(pair_n[i]) else 0})
    # crude effective-independent-count: sum of eigenvalues>0.1 of |corr| proxy
    filled = [[(mat[i][j] if mat[i][j] is not None else (1.0 if i == j else 0.0)) for j in range(n)] for i in range(n)]
    try:
        ev = np.linalg.eigvalsh(np.array(filled))
        eff_independent = int(round(float(np.sum(ev > 0.5))))
    except Exception:
        eff_independent = None
    return {"expiry": expiry, "signals": names, "matrix": mat, "pair_n": pair_n,
            "redundancy": sorted(redundancy, key=lambda r: -(r["avg_abs_corr"] or 0)),
            "effective_independent": eff_independent, "n_signals": n,
            "note": "Correlation of signal scores across snapshots. |corr|>0.7 ≈ "
                    "near-duplicate (redundant conviction); ≈0 = independent bet; "
                    "<−0.7 = systematically opposed. effective_independent = # of "
                    "correlation-matrix eigenvalues > 0.5 (rough count of truly "
                    "distinct bets). Descriptive only on thin history (D-MA-04)."}


def _backtest_default_expiry() -> str | None:
    """For BACKTESTING, default to the most recent *completed* (already-expired)
    expiry with captures — that's the one with full session history. The current
    in-progress expiry (e.g. a fresh weekly) usually has only a day or two of data,
    which would make every window snap to the last session. `suggest()` keeps using
    the current expiry (that's what you trade live)."""
    from datetime import datetime, timezone
    da = DataAccess(_CFG.db_path)
    today = datetime.now(timezone.utc).date()
    expired = []
    for e in da.expiries():
        try:
            ed = datetime.fromisoformat(e.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if ed <= today and da.list_captures(expiry=e):
            expired.append((ed, e))
    if expired:
        return max(expired)[1]
    return _latest_expiry(None)          # fallback: nothing expired yet


def _diag(expiry=None) -> dict:
    da = DataAccess(_CFG.db_path)
    exps = da.expiries()
    return {"db_path": _CFG.db_path, "n_expiries": len(exps),
            "expiries": exps[-5:],
            "captures_for_expiry": len(da.list_captures(expiry=expiry)) if expiry else None}


# --------------------------------------------------------------------------
def suggest(expiry: str | None = None, now: str | None = None,
            min_edge_cost_mult: float = 0.0) -> dict:
    expiry = _latest_expiry(expiry)
    if not expiry:
        return {"error": "no expiries in DB", "diag": _diag()}
    da = DataAccess(_CFG.db_path)
    if not now:
        caps = da.list_captures(expiry=expiry)
        if not caps:
            return {"error": f"no captures for {expiry}", "diag": _diag(expiry)}
        now = caps[-1]["captured_at"]
    out = suggester.suggest(_cfg_with(min_edge_cost_mult), now, expiry).as_dict()
    if not out.get("decision"):        # e.g. no chain snapshot as-of now
        out["diag"] = _diag(expiry)
    return out


def drawdown_insurance(date: str | None = None, expiry: str | None = None,
                       now: str | None = None) -> dict:
    """Liquidity-derisk overlay + recommended tail hedge, standalone.

    Resolves the decision time: explicit `now`, else the last NIFTY 1m bar on
    `date` (replay a past session, e.g. 2026-07-08), else the latest capture.
    Returns the derisk intensity, the five fingerprint components, the raw
    cross-asset reads, and the sized long-put hedge (if the overlay fired)."""
    from .signals import bundle as signal_bundle
    from .signals.data_access import days_to_expiry
    expiry = _latest_expiry(expiry)
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    if not now:
        if date:
            r = da.bars("NIFTY", "1m", end=f"{date}T23:59:59Z", limit=1)
            if not r:
                # any last bar on/at that date
                with da._conn() as c:  # type: ignore[attr-defined]
                    row = c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol='NIFTY' "
                                    "AND timeframe='1m' AND SUBSTR(ts,1,10)=?", (date,)).fetchone()
                now = row[0] if row and row[0] else None
            else:
                now = r[-1]["ts"]
        if not now:
            caps = da.list_captures(expiry=expiry)
            now = caps[-1]["captured_at"] if caps else None
    if not now:
        return {"error": f"no NIFTY bars for {date or 'latest'}"}

    from .signals import derisk_preopen as _pre
    b = signal_bundle.evaluate(_CFG.db_path, now, expiry,
                               veto_days=_CFG.gates.event_veto_days)
    sig = b.signals.get("derisk_liquidity")           # CONFIRM (session, coincident)
    chain = da.chain_as_of(now, expiry)

    # LEAD: pre-open overnight fingerprint, evaluated as-of ~09:14 IST (03:44 UTC)
    # of this session — before the cash open, so it can arm the hedge cheaply.
    preopen_now = f"{now[:10]}T03:44:00Z"
    pre_sig = _pre.compute(da, preopen_now, {"derisk_trigger": _CFG.hedge.trigger})
    pd = pre_sig.detail if pre_sig.status != "NO_DATA" else {}
    d = sig.detail if sig is not None else {}
    pre_int = float(pd.get("intensity", 0.0))
    ses_int = float(d.get("intensity", 0.0))

    # Size the hedge off whichever fired FIRST/higher — in practice the pre-open
    # lead — priced on the earliest available chain of the session (buy at open).
    drive_int = max(pre_int, ses_int)
    hedge_chain = chain
    if date:
        first_caps = da.list_captures(expiry=expiry)
        same = [c for c in (first_caps or []) if c["captured_at"][:10] == now[:10]]
        if same:
            hedge_chain = da.chain_as_of(same[0]["captured_at"], expiry) or chain
    straddle = b.context.get("atm_straddle_pts")
    em = (0.8 * straddle) if straddle else None
    hedge = None
    if drive_int > 0 and hedge_chain is not None:
        hedge = constructor.build_tail_hedge(
            hedge_chain, _CFG, drive_int, expected_move_pts=em,
            dte_days=days_to_expiry(now, expiry))

    return {"now": now, "expiry": expiry, "date": date,
            "trigger": _CFG.hedge.trigger,
            "fired": bool(hedge),
            "drive_intensity": round(drive_int, 3),
            "preopen": {"status": pre_sig.status, "now": preopen_now,
                        "intensity": pre_int, "armed": pre_int >= _CFG.hedge.trigger,
                        "components": pd.get("components", {}),
                        "reads": pd.get("reads", {})},
            "session": {"status": (sig.status if sig is not None else "NO_DATA"),
                        "now": now, "intensity": ses_int,
                        "armed": ses_int >= _CFG.hedge.trigger,
                        "components": d.get("components", {}),
                        "reads": d.get("reads", {})},
            "hedge": hedge,
            "spot": (hedge_chain.spot if hedge_chain else None)}


# --------------------------------------------------------------------------
def get_portfolio(expiry: str | None = None, live_chain: dict | None = None,
                  live_prices: dict | None = None) -> dict:
    book = Book()
    expiry = _latest_expiry(expiry)
    positions = book.list()
    if expiry:
        ctx = context.build_context(_CFG.db_path, expiry, live_chain=live_chain,
                                    live_prices=live_prices)
        val = valuation.value_book(positions, ctx["chain"], ctx["symbol_prices"],
                                   ctx["spot"] or 0.0)
        val["source"] = ctx["source"]; val["as_of"] = ctx["as_of"]
    else:
        val = {"lines": [], "total_pnl_rupees": 0, "note": "no expiry/data"}
    return {"positions": positions, "valuation": val, "expiry": expiry}


def add_suggested(expiry: str | None = None, now: str | None = None) -> dict:
    """Add the current suggested structure straight into the book."""
    sug = suggest(expiry, now)
    st = sug.get("structure")
    if not st:
        return {"ok": False, "error": "no tradeable structure to add",
                "note": sug.get("note")}
    legs = [(l["side"], l["strike"], l["sign"]) for l in st["legs"]]
    # recover entry prices by re-pricing at the suggestion snapshot
    da = DataAccess(_CFG.db_path)
    exp = _latest_expiry(expiry)
    chain = da.chain_as_of(sug["now"], exp)
    entry = {(s, k): (chain.call_ltp if s == "call" else chain.put_ltp).get(k, 0.0)
             for s, k, _ in legs}
    pos = Book().add_option_strategy(st["family"], legs, entry, _CFG.lot_size,
                                     label=f"{st['family']} " +
                                     "/".join(str(int(k)) for _, k, _ in legs))
    return {"ok": True, "position": pos.as_dict()}


def futures_action_score(entry_ts: str | None = None, expiry: str | None = None,
                         position_lots: int = 1, lam: float = 0.5,
                         horizon_frac: float = 1.0, max_lots: int = 2,
                         allow_reverse: bool = True, risk_drift_frac: float = 1.0) -> dict:
    """Score HOLD/EXIT/ADD/REDUCE/REVERSE for a NIFTY futures position at one point
    in time — the point-in-time view of the forecast optimizer (reproduces the
    worked examples). `position_lots` is signed (+long / −short)."""
    from .backtest import walkforward as _wf
    from .strategy import futures_action_eval as _fae
    exp = _latest_expiry(expiry) or _backtest_default_expiry()
    if not exp:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    now = entry_ts
    if not now:
        caps = da.list_captures(expiry=exp)
        if not caps:
            return {"error": f"no captures for {exp}"}
        now = caps[-1]["captured_at"]
    try:
        reg, _pin, sig = _wf._regime_at(_CFG, now, exp, None)
    except Exception as e:
        return {"error": f"could not read the regime at {now}: {e}"}
    chain = da.chain_as_of(now, exp)
    spot = float(getattr(chain, "spot", 0.0) or 0.0)
    if spot <= 0:
        return {"error": f"no spot at {now}"}
    res = _fae.evaluate_futures_actions(
        spot, int(position_lots), reg, _CFG, lam=lam, horizon_frac=horizon_frac,
        max_lots=int(max_lots), allow_reverse=bool(allow_reverse),
        risk_drift_frac=risk_drift_frac)
    res["as_of"] = now
    res["spot"] = round(spot, 1)
    res["signal"] = sig
    return res


def add_candidate(family: str, expiry: str = None, now: str = None,
                  exchange: str = "NFO") -> dict:
    """Add a specific candidate family (built + priced at the snapshot) to the book.
    The chosen option `expiry` and `exchange` (NFO for index options) are stored on
    the position so the structure carries its own expiry — not the Simulate box's."""
    expiry = _latest_expiry(expiry)
    if not expiry:
        return {"ok": False, "error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    if not now:
        caps = da.list_captures(expiry=expiry)
        if not caps:
            return {"ok": False, "error": f"no captures for {expiry}"}
        now = caps[-1]["captured_at"]
    chain = da.chain_as_of(now, expiry)
    st = constructor.build(family, chain, _CFG)
    if st is None:
        return {"ok": False, "error": f"{family} not priceable at this snapshot"}
    legs = [(s, k, g) for s, k, g in st.legs]
    entry = {(s, k): (chain.call_ltp if s == "call" else chain.put_ltp).get(k, 0.0)
             for s, k, _ in legs}
    strikes = "/".join(str(int(k)) for _, k, _ in legs)
    pos = Book().add_option_strategy(
        family, legs, entry, _CFG.lot_size,
        label=f"{family} {strikes} · {exchange} · exp {str(expiry)[:10]}",
        exchange=exchange, expiry=expiry)
    return {"ok": True, "position": pos.as_dict()}


def add_position(kind: str, **f) -> dict:
    book = Book()
    if kind == "future":
        pos = book.add_future(f["symbol"], float(f["entry_price"]), int(f["qty"]),
                              int(f.get("lot_size", _CFG.lot_size)), f.get("label"),
                              exchange=f.get("exchange", "NFO"), expiry=f.get("expiry"))
    elif kind == "stock":
        pos = book.add_stock(f["symbol"], float(f["entry_price"]), int(f["qty"]),
                             f.get("label"), exchange=f.get("exchange", "NSE"))
    elif kind == "option_strategy":
        legs = [tuple(l) for l in f["legs"]]
        entry = {(s, k): float(v) for (s, k), v in f["entry_prices"].items()} \
            if isinstance(f.get("entry_prices"), dict) else {}
        # If no entry premiums were supplied (a leg added by hand from the Desk Book),
        # price each leg's LTP from the option chain at the latest snapshot for its
        # expiry — otherwise the position shows ₹0. (The backtest still re-prices at
        # the chosen entry time; this is the display/reference premium.)
        if not entry:
            exp = _latest_expiry(f.get("expiry"))
            if exp:
                try:
                    da = DataAccess(_CFG.db_path)
                    caps = da.list_captures(expiry=exp)
                    if caps:
                        chain = da.chain_as_of(caps[-1]["captured_at"], exp)
                        for (s, k, _sgn) in legs:
                            src = chain.call_ltp if s == "call" else chain.put_ltp
                            entry[(s, float(k))] = float(src.get(float(k),
                                                        src.get(k, 0.0)) or 0.0)
                except Exception:
                    pass
        pos = book.add_option_strategy(f["family"], legs, entry,
                                       int(f.get("lot_size", _CFG.lot_size)),
                                       f.get("label"),
                                       exchange=f.get("exchange", "NFO"),
                                       expiry=f.get("expiry"))
    else:
        return {"ok": False, "error": f"unknown kind {kind}"}
    return {"ok": True, "position": pos.as_dict()}


def remove_position(pos_id: str) -> dict:
    return {"ok": Book().remove(pos_id)}


def clear_portfolio() -> dict:
    Book().clear(); return {"ok": True}


# --------------------------------------------------------------------------
# Directional roster from the single source (signals/registry.py) — never hardcode.
from .signals.registry import directional_names as _directional_names
_DIR_SIGNAL_NAMES = _directional_names()


_IST_OFFSET_MIN = 330          # IST = UTC + 5:30

# Session phases in IST minutes-since-midnight. `full` spans the session; `open15`
# is a diagnostic subset of morning (matches the volume-window-matrix layout).
_PHASES = [
    ("open15",  555, 570,  "Open 15m",   "09:15–09:30"),
    ("morning", 555, 600,  "Morning",    "09:15–10:00"),
    ("midday",  600, 885,  "Midday",     "10:00–14:45"),
    ("eod",     885, 930,  "End of day", "14:45–15:30"),
]


def _ist_minute(ts_iso: str) -> int:
    """IST minutes-since-midnight for a UTC capture timestamp."""
    from datetime import datetime
    t = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (t.hour * 60 + t.minute + _IST_OFFSET_MIN) % (24 * 60)


def _attach_front_expiry(da: "DataAccess", caps: list) -> list:
    """Tag each capture with the correct FRONT option expiry to evaluate it against,
    so date-first views can span multiple expiries. For each capture we pick the
    NEAREST expiry that has not yet passed AS OF that capture's date (expiry_date >=
    capture_date) — i.e. the live front series at that moment. This rolls
    automatically: captures before 14-Jul use the 14-Jul expiry, captures from 15-Jul
    on roll to the 28-Jul expiry. If a capture somehow only carries already-expired
    chains, we fall back to the latest one it has (never a random old series)."""
    import sqlite3
    ids = [c["capture_id"] for c in caps if c.get("capture_id") is not None]
    if not ids:
        return caps
    con = sqlite3.connect(_CFG.db_path)
    try:
        q = ("SELECT capture_id, expiry FROM chain_rows WHERE capture_id IN (%s) "
             "GROUP BY capture_id, expiry" % ",".join("?" * len(ids)))
        by_cap: dict = {}
        for cid, exp in con.execute(q, ids):
            by_cap.setdefault(cid, []).append(exp)
    finally:
        con.close()
    for c in caps:
        exps = sorted(by_cap.get(c["capture_id"], []))
        if not exps:
            c["expiry"] = None
            continue
        cd = c["captured_at"][:10]
        future = [e for e in exps if e[:10] >= cd]     # not-yet-expired as of this capture
        c["expiry"] = future[0] if future else exps[-1]
    return caps


def _eval_signals_series(da: "DataAccess", caps: list, expiry: str | None = None):
    """Evaluate the bundle at EVERY capture once and return the directional roster
    plus a per-capture record list. Shared by signal_phase_grid and
    signal_timeseries so the (expensive) eval loop lives in exactly one place. Each
    capture is evaluated with ITS OWN front expiry (c['expiry']) when present, so a
    date range can cross expiry boundaries; `expiry` is the fallback.

    records: [{ts, ist_min, spot, vals:{name:(score, conf, ok)}}]"""
    from .signals import bundle as _sb
    from .signals import registry as _reg
    dir_specs = [s for s in _reg.REGISTRY if s.kind == "directional"]
    bc = _window_cache(da, caps)
    records = []
    for c in caps:
        ts = c["captured_at"]
        exp = c.get("expiry") or expiry
        b = _sb.evaluate(_CFG.db_path, ts, exp, veto_days=_CFG.gates.event_veto_days, bar_cache=bc)
        vals = {}
        for spec in dir_specs:
            sig = b.get(spec.name)
            ok = bool(sig and getattr(sig, "status", "") == "OK")
            vals[spec.name] = (float(getattr(sig, "score", 0.0)) if ok else None,
                               float(getattr(sig, "confidence", 0.0)) if ok else 0.0, ok)
        records.append({"ts": ts, "ist_min": _ist_minute(ts),
                        "spot": float(c.get("spot") or b.spot or 0.0), "vals": vals})
    return dir_specs, records


def signal_timeseries(expiry: str | None = None, window_days=None,
                      date_from: str | None = None, date_to: str | None = None) -> dict:
    """Time series of the NIFTY level and EVERY directional signal's score across
    captures — the input for a price chart with per-signal buy/sell markers. The UI
    picks a signal and a threshold; a score ≥ +thr is a BUY (expects the index up),
    ≤ −thr a SELL (expects it down). Roster/weights from the single sources."""
    from .config.settings import SignalWeights
    da = DataAccess(_CFG.db_path)
    # Date-FIRST: span ALL captures across ALL expiries (so recent dates under the
    # current, not-yet-expired expiry are included). If an explicit `expiry` is
    # passed, restrict to it; otherwise every date with data is selectable.
    all_caps = da.list_captures(expiry=expiry) if expiry else da.list_captures()
    if not all_caps:
        return {"error": "no captures in DB"}
    _attach_front_expiry(da, all_caps)
    session_dates = sorted({c["captured_at"][:10] for c in all_caps})
    # date range wins over window_days; both optional. Keeps the replay bounded.
    if date_from or date_to:
        lo = date_from or "0000-00-00"
        hi = date_to or "9999-99-99"
        caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    else:
        caps = _apply_window(all_caps, window_days)
    if len(caps) < 3:
        return {"error": "not enough captures in this range",
                "session_dates": session_dates}
    W = SignalWeights().as_dict()
    dir_specs, records = _eval_signals_series(da, caps)
    times = [r["ts"] for r in records]
    spot = [round(r["spot"], 2) for r in records]
    scores = {s.name: [(None if r["vals"][s.name][0] is None else round(r["vals"][s.name][0], 3))
                       for r in records] for s in dir_specs}
    conf = {s.name: [round(r["vals"][s.name][1], 3) for r in records] for s in dir_specs}
    roster = [{"name": s.name, "label": s.display, "family": s.family,
               "weight": round(W.get(s.name, 0.0), 3)} for s in dir_specs]
    # signal-INDEPENDENT tape regime (trend vs chop) per capture, no lookahead —
    # the follow/fade gate for the momentum-vs-reversion problem.
    from .strategy.tape_regime import series_regime
    tape = series_regime([r["spot"] for r in records])
    return {"expiry": expiry, "times": times, "spot": spot, "scores": scores,
            "conf": conf, "signals": roster,
            "tape_regime": [t["regime"] for t in tape],
            "tape_er": [t["er"] for t in tape],
            "session_dates": session_dates,
            "date_from": caps[0]["captured_at"][:10], "date_to": caps[-1]["captured_at"][:10],
            "note": "buy = score ≥ +threshold (expects NIFTY up); sell = score ≤ −threshold "
                    "(expects down). Descriptive; thin history (D-MA-04)."}


def _label_regimes(records: list, spots: list, caps: list, regime_by: str):
    """SINGLE SOURCE for the per-capture regime label used by BOTH the regime×horizon
    study and the signal-policy backtest. Returns (regimes, reg_names, meta) where
    `regimes` is aligned to records (None = unlabelled), `reg_names` the axis, and
    meta carries the tape-split diagnostics. Keeping this in one place means the
    backtest trades exactly the regimes the study measured — no drift."""
    import numpy as _np
    from .strategy.tape_regime import er_series, split_trend_chop
    meta = {"vol_src": "n/a", "vol_median": 0.0, "er_median": 0.0}
    if regime_by == "none":
        # UNCONDITIONAL: one 'all' bucket — the whole sample pools together (max power).
        return ["all"] * len(records), ["all"], meta
    if regime_by == "oi":
        # OI-CONVICTION axis from futures_oi_regime's (score, confidence): buildup
        # ±0.70/0.70, covering/unwinding ±0.35/0.35, COILED 0.0/0.40, churn 0.0/0.15.
        # Coiled and churn both score ~0 (no direction) but confidence separates them —
        # coiled is heavy two-sided positioning (real), churn is noise.
        oi_sc = [r["vals"].get("futures_oi_regime", (None, None))[:2] for r in records]
        def _oig(sc):
            s, cf = sc[0], (sc[1] if len(sc) > 1 else None)
            if s is None:
                return None
            a = abs(s)
            if a > 0.6:
                return "conviction"
            if a > 0.25:
                return "hollow"
            return "coiled" if (cf is not None and cf >= 0.30) else "churn"
        return [_oig(sc) for sc in oi_sc], ["conviction", "hollow", "coiled", "churn"], meta
    # tape_vol: trend/chop at the MEDIAN efficiency ratio × vol lo/hi (VIX or realized).
    tape, er_median = split_trend_chop(er_series(spots))
    vix = [c.get("vix") for c in caps]
    rets = [0.0] + [(spots[i] / spots[i - 1] - 1.0) if spots[i - 1] else 0.0
                    for i in range(1, len(spots))]
    def _rvol(i, w=8):
        seg = rets[max(1, i - w + 1):i + 1]
        return float(_np.std(seg)) if len(seg) >= 2 else 0.0
    vix_ok = all(v is not None for v in vix) and len({round(v, 4) for v in vix}) >= 3
    if vix_ok:
        vol_proxy, vol_src = [float(v) for v in vix], "India VIX"
    else:
        vol_proxy, vol_src = [_rvol(i) for i in range(len(records))], "realized vol"
    vmed = float(_np.median(vol_proxy)) if vol_proxy else 0.0
    vlab = ["hi" if v >= vmed else "lo" for v in vol_proxy]
    regimes = [(f"{tape[i]}·{vlab[i]}" if tape[i] else None) for i in range(len(records))]
    reg_names = [f"{t}·{v}" for t in ("trend", "chop") for v in ("lo", "hi")]
    meta = {"vol_src": vol_src, "vol_median": vmed, "er_median": er_median}
    return regimes, reg_names, meta


def signal_regime_horizon(date_from: str | None = None, date_to: str | None = None,
                          min_n: int = 20, regime_by: str = "tape_vol",
                          horizons: str = "15,30,60", min_move_pts: float = 0.0,
                          n_boot: int = 1000) -> dict:
    """CONDITIONAL-alpha study: IC of every directional signal split by tape REGIME
    (trend / chop / neutral) × forward HORIZON. Answers "WHEN is this signal good?"
    rather than "is it good on average".

    Each cell carries its own sample count `n` and an `ok` flag (n >= min_n). Cells
    below the sample gate are NOT to be trusted — with 5 regimes × 4 horizons × N
    signals you are testing hundreds of hypotheses, so an ungated matrix on thin data
    is guaranteed to show spurious 'conditional alpha' (Harvey-Liu-Zhu / data-snoop).
    Reuses the ONE eval loop and the tape-regime primitive — nothing re-implemented.

    Horizons are in MINUTES but bounded by capture cadence: with ~30-min captures the
    sub-30m cells stay empty (unmeasurable), which the n gate makes explicit."""
    import numpy as np
    from datetime import datetime
    from .strategy.tape_regime import er_series, split_trend_chop
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    if not all_caps:
        return {"error": "no captures in DB"}
    _attach_front_expiry(da, all_caps)
    session_dates = sorted({c["captured_at"][:10] for c in all_caps})
    if date_from or date_to:
        lo, hi = (date_from or "0000-00-00"), (date_to or "9999-99-99")
        caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    else:
        caps = _apply_window(all_caps, 5)          # bounded default
    if len(caps) < 5:
        return {"error": "not enough captures in this range", "session_dates": session_dates}

    dir_specs, records = _eval_signals_series(da, caps)
    spots = [r["spot"] for r in records]
    dates = [r["ts"][:10] for r in records]
    tmin = [datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp() / 60.0
            for r in records]
    # USER-DEFINED horizons (minutes). Forward returns are measured off NIFTY 1-minute
    # bars (not the next capture), so any horizon — incl. 15m finer than the ~30m
    # capture cadence — is measurable, matching a signal's true lifetime.
    try:
        horizon_list = [int(float(h)) for h in str(horizons).split(",") if h.strip()]
        horizon_list = sorted({h for h in horizon_list if h > 0}) or [15, 30, 60]
    except Exception:
        horizon_list = [15, 30, 60]
    horizons_out = horizon_list
    vol_src, vmed, er_median = "n/a", 0.0, 0.0

    # minute-bar price lookup for fine-horizon forward returns
    import bisect as _bisect
    from datetime import timedelta as _td
    _start_dt = datetime.fromisoformat(caps[0]["captured_at"].replace("Z", "+00:00"))
    _end_dt = (datetime.fromisoformat(caps[-1]["captured_at"].replace("Z", "+00:00"))
               + _td(minutes=max(horizon_list) + 5))
    # CRITICAL: da.bars() truncates to the LAST `limit` rows (ORDER BY ts DESC LIMIT).
    # With the default limit it would keep only the final ~400 minutes — i.e. the last
    # session — so every earlier capture's forward price would be missing and get
    # dropped, silently collapsing a multi-day POOL down to one day (eff_n never grows
    # past a single session). Size the limit to span the full [start, end] window so the
    # pool actually pools. Overnight gaps make this a loose upper bound, which is fine.
    _span_min = int((_end_dt - _start_dt).total_seconds() / 60) + 100
    _mb = da.bars("NIFTY", "1m", start=caps[0]["captured_at"],
                  end=_end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), limit=max(_span_min, 400))
    _mt = [datetime.fromisoformat(b["ts"].replace("Z", "+00:00")).timestamp() / 60.0 for b in _mb]
    _mp = [b["close"] for b in _mb]

    def _price_at(t):
        k = _bisect.bisect_left(_mt, t)
        if k < len(_mt) and _mt[k] - t <= 3:
            return _mp[k]
        if k > 0 and t - _mt[k - 1] <= 3:
            return _mp[k - 1]
        return None

    regimes, reg_names, _rmeta = _label_regimes(records, spots, caps, regime_by)
    vol_src = _rmeta["vol_src"]; vmed = _rmeta["vol_median"]; er_median = _rmeta["er_median"]

    def _fwd(i, h):
        # forward return over EXACTLY h minutes, off the minute bars. Within-day is
        # enforced naturally: a target past the close has no bar within tolerance → None.
        t0 = tmin[i]
        p0 = _price_at(t0)
        p1 = _price_at(t0 + h)
        return (p1 - p0) if (p0 is not None and p1 is not None) else None

    matrix = {}
    for spec in dir_specs:
        sc = [r["vals"][spec.name][0] for r in records]
        cell = {}
        for reg in reg_names:
            cell[reg] = {}
            for h in horizon_list:
                xs, ys, tu, dpc, ss = [], [], [], [], []
                for i in range(len(records)):
                    if regimes[i] != reg or sc[i] is None:
                        continue
                    f = _fwd(i, h)
                    if f is None:
                        continue
                    # DEAD BAND: a move too small to trade net of costs is economic noise,
                    # so optionally drop |move| < min_move_pts. If the IC only survives on
                    # sub-cost wiggles, the "edge" is un-monetizable ranking of noise.
                    if min_move_pts > 0 and abs(f) < min_move_pts:
                        continue
                    xs.append(sc[i]); ys.append(f); tu.append(tmin[i])
                    dpc.append((f / spots[i] * 100.0) if spots[i] else 0.0)  # signed move %
                    ss.append(dates[i])                                       # session label
                # EFFECTIVE independent count: greedily count non-overlapping forward
                # windows (each ≥ h minutes after the last kept one). This exposes the
                # "n=16 but all consecutive minutes = 1 real event" trap — overlapping
                # windows share the same future and are NOT independent observations.
                eff, last = 0, -1e18
                for t in sorted(tu):
                    if t - last >= h:
                        eff += 1; last = t
                ic = (float(np.corrcoef(xs, ys)[0, 1]) if len(xs) >= 3
                      and np.std(xs) > 0 and np.std(ys) > 0 else None)
                # ECONOMIC layer — IC only orders moves; these say whether the moves are
                # big enough to trade. dir% = trading IN the signal's direction:
                # sign(score)×move as a % of spot. Its mean is gross expectancy per trade
                # (points of edge before costs); hit = fraction that went the right way.
                exp_lo = exp_hi = exp_se = exp_ppos = None
                n_days = 0
                if xs:
                    import numpy as _np
                    _sc = _np.sign(_np.array(xs)); _mv = _np.array(dpc)
                    dir_pct = _sc * _mv                       # P&L% of trading the signal's side
                    avg_abs = float(_np.mean(_np.abs(_mv)))
                    gross_exp = float(_np.mean(dir_pct))
                    hit = float(_np.mean(dir_pct > 0))
                    # SESSION-BLOCK BOOTSTRAP for P(edge). Intraday minutes within a day are
                    # autocorrelated, so the independent unit is the SESSION, not the tick —
                    # resample whole days (with replacement) and recompute expectancy. The
                    # spread of those replicates is the honest uncertainty on the edge; a
                    # naive per-minute bootstrap would understate it exactly like raw n did.
                    days = sorted(set(ss))
                    n_days = len(days)
                    if n_days >= 3 and n_boot > 0:
                        by = {d: [] for d in days}
                        for k in range(len(ss)):
                            by[ss[k]].append(dir_pct[k])
                        d_sum = _np.array([float(_np.sum(by[d])) for d in days])
                        d_cnt = _np.array([len(by[d]) for d in days], float)
                        idx = _np.random.default_rng(12345).integers(0, n_days, size=(n_boot, n_days))
                        s = d_sum[idx].sum(axis=1)
                        c = d_cnt[idx].sum(axis=1)
                        reps = _np.divide(s, c, out=_np.full_like(s, _np.nan), where=c > 0)
                        reps = reps[~_np.isnan(reps)]
                        if len(reps) >= 20:
                            exp_lo = float(_np.percentile(reps, 2.5))
                            exp_hi = float(_np.percentile(reps, 97.5))
                            exp_se = float(_np.std(reps))
                            exp_ppos = float(_np.mean(reps > 0))    # P(edge has the right sign)
                else:
                    avg_abs = gross_exp = hit = None
                cell[reg][h] = {"ic": (round(ic, 3) if ic is not None else None),
                                "n": len(xs), "eff_n": eff, "n_days": n_days, "ok": eff >= min_n,
                                "avg_abs_move_pct": (round(avg_abs, 4) if avg_abs is not None else None),
                                "gross_exp_pct": (round(gross_exp, 4) if gross_exp is not None else None),
                                "hit": (round(hit, 3) if hit is not None else None),
                                "exp_lo_pct": (round(exp_lo, 4) if exp_lo is not None else None),
                                "exp_hi_pct": (round(exp_hi, 4) if exp_hi is not None else None),
                                "exp_se_pct": (round(exp_se, 5) if exp_se is not None else None),
                                "exp_p_pos": (round(exp_ppos, 3) if exp_ppos is not None else None)}
        matrix[spec.name] = cell

    roster = [{"name": s.name, "label": s.display, "family": s.family} for s in dir_specs]
    return {"matrix": matrix, "regimes": reg_names, "horizons": horizons_out,
            "min_n": min_n, "signals": roster, "session_dates": session_dates,
            "regime_by": regime_by,
            "vol_proxy": vol_src, "vol_median": round(vmed, 4),
            "er_median": round(er_median, 3),
            "date_from": caps[0]["captured_at"][:10], "date_to": caps[-1]["captured_at"][:10],
            "note": f"Forward returns measured off NIFTY 1-min bars, so horizons {horizons_out} "
                    f"(min) are exact — 15m is now measurable even at 30-min capture cadence. "
                    f"IC by (tape × vol) regime × horizon. Tape split trend/chop at "
                    f"the median efficiency ratio {round(er_median, 3)} (no neutral band); vol lo/hi "
                    f"at the median {vol_src}. Each cell needs n≥min_n; greyed = under-sampled. "
                    f"4×4 cells is still multiple testing — treat a bright cell as a hypothesis, not "
                    f"a finding, until it clears the gate over ≥60 sessions (Harvey-Liu-Zhu). "
                    f"n = raw observations; eff_n = INDEPENDENT (non-overlapping) windows — the "
                    f"gate uses eff_n, so 16 consecutive minutes (eff_n≈1) can't pass as a sample."}


def _ic_inputs(date_from, date_to, horizon, controls):
    """Shared loader for the incremental-IC studies: per-capture signal scores + the
    forward return (off NIFTY minute bars) + the control (blend) columns. Returns a
    dict of arrays, or {'error':...}. One place so the unconditional and CONDITIONAL
    (per-regime) studies difference against exactly the same data and controls."""
    import numpy as np
    import bisect as _bisect
    from datetime import datetime, timedelta
    from .signals import registry as _reg
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    if not all_caps:
        return {"error": "no captures in DB"}
    _attach_front_expiry(da, all_caps)
    session_dates = sorted({c["captured_at"][:10] for c in all_caps})
    if date_from or date_to:
        lo, hi = (date_from or "0000-00-00"), (date_to or "9999-99-99")
        caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    else:
        caps = all_caps
    if len(caps) < 10:
        return {"error": "not enough captures in range", "session_dates": session_dates}

    dir_specs, records = _eval_signals_series(da, caps)
    names = [s.name for s in dir_specs]
    if controls is None:
        controls = [n for n, w in _reg.default_weights().items() if w > 0]   # the live blend
    controls = [c for c in controls if c in names]

    tmin = [datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp() / 60.0 for r in records]
    spots = [r["spot"] for r in records]
    start = caps[0]["captured_at"]
    end_dt = datetime.fromisoformat(caps[-1]["captured_at"].replace("Z", "+00:00")) + timedelta(minutes=horizon + 5)
    span = int((end_dt - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() / 60) + 100
    mb = da.bars("NIFTY", "1m", start=start, end=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), limit=max(span, 400))
    mt = [datetime.fromisoformat(b["ts"].replace("Z", "+00:00")).timestamp() / 60.0 for b in mb]
    mp = [b["close"] for b in mb]

    def _price_at(t):
        k = _bisect.bisect_left(mt, t)
        if k < len(mt) and mt[k] - t <= 3:
            return mp[k]
        if k > 0 and t - mt[k - 1] <= 3:
            return mp[k - 1]
        return None

    fwd = []
    for i in range(len(records)):
        p0, p1 = _price_at(tmin[i]), _price_at(tmin[i] + horizon)
        fwd.append(((p1 - p0) / spots[i] * 100.0) if (p0 is not None and p1 is not None and spots[i]) else np.nan)
    sc = {n: np.array([np.nan if r["vals"][n][0] is None else r["vals"][n][0] for r in records], float)
          for n in names}
    return {"records": records, "caps": caps, "names": names, "spots": spots,
            "sc": sc, "fwd": np.array(fwd, float), "Zc": [sc[c] for c in controls],
            "controls": controls, "session_dates": session_dates}


def _incremental_ic_rows(inp, base_mask=None):
    """Per-signal {ic, incremental_ic, n} over the captures selected by base_mask
    (None = all). incremental_ic = partial correlation of the signal with the forward
    return after regressing out the control columns."""
    import numpy as np
    from .signals import registry as _reg
    sc, fwd, Zc, controls = inp["sc"], inp["fwd"], inp["Zc"], inp["controls"]

    def _resid(y, mask):
        yv = y[mask]
        if not Zc:
            return yv - yv.mean()
        Z = np.column_stack([np.ones(int(mask.sum()))] + [z[mask] for z in Zc])
        beta, *_ = np.linalg.lstsq(Z, yv, rcond=None)
        return yv - Z @ beta

    rows = []
    for n in inp["names"]:
        x = sc[n]
        mask = ~np.isnan(x) & ~np.isnan(fwd)
        for z in Zc:
            mask &= ~np.isnan(z)
        if base_mask is not None:
            mask = mask & base_mask
        k = int(mask.sum())
        ic = inc = None
        if k >= 10 and np.std(x[mask]) > 0 and np.std(fwd[mask]) > 0:
            ic = float(np.corrcoef(x[mask], fwd[mask])[0, 1])
            if n not in controls:
                rx, rf = _resid(x, mask), _resid(fwd, mask)
                if np.std(rx) > 0 and np.std(rf) > 0:
                    inc = float(np.corrcoef(rx, rf)[0, 1])
        rows.append({"name": n, "label": _reg.BY_NAME[n].display, "is_control": n in controls,
                     "ic": (round(ic, 3) if ic is not None else None),
                     "incremental_ic": (round(inc, 3) if inc is not None else None), "n": k})
    return rows


def signal_incremental_ic(date_from: str | None = None, date_to: str | None = None,
                          horizon: int = 30, controls: list | None = None) -> dict:
    """INCREMENTAL INFORMATION: each signal's standalone IC vs its INCREMENTAL IC — the
    partial correlation with the forward return AFTER regressing out the current blend.
    Big standalone IC + ~0 incremental = redundant passenger; incremental IC that
    survives = it adds NEW information. Descriptive on limited data."""
    inp = _ic_inputs(date_from, date_to, horizon, controls)
    if "error" in inp:
        return inp
    rows = _incremental_ic_rows(inp)
    rows.sort(key=lambda d: -(abs(d["incremental_ic"]) if d["incremental_ic"] is not None else -1))
    return {"horizon": horizon, "controls": inp["controls"], "signals": rows,
            "session_dates": inp["session_dates"], "n_captures": len(inp["records"]),
            "note": "incremental_ic = partial correlation with the forward return AFTER removing what "
                    "the control signals (current blend) explain. ~0 incremental = redundant passenger; "
                    "survives = adds NEW information. Descriptive on limited data (needs ≥60 sessions)."}


def signal_conditional_incremental_ic(date_from: str | None = None, date_to: str | None = None,
                                      horizon: int = 30, regime_by: str = "tape_vol",
                                      controls: list | None = None) -> dict:
    """CONDITIONAL incremental IC — incremental IC computed WITHIN each regime. Answers
    'under what conditions does this signal deserve to exist?': a signal that adds
    information in TREND but ~0 in RANGE should only vote in trend. Reuses the single
    regime-labeler (_label_regimes) so the regimes match the pooled study exactly."""
    import numpy as np
    inp = _ic_inputs(date_from, date_to, horizon, controls)
    if "error" in inp:
        return inp
    regimes, reg_names, _ = _label_regimes(inp["records"], inp["spots"], inp["caps"], regime_by)
    reg_arr = np.array([r if r is not None else "∅" for r in regimes])
    matrix = {}
    for reg in reg_names:
        base = reg_arr == reg
        for row in _incremental_ic_rows(inp, base_mask=base):
            m = matrix.setdefault(row["name"], {"label": row["label"], "is_control": row["is_control"], "cells": {}})
            m["cells"][reg] = {"ic": row["ic"], "incremental_ic": row["incremental_ic"], "n": row["n"]}
    return {"horizon": horizon, "regime_by": regime_by, "controls": inp["controls"],
            "regimes": reg_names, "matrix": matrix, "session_dates": inp["session_dates"],
            "n_captures": len(inp["records"]),
            "note": "Incremental IC PER REGIME. A signal with incremental IC in one regime and ~0 in "
                    "another should be MUTED outside the regime where it adds information — that is the "
                    "conditional-activation rule, learned not assumed. Descriptive on limited data."}


def _partial_ic(inp, target, control_names, base_mask=None):
    """Incremental IC of `target` controlling for a SPECIFIC set of signals (leave-one-out
    within a factor). None when too few samples. Used to pick a factor's primary estimator
    by its unique information vs the OTHER cluster members — not vs the blend."""
    import numpy as np
    sc, fwd = inp["sc"], inp["fwd"]
    x = sc[target]
    Zc = [sc[c] for c in control_names if c != target]
    mask = ~np.isnan(x) & ~np.isnan(fwd)
    for z in Zc:
        mask &= ~np.isnan(z)
    if base_mask is not None:
        mask &= base_mask
    if int(mask.sum()) < 10 or np.std(x[mask]) == 0 or np.std(fwd[mask]) == 0:
        return None
    def _r(y):
        yv = y[mask]
        if not Zc:
            return yv - yv.mean()
        Z = np.column_stack([np.ones(int(mask.sum()))] + [z[mask] for z in Zc])
        beta, *_ = np.linalg.lstsq(Z, yv, rcond=None)
        return yv - Z @ beta
    rx, rf = _r(x), _r(fwd)
    if np.std(rx) == 0 or np.std(rf) == 0:
        return None
    return float(np.corrcoef(rx, rf)[0, 1])


def signal_factor_discovery(date_from: str | None = None, date_to: str | None = None,
                            corr_threshold: float = 0.6, horizon: int = 30) -> dict:
    """FACTOR DISCOVERY — group the directional signals into latent factors from the DATA,
    not by hand. Signals are clustered by score correlation (connected components at
    |corr| ≥ threshold); within each factor the PRIMARY estimator is the one with the
    highest incremental IC (it carries the most unique predictive information), the rest
    are supporting. `cohesion` = average intra-factor |corr| = how tight/robust the cluster
    is. This is the transition from 'which signals vote' to 'which market properties exist':
    signals become sensors, factors become the state. Descriptive on limited data —
    clusters firm up with sessions."""
    import numpy as np
    from .signals import registry as _reg
    from .analysis.signal_ensemble import corr_matrix_full
    inp = _ic_inputs(date_from, date_to, horizon, None)
    if "error" in inp:
        return inp
    names, sc = inp["names"], inp["sc"]
    cols = {n: [None if np.isnan(v) else float(v) for v in sc[n]] for n in names}
    _, mat, _pn = corr_matrix_full(cols)
    n = len(names)

    # cluster: union-find over edges with |corr| >= threshold (connected components).
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in range(n):
        for j in range(i + 1, n):
            if mat[i][j] is not None and abs(mat[i][j]) >= corr_threshold:
                parent[find(i)] = find(j)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    inc = {r["name"]: r for r in _incremental_ic_rows(inp)}   # standalone IC per signal

    factors = []
    for idx in groups.values():
        members = [names[i] for i in idx]
        pairs = [abs(mat[i][j]) for a, i in enumerate(idx) for j in idx[a + 1:] if mat[i][j] is not None]
        cohesion = float(np.mean(pairs)) if pairs else 1.0
        # WITHIN-factor incremental IC: each member's unique info vs the OTHER members —
        # this is what picks the primary (the member that carries the factor's signal),
        # unbiased by the blend controls.
        wf = {m: _partial_ic(inp, m, [x for x in members if x != m]) for m in members}
        def _key(nm):
            v, ic = wf.get(nm), inc.get(nm, {}).get("ic")
            return abs(v) if v is not None else (abs(ic) if ic is not None else -1.0)
        ranked = sorted(members, key=_key, reverse=True)
        roles = []
        for r_i, m in enumerate(ranked):
            spec = _reg.BY_NAME[m]
            role = "primary" if r_i == 0 else ("confidence modifier" if spec.signal_class == "confirmation"
                                               else "supporting")
            roles.append({"name": m, "label": spec.display, "family": spec.family,
                          "signal_class": spec.signal_class, "ic": inc.get(m, {}).get("ic"),
                          "within_factor_incr_ic": (round(wf[m], 3) if wf[m] is not None else None),
                          "role": role})
        factors.append({"members": members, "primary": ranked[0], "n_members": len(members),
                        "cohesion": round(cohesion, 2), "roles": roles})
    factors.sort(key=lambda f: -f["n_members"])
    return {"corr_threshold": corr_threshold, "horizon": horizon, "n_signals": n,
            "n_factors": len(factors), "factors": factors,
            "session_dates": inp["session_dates"], "n_captures": len(inp["records"]),
            "note": "Factors DISCOVERED by clustering signals on score correlation, not assigned by "
                    "hand. Primary = highest incremental IC in the cluster. cohesion = avg intra-factor "
                    "|corr| (cluster confidence: ~1 tight, low = loose/ambiguous). Descriptive on "
                    "limited data — re-run as sessions accumulate; unstable clusters mean 'not enough "
                    "data to say', not a real factor."}


def _classify_regime(pin, tight, crowd, strad_pct):
    """TRANSPARENT, PRIOR heuristic market-type label from the regime signals. Every
    branch is flagged UNVERIFIED because the regime→behaviour link (does a pin imply
    reversion? does weak-pin imply trend?) has NOT been validated on this data — that is
    exactly what the conditional P(edge) study is for. Descriptive, never prescriptive."""
    if pin is None:
        return "UNKNOWN", "insufficient option-chain data to read the regime"
    if strad_pct is not None and strad_pct > 5:
        return ("VOLATILITY EXPANSION",
                "ATM straddle expanding — a larger move is being priced (direction NOT implied; UNVERIFIED)")
    if pin > 0.6 and (tight or 0) > 0.5:
        return ("PIN / RANGE",
                "strong, concentrated pin — reversion/range-prone in theory (UNVERIFIED on this data)")
    if pin < 0.35:
        return ("TREND-PRONE",
                "weak pin — little gamma anchoring, moves can extend in theory (UNVERIFIED)")
    return "BALANCED", "no dominant regime signal"


def market_state(now: str | None = None) -> dict:
    """Wrapper so the endpoint NEVER returns a bare 500 — any failure comes back as a
    JSON {error, trace} the UI can display (and we can debug from)."""
    try:
        return _market_state_impl(now)
    except Exception as e:
        import traceback
        return {"error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc().splitlines()[-4:]}


def _market_state_impl(now: str | None = None) -> dict:
    """Current market-STATE snapshot, grouped by the three signal classes:
      * regime       — pin/vol state (strengths 0..1, NEVER a direction),
      * directional  — the position votes + a net blended score,
      * execution    — VWAP / volume / momentum confirmation reads.
    Plus a TRANSPARENT, flagged regime-type heuristic. This surfaces the regime and
    execution layers the directional Signal window never showed — but it asserts no
    conditional reliability, because none is validated yet."""
    from .signals import bundle as _sb
    from .signals import registry as _reg
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    if not all_caps:
        return {"error": "no captures in DB"}
    _attach_front_expiry(da, all_caps)
    caps = all_caps if not now else [c for c in all_caps if c["captured_at"] <= now]
    if not caps:
        return {"error": "no capture at/before the requested time"}
    cap = caps[-1]
    ts, exp = cap["captured_at"], cap.get("expiry")
    # same bar-cache eval path the study endpoints use (proven, and avoids per-signal
    # constituent DB queries that are slow on the full production price_bars table).
    bc = _window_cache(da, [cap])
    b = _sb.evaluate(_CFG.db_path, ts, exp, veto_days=_CFG.gates.event_veto_days, bar_cache=bc)
    spot = float(cap.get("spot") or getattr(b, "spot", 0.0) or 0.0)

    def pack(name):
        spec = _reg.BY_NAME[name]
        try:
            s = b.get(name)
            ok = bool(s and getattr(s, "status", "") == "OK")
            return {"name": name, "label": spec.display, "family": spec.family,
                    "score": round(float(getattr(s, "score", 0.0)), 3) if ok else None,
                    "confidence": round(float(getattr(s, "confidence", 0.0)), 3) if ok else None,
                    "status": getattr(s, "status", "NO_DATA") if s else "NO_DATA",
                    "detail": (getattr(s, "detail", {}) or {}) if s else {}}
        except Exception as e:      # a single signal must not kill the whole panel
            return {"name": name, "label": spec.display, "family": spec.family,
                    "score": None, "confidence": None, "status": f"ERROR: {type(e).__name__}",
                    "detail": {}}

    regime = [pack(n) for n in _reg.by_class("regime")]
    dir_names = [n for n in _reg.directional_names() if _reg.BY_NAME[n].signal_class == "position"]
    directional = [pack(n) for n in dir_names]
    execution = [pack(n) for n in _reg.by_class("confirmation")]

    # net directional score = weighted blend over the blended core (the 2 that vote)
    w = _reg.default_weights()
    num = den = 0.0
    for n in _reg.blended_names():
        s = b.get(n)
        if s and getattr(s, "status", "") == "OK" and w.get(n, 0) > 0:
            num += w[n] * float(s.score); den += w[n]
    net = round(num / den, 3) if den > 0 else None

    def sv(name):
        s = b.get(name)
        return float(s.score) if s and getattr(s, "status", "") == "OK" else None
    strad = b.get("straddle_flow")
    strad_pct = (getattr(strad, "detail", {}) or {}).get("change_pct") if strad and \
        getattr(strad, "status", "") == "OK" else None
    mtype, note = _classify_regime(sv("pin_pressure"), sv("oi_dispersion"),
                                   sv("oi_entropy"), strad_pct)

    # ── MARKET QUALITY: how COHERENT and COMPLETE is today's evidence? (descriptive,
    #    not predictive) — agreement of the votes + data completeness − contradictions.
    all_sigs = regime + directional + execution
    n_total = len(all_sigs)
    n_ok = sum(1 for s in all_sigs if s["status"] == "OK")
    completeness = (n_ok / n_total) if n_total else 0.0
    dir_scores = [s["score"] for s in directional if s["score"] is not None and s["score"] != 0]
    signs = [1 if x > 0 else -1 for x in dir_scores]
    agreement = (abs(sum(signs)) / len(signs)) if signs else 0.0   # 1 = unanimous, 0 = split

    # CONTRADICTIONS: conflicting strong evidence, surfaced instead of silently averaged.
    contradictions = []
    strong = [s for s in directional if s["score"] is not None and abs(s["score"]) >= 0.30]
    bull = [s for s in strong if s["score"] > 0]
    bear = [s for s in strong if s["score"] < 0]
    if bull and bear:
        # NB: local names must NOT shadow the bundle `b` (it's used below for factors/straddle)
        top_bull = max(bull, key=lambda s: s["score"]); top_bear = min(bear, key=lambda s: s["score"])
        contradictions.append({"a": top_bull["label"], "b": top_bear["label"],
                               "note": f"{top_bull['label']} bullish (+{top_bull['score']}) vs "
                                       f"{top_bear['label']} bearish ({top_bear['score']})"})
    vwap = next((s for s in execution if s["name"] == "vwap"), None)
    if net is not None and vwap and vwap["score"] is not None and abs(net) > 0.2 \
            and abs(vwap["score"]) > 0.2 and (net > 0) != (vwap["score"] > 0):
        contradictions.append({"a": "Net direction", "b": "VWAP",
                               "note": "net directional tilt disagrees with price vs VWAP"})

    q = 0.5 * agreement + 0.5 * completeness - 0.15 * len(contradictions)
    grade = "HIGH" if q > 0.7 else "MEDIUM" if q > 0.45 else "LOW"
    quality = {
        "grade": grade, "agreement": round(float(agreement), 2),
        "completeness": round(float(completeness), 2), "n_ok": int(n_ok), "n_total": int(n_total),
        "contradictions": contradictions, "n_contradictions": len(contradictions),
        "note": "How COHERENT/COMPLETE the current evidence is — NOT a prediction. LOW = signals "
                "disagree or data is thin, so trust any read less. It describes the evidence, it "
                "does not forecast direction."}

    # regime 'why' — which regime signals are firing, so the label explains itself.
    why = [f"{s['label']}: {s['detail'].get('regime', round(s['score'], 2) if s['score'] is not None else '—')}"
           for s in regime if s["status"] == "OK"]

    # FACTOR BELIEFS: aggregate the sensors into per-property estimates + confidence
    from .factors import evaluate_factors
    factors = evaluate_factors(b)

    return {"ts": ts, "expiry": exp, "spot": round(spot, 1),
            "regime": regime, "directional": directional, "execution": execution,
            "factors": factors,
            "net_directional_score": net, "market_type": mtype, "regime_note": note,
            "regime_why": why, "market_quality": quality,
            "disclaimer": "Market type & quality are DESCRIPTIVE heuristics, not validated "
                          "classifiers. Whether a regime implies reversion or trend — and whether "
                          "the net score is tradeable — is under measurement (P(edge)); no proven "
                          "edge yet. Context, NOT advice."}


def signal_phase_grid(expiry: str | None = None, date: str | None = None,
                      oi_symbol: str = "NIFTY") -> dict:
    """Signal × session-phase grid: how each signal's directional read evolved
    through ONE day. Rows = signals, columns = Open15 / Morning / Midday / EOD; each
    cell is the signal's MEAN score across that phase's captures (with mean confidence
    and n). Also returns the blended net per phase and the NIFTY move per phase, so
    you can see whether a signal persisted, faded, or flipped as the session ran.

    The roster, weights and blend all come from the single sources (registry /
    SignalWeights / strategy.blend) — nothing is hardcoded here."""
    import numpy as np
    from .signals import bundle as _sb
    from .signals import registry as _reg
    from .config.settings import SignalWeights
    da = DataAccess(_CFG.db_path)
    # Date-FIRST: the day list spans ALL captures across ALL expiries, so recent
    # dates under the current expiry are selectable (not just the last completed one).
    pool = da.list_captures(expiry=expiry) if expiry else da.list_captures()
    if not pool:
        return {"error": "no captures in DB"}
    _attach_front_expiry(da, pool)
    # pick the day: requested date, else the last day present
    days = sorted({c["captured_at"][:10] for c in pool})
    day = date if (date and date in days) else days[-1]
    caps = [c for c in pool if c["captured_at"][:10] == day]
    if len(caps) < 2:
        return {"error": f"only {len(caps)} captures on {day}", "days": days}

    W = SignalWeights().as_dict()
    # one shared eval loop (also used by signal_timeseries); per-capture expiry
    dir_specs, records = _eval_signals_series(da, caps)
    per_cap = [(r["ist_min"], r["spot"], r["vals"]) for r in records]

    def _phase_caps(lo, hi):
        return [pc for pc in per_cap if lo <= pc[0] < hi]

    grid = {}
    for spec in dir_specs:
        row = {}
        for key, lo, hi, _lbl, _t in _PHASES:
            pcs = _phase_caps(lo, hi)
            ss = [pc[2][spec.name][0] for pc in pcs if pc[2][spec.name][2]]
            cs = [pc[2][spec.name][1] for pc in pcs if pc[2][spec.name][2]]
            row[key] = ({"score": round(float(np.mean(ss)), 3),
                         "conf": round(float(np.mean(cs)), 3), "n": len(ss)}
                        if ss else {"score": None, "conf": 0.0, "n": 0})
        grid[spec.name] = row

    # blended net + NIFTY move per phase
    net_row, move_row = {}, {}
    for key, lo, hi, _lbl, _t in _PHASES:
        pcs = _phase_caps(lo, hi)
        nets = []
        for pc in pcs:
            num = den = 0.0
            for spec in dir_specs:
                w = W.get(spec.name, 0.0)
                sc, cf, ok = pc[2][spec.name]
                if w > 0 and ok:
                    num += w * cf * sc
                    den += w * cf
            if den:
                nets.append(num / den)
        net_row[key] = round(float(np.mean(nets)), 3) if nets else None
        if pcs:
            move_row[key] = round(pcs[-1][1] - pcs[0][1], 1)     # spot change across the phase
        else:
            move_row[key] = None

    # futures-OI positioning per phase — the CONVICTION overlay (who drove the move).
    # Reuses backend.quant.intraday_oi (the SAME engine as the Macro Shock view) —
    # not re-implemented here. `oi_symbol` lets you check a stock (Reliance/ICICI):
    # futures OI if captured, else a volume proxy on the cash bars.
    oi_row = _phase_oi_positioning(_CFG.db_path, day, oi_symbol)

    roster = [{"name": s.name, "label": s.display, "family": s.family,
               "weight": round(W.get(s.name, 0.0), 3)} for s in dir_specs]
    return {"expiry": (caps[0].get("expiry") if caps else expiry), "date": day, "days": days,
            "phases": [{"key": k, "label": l, "time": t} for k, _lo, _hi, l, t in _PHASES],
            "signals": roster, "grid": grid, "net": net_row, "nifty_move": move_row,
            "oi_positioning": oi_row, "oi_symbol": oi_symbol,
            "oi_symbols": ["NIFTY"] + sorted(set(da.available_symbols("1m")) - {"NIFTY"}),
            "note": "cell = mean signal score across that phase's captures; "
                    "net = weighted blend; move = NIFTY spot change over the phase; "
                    "OI positioning = futures conviction overlay (who drove it). "
                    "Descriptive, thin history (D-MA-04)."}


def _phase_oi_positioning(db_path: str, day: str, symbol: str = "NIFTY") -> dict:
    """Positioning per phase for `symbol` — REUSES backend.quant.intraday_oi (the
    same engine behind the Macro Shock view). Tries FUTURES OI first ({symbol}_FUT_1),
    then falls back to the VOLUME proxy on the cash symbol (weaker, flagged). No rule
    re-implemented here (CLAUDE.md DRY). Pending, data-honest, when neither is present."""
    from backend.quant import intraday_oi as _ioi
    from .signals.futures_oi import _LEAN
    keys = ("open15", "morning", "midday", "eod")
    fut_symbol = symbol if symbol.upper().endswith(("_FUT_1", "_FUT_2")) else f"{symbol}_FUT_1"
    v = _ioi.analyze(db_path, day, fut_symbol)
    proxy = None
    if not v.get("available"):
        vv = _ioi.analyze_volume(db_path, day, symbol)     # cash volume proxy
        if vv.get("available"):
            v, proxy = vv, "volume"
    if not v.get("available"):
        return {k: {"regime": None, "pending": True} for k in keys} | {
            "_note": v.get("note", "no futures OI and no volume for this symbol"),
            "_symbol": symbol, "_source": "backend.quant.intraday_oi (Macro Shock engine)"}

    legs = v.get("legs", {})

    def _cell(leg):
        if not leg or leg.get("kind") in (None, "oi_unavailable"):
            return {"regime": None, "pending": True}
        kind = leg["kind"]
        lean, conviction = _LEAN.get(kind, ("neutral", False))
        return {"regime": kind.replace("_", " "), "kind": kind, "lean": lean,
                "conviction": conviction, "note": leg.get("read"),
                "dP": leg.get("d_price_pts"),
                "dOI": leg.get("d_oi"), "vol_ratio": leg.get("vol_ratio"),
                "proxy": proxy, "pending": False}

    # intraday_oi legs are morning / midday / afternoon; map afternoon → our eod.
    return {"open15": {"regime": None, "pending": True},
            "morning": _cell(legs.get("morning")),
            "midday": _cell(legs.get("midday")),
            "eod": _cell(legs.get("afternoon")),
            "_symbol": symbol, "_proxy": proxy,
            "_source": "backend.quant.intraday_oi (same engine as the Macro Shock view)",
            "_note": ("VOLUME proxy (no futures OI) — participation, not true positioning; weaker."
                      if proxy else
                      "OI legs use intraday_oi's morning/midday/afternoon boundaries; "
                      "afternoon shown under End of day.")}


def _apply_window(caps: list, window_days) -> list:
    """Keep only the last N session days (distinct capture dates incl. expiry)."""
    if not window_days:
        return caps
    dates = sorted({c["captured_at"][:10] for c in caps})
    keep = set(dates[-int(window_days):])
    return [c for c in caps if c["captured_at"][:10] in keep]


def _store_scores(expiry: str) -> dict:
    """{ts: {signal: score}} of OK signals from the feature store — the "compute
    once (at backfill), read many" cache. Empty {} if the store isn't backfilled,
    in which case callers fall back to live evaluation. Only signals flagged OK are
    included, matching the live path exactly."""
    try:
        from .features import store as _fs
        rows = _fs.query(_CFG.db_path, expiry, limit=100000)
    except Exception:
        return {}
    out: dict = {}
    for r in rows:
        ts = r.get("ts")
        if not ts:
            continue
        d = {}
        for s in _DIR_SIGNAL_NAMES:
            sc = r.get(f"sig_{s}_score")
            ok = r.get(f"sig_{s}_ok")
            # OK flag present → honour it; older rows without the flag → use presence
            if isinstance(sc, (int, float)) and (ok is None or ok == 1):
                d[s] = float(sc)
        if d:
            out[ts] = d
    return out


def _window_cache(da: "DataAccess", caps: list, pad_days: int = 2):
    """Bulk-load every symbol's 1m bars over the caps' span (plus a lookback pad)
    into an in-memory BarCache, so the per-snapshot `bundle.evaluate` loop does
    in-memory slices instead of ~8 fresh SQLite queries each — the N+1 that made
    a live analytics run ~8× slower than it needs to be. Scoped to the window so
    RAM stays bounded. Returns None if the window is empty."""
    if not caps:
        return None
    try:
        from datetime import datetime, timedelta
        from .signals.data_access import BarCache
        from .signals.data_access import CROSS_ASSET_SYMBOLS
        start = (datetime.fromisoformat(caps[0]["captured_at"].replace("Z", "+00:00"))
                 - timedelta(days=pad_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = caps[-1]["captured_at"]
        # available symbols PLUS the cross-asset names signals may ask for, so a
        # symbol absent from the DB is negative-cached, not re-queried every call.
        syms = list(dict.fromkeys(da.available_symbols("1m") + CROSS_ASSET_SYMBOLS))
        return BarCache(_CFG.db_path, syms, start=start, end=end)
    except Exception:
        return None                                  # fall back to live queries


def signal_backtest_all(horizon_hours: float = 3.0, expiry: str | None = None,
                        window_days=None, max_points: int = 120,
                        sample_minutes: float | None = None, progress_cb=None) -> dict:
    """Scoreboard: rank every signal by how reliably it predicts the forward NIFTY
    move. Per signal: hit rate, IC (correlation), spread (top-half − bottom-half
    forward return), and a Sharpe-like consistency of its directional call. If
    `sample_minutes` is set, evaluations are spaced that far apart so observations
    don't overlap the horizon (honest statistics); otherwise bounded by max_points."""
    import numpy as np
    from datetime import datetime, timedelta
    from .signals import bundle as _sb
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    caps = _apply_window(da.list_captures(expiry=expiry), window_days)
    if len(caps) < 3:
        return {"error": "not enough snapshots in this window"}
    times = [datetime.fromisoformat(c["captured_at"].replace("Z", "+00:00")) for c in caps]
    horizon = timedelta(hours=horizon_hours)

    def _cadence():
        ds = sorted((times[i + 1] - times[i]).total_seconds() / 60 for i in range(min(len(times) - 1, 200)))
        ds = [d for d in ds if d > 0]
        return ds[len(ds) // 2] if ds else 1.0
    cadence = _cadence()
    if sample_minutes:
        stride = max(1, round(sample_minutes / cadence))
    else:
        stride = max(1, len(caps) // max_points)
    sample_min_eff = round(stride * cadence, 1)
    horizon_min = horizon_hours * 60
    overlap = sample_min_eff < horizon_min

    acc = {s: {"score": [], "fwd": []} for s in _DIR_SIGNAL_NAMES}
    _bc = _window_cache(da, caps)               # 8× faster: kill per-snapshot N+1
    n_snap = 0
    idxs = list(range(0, len(caps), stride))
    total_steps = len(idxs)
    for step, i in enumerate(idxs):
        if progress_cb:
            progress_cb(step, total_steps)
        t0 = times[i]
        j = next((k for k in range(i + 1, len(caps)) if times[k] >= t0 + horizon), None)
        if j is None:
            continue
        b = _sb.evaluate(_CFG.db_path, caps[i]["captured_at"], expiry,
                         veto_days=_CFG.gates.event_veto_days, bar_cache=_bc)
        if b.spot in (None, 0.0):
            continue
        fwd = (caps[j]["spot"] - b.spot) / b.spot * 100.0
        n_snap += 1
        for s in _DIR_SIGNAL_NAMES:
            sg = b.get(s)
            if sg is None or sg.status != "OK":
                continue
            acc[s]["score"].append(sg.score); acc[s]["fwd"].append(fwd)
    if progress_cb:
        progress_cb(total_steps, total_steps)

    # metrics from the ONE shared engine (HARD RULE 12): ic/hit/spread/rank_ic from
    # signal_metrics, the directional 'sharpe' from directional_sharpe — identical
    # values to before, plus rank_ic, all sourced from strategy_framework/analysis.
    from .analysis.signal_ensemble import signal_metrics as _sig_metrics, directional_sharpe as _dsharpe
    table = []
    for s in _DIR_SIGNAL_NAMES:
        sc = np.array(acc[s]["score"], dtype=float); fw = np.array(acc[s]["fwd"], dtype=float)
        if len(sc) < 3:
            table.append({"signal": s, "n": len(sc), "hit_rate": None, "ic": None,
                          "spread": None, "sharpe": None})
            continue
        m = _sig_metrics(sc, fw)
        sh = _dsharpe(sc, fw)
        table.append({"signal": s, "n": len(sc), "n_active": int((np.abs(sc) >= 0.1).sum()),
                      "hit_rate": m["hit"], "ic": m["ic"], "rank_ic": m["rank_ic"],
                      "spread": m["spread"],
                      "sharpe": round(sh, 3) if sh is not None else None})
    # rank by Sharpe-like reliability (falls back to hit rate)
    table.sort(key=lambda r: (r["sharpe"] is None, -(r["sharpe"] or -9)))
    best = next((r["signal"] for r in table if r["sharpe"] is not None), None)
    return {"expiry": expiry, "horizon_hours": horizon_hours, "window_days": window_days,
            "n_snapshots": n_snap, "sample_minutes": sample_min_eff, "overlap": overlap,
            "effective_n": n_snap if not overlap else round(n_snap * sample_min_eff / horizon_min, 1),
            "best": best, "session_dates": sorted({c["captured_at"][:10] for c in caps}),
            "table": table,
            "note": ("Ranked by Sharpe-like reliability of the directional call. "
                     "IC=corr(score,fwd); spread=top−bottom half fwd return. "
                     + ("⚠ sampling finer than horizon → overlapping observations, "
                        "significance inflated (see effective_n)." if overlap else
                        "sampling ≈ horizon → independent observations.")
                     + " Descriptive only on thin history (D-MA-04).")}


def signal_horizon_curve(signal: str, expiry: str | None = None,
                         window_days=None, sample_minutes: float | None = None) -> dict:
    """One signal's IC / hit rate / Sharpe across horizons (5/15/30/60 min + EOD)
    in a single pass — shows the signal's 'information half-life': where its edge
    peaks and how fast it decays."""
    import numpy as np
    from datetime import datetime, timedelta
    from .signals import bundle as _sb
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    caps = _apply_window(da.list_captures(expiry=expiry), window_days)
    if len(caps) < 3:
        return {"error": "not enough snapshots in this window"}
    times = [datetime.fromisoformat(c["captured_at"].replace("Z", "+00:00")) for c in caps]
    ds = sorted((times[i + 1] - times[i]).total_seconds() / 60 for i in range(min(len(times) - 1, 200)))
    cadence = next((d for d in ds if d > 0), 1.0)
    stride = max(1, round(sample_minutes / cadence)) if sample_minutes else max(1, len(caps) // 120)

    horizons = [("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60), ("eod", None)]
    _bc = _window_cache(da, caps)               # 8× faster: kill per-snapshot N+1
    acc = {h: {"s": [], "f": []} for h, _ in horizons}
    for i in range(0, len(caps), stride):
        b = _sb.evaluate(_CFG.db_path, caps[i]["captured_at"], expiry,
                         veto_days=_CFG.gates.event_veto_days, bar_cache=_bc)
        sg = b.get(signal)
        if sg is None or sg.status != "OK" or b.spot in (None, 0.0):
            continue
        sc, sp = sg.score, b.spot
        for label, mins in horizons:
            if mins is None:                          # end of day
                date0 = caps[i]["captured_at"][:10]
                same = [c for c in caps if c["captured_at"][:10] == date0 and c["captured_at"] > caps[i]["captured_at"]]
                if same:
                    acc[label]["s"].append(sc); acc[label]["f"].append((same[-1]["spot"] - sp) / sp * 100)
            else:
                j = next((k for k in range(i + 1, len(caps)) if times[k] >= times[i] + timedelta(minutes=mins)), None)
                if j is not None:
                    acc[label]["s"].append(sc); acc[label]["f"].append((caps[j]["spot"] - sp) / sp * 100)

    curve = []
    # metrics from the ONE shared engine (HARD RULE 12) — values identical to before.
    from .analysis.signal_ensemble import signal_metrics as _sig_metrics, directional_sharpe as _dsharpe
    for label, _ in horizons:
        s = np.array(acc[label]["s"], dtype=float); f = np.array(acc[label]["f"], dtype=float)
        if len(s) < 3:
            curve.append({"horizon": label, "n": len(s), "ic": None, "hit_rate": None, "sharpe": None})
            continue
        m = _sig_metrics(s, f); sh = _dsharpe(s, f)
        curve.append({"horizon": label, "n": int(len(s)), "ic": m["ic"], "rank_ic": m["rank_ic"],
                      "hit_rate": m["hit"],
                      "sharpe": round(sh, 3) if sh is not None else None})
    return {"signal": signal, "expiry": expiry, "curve": curve,
            "note": "IC/hit/Sharpe of the signal across horizons — where its edge lives. "
                    "Descriptive only on thin history (D-MA-04)."}


def signal_effectiveness(expiry: str | None = None, window_days=None,
                         sample_minutes: float | None = None, max_points: int = 160,
                         vix_regime: str | None = None, source: str = "auto",
                         progress_cb=None) -> dict:
    """Signal × horizon effectiveness heatmap — the 'signal discovery' grid. In ONE
    pass over the snapshots (bundle evaluated once each), score every directional
    signal against EVERY forward horizon (5m/15m/30m/1h/2h/3h/EOD/next-day) and
    report IC, Rank IC (Spearman), spread, Sharpe and hit rate per cell. Reading a
    row tells you a signal's natural time scale (e.g. OI-change is short-horizon,
    RND-drift medium, trend long); the brightest cell is its best horizon."""
    import numpy as np
    from collections import defaultdict
    from datetime import datetime, timedelta
    from .signals import bundle as _sb
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    caps = _apply_window(da.list_captures(expiry=expiry), window_days)
    if len(caps) < 3:
        return {"error": "not enough snapshots in this window"}
    times = [datetime.fromisoformat(c["captured_at"].replace("Z", "+00:00")) for c in caps]
    ds = sorted((times[i + 1] - times[i]).total_seconds() / 60 for i in range(min(len(times) - 1, 200)))
    ds = [d for d in ds if d > 0]
    cadence = ds[len(ds) // 2] if ds else 1.0
    stride = max(1, round(sample_minutes / cadence)) if sample_minutes else max(1, len(caps) // max_points)

    horizons = [("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("2h", 120),
                ("3h", 180), ("eod", None), ("nextday", "ND")]
    by_date = defaultdict(list)
    for idx, c in enumerate(caps):
        by_date[c["captured_at"][:10]].append(idx)
    dates_sorted = sorted(by_date)
    next_date = {d: (dates_sorted[k + 1] if k + 1 < len(dates_sorted) else None)
                 for k, d in enumerate(dates_sorted)}
    names = _DIR_SIGNAL_NAMES
    acc = {s: {h[0]: {"s": [], "f": []} for h in horizons} for s in names}

    # "compute once, read many": read precomputed signal scores from the feature
    # store when present; fall back to live evaluation only for snapshots the store
    # doesn't have (or when source="live").
    from .features.extractor import vix_regime as _vr
    store = {} if source == "live" else _store_scores(expiry)
    used_store = 0
    _bc = _window_cache(da, caps) if source != "store" else None

    idxs = list(range(0, len(caps), stride))
    kept = 0
    for step, i in enumerate(idxs):
        if progress_cb:
            progress_cb(step, len(idxs))
        ts_i = caps[i]["captured_at"]
        if ts_i in store:                               # fast path — no re-evaluation
            sp = caps[i]["spot"]
            if sp in (None, 0.0):
                continue
            if vix_regime and _vr(caps[i].get("vix")) != vix_regime:
                continue
            scores = {s: store[ts_i].get(s) for s in names}
            used_store += 1
        elif source == "store":                         # store-only: skip misses
            continue
        else:                                           # live evaluation fallback
            b = _sb.evaluate(_CFG.db_path, ts_i, expiry,
                             veto_days=_CFG.gates.event_veto_days, bar_cache=_bc)
            if b.spot in (None, 0.0):
                continue
            if vix_regime and _vr(b.context.get("vix")) != vix_regime:
                continue
            sp = b.spot
            scores = {s: (b.get(s).score if b.get(s).status == "OK" else None) for s in names}
        kept += 1
        date0 = ts_i[:10]
        for label, mins in horizons:
            fwd = None
            if mins == "ND":
                nd = next_date.get(date0)
                if nd:
                    j = by_date[nd][-1]; fwd = (caps[j]["spot"] - sp) / sp * 100
            elif mins is None:                        # end of day
                later = [j for j in by_date[date0] if caps[j]["captured_at"] > caps[i]["captured_at"]]
                if later:
                    fwd = (caps[later[-1]]["spot"] - sp) / sp * 100
            else:
                j = next((k for k in range(i + 1, len(caps)) if times[k] >= times[i] + timedelta(minutes=mins)), None)
                if j is not None:
                    fwd = (caps[j]["spot"] - sp) / sp * 100
            if fwd is None:
                continue
            for s in names:
                if scores[s] is None:
                    continue
                acc[s][label]["s"].append(scores[s]); acc[s][label]["f"].append(fwd)
    if progress_cb:
        progress_cb(len(idxs), len(idxs))

    def _spearman(x, y):
        xr = np.argsort(np.argsort(x)).astype(float)
        yr = np.argsort(np.argsort(y)).astype(float)
        return float(np.corrcoef(xr, yr)[0, 1]) if xr.std() > 0 and yr.std() > 0 else 0.0

    matrix = {}
    for s in names:
        # per-cell metrics from the ONE shared definition (HARD RULE 12) — the agent
        # and this UI heatmap now compute IC / rank-IC / spread / sharpe / hit the same way.
        from .analysis.signal_ensemble import signal_metrics as _sig_metrics
        row = {label: _sig_metrics(acc[s][label]["s"], acc[s][label]["f"]) for label, _ in horizons}
        best = max((h for h, _ in horizons),
                   key=lambda h: abs(row[h]["ic"]) if row[h]["ic"] is not None else -1)
        matrix[s] = {"cells": row, "best_horizon": best if row[best]["ic"] is not None else None}
    return {"expiry": expiry, "signals": names, "horizons": [h[0] for h in horizons],
            "matrix": matrix, "sample_minutes": round(stride * cadence, 1),
            "n_evals": kept if vix_regime else len(idxs), "window_days": window_days,
            "vix_regime": vix_regime,
            "source": ("store" if used_store == kept and kept else
                       "live" if used_store == 0 else "mixed"),
            "from_store": used_store,
            "note": ("IC / Rank IC / Spread / Sharpe / Hit for every signal × horizon, one pass. "
                     + (f"Filtered to VIX regime '{vix_regime}' ({kept} snapshots). " if vix_regime else "")
                     + "Pick the metric to colour the map; the strongest cell in each row (☆) is "
                     "that signal's best horizon. Short-horizon signals light up left, slow ones "
                     "right. Descriptive only on thin history (D-MA-04); next-day needs ≥2 session "
                     "dates in the window.")}


def signal_backtest(signal: str, horizon_hours: float = 3.0,
                    expiry: str | None = None, window_days=None, max_points: int = 80,
                    progress_cb=None) -> dict:
    """Validate one signal: at each snapshot, compare its score to the NIFTY move
    `horizon_hours` later. Returns hit rate, correlation, avg forward move when
    the signal was bullish vs bearish, score-bucket → avg-forward-move bins, and
    the biggest misses (high conviction, wrong direction)."""
    import numpy as np
    from datetime import datetime, timedelta
    from .signals import bundle as _sb
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    caps = _apply_window(da.list_captures(expiry=expiry), window_days)
    if len(caps) < 3:
        return {"error": "not enough snapshots in this window"}

    def _parse(t):
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    times = [_parse(c["captured_at"]) for c in caps]
    horizon = timedelta(hours=horizon_hours)
    stride = max(1, len(caps) // max_points)

    pts = []
    _bc = _window_cache(da, caps)               # 8× faster: kill per-snapshot N+1
    idxs = list(range(0, len(caps), stride))
    for step, i in enumerate(idxs):
        if progress_cb:
            progress_cb(step, len(idxs))
        t0, ts = times[i], caps[i]["captured_at"]
        b = _sb.evaluate(_CFG.db_path, ts, expiry, veto_days=_CFG.gates.event_veto_days, bar_cache=_bc)
        sg = b.get(signal)
        if sg is None or sg.status != "OK" or b.spot in (None, 0.0):
            continue
        # forward spot: first capture at/after t0 + horizon
        j = next((k for k in range(i + 1, len(caps)) if times[k] >= t0 + horizon), None)
        if j is None:
            continue
        spot_now, spot_fut = b.spot, caps[j]["spot"]
        fwd = (spot_fut - spot_now) / spot_now * 100.0
        pts.append({"ts": ts, "score": round(sg.score, 3), "confidence": round(sg.confidence, 3),
                    "spot_now": round(spot_now, 1), "spot_future": round(spot_fut, 1),
                    "fwd_move_pts": round(spot_fut - spot_now, 1),
                    "fwd_ret_pct": round(fwd, 3),
                    "hit": bool((sg.score > 0 and fwd > 0) or (sg.score < 0 and fwd < 0))})
    if progress_cb:
        progress_cb(len(idxs), len(idxs))
    if len(pts) < 3:
        return {"error": f"not enough forward data for a {horizon_hours}h horizon on this expiry",
                "n": len(pts)}

    scores = np.array([p["score"] for p in pts])
    fwds = np.array([p["fwd_ret_pct"] for p in pts])
    active = np.abs(scores) >= 0.1                 # only count when the signal took a side
    hits = np.array([p["hit"] for p in pts])[active]
    corr = float(np.corrcoef(scores, fwds)[0, 1]) if len(pts) > 2 and scores.std() > 0 else 0.0

    bulls = fwds[scores >= 0.1]; bears = fwds[scores <= -0.1]
    buckets = [(-1.01, -0.5), (-0.5, -0.2), (-0.2, 0.2), (0.2, 0.5), (0.5, 1.01)]
    binned = []
    for lo, hi in buckets:
        m = (scores >= lo) & (scores < hi)
        binned.append({"bucket": f"{lo if lo>-1 else -1:g}..{hi if hi<1 else 1:g}",
                       "n": int(m.sum()),
                       "avg_fwd_ret_pct": round(float(fwds[m].mean()), 3) if m.any() else None})

    misses = sorted([p for p in pts if not p["hit"] and abs(p["score"]) >= 0.3],
                    key=lambda p: -abs(p["score"]))[:8]

    return {
        "signal": signal, "expiry": expiry, "horizon_hours": horizon_hours,
        "n": len(pts), "n_active": int(active.sum()),
        "hit_rate": round(float(hits.mean()), 3) if active.any() else None,
        "correlation": round(corr, 3),
        "avg_move_when_bullish_pct": round(float(bulls.mean()), 3) if bulls.size else None,
        "avg_move_when_bearish_pct": round(float(bears.mean()), 3) if bears.size else None,
        "buckets": binned, "misses": misses, "points": pts,
        "note": ("directional hit-rate + correlation of signal score vs forward NIFTY move; "
                 "descriptive only on thin history (D-MA-04)"),
    }


def chain_at(expiry: str | None = None, at: str | None = None,
             family: str | None = None) -> dict:
    """Option-chain snapshot as-of `at`: NIFTY spot + per-strike call/put LTP/OI.
    Feeds the manual strike builder — pick a time, see prices that were live then.
    If `family` is given, also returns a pre-filled leg `template` (default strikes
    + premiums) the user can then adjust to any tradable strike."""
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    da = DataAccess(_CFG.db_path)
    snap = da.chain_as_of(at, expiry) if at else None
    if snap is None:
        return {"error": f"no option-chain snapshot at/before {at} for this expiry"}
    rows = [{"strike": k,
             "call_ltp": round(snap.call_ltp.get(k, 0.0), 2),
             "put_ltp": round(snap.put_ltp.get(k, 0.0), 2),
             "call_oi": snap.call_oi.get(k, 0.0), "put_oi": snap.put_oi.get(k, 0.0)}
            for k in snap.strikes]
    out = {"expiry": expiry, "ts": snap.ts, "spot": snap.spot, "vix": snap.vix,
           "atm": snap.atm_strike(), "rows": rows}
    if family:
        from .signals.data_access import days_to_expiry as _dte
        st = constructor.build(family, snap, _CFG, dte_days=_dte(snap.ts, expiry))
        if st is None:
            out["template_error"] = f"{family} not priceable at that time"
        else:
            out["template"] = {
                "family": family,
                "legs": [{"action": "BUY" if g > 0 else "SELL",
                          "side": s, "strike": k, "sign": g,
                          "premium": round(st.premiums.get((s, k), 0.0), 2)}
                         for s, k, g in st.legs]}
    return out


def simulate(expiry: str | None = None, entry_ts: str = None,
             family: str | None = None, legs: list | None = None,
             exit_mode: str = "manage", roll_directional: bool = False,
             stop_loss: bool = False, stop_loss_rupees: float | None = None,
             cooldown_min: float | None = None, max_rolls: int | None = None,
             persist_near: int | None = None,
             harvest: bool = False, min_harvest_inr: float = 100.0,
             take_profit: bool = False, take_profit_frac: float = 0.6,
             max_manage: int | None = None,
             proactive: bool = False, proactive_lambda: float = 0.5,
             proactive_horizon_frac: float = 1.0, proactive_min_edge: float = 5.0,
             proactive_risk_drift: float = 1.0,
             proactive_max_harvests: int | None = None,
             proactive_max_harvest_debt: float | None = None,
             proactive_min_wing_buffer: float | None = None,
             proactive_min_width: float = 200.0,
             harvest_gate: str = "off",
             fut_max_lots: int = 2, fut_allow_reverse: bool = True,
             regime_expiry: str | None = None) -> dict:
    """Open one structure at a chosen past entry_ts and simulate it forward.
    `legs` (explicit [side,strike,sign] list) takes priority over `family`."""
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    if not entry_ts:
        return {"error": "entry_ts (past date/time) is required"}
    # A future's `expiry` is its (monthly) contract expiry, which is NOT an option
    # expiry — but the forecast optimizer's regime read needs an option chain. Resolve
    # a valid option expiry for that read (latest completed) unless one was given.
    if family in ("long_future", "short_future") and not regime_expiry:
        regime_expiry = _backtest_default_expiry() or _latest_expiry(None)
    norm_legs = [(str(l[0]), float(l[1]), int(l[2])) for l in legs] if legs else None
    return walkforward.simulate_one(_CFG, expiry, entry_ts, family=family, legs=norm_legs,
                                    fut_max_lots=fut_max_lots, fut_allow_reverse=fut_allow_reverse,
                                    regime_expiry=regime_expiry,
                                    exit_mode=exit_mode, roll_directional=roll_directional,
                                    stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                                    cooldown_min=cooldown_min, max_rolls=max_rolls,
                                    persist_near=persist_near,
                                    harvest=harvest, min_harvest_inr=min_harvest_inr,
                                    take_profit=take_profit, take_profit_frac=take_profit_frac,
                                    proactive=proactive, proactive_lambda=proactive_lambda,
                                    proactive_horizon_frac=proactive_horizon_frac,
                                    proactive_min_edge=proactive_min_edge,
                                    proactive_risk_drift=proactive_risk_drift,
                                    proactive_max_harvests=proactive_max_harvests,
                                    proactive_max_harvest_debt=proactive_max_harvest_debt,
                                    proactive_min_wing_buffer=proactive_min_wing_buffer,
                                    proactive_min_width=proactive_min_width,
                                    harvest_gate=harvest_gate,
                                    **({"max_manage": int(max_manage)} if max_manage else {}))


def simulate_stock(symbol: str, side: str = "long", qty: int = 1, entry_ts: str = None,
                   expiry: str | None = None, exit_mode: str = "manage",
                   stop_loss: bool = False, stop_loss_rupees: float | None = None,
                   take_profit: bool = False, take_profit_frac: float = 0.6) -> dict:
    """Backtest a long/short STOCK linearly — walk its own 1-min price path. `expiry`
    is just the exit/end date; the symbol must exist in price_bars (constituents do)."""
    if not entry_ts:
        return {"error": "entry_ts required"}
    from .signals.data_access import DataAccess
    from .backtest import walkforward as _wf
    da = DataAccess(_CFG.db_path)
    sign = 1 if str(side).lower().startswith("long") else -1
    return _wf._simulate_linear(_CFG, da, str(symbol).upper(), sign, int(qty), entry_ts,
                                expiry or _backtest_default_expiry(),
                                label=f"{side} {symbol}", kind="stock",
                                stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                                take_profit=take_profit, take_profit_frac=take_profit_frac)


def backtest_book_position(pos_id: str, entry_ts: str = None, expiry: str | None = None,
                           exit_mode: str = "manage", stop_loss: bool = False,
                           stop_loss_rupees: float | None = None,
                           take_profit: bool = False, take_profit_frac: float = 0.6) -> dict:
    """Backtest ONE position picked from the Desk Book — routes by kind (option
    strategy / future / stock) to the right engine. `entry_ts`/`expiry` are chosen
    for the replay; the book position supplies WHAT to trade (family/legs/symbol/side)."""
    pos = next((p for p in Book().list() if p["id"] == pos_id), None)
    if not pos:
        return {"error": "position not found in the book"}
    kind, pl = pos["kind"], pos.get("payload", {})
    common = dict(exit_mode=exit_mode, stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                  take_profit=take_profit, take_profit_frac=take_profit_frac)
    if kind == "option_strategy":
        # the structure carries its OWN option expiry; the request expiry only overrides it
        return simulate(expiry or pl.get("expiry"), entry_ts,
                        family=pl.get("family"), legs=pl.get("legs"), **common)
    end = expiry or pl.get("expiry")                 # a future/stock uses its OWN expiry/end date
    if kind == "future":
        sym = (pl.get("symbol") or "NIFTY")
        qv = pl.get("qty", 1) or 1
        sign = 1 if qv >= 0 else -1
        # the forecast optimizer (HOLD/EXIT/ADD/REDUCE/REVERSE) runs advisory-only
        # when the replay is in Manage mode.
        adv = (exit_mode == "manage")
        if sym.upper() == "NIFTY":                   # spot-tracked NIFTY future
            fam = "long_future" if sign > 0 else "short_future"
            return simulate(end, entry_ts, family=fam, proactive=adv, **common)
        # an actual futures SERIES — walk its own price bars linearly
        from .backtest import walkforward as _wf
        from .signals.data_access import DataAccess
        da = DataAccess(_CFG.db_path)
        return _wf._simulate_linear(
            _CFG, da, sym.upper(), sign, int(pl.get("lot_size", _CFG.lot_size)), entry_ts, end,
            label=f"{'long' if sign > 0 else 'short'} {sym}", kind="future",
            stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
            take_profit=take_profit, take_profit_frac=take_profit_frac,
            advisory=adv, regime_expiry=(_backtest_default_expiry() or _latest_expiry(None)))
    if kind == "stock":
        qty = pl.get("qty", 1) or 1
        return simulate_stock(pl.get("symbol"), "long" if qty >= 0 else "short", abs(qty),
                              entry_ts, end, **common)
    return {"error": f"can't backtest position kind '{kind}'"}


def simulate_compare(expiry: str | None = None, entry_ts: str = None,
                     family: str | None = None, legs: list | None = None,
                     exit_mode: str = "manage", stop_loss: bool = False,
                     stop_loss_rupees: float | None = None,
                     lam: float = 0.5, risk_drift: float = 0.0,
                     max_harvests: int | None = 2,
                     max_harvest_debt: float | None = 100.0,
                     max_rolls: int | None = None, cooldown_min: float | None = None,
                     persist_near: int | None = None, min_harvest_inr: float = 100.0) -> dict:
    """Run the SAME entry four ways (A/B/C/D harvest strategies) and return a
    per-strategy stats table — the single-trade side-by-side comparison."""
    order = ["A_current", "B_never", "C_optimizer", "D_opt_budget"]
    configs = {"A_current": (True, "off"), "B_never": (False, "off"),
               "C_optimizer": (True, "optimizer"), "D_opt_budget": (True, "both")}
    rows, by_ts = [], {}
    for name in order:
        hv, gate = configs[name]
        r = simulate(expiry, entry_ts, family, legs, exit_mode=exit_mode,
                     stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                     max_rolls=max_rolls, cooldown_min=cooldown_min,
                     persist_near=persist_near, min_harvest_inr=min_harvest_inr,
                     harvest=hv, harvest_gate=gate, proactive=(gate != "off"),
                     proactive_lambda=lam, proactive_risk_drift=risk_drift,
                     proactive_max_harvests=(max_harvests if gate == "both" else None),
                     proactive_max_harvest_debt=(max_harvest_debt if gate == "both" else None))
        if r.get("error"):
            rows.append({"strategy": name, "error": r["error"]}); continue
        s = r.get("stats", {}) or {}
        rows.append({"strategy": name, "gate": gate,
                     "total_pnl_inr": s.get("total_pnl_inr"),
                     "total_return_pct": s.get("total_return_pct"),
                     "max_drawdown_pct": s.get("max_drawdown_pct"),
                     "n_adjustments": s.get("n_adjustments"),
                     "n_harvests": s.get("n_harvests"),
                     "n_vetoes": s.get("n_vetoes")})
        # collect per-timestamp action for the aligned grid
        for d in r.get("decisions", []):
            slot = by_ts.setdefault(d["ts"], {"ts": d["ts"], "spot": d.get("spot")})
            slot[name] = {"action": d.get("action"), "mark": d.get("mark_pnl"),
                          "roll": d.get("roll"), "reason": d.get("reason"),
                          "orders": d.get("orders"),
                          "signals": (d.get("signal") or {}).get("regime"),
                          "net_score": (d.get("signal") or {}).get("net_score")}

    # aligned timeline: one row per timestamp, a cell per strategy. `diverge` flags
    # rows where the four strategies did NOT all take the same action — the moments
    # that actually matter.
    timeline = []
    for ts in sorted(by_ts):
        slot = by_ts[ts]
        acts = {slot.get(n, {}).get("action") for n in order if slot.get(n)}
        slot["diverge"] = len(acts) > 1
        timeline.append(slot)
    return {"entry_ts": entry_ts, "expiry": expiry, "rows": rows,
            "timeline": timeline, "order": order}


def compare_harvest(expiry: str | None = None, window_days: float | None = None,
                    exit_mode: str = "manage", freq_minutes: float | None = None,
                    stop_loss: bool = False, stop_loss_rupees: float | None = None,
                    lam: float = 0.5, risk_drift: float = 0.0,
                    max_harvests: int | None = 2, max_harvest_debt: float | None = 100.0,
                    mps_benchmark: str = "net") -> dict:
    """A/B/C/D harvest experiment: run the SAME window four ways and compare —
      A Current      : harvest whenever the rule fires
      B Never        : no harvesting (defend threatened wings only)
      C Optimizer    : harvest only when the optimizer says it beats HOLD
      D Opt+Budget   : optimizer gate AND a harvest budget (max/day, max debt)
    Returns a metrics row per strategy so risk-adjusted returns can be compared."""
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    common = dict(exit_mode=exit_mode, freq_minutes=freq_minutes, window_days=window_days,
                  stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                  mps_benchmark=mps_benchmark)
    configs = {
        "A_current":   dict(harvest=True,  harvest_gate="off"),
        "B_never":     dict(harvest=False, harvest_gate="off"),
        "C_optimizer": dict(harvest=True,  harvest_gate="optimizer",
                            harvest_lambda=lam, harvest_risk_drift=risk_drift),
        "D_opt_budget": dict(harvest=True, harvest_gate="both",
                             harvest_lambda=lam, harvest_risk_drift=risk_drift,
                             harvest_max=max_harvests, harvest_max_debt=max_harvest_debt),
    }
    rows = []
    shared: dict = {}                      # regimes/bars/suggestions shared across the 4 runs
    for name, extra in configs.items():
        res = walkforward.run(_CFG, expiry, **common, **extra, _shared_caches=shared)
        m = res.metrics
        rows.append({
            "strategy": name,
            "net_pnl_rupees": m.get("total_pnl_rupees"),
            "hit_rate": m.get("hit_rate"),
            "max_drawdown_pts": m.get("max_drawdown_pts"),
            "n_trades": m.get("n_trades"),
            "n_harvests": m.get("n_harvests"),
            "n_harvest_vetoes": m.get("n_harvest_vetoes"),
            "mps0_capture_pct": m.get("capture_pct"),
            "total_cost_inr": m.get("total_cost_inr"),
        })
    return {"expiry": expiry, "rows": rows,
            "params": {"lambda": lam, "risk_drift": risk_drift,
                       "max_harvests": max_harvests, "max_harvest_debt": max_harvest_debt}}


def backtest(mode: str = "auto", expiry: str | None = None,
             exit_mode: str = "manage", hold: int = 2,
             freq_minutes: float | None = None,
             roll_directional: bool = False,
             window_days: float | None = None,
             stop_loss: bool = False,
             stop_loss_rupees: float | None = None,
             cooldown_min: float | None = None, max_rolls: int | None = None,
             persist_near: int | None = None,
             harvest: bool = False, min_harvest_inr: float = 100.0,
             take_profit: bool = False, take_profit_frac: float = 0.6,
             max_manage: int | None = None,
             min_edge_cost_mult: float = 0.0, mps_benchmark: str = "off") -> dict:
    # backtest defaults to the most recent COMPLETED expiry (full history),
    # not the current in-progress one.
    expiry = expiry or _backtest_default_expiry()
    if not expiry:
        return {"error": "no expiries in DB"}
    if mode == "book":
        res = portfolio_bt.run_book_backtest(_CFG, Book().list(), expiry,
                                             freq_minutes=freq_minutes)
        return {"mode": "book", "expiry": expiry, **res}
    cfg = _cfg_with(min_edge_cost_mult)
    res = walkforward.run(cfg, expiry, exit_mode=exit_mode, hold=hold,
                          freq_minutes=freq_minutes,
                          roll_directional=roll_directional,
                          window_days=window_days, stop_loss=stop_loss,
                          stop_loss_rupees=stop_loss_rupees,
                          cooldown_min=cooldown_min, max_rolls=max_rolls,
                          persist_near=persist_near,
                          harvest=harvest, min_harvest_inr=min_harvest_inr,
                          take_profit=take_profit, take_profit_frac=take_profit_frac,
                          mps_benchmark=mps_benchmark,
                          **({"max_manage": int(max_manage)} if max_manage else {}))
    return {"mode": "auto", "expiry": expiry, "exit_mode": exit_mode,
            "metrics": res.metrics, "trades": res.trades,
            "n_decisions": len(res.decisions)}
