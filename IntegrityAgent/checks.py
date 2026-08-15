"""
IntegrityAgent/checks.py
========================
The invariant / single-source-of-truth checks for the whole NiftyOptions codebase.

Each check is a zero-arg function returning (name, ok: bool, detail: str). They come
in two flavours:
  * runtime  — import the modules and assert a live invariant (roster consistency,
               engine formula correctness, lot-size equality).
  * static   — read a source file and assert a structural rule (no duplicate corr
               loop, no hardcoded signal list / lot literal), for the few modules
               that can't be imported cheaply (e.g. backend/main.py's heavy deps).

Register a check with the @check decorator; the runner and the pytest bridge both
iterate `CHECKS`.
"""
from __future__ import annotations
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS: list = []


def check(fn):
    CHECKS.append(fn)
    return fn


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── single source of truth: signal roster ────────────────────────────────────
@check
def registry_weights_consistent():
    from strategy_framework.config.settings import SignalWeights
    from strategy_framework.signals import registry as R
    w = SignalWeights().as_dict()
    roster_ok = list(w) == R.directional_names()
    s = round(sum(w.values()), 6)
    return ("SignalWeights roster + sum derive from registry",
            roster_ok and abs(s - 1.0) < 1e-9,
            f"roster_match={roster_ok}, weight_sum={s} (want 1.0)")


@check
def rosters_all_from_registry():
    from strategy_framework.strategy import regime
    from strategy_framework import api
    from strategy_framework.backtest import walkforward
    from strategy_framework.signals import registry as R
    ok = (set(regime._DIRECTIONAL) == set(R.blended_names())
          and set(regime._MOMENTUM_FAMILY) == set(R.momentum_names())
          and set(api._DIR_SIGNAL_NAMES) == set(R.directional_names())
          and set(walkforward._DIR_SIGNALS) == set(R.blended_names()))
    return ("regime / api / walkforward rosters all == registry", ok,
            "no hardcoded signal list drifted from signals/registry.py")


@check
def bundle_evaluates_whole_registry():
    from strategy_framework.signals import registry as R
    src = _src("strategy_framework/signals/bundle.py")
    ok = "_registry.REGISTRY" in src and "bundle.add(heavyweight_leadership" not in src
    return ("bundle.py iterates the registry (no hardcoded add list)", ok,
            f"{len(R.REGISTRY)} signals declared once in the registry")


# ── single source of truth: correlation + metrics engine ─────────────────────
@check
def corr_matrix_full_correct():
    from strategy_framework.analysis.signal_ensemble import corr_matrix_full
    cols = {"a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10], "c": [5, 3, 4, 1, 2]}
    _, mat, _ = corr_matrix_full(cols)
    ok = abs(mat[0][1] - 1.0) < 1e-9 and abs(mat[0][0] - 1.0) < 1e-9
    return ("engine corr_matrix_full correctness", ok, f"corr(a,b)={round(mat[0][1], 3)} (want 1.0)")


@check
def signal_metrics_matches_numpy():
    import numpy as np
    from strategy_framework.analysis.signal_ensemble import signal_metrics
    x = [0.1, 0.5, -0.3, 0.8, -0.6, 0.2, 0.4, -0.7]
    y = [0.2, 0.4, -0.1, 0.9, -0.5, 0.0, 0.3, -0.4]
    m = signal_metrics(x, y)
    ref = round(float(np.corrcoef(x, y)[0, 1]), 3)
    ok = m["ic"] == ref and set(m) == {"n", "ic", "rank_ic", "spread", "sharpe", "hit"}
    return ("engine signal_metrics IC == np.corrcoef & full metric set", ok,
            f"ic={m['ic']} ref={ref}")


@check
def api_correlation_uses_engine():
    src = _src("strategy_framework/api.py")
    imported = "from .analysis.signal_ensemble import corr_matrix_full" in src
    # the old inline pairwise loop used np.corrcoef(av, bv); it must be gone
    no_dup_loop = "np.corrcoef(av, bv)" not in src
    return ("api.signal_correlation routes through engine (no inline corr loop)",
            imported and no_dup_loop, f"imports_engine={imported}, inline_loop_gone={no_dup_loop}")


@check
def api_metrics_use_engine():
    src = _src("strategy_framework/api.py")
    ok = "signal_metrics as _sig_metrics" in src and "directional_sharpe as _dsharpe" in src
    return ("api effectiveness / scoreboard / horizon-curve use engine metrics", ok,
            "signal_metrics + directional_sharpe imported in api.py")


# ── single source of truth: contract params (lot size) ───────────────────────
@check
def lot_size_single_source():
    from exchange_config import NIFTY_LOT_SIZE
    from strategy_framework.config.settings import LOT_SIZE, FrameworkConfig
    from backend.quant.risk_budget import RiskConfig
    vals = {NIFTY_LOT_SIZE, LOT_SIZE, FrameworkConfig().lot_size, RiskConfig().lot_size}
    ok = vals == {65}
    return ("lot size == 65 from exchange_config everywhere", ok, f"values={sorted(vals)}")


@check
def no_stray_lot_literals():
    bad = []
    for path in ["strategy_framework/strategy/directional.py",
                 "strategy_framework/backtest/metrics.py",
                 "backend/quant/risk_budget.py", "backend/quant/portfolio.py",
                 "backend/quant/nse_csv_loader.py", "chain_store.py"]:
        for line in _src(path).splitlines():
            if re.search(r"(lot_size|lot)[^=\n#]*=\s*\(?\s*(75|65)\b", line) and "NIFTY_LOT_SIZE" not in line:
                bad.append(f"{path}: {line.strip()[:60]}")
    return ("no hardcoded lot-size literals in production modules", not bad,
            f"offenders={bad}" if bad else "all import NIFTY_LOT_SIZE")


# ── single source of truth: index-volume reconstruction ──────────────────────
@check
def index_volume_shared():
    missing = [m for m in ("technical_momentum", "vwap", "rel_volume")
               if "per_bar_index_volume" not in _src(f"strategy_framework/signals/{m}.py")]
    return ("index-volume reconstruction shared (per_bar_index_volume)", not missing,
            f"not importing shared helper: {missing}" if missing else
            "technical_momentum / vwap / rel_volume all use the one helper")


@check
def pinned_zero_signals_have_no_weight():
    """Signals whose data isn't ready (data_ready=False) must stay at weight 0 —
    in the config AND in anything the Calibration Agent could propose."""
    from strategy_framework.signals import registry as R
    from strategy_framework.config.settings import SignalWeights
    w = SignalWeights().as_dict()
    pinned = R.pinned_zero_names()
    offenders = [n for n in pinned if w.get(n, 0.0) != 0.0]
    cal_src = _src("CalibrationAgent/calibrate.py")
    masked = "_apply_pins" in cal_src and "PINNED_ZERO" in cal_src
    return ("data-not-ready signals pinned at weight 0 (config + calibration)",
            not offenders and masked,
            f"pinned={pinned}, nonzero={offenders}, calibrator_masks={masked}")


@check
def blend_math_shared():
    """regime (live) and the Calibration Agent (offline) must use ONE blend, else
    calibration validates a formula the desk doesn't trade."""
    reg_src = _src("strategy_framework/strategy/regime.py")
    cal_src = _src("CalibrationAgent/calibrate.py")
    reg_ok = "from .blend import blend_net" in reg_src and "num += eff_w * sig.score" not in reg_src
    cal_ok = "from strategy_framework.strategy.blend import blend_net" in cal_src
    return ("regime + CalibrationAgent share strategy/blend.py", reg_ok and cal_ok,
            f"regime_uses_shared={reg_ok}, calibrator_uses_shared={cal_ok}")


@check
def calibration_is_advisory_only():
    """The calibration loop must never mutate SignalWeights (HARD RULE 11)."""
    src = _src("CalibrationAgent/calibrate.py") + _src("CalibrationAgent/run.py")
    writes = ("settings.SignalWeights =" in src or "cfg.weights =" in src.replace("c.weights =", ""))
    return ("calibration is advisory-only (never writes SignalWeights)", not writes,
            "no global weight mutation found" if not writes else "found a weight write!")


@check
def registry_self_validates():
    from strategy_framework.signals import registry as R
    try:
        v = R.validate()
        ok = v["blended_weight_sum"] == 1.0 and v["directional"] == 22
        return ("signals/registry.validate() passes", ok, str(v))
    except Exception as e:
        return ("signals/registry.validate() passes", False, f"raised {e}")


@check
def frontend_no_hardcoded_signal_roster():
    """The UI must not declare signal names — it reads them from
    /api/strategy/config (→ signals/registry.py). This is the check that would have
    caught the futures_* signals silently missing from the Signal view: the frontend
    held its own 12-entry list while the registry had grown to 15 directional.

    Rule: no file under src/ may mention 2+ registry signal names in EXECUTABLE code.
    Comments and the roster module itself are exempt (they legitimately name signals
    for documentation), as is a single incidental reference."""
    from strategy_framework.signals import registry as R
    names = [s.name for s in R.REGISTRY]
    src_dir = os.path.join(ROOT, "src")
    exempt = {os.path.join(src_dir, "lib", "signalRoster.ts")}
    offenders = []
    for dirpath, _dirs, files in os.walk(src_dir):
        if "node_modules" in dirpath:
            continue
        for fn in files:
            if not fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue
            path = os.path.join(dirpath, fn)
            if path in exempt:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            # strip // line comments and /* block comments */ before matching
            code = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
            code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
            hits = sorted({n for n in names if n in code})
            if len(hits) >= 2:
                offenders.append(f"{os.path.relpath(path, ROOT)} names {hits}")
    return ("frontend holds no hardcoded signal roster", not offenders,
            "; ".join(offenders) if offenders
            else f"scanned src/ against {len(names)} registry names — clean")


@check
def config_endpoint_serves_full_roster():
    """/api/strategy/config must serve EVERY registry signal, with the fields the UI
    renders. If this drifts, a signal exists in the engine but is invisible in the UI —
    exactly the failure this whole wiring exists to prevent."""
    from strategy_framework.api import config_summary
    from strategy_framework.signals import registry as R
    c = config_summary()
    served = [s["name"] for s in c.get("signals", [])]
    complete = served == [s.name for s in R.REGISTRY]
    required = {"name", "label", "family", "kind", "weight", "blended",
                "data_ready", "method", "detail_keys", "feature_key"}
    fields_ok = all(required <= set(s) for s in c.get("signals", []))
    lot_ok = c.get("lot_size") == __import__("exchange_config").NIFTY_LOT_SIZE
    return ("config endpoint serves the full registry roster",
            complete and fields_ok and lot_ok,
            f"served={len(served)}/{len(R.REGISTRY)}, fields_ok={fields_ok}, "
            f"lot_size_matches_exchange_config={lot_ok}")


@check
def momentum_window_single_source():
    """The price-return lookback must come from config.MomentumWindow via ctx — no
    signal may carry a hardcoded window or tanh scale. Also asserts BACKWARD
    COMPATIBILITY: at each signal's historical window the derived scale must equal
    the literal it replaced, so this refactor changed no behaviour at those windows."""
    from strategy_framework.config.settings import MomentumWindow
    m = MomentumWindow()
    hist = {"rel_volume": (15, 0.12), "futures_flow": (15, 0.12),
            "vol_index": (30, 0.15), "heavyweight_leadership": (60, 0.60)}
    bad = [f"{s}@{n}: {m.scale_for(s, n):.6f} != {old}"
           for s, (n, old) in hist.items() if abs(m.scale_for(s, n) - old) > 1e-9]
    # no literal `scale=0.12`-style constants left in the four signal modules
    lits = []
    for s in hist:
        src = _src(f"strategy_framework/signals/{s}.py")
        if re.search(r"scale\s*=\s*0\.\d+", src):
            lits.append(s)
    return ("momentum window + scale derive from config (and are back-compatible)",
            not bad and not lits,
            f"scale_mismatches={bad or 'none'}, modules_with_literal_scale={lits or 'none'}")


@check
def momentum_window_is_global_and_live():
    """The return window must be ONE setting: changing it has to reach configs that
    were constructed at import time (settings.DEFAULT, api._CFG), not just newly
    built ones. This is the 'I changed the setting and nothing happened' bug —
    MomentumWindow.bars() must read config/runtime.py at CALL time."""
    from strategy_framework.config.settings import DEFAULT
    from strategy_framework.config import runtime
    import strategy_framework.api as _api
    before = runtime.get_lookback_min()
    try:
        target = 60 if before != 60 else 30
        runtime.set_lookback_min(target)
        live = (DEFAULT.momentum.bars() == target and _api._CFG.momentum.bars() == target)
        persisted = runtime.get_lookback_min() == target
    finally:
        runtime.set_lookback_min(before)
    restored = DEFAULT.momentum.bars() == before
    return ("return window is one global, live setting", live and persisted and restored,
            f"import-time configs follow={live}, persisted={persisted}, restored={restored}")


@check
def feature_store_stamps_momentum_window():
    """Stored feature rows must record the lookback they were computed at — signal
    scores are a function of the window, so a mixed-vintage store silently corrupts
    every IC / correlation number."""
    from strategy_framework.features import store as fs
    ok = hasattr(fs, "window_audit") and "lookback_bars" in _src("strategy_framework/features/store.py")
    return ("feature store stamps the momentum window", ok,
            "upsert stamps lookback_bars; store.window_audit() reports staleness")


@check
def market_health_is_data_honest():
    """The daily gauge must never fabricate a moving average on thin data: a 200-DMA
    sub-score on < 200 sessions has to report data_ready=False with a null score, and
    the headline must be normalised over AVAILABLE points with an honest coverage_pct.
    This is the market-health analogue of PRIOR-until-calibrated (D-MA-04)."""
    import sqlite3, tempfile, os as _os
    from strategy_framework.market_health import trend as T
    fd, db = tempfile.mkstemp(suffix=".db"); _os.close(fd)
    try:
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                    "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
        for i in range(30):                      # 30 sessions: no 200-DMA possible
            con.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1d',?,?,?,?,?,0)",
                        (f"2026-01-{i+1:02d}T00:00:00Z", 100 + i, 101 + i, 99 + i, 100 + i))
        con.commit(); con.close()
        it = T.index_trend(db, "NIFTY")["sub"]["px_vs_200dma"]
        rep = T.market_health(db)
        ok = (it["data_ready"] is False and it["score01"] is None
              and rep["coverage_pct"] < 100 and rep.get("prior") is True)
        return ("market-health never fabricates an MA on thin data", ok,
                f"px_vs_200dma pending={not it['data_ready']}, "
                f"coverage={rep['coverage_pct']}%, prior={rep.get('prior')}")
    finally:
        try: _os.unlink(db)
        except OSError: pass


@check
def market_health_points_single_source():
    """Every scored component must draw its weight from the ONE POINTS dict — no
    stray point budget hardcoded in a layer function (DRY / calibratable-later)."""
    from strategy_framework.market_health.trend import (
        POINTS, _INDEX_KEYS, _BREADTH_KEYS, _SECTOR_KEYS, _LEADERSHIP_KEYS)
    missing = [k for k in (*_INDEX_KEYS, *_BREADTH_KEYS, *_SECTOR_KEYS, *_LEADERSHIP_KEYS)
               if k not in POINTS]
    return ("market-health point budget is single-source", not missing,
            f"missing_from_POINTS={missing or 'none'}, total={sum(POINTS.values())}")


@check
def oi_classifier_single_source():
    """The futures price×OI positioning rule must live in ONE place
    (backend.quant.intraday_oi._label — also behind the Macro Shock view). The
    strategy adapter must DELEGATE to it, not re-implement the sign-pair logic, and
    the phase grid must reuse intraday_oi.analyze rather than its own leg loop."""
    adapter = _src("strategy_framework/signals/futures_oi.py")
    delegates = "from backend.quant.intraday_oi import _label" in adapter
    # the adapter must NOT contain its own regime string literals as return values
    reimpl = ('"short_buildup"' in adapter and "return" in adapter
              and "_oi_label" not in adapter)
    api_src = _src("strategy_framework/api.py")
    grid_reuses = "intraday_oi as _ioi" in api_src and "_ioi.analyze(" in api_src
    ok = delegates and not reimpl and grid_reuses
    return ("futures-OI classifier is single-source (intraday_oi)", ok,
            f"adapter_delegates={delegates}, no_reimpl={not reimpl}, "
            f"phase_grid_reuses_analyze={grid_reuses}")


def run_all() -> list[tuple[str, bool, str]]:
    """Execute every check, catching exceptions as failures."""
    out = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as e:  # a check that errors is a failure, not a crash
            out.append((fn.__name__, False, f"raised {type(e).__name__}: {e}"))
    return out
