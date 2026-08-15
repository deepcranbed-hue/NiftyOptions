"""
strategy_framework/analysis/signal_ensemble.py
==============================================
INSTRUMENT-AGNOSTIC signal-ensemble analyzer + weight proposer.

Given a set of signal SCORES over time (and optionally the forward RETURNS of
whatever instrument you're trying to trade — an index, a stock, an index future,
anything), it measures how INDEPENDENT the signals are and proposes weights three
different ways so you can compare them.

It knows nothing about NIFTY, options, or any specific market — it operates purely
on:
    scores : { signal_name: [score_t, ...] }   each score ∈ [-1, 1], None = missing
    fwd    : [forward_return_t, ...]            aligned to the score rows (optional)

so the SAME engine serves the index, a single stock, or a future — you just feed it
the scores + the target instrument's forward returns.

Outputs
-------
  * correlation matrix (pairwise, NaN-aware)
  * redundancy per signal (avg |corr| to the others — higher = more duplicative)
  * effective-independent count (participation ratio of the correlation spectrum)
  * families (connected components at a correlation threshold)
  * IC per signal (corr of score vs forward return), if fwd is given
  * proposed weights via THREE methods (compared side by side):
      1. inverse_redundancy — diversification only, NO forward returns needed
      2. mv_ic             — Σ⁻¹·IC mean-variance optimal (needs IC; shrinkage-regularised)
      3. family            — cluster → equal budget per family → split within by IC

All weights are non-negative and sum to 1 (a weight is IMPORTANCE, not direction —
the sign lives in each signal's score). Everything is descriptive/PRIOR; the caller
decides how many observations are enough to trust it.
"""
from __future__ import annotations
import numpy as np


# ── matrix assembly ───────────────────────────────────────────────────────────
def _matrix(scores: dict[str, list]):
    """dict{name:[..]} -> (names, 2D array with np.nan for missing)."""
    names = list(scores.keys())
    n = max((len(v) for v in scores.values()), default=0)
    M = np.full((n, len(names)), np.nan)
    for j, name in enumerate(names):
        v = scores[name]
        for i, x in enumerate(v):
            if x is not None and not (isinstance(x, float) and np.isnan(x)):
                M[i, j] = float(x)
    return names, M


def _live_cols(names, M, min_obs=3):
    """Indices of signals with >= min_obs real observations and non-zero variance."""
    keep = []
    for j in range(M.shape[1]):
        col = M[:, j][~np.isnan(M[:, j])]
        if len(col) >= min_obs and col.std() > 1e-12:
            keep.append(j)
    return keep


# ── independence structure ────────────────────────────────────────────────────
def corr_matrix_full(cols: dict[str, list]):
    """THE canonical pairwise-correlation primitive for a set of score columns.

    `cols`: {name: [score_t, ...]} with None for missing. Returns
    (names, matrix, pair_n) where matrix[i][j] is the Pearson correlation over the
    overlapping non-missing pairs (None if <3 overlap or a column is constant; the
    diagonal is 1.0), and pair_n[i][j] is that overlap count. Values are UNROUNDED —
    callers round as they wish.

    Shared home per HARD RULE 12: both `analyze_ensemble` (here) and
    `api.signal_correlation` build their correlation matrix from this one function
    instead of each re-implementing the corrcoef loop.
    """
    names = list(cols)
    n = len(names)
    arr = {s: np.array([np.nan if x is None else float(x) for x in cols[s]], float) for s in names}
    mat = [[None] * n for _ in range(n)]
    pair_n = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            a, b = arr[names[i]], arr[names[j]]
            m = ~np.isnan(a) & ~np.isnan(b)
            c = int(m.sum())
            pair_n[i][j] = c          # ALWAYS record the true overlap, even if we skip
            if c < 3:                 # too few shared rows to estimate a correlation
                continue
            av, bv = a[m], b[m]
            if av.std() > 0 and bv.std() > 0:
                mat[i][j] = float(np.corrcoef(av, bv)[0, 1])
            elif i == j:
                mat[i][j] = 1.0
    return names, mat, pair_n


def _dense_corr(M, idx):
    """Dense NaN-aware correlation array over the selected columns, via the shared
    `corr_matrix_full` primitive (no duplicate corrcoef loop)."""
    cols = {str(j): [None if np.isnan(M[t, j]) else M[t, j] for t in range(M.shape[0])] for j in idx}
    _, mat, _pn = corr_matrix_full(cols)
    C = np.array([[np.nan if v is None else v for v in row] for row in mat], float)
    np.fill_diagonal(C, 1.0)
    return C


def redundancy(C):
    """avg |corr| of each signal to the others (higher = more duplicative)."""
    k = C.shape[0]
    out = np.full(k, np.nan)
    for i in range(k):
        offs = [abs(C[i, j]) for j in range(k) if j != i and not np.isnan(C[i, j])]
        if offs:
            out[i] = float(np.mean(offs))
    return out


def effective_independent(C):
    """Participation ratio of the correlation spectrum: (Σλ)² / Σλ².
    k identical signals -> 1; k independent signals -> k. A continuous 'how many
    truly distinct bets do these signals represent' measure."""
    F = np.where(np.isnan(C), 0.0, C).copy()
    np.fill_diagonal(F, 1.0)
    try:
        ev = np.linalg.eigvalsh(F)
        ev = ev[ev > 1e-9]
        return float((ev.sum() ** 2) / np.square(ev).sum()) if ev.size else None
    except Exception:
        return None


def clusters(C, names, threshold=0.6):
    """Connected components where |corr| >= threshold = a 'family' of signals that
    move together (and should therefore SHARE a weight budget, not stack)."""
    k = len(names)
    parent = list(range(k))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(k):
        for j in range(i + 1, k):
            if not np.isnan(C[i, j]) and abs(C[i, j]) >= threshold:
                parent[find(i)] = find(j)
    groups: dict = {}
    for i in range(k):
        groups.setdefault(find(i), []).append(names[i])
    return list(groups.values())


def information_coefficient(M, fwd, idx):
    """corr(score, forward return) per selected signal. None if insufficient / no fwd."""
    if fwd is None:
        return {j: None for j in idx}
    f = np.full(M.shape[0], np.nan)
    for i, x in enumerate(fwd[:M.shape[0]]):
        if x is not None and not (isinstance(x, float) and np.isnan(x)):
            f[i] = float(x)
    out = {}
    for j in idx:
        m = ~np.isnan(M[:, j]) & ~np.isnan(f)
        if m.sum() >= 5 and M[m, j].std() > 0 and f[m].std() > 0:
            out[j] = float(np.corrcoef(M[m, j], f[m])[0, 1])
        else:
            out[j] = None
    return out


# ── canonical per-signal metrics (THE shared definitions — agent AND api use these)
def _spearman(x, y) -> float:
    xr = np.argsort(np.argsort(x)).astype(float)
    yr = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(xr, yr)[0, 1]) if xr.std() > 0 and yr.std() > 0 else 0.0


def signal_metrics(sc, fw) -> dict:
    """All per-signal skill metrics vs a forward return, NaN-aware. This is THE one
    definition used by the Signal Weight Agent AND the api analytics endpoints
    (signal_effectiveness / scoreboard / horizon-curve) so every surface agrees.

      ic      : Pearson corr(score, fwd)
      rank_ic : Spearman (rank) corr — robust to outliers
      hit     : directional agreement where |score| ≥ 0.1
      spread  : mean fwd of top-half scores − mean fwd of bottom-half (median split)
      sharpe  : consistency of the median-split long/short, mean/σ of sign·fwd
    """
    sc = np.asarray(sc, float); fw = np.asarray(fw, float)
    n = min(len(sc), len(fw))
    sc, fw = sc[:n], fw[:n]
    m = ~np.isnan(sc) & ~np.isnan(fw)
    sc, fw = sc[m], fw[m]
    k = len(sc)
    if k < 3:
        return {"n": int(k), "ic": None, "rank_ic": None, "spread": None,
                "sharpe": None, "hit": None}
    # per-metric guards (matches api.signal_effectiveness exactly so the UI heatmap
    # is byte-identical when it routes through here).
    ic = float(np.corrcoef(sc, fw)[0, 1]) if sc.std() > 0 else 0.0
    ric = _spearman(sc, fw)
    act = np.abs(sc) >= 0.1
    hit = float(np.mean(np.sign(sc[act]) == np.sign(fw[act]))) if act.any() else None
    med = np.median(sc); hi = fw[sc >= med]; lo = fw[sc < med]
    spread = (float(hi.mean()) - float(lo.mean())) if (len(hi) and len(lo)) else None
    pnl = np.where(sc >= med, 1.0, -1.0) * fw
    sharpe = float(pnl.mean() / (pnl.std() + 1e-9)) if (k > 1 and pnl.std() > 0) else None
    return {"n": int(k), "ic": round(ic, 3), "rank_ic": round(ric, 3),
            "spread": round(spread, 3) if spread is not None else None,
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "hit": round(hit, 3) if hit is not None else None}


def directional_sharpe(sc, fw) -> float | None:
    """Consistency of the directional CALL: mean/σ of sign(score)·fwd over the
    *active* rows (|score| ≥ 0.1). This is the scoreboard / horizon-curve variant of
    'sharpe' (vs signal_metrics' median-split one) — kept here so that math also
    lives in the one engine, not inline in api.py."""
    sc = np.asarray(sc, float); fw = np.asarray(fw, float)
    n = min(len(sc), len(fw)); sc, fw = sc[:n], fw[:n]
    m = ~np.isnan(sc) & ~np.isnan(fw)
    sc, fw = sc[m], fw[m]
    act = np.abs(sc) >= 0.1
    if act.sum() <= 1:
        return None
    pnl = np.sign(sc[act]) * fw[act]
    return float(pnl.mean() / (pnl.std() + 1e-9)) if pnl.std() > 0 else None


def metrics_by_horizon(scores_by_signal: dict[str, list],
                       fwd_by_horizon: dict[str, list]) -> dict:
    """Signal × horizon metric grid. `scores_by_signal`: {signal:[score_t]};
    `fwd_by_horizon`: {label:[fwd_return_t]} aligned to the same rows. Returns
    {signal: {"cells": {horizon: metrics}, "best_horizon": h}} — the multi-horizon
    comparison the agent reports and the UI heatmap renders, from one definition."""
    out = {}
    for s, sc in scores_by_signal.items():
        cells = {h: signal_metrics(sc, fw) for h, fw in fwd_by_horizon.items()}
        best = max(cells, key=lambda h: abs(cells[h]["ic"]) if cells[h]["ic"] is not None else -1)
        out[s] = {"cells": cells, "best_horizon": best if cells[best]["ic"] is not None else None}
    return out


# ── weight methods (all return dict{name: weight}, weights >= 0 sum to 1) ──────
def _normalize(names, w):
    w = np.clip(np.asarray(w, float), 0.0, None)
    s = w.sum()
    if s <= 0:
        w = np.ones(len(w))
        s = w.sum()
    return {n: round(float(wi / s), 4) for n, wi in zip(names, w)}


def w_inverse_redundancy(names, red, eps=0.1):
    """More independent (lower avg |corr|) -> more weight. No forward returns needed."""
    r = np.where(np.isnan(red), np.nanmean(red) if np.isfinite(np.nanmean(red)) else 0.5, red)
    return _normalize(names, 1.0 / (eps + r))


def w_mv_ic(names, C, ic_vec, shrinkage=0.15):
    """Mean-variance optimal: w ∝ Σ⁻¹·IC. Σ = shrinkage-regularised correlation
    (so the inverse is stable on modest samples). Negative optimal weights (a signal
    that is anti-predictive) are clipped to 0 and flagged by the caller — on thin
    data a negative IC is usually noise, not a real inversion to bet against.

    NOTE (HARD RULE 12): this is deliberately NOT
    `backend/quant/dispersion_engine.compute_ledoit_wolf_correlation`. That is a
    full Ledoit-Wolf estimator for a stock-returns covariance (pandas/sklearn); here
    we only need a light constant shrink-to-identity to keep a small already-computed
    correlation matrix invertible. Different input, different purpose — not a dup."""
    ic = np.array([0.0 if (v is None or np.isnan(v)) else v for v in ic_vec], float)
    if np.allclose(ic, 0):
        return None                        # no usable IC -> method not applicable
    k = C.shape[0]
    S = np.where(np.isnan(C), 0.0, C).copy()
    np.fill_diagonal(S, 1.0)
    S = (1 - shrinkage) * S + shrinkage * np.eye(k)   # Ledoit-Wolf-style shrink to I
    try:
        w = np.linalg.solve(S, ic)
    except Exception:
        w = ic
    return _normalize(names, w)


def w_family(names, fams, ic_map, equal_family_budget=True):
    """Cluster -> budget per family -> split within family by max(IC,0) (else equal).
    Correlated signals SHARE their family's budget, so they can't stack into
    duplicate votes. Families get equal budget by default (pure diversification);
    set equal_family_budget=False to tilt family budgets by their mean |IC|."""
    name_ic = {n: (ic_map.get(n) or 0.0) for n in names}
    # family budgets
    if equal_family_budget or not any(name_ic.values()):
        fam_budget = {i: 1.0 / len(fams) for i in range(len(fams))}
    else:
        strengths = [max(1e-6, np.mean([abs(name_ic[n]) for n in fam])) for fam in fams]
        tot = sum(strengths)
        fam_budget = {i: strengths[i] / tot for i in range(len(fams))}
    w = {}
    for i, fam in enumerate(fams):
        pos = {n: max(name_ic[n], 0.0) for n in fam}
        tot = sum(pos.values())
        for n in fam:
            share = (pos[n] / tot) if tot > 0 else (1.0 / len(fam))
            w[n] = fam_budget[i] * share
    return _normalize(names, [w[n] for n in names])


# ── orchestrator ──────────────────────────────────────────────────────────────
def analyze_ensemble(scores: dict[str, list], fwd_returns: list | None = None,
                     cluster_threshold: float = 0.6, shrinkage: float = 0.15,
                     min_coverage: float = 0.0, common_sample: bool = False) -> dict:
    """Full report: independence structure + IC + three proposed weight vectors.

    Ragged data (a signal whose feed starts later than the others) is handled two
    ways, because pairwise-NaN correlation alone silently mixes samples:
      * `min_coverage` — a signal must cover this FRACTION of the window to influence
        correlations / families / weights. Default 0.0 (OFF) because real signals
        legitimately flicker to NO_DATA (observed 54–79% coverage for healthy ones),
        so a fixed high gate would exclude everything. Set it deliberately to screen
        out a genuinely sparse feed; `coverage` + `starts_at_row` are always reported
        so raggedness is visible either way.
      * `common_sample` — restrict EVERY computation to the rows where all included
        signals have data (the intersection), so each correlation cell comes from
        identical rows. Costs history, buys a coherent (PSD-safe) matrix.
    """
    all_names, M = _matrix(scores)
    n_rows0 = M.shape[0]
    coverage = {all_names[j]: (round(float((~np.isnan(M[:, j])).sum()) / n_rows0, 3)
                               if n_rows0 else 0.0) for j in range(len(all_names))}
    # FIRST row where each signal has data — a feed that starts late (e.g. futures
    # from 5 Jul while the index starts 29 Jun) shows up here as a non-zero start,
    # which a coverage % alone would hide.
    starts_at_row = {}
    for j, nm in enumerate(all_names):
        present = np.where(~np.isnan(M[:, j]))[0]
        starts_at_row[nm] = int(present[0]) if len(present) else None
    base_idx = _live_cols(names=all_names, M=M)          # ≥3 obs + non-constant
    excluded = [all_names[j] for j in range(len(all_names)) if j not in base_idx]
    idx = [j for j in base_idx if coverage[all_names[j]] >= min_coverage]
    low_cov = [all_names[j] for j in base_idx if j not in idx]
    live = [all_names[j] for j in idx]
    if len(live) < 2:
        return {"error": "need >= 2 signals passing the coverage gate", "live": live,
                "excluded": excluded, "excluded_low_coverage": low_cov,
                "coverage": coverage}

    fwd_returns = list(fwd_returns) if fwd_returns is not None else None
    if common_sample:
        keep = np.where(~np.isnan(M[:, idx]).any(axis=1))[0]
        if len(keep) >= 3:
            M = M[keep, :]
            if fwd_returns is not None:
                fwd_returns = [fwd_returns[i] for i in keep if i < len(fwd_returns)]

    Msub = M[:, idx]
    C = _dense_corr(M, idx)
    # how many rows each correlation actually rests on (so a 3-row cell can't be
    # mistaken for a 400-row one)
    pair_overlap = {}
    for a, ja in enumerate(idx):
        pair_overlap[all_names[ja]] = {}
        for b, jb in enumerate(idx):
            ov = int((~np.isnan(M[:, ja]) & ~np.isnan(M[:, jb])).sum())
            pair_overlap[all_names[ja]][all_names[jb]] = ov
    red = redundancy(C)
    eff = effective_independent(C)
    fams = clusters(C, live, threshold=cluster_threshold)
    ic_by_pos = information_coefficient(M, fwd_returns, idx)
    ic_map = {live[a]: ic_by_pos[idx[a]] for a in range(len(live))}
    ic_vec = [ic_map[n] for n in live]

    weights = {
        "inverse_redundancy": w_inverse_redundancy(live, red),
        "mv_ic": w_mv_ic(live, C, ic_vec, shrinkage=shrinkage),
        "family": w_family(live, fams, ic_map),
    }
    n_obs = int(np.max([(~np.isnan(Msub[:, a])).sum() for a in range(len(live))]))
    return {
        "signals": live,
        "excluded_no_data": excluded,
        "excluded_low_coverage": low_cov,
        "coverage": coverage,
        "starts_at_row": starts_at_row,
        "common_sample": bool(common_sample),
        "n_rows_used": int(M.shape[0]),
        "min_coverage": min_coverage,
        "pair_n": pair_overlap,
        "n_obs": n_obs,
        "correlation": {live[a]: {live[b]: (None if np.isnan(C[a, b]) else round(float(C[a, b]), 2))
                                  for b in range(len(live))} for a in range(len(live))},
        "redundancy": {live[a]: (None if np.isnan(red[a]) else round(float(red[a]), 3))
                       for a in range(len(live))},
        "effective_independent": round(eff, 2) if eff is not None else None,
        "families": fams,
        "ic": {n: (None if v is None else round(v, 3)) for n, v in ic_map.items()},
        "weights": weights,
        "params": {"cluster_threshold": cluster_threshold, "shrinkage": shrinkage},
        "notes": ("weights are non-negative and sum to 1 (importance, not direction). "
                  "mv_ic is null when no usable IC (no/thin forward returns). "
                  "All PRIOR/descriptive — the CALLER decides if n_obs is enough to trust."),
    }


def format_report(rep: dict) -> str:
    """Human-readable text version of analyze_ensemble()'s output."""
    if "error" in rep:
        return f"ensemble error: {rep['error']}  (live={rep.get('live')})"
    L = rep["signals"]
    excl = rep.get("excluded_no_data", [])
    lowc = rep.get("excluded_low_coverage", [])
    cov = rep.get("coverage", {})
    total = len(L) + len(excl) + len(lowc)
    out = []
    out.append(f"n_obs={rep['n_obs']}   effective-independent bets≈{rep['effective_independent']} "
               f"of {len(L)} live signals  ({total} in roster, {len(excl)} NO_DATA, "
               f"{len(lowc)} below coverage gate)")
    out.append(f"sample: {rep.get('n_rows_used','?')} rows used · "
               f"{'COMMON-SAMPLE (identical rows for every cell)' if rep.get('common_sample') else 'pairwise overlap (cells may use different rows)'}"
               f" · coverage gate ≥{rep.get('min_coverage', 0)*100:.0f}%")
    out.append("\nfamilies (correlated → share a budget):")
    for i, fam in enumerate(rep["families"]):
        out.append(f"  family {i+1}: {', '.join(fam)}")
    # FULL roster shown — live signals with numbers, NO_DATA ones explicitly listed
    # (so futures_flow / macro signals never look silently missing).
    out.append("\ncoverage | redundancy (avg |corr|; high = duplicative) | IC (skill vs fwd return):")
    for n in sorted(L, key=lambda x: -(rep['redundancy'].get(x) or 0)):
        r = rep['redundancy'].get(n); ic = rep['ic'].get(n)
        c = cov.get(n); st = rep.get("starts_at_row", {}).get(n)
        late = f" starts@row{st}" if st else ""
        out.append(f"  {n:24} cov={(f'{c*100:3.0f}%' if c is not None else ' . '):<6}"
                   f"red={r if r is not None else ' . ':<6} "
                   f"IC={('%+.2f' % ic) if ic is not None else '  .'}{late}")
    for n in lowc:
        c = cov.get(n)
        out.append(f"  {n:24} cov={(f'{c*100:3.0f}%' if c is not None else ' . '):<6}"
                   f"HELD OUT — below the coverage gate (data starts late / gaps)")
    for n in excl:
        out.append(f"  {n:24} NO_DATA — no scores in this DB, can't be judged")
    out.append("\nproposed weights (compare):")
    methods = [m for m in ("inverse_redundancy", "mv_ic", "family") if rep["weights"].get(m)]
    out.append("  " + " " * 22 + "".join(f"{m[:12]:>13}" for m in methods))
    for n in L:
        row = "".join(f"{rep['weights'][m][n]:>13.3f}" for m in methods)
        out.append(f"  {n:22}{row}")
    for n in lowc:
        row = "".join(f"{'—':>13}" for _ in methods)
        out.append(f"  {n:22}{row}   (below coverage gate — 0 weight)")
    for n in excl:
        row = "".join(f"{'—':>13}" for _ in methods)
        out.append(f"  {n:22}{row}   (NO_DATA — 0 weight until data exists)")
    out.append("\n" + rep["notes"])
    return "\n".join(out)


def format_horizon_table(rep: dict, metric: str = "ic") -> str:
    """Text version of a signal × horizon grid (study_horizons output). `metric` is
    one of ic / rank_ic / spread / sharpe / hit. `*` marks each signal's best horizon."""
    if "error" in rep:
        return f"horizon study error: {rep['error']}"
    grid = rep["grid"]; hs = rep["horizons"]
    out = [f"Signal × horizon {metric.upper()}  (target={rep['target']}, n_obs={rep['n_obs']}, "
           f"sampled every {rep.get('sample_minutes','?')}m, source={rep.get('source')}):"]
    # effective (overlap-adjusted) independent samples per horizon — trust THIS, not n_obs
    eff = rep.get("effective_n", {}); ov = rep.get("overlap", {})
    out.append("  " + f"{'eff-N (independent)':22}"
               + "".join(f"{(str(eff.get(h,'?'))+('*' if ov.get(h) else '')):>8}" for h in hs) + f"{'':>8}")
    out.append("  " + f"{'signal':22}" + "".join(f"{h:>8}" for h in hs) + f"{'best':>8}")

    def _best_abs(s):
        b = grid[s]["best_horizon"]
        v = grid[s]["cells"][b].get(metric) if b else None
        return abs(v) if v is not None else -1
    for s in sorted(grid, key=_best_abs, reverse=True):
        cells = grid[s]["cells"]; best = grid[s]["best_horizon"]
        row = ""
        for h in hs:
            v = cells[h].get(metric)
            mark = "*" if h == best else " "
            row += (f"{v:>+7.2f}{mark}" if v is not None else f"{'·':>7} ")
        out.append(f"  {s:22}{row}{str(best or '·'):>8}")
    if rep.get("excluded_no_data"):
        out.append("  excluded (NO_DATA): " + ", ".join(rep["excluded_no_data"]))
    out.append(f"\n  eff-N = overlap-adjusted INDEPENDENT samples (trust this, not n_obs); "
               f"* = windows overlap (sampled finer than the horizon → inflated). "
               f"metric={metric}: ic=Pearson · rank_ic=Spearman · spread=top−bottom-half fwd · "
               f"sharpe=median-split consistency · hit=direction agreement. {rep.get('note','')}")
    return "\n".join(out)


if __name__ == "__main__":
    # self-test on SYNTHETIC data with a KNOWN structure:
    #   A,B nearly identical (a family); C independent; D = weak noise.
    rng = np.random.default_rng(0)
    n = 400
    a = rng.normal(size=n)
    scores = {
        "A": list(np.tanh(a + 0.05 * rng.normal(size=n))),          # family with B
        "B": list(np.tanh(a + 0.05 * rng.normal(size=n))),          # ~identical to A
        "C": list(np.tanh(rng.normal(size=n))),                     # independent
        "D": list(np.tanh(0.2 * rng.normal(size=n))),               # weak/independent
    }
    fwd = list(0.6 * np.array(scores["C"]) + 0.2 * a + 0.3 * rng.normal(size=n))  # C is the real edge
    rep = analyze_ensemble(scores, fwd)
    print(format_report(rep))
    assert rep["effective_independent"] < len(rep["signals"]), "A,B should collapse independence"
    assert any(set(f) >= {"A", "B"} for f in rep["families"]), "A,B must cluster together"
    print("\nself-test OK")
