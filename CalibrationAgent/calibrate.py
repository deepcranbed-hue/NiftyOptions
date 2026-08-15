"""
CalibrationAgent/calibrate.py
=============================
Walk-forward calibration of the signal blend weights.

The loop, per fold:
    TRAIN window (N sessions)  → run the ensemble study → candidate weight vectors
                                  (inverse_redundancy, mv_ic, family) + the INCUMBENT
    TEST window (M sessions)   → score every candidate OUT-OF-SAMPLE on data the
                                  proposal never saw: blended-signal IC, hit rate,
                                  and (opt-in) real options-backtest P&L
    step forward, repeat       → aggregate: which method generalises, how often it
                                  beats the incumbent, by how much

Fidelity rules that make this meaningful:
  * the out-of-sample blend uses `strategy/blend.py` — the SAME math production
    trades, not a re-implementation;
  * confidences come from a LIVE bundle evaluation (the feature store doesn't keep
    effective confidence for weight-0 signals, so it can't serve this faithfully);
  * nothing here mutates SignalWeights — it is ADVISORY ONLY (HARD RULE 11).
"""
from __future__ import annotations
from datetime import datetime, timedelta

from strategy_framework.config.settings import FrameworkConfig, SignalWeights
from strategy_framework.signals import bundle as _sb, registry as _reg
from strategy_framework.signals.data_access import DataAccess
from strategy_framework.strategy.blend import blend_net
from strategy_framework.analysis import signal_ensemble as eng

_CFG = FrameworkConfig()
DIRECTIONAL = _reg.directional_names()
MOMENTUM = _reg.momentum_names()
METHODS = ("inverse_redundancy", "mv_ic", "family")


def _parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def gather(expiry: str, horizon_min: int = 60, sample_minutes: float | None = 30,
           window_days: float | None = None, db_path: str | None = None) -> list[dict]:
    """One live pass over the snapshots collecting everything calibration needs:
    per-snapshot signal scores + effective confidences + the time-of-day multiplier
    + the forward return of the index. Returns records sorted by time."""
    da = DataAccess(db_path or _CFG.db_path)
    caps = da.list_captures(expiry=expiry)
    if window_days:
        dates = sorted({c["captured_at"][:10] for c in caps})
        keep = set(dates[-int(window_days):])
        caps = [c for c in caps if c["captured_at"][:10] in keep]
    if len(caps) < 10:
        return []
    times = [_parse(c["captured_at"]) for c in caps]

    # cadence → stride (bound the number of live bundle evaluations)
    ds = sorted((times[i + 1] - times[i]).total_seconds() / 60.0 for i in range(min(len(times) - 1, 300)))
    ds = [d for d in ds if d > 0]
    cadence = ds[len(ds) // 2] if ds else 1.0
    stride = max(1, round(sample_minutes / cadence)) if sample_minutes else 1

    bc = None
    try:
        from strategy_framework.signals.data_access import BarCache, CROSS_ASSET_SYMBOLS
        start = (times[0] - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        syms = list(dict.fromkeys(da.available_symbols("1m") + CROSS_ASSET_SYMBOLS))
        bc = BarCache(_CFG.db_path, syms, start=start, end=caps[-1]["captured_at"])
    except Exception:
        bc = None

    out = []
    for i in range(0, len(caps), stride):
        c = caps[i]
        ts = c["captured_at"]
        j = next((k for k in range(i + 1, len(caps))
                  if times[k] >= times[i] + timedelta(minutes=horizon_min)), None)
        if j is None:
            continue
        p0, p1 = c.get("spot"), caps[j].get("spot")
        if not p0 or not p1:
            continue
        b = _sb.evaluate(_CFG.db_path, ts, expiry, veto_days=_CFG.gates.event_veto_days, bar_cache=bc)
        if not b.spot:
            continue
        tod = b.get("time_of_day").detail if b.get("time_of_day") else {}
        out.append({
            "ts": ts, "session": ts[:10],
            "scores": {n: (b.get(n).score if b.get(n) and b.get(n).status == "OK" else None)
                       for n in DIRECTIONAL},
            "confs": {n: (b.get(n).confidence if b.get(n) and b.get(n).status == "OK" else 0.0)
                      for n in DIRECTIONAL},
            "mom_mult": float(tod.get("momentum_multiplier", 1.0)),
            "fwd": (p1 / p0 - 1.0) * 100.0,
        })
    return out


def make_folds(sessions: list[str], train_n: int, test_n: int, step: int | None = None):
    """Rolling walk-forward splits: [train_n sessions] → [test_n held-out sessions]."""
    step = step or test_n
    folds = []
    i = 0
    while i + train_n + test_n <= len(sessions):
        folds.append((sessions[i:i + train_n], sessions[i + train_n:i + train_n + test_n]))
        i += step
    return folds


def _subset(records, sessions):
    s = set(sessions)
    return [r for r in records if r["session"] in s]


PINNED_ZERO = _reg.pinned_zero_names()   # data not ready/trusted → may never get weight


def _apply_pins(wmap: dict) -> dict:
    """Force PINNED_ZERO signals to 0 and renormalise. A signal whose data isn't
    trusted can never be handed weight by a proposal, no matter how well it scores."""
    w = {n: (0.0 if n in PINNED_ZERO else float(wmap.get(n, 0.0))) for n in DIRECTIONAL}
    s = sum(w.values())
    return {n: (v / s if s > 0 else 0.0) for n, v in w.items()}


def _candidates(train_recs) -> dict:
    """Fit candidate weight vectors on the TRAIN window (the study), plus incumbent.
    Pinned-zero signals are masked out of EVERY candidate, so the out-of-sample test
    only ever grades weightings we'd actually be allowed to adopt."""
    scores = {n: [r["scores"].get(n) for r in train_recs] for n in DIRECTIONAL}
    fwd = [r["fwd"] for r in train_recs]
    rep = eng.analyze_ensemble(scores, fwd)
    cands = {"incumbent": SignalWeights().as_dict()}
    if "error" not in rep:
        for m in METHODS:
            w = rep["weights"].get(m)
            if w:
                cands[m] = _apply_pins({n: float(w.get(n, 0.0)) for n in DIRECTIONAL})
    return cands


def _oos(test_recs, wmap) -> dict:
    """Score a weight vector OUT-OF-SAMPLE: build the blended net_score with the
    PRODUCTION blend, then measure IC / hit vs the forward return."""
    net, fwd = [], []
    for r in test_recs:
        ns, _nc, _ = blend_net(DIRECTIONAL, wmap, r["scores"], r["confs"],
                               momentum_names=MOMENTUM, mom_mult=r["mom_mult"])
        net.append(ns)
        fwd.append(r["fwd"])
    m = eng.signal_metrics(net, fwd)
    return {"ic": m["ic"], "hit": m["hit"], "n": m["n"]}


def _pnl(cfg, expiry, test_recs, wmap) -> float | None:
    """Opt-in: real options walk-forward P&L on the TEST window with these weights."""
    try:
        import copy
        from strategy_framework.backtest import walkforward
        c = copy.deepcopy(cfg)
        c.weights = SignalWeights(overrides=dict(wmap))
        res = walkforward.run(c, expiry, exit_mode="horizon", hold=2,
                              start=test_recs[0]["ts"], end=test_recs[-1]["ts"])
        return float(res.metrics.get("total_pnl_rupees") or 0.0)
    except Exception:
        return None


def calibrate(expiry: str | None = None, train_sessions: int = 3, test_sessions: int = 1,
              horizon_min: int = 60, sample_minutes: float | None = 30,
              window_days: float | None = None, with_pnl: bool = False,
              db_path: str | None = None) -> dict:
    """Run the walk-forward calibration. ADVISORY ONLY — returns evidence + a proposal;
    never writes SignalWeights."""
    da = DataAccess(db_path or _CFG.db_path)
    if not expiry:
        exps = da.expiries()
        expiry = next((e for e in reversed(exps) if da.list_captures(expiry=e)), None)
    if not expiry:
        return {"error": "no expiries in DB"}

    recs = gather(expiry, horizon_min, sample_minutes, window_days, db_path)
    if len(recs) < 10:
        return {"error": f"only {len(recs)} usable snapshots for {expiry}"}
    sessions = sorted({r["session"] for r in recs})
    folds = make_folds(sessions, train_sessions, test_sessions)
    if not folds:
        return {"error": f"need ≥{train_sessions + test_sessions} sessions, have {len(sessions)}",
                "sessions": sessions}

    per_fold, agg = [], {}
    for fi, (tr, te) in enumerate(folds):
        tr_recs, te_recs = _subset(recs, tr), _subset(recs, te)
        if len(tr_recs) < 5 or len(te_recs) < 3:
            continue
        cands = _candidates(tr_recs)
        row = {"fold": fi + 1, "train": tr, "test": te,
               "n_train": len(tr_recs), "n_test": len(te_recs), "results": {}}
        for name, wmap in cands.items():
            r = _oos(te_recs, wmap)
            if with_pnl:
                r["pnl"] = _pnl(_CFG, expiry, te_recs, wmap)
            row["results"][name] = r
            agg.setdefault(name, []).append(r)
        per_fold.append(row)

    if not per_fold:
        return {"error": "no usable folds (windows too thin)", "sessions": sessions}

    # aggregate: mean OOS metrics + win-rate vs incumbent
    summary = {}
    inc = agg.get("incumbent", [])
    for name, rows in agg.items():
        ics = [r["ic"] for r in rows if r["ic"] is not None]
        hits = [r["hit"] for r in rows if r["hit"] is not None]
        pnls = [r["pnl"] for r in rows if r.get("pnl") is not None]
        wins = sum(1 for k, r in enumerate(rows)
                   if r["ic"] is not None and k < len(inc) and inc[k]["ic"] is not None
                   and r["ic"] > inc[k]["ic"])
        summary[name] = {
            "folds": len(rows),
            "mean_ic": round(sum(ics) / len(ics), 4) if ics else None,
            "mean_hit": round(sum(hits) / len(hits), 4) if hits else None,
            "mean_pnl": round(sum(pnls) / len(pnls), 0) if pnls else None,
            "beats_incumbent_folds": wins if name != "incumbent" else None,
        }

    ranked = sorted((n for n in summary if n != "incumbent"),
                    key=lambda n: (summary[n]["mean_ic"] is None, -(summary[n]["mean_ic"] or -9)))
    best = ranked[0] if ranked else None
    inc_ic = summary.get("incumbent", {}).get("mean_ic")
    best_ic = summary.get(best, {}).get("mean_ic") if best else None
    improves = (best_ic is not None and inc_ic is not None and best_ic > inc_ic)

    proposal = None
    if best and improves:
        full = _candidates(recs).get(best)        # refit on the FULL window
        proposal = {"method": best, "weights": full,
                    "oos_mean_ic": best_ic, "incumbent_mean_ic": inc_ic,
                    "beats_incumbent_folds": summary[best]["beats_incumbent_folds"],
                    "of_folds": summary[best]["folds"]}

    return {
        "expiry": expiry, "sessions": sessions, "n_sessions": len(sessions),
        "n_snapshots": len(recs), "horizon_min": horizon_min,
        "sample_minutes": sample_minutes, "with_pnl": with_pnl,
        "train_sessions": train_sessions, "test_sessions": test_sessions,
        "folds": per_fold, "summary": summary, "best_method": best,
        "improves_on_incumbent": improves, "proposal": proposal,
        "sufficient": len(sessions) >= 60,
        "note": ("ADVISORY ONLY — nothing was written to SignalWeights. "
                 "Out-of-sample scores use the production blend (strategy/blend.py). "
                 + ("" if len(sessions) >= 60 else
                    f"⚠ only {len(sessions)} sessions (<60, D-MA-04): treat as plumbing, "
                    "not a calibrated edge.")),
    }
