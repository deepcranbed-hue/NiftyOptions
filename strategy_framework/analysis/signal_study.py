"""
strategy_framework/analysis/signal_study.py
============================================
Framework ADAPTER for the instrument-agnostic `signal_ensemble` engine.

It pulls, from the project DB:
  * each directional signal's SCORE at every snapshot (feature store fast-path,
    live bundle-evaluation fallback), and
  * the forward RETURN of a chosen TARGET instrument (the index, a constituent
    stock, or an index future — that's the "use it for stock / index / future"
    axis: same signals, you pick what they're scored against),

aligns them, and hands them to `signal_ensemble.analyze_ensemble`, which does the
independence + weight math with no knowledge of this framework.

    study(target="NIFTY", horizon="60m")      -> ensemble report dict
"""
from __future__ import annotations
from datetime import datetime, timedelta

from ..signals.data_access import DataAccess
from ..signals import bundle as _sb
from ..config.settings import FrameworkConfig, SignalWeights
from . import signal_ensemble as eng

_CFG = FrameworkConfig()

# The signal roster is NOT hardcoded — it's derived from SignalWeights (the ONE
# config that defines which signals participate in the directional blend). Add a new
# directional signal there (which you must do anyway to give it a weight, even 0.0)
# and it is picked up here automatically. Non-directional gates/overlays
# (time_of_day, earnings_events, derisk_*) have no weight, so they're correctly
# excluded. Single source of truth — see CLAUDE.md / HARD RULE 12.
SIGNALS = list(SignalWeights().as_dict().keys())

_HORIZONS = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "2h": 120, "3h": 180}


def _parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _cadence_min(times) -> float:
    """Median spacing between consecutive snapshots, in minutes (defaults to 1.0)."""
    ds = sorted((times[i + 1] - times[i]).total_seconds() / 60.0
                for i in range(min(len(times) - 1, 300)))
    ds = [d for d in ds if d > 0]
    return ds[len(ds) // 2] if ds else 1.0


def _target_price(da, target, ts, cap_spot):
    """Price of `target` as-of ts. NIFTY uses the capture's spot; anything else is
    read from its own 1m bars (last bar at/before ts)."""
    if target.upper() == "NIFTY":
        return cap_spot
    b = da.bars(target, "1m", end=ts, limit=1)
    return float(b[-1]["close"]) if b else None


def _default_expiry(da):
    exps = da.expiries()
    for e in reversed(exps):
        if da.list_captures(expiry=e):
            return e
    return exps[-1] if exps else None


_ALL_HORIZONS = ["5m", "15m", "30m", "60m", "2h", "3h"]


def _gather(target, expiry, horizons, window_days, source, db_path, sample_minutes=None):
    """Load aligned signal scores + the target's forward return at EACH horizon.
    Returns (scores_by_signal, fwd_by_horizon, meta). Shared by study() and
    study_horizons() — one loader, no duplication.

    `sample_minutes`: if set, enter only every ~`sample_minutes` (strided) so that
    forward windows overlap less — trading raw row count for INDEPENDENCE. None =
    every snapshot (default; matches the old behaviour exactly)."""
    da = DataAccess(db_path or _CFG.db_path)
    expiry = expiry or _default_expiry(da)
    if not expiry:
        return None, None, {"error": "no expiries in DB"}
    hz = {h: _HORIZONS[h] for h in horizons if h in _HORIZONS}
    if not hz:
        return None, None, {"error": f"unknown horizon(s): {horizons}"}
    caps = da.list_captures(expiry=expiry)
    if window_days:
        dates = sorted({c["captured_at"][:10] for c in caps})
        keep = set(dates[-int(window_days):])
        caps = [c for c in caps if c["captured_at"][:10] in keep]
    if len(caps) < 5:
        return None, None, {"error": f"only {len(caps)} snapshots for {expiry} — too few"}
    times = [_parse(c["captured_at"]) for c in caps]

    cadence = _cadence_min(times)
    stride = max(1, round(sample_minutes / cadence)) if sample_minutes else 1
    sample_min_eff = round(stride * cadence, 1)

    store = {}
    if source in ("store", "auto"):
        try:
            from ..features import store as _fs
            for r in _fs.query(_CFG.db_path, expiry, limit=100000):
                ts = r.get("ts")
                if ts:
                    store[ts] = {s: r.get(f"sig_{s}_score") for s in SIGNALS
                                 if isinstance(r.get(f"sig_{s}_score"), (int, float))}
        except Exception:
            store = {}
    _bc = None
    if source in ("live", "auto"):
        try:
            from ..signals.data_access import BarCache, CROSS_ASSET_SYMBOLS
            start = (times[0] - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            syms = list(dict.fromkeys(da.available_symbols("1m") + CROSS_ASSET_SYMBOLS))
            _bc = BarCache(_CFG.db_path, syms, start=start, end=caps[-1]["captured_at"])
        except Exception:
            _bc = None

    scores: dict[str, list] = {s: [] for s in SIGNALS}
    fwd: dict[str, list] = {h: [] for h in hz}
    used = 0
    for i in range(0, len(caps), stride):          # strided entries → less overlap
        c = caps[i]
        ts = c["captured_at"]
        p0 = _target_price(da, target, ts, c.get("spot"))
        if not p0:
            continue
        fr = {}
        for h, mins in hz.items():
            j = next((k for k in range(i + 1, len(caps)) if times[k] >= times[i] + timedelta(minutes=mins)), None)
            p1 = _target_price(da, target, caps[j]["captured_at"], caps[j].get("spot")) if j is not None else None
            fr[h] = (p1 / p0 - 1.0) * 100.0 if p1 else None
        if all(v is None for v in fr.values()):
            continue
        row = store.get(ts)
        if row is None and source != "store":
            b = _sb.evaluate(_CFG.db_path, ts, expiry, veto_days=_CFG.gates.event_veto_days, bar_cache=_bc)
            row = {s: (b.get(s).score if b.get(s) and b.get(s).status == "OK" else None) for s in SIGNALS}
        if not row:
            continue
        for s in SIGNALS:
            scores[s].append(row.get(s))
        for h in hz:
            fwd[h].append(fr[h])
        used += 1
    meta = {"target": target, "expiry": expiry, "n": used,
            "sample_minutes": sample_min_eff, "cadence_min": round(cadence, 2),
            "horizon_min": hz,
            "source": "store" if (source == "store" or (source == "auto" and not _bc)) else source}
    return scores, fwd, meta


def _effective_n(n: int, sample_min: float, horizon_min: float):
    """Overlap-adjusted independent sample count. When you sample FINER than the
    forward horizon, consecutive windows overlap and share most of the same future,
    so the true independent count ≈ n × (sample_interval / horizon). If you sample
    at/above the horizon the windows don't overlap and effective_n = n."""
    overlap = sample_min < horizon_min
    eff = n if not overlap else round(n * sample_min / horizon_min, 1)
    return eff, overlap


def study(target: str = "NIFTY", expiry: str | None = None, horizon: str = "60m",
          window_days: float | None = None, source: str = "auto",
          cluster_threshold: float = 0.6, db_path: str | None = None,
          sample_minutes: float | None = None, min_coverage: float = 0.8,
          common_sample: bool = False) -> dict:
    """Ensemble study for `target` at one forward `horizon` — independence + IC +
    three weight vectors. `source`: 'store' | 'live' | 'auto'. `sample_minutes`
    spaces entries to reduce overlap (None = every snapshot)."""
    scores, fwd, meta = _gather(target, expiry, [horizon], window_days, source, db_path, sample_minutes)
    if scores is None:
        return meta
    if meta["n"] < 5:
        return {"error": f"only {meta['n']} aligned observations — widen window or check data",
                "target": target, "expiry": meta["expiry"]}
    rep = eng.analyze_ensemble(scores, fwd[horizon], cluster_threshold=cluster_threshold,
                               min_coverage=min_coverage, common_sample=common_sample)
    eff, overlap = _effective_n(meta["n"], meta["sample_minutes"], meta["horizon_min"][horizon])
    rep.update({"target": target, "expiry": meta["expiry"], "horizon": horizon,
                "source": meta["source"], "sample_minutes": meta["sample_minutes"],
                "effective_n": eff, "overlap": overlap})
    return rep


def study_horizons(target: str = "NIFTY", expiry: str | None = None,
                   horizons: list | None = None, window_days: float | None = None,
                   source: str = "auto", db_path: str | None = None,
                   sample_minutes: float | None = None) -> dict:
    """Signal × horizon skill grid (IC / rank-IC / spread / sharpe / hit at 5m…3h) —
    the multi-horizon comparison, from the ONE shared metric definition. Reports the
    overlap-adjusted EFFECTIVE independent sample count per horizon."""
    horizons = horizons or _ALL_HORIZONS
    scores, fwd, meta = _gather(target, expiry, horizons, window_days, source, db_path, sample_minutes)
    if scores is None:
        return meta
    if meta["n"] < 5:
        return {"error": f"only {meta['n']} aligned observations", "target": target}
    live = {s: v for s, v in scores.items() if any(x is not None for x in v)}
    excluded = [s for s in scores if s not in live]
    grid = eng.metrics_by_horizon(live, fwd)
    hs = [h for h in horizons if h in _HORIZONS]
    eff_n, overlap = {}, {}
    for h in hs:
        eff_n[h], overlap[h] = _effective_n(meta["n"], meta["sample_minutes"], meta["horizon_min"][h])
    return {"target": target, "expiry": meta["expiry"], "n_obs": meta["n"],
            "source": meta["source"], "horizons": hs, "grid": grid,
            "excluded_no_data": excluded, "sample_minutes": meta["sample_minutes"],
            "effective_n": eff_n, "overlap": overlap,
            "note": "signal × horizon skill grid. n_obs are raw rows; effective_n is the "
                    "overlap-adjusted INDEPENDENT count per horizon (trust that, not n_obs). "
                    "Descriptive/PRIOR until ≥60 sessions (D-MA-04)."}
