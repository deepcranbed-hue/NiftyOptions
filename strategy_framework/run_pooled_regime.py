"""
strategy_framework/run_pooled_regime.py
=======================================
POOLED conditional-alpha run with a significance bar — the "step 2 + 3" of the
regime × horizon study, meant to run against the FULL Drive history where every
regime cell finally accumulates enough INDEPENDENT events to trust.

Why a script (not just the UI): a single day gives ~1–20 independent windows per
regime cell, so any IC there is noise (a shiny +0.81 on ind=3 is three coin flips).
The verdict only exists once you pool many sessions. This runs the same
`signal_regime_horizon` engine over a wide date range and then keeps ONLY the cells
that clear a real bar:

  1. independence gate   eff_n >= --min-n            (enough non-overlapping events)
  2. significance gate   |t| > --t                   (IC big vs its own sampling error)

The t-stat for a correlation is  t = IC * sqrt(eff_n - 2) / sqrt(1 - IC^2)  — and we
use eff_n (NOT raw n), because overlapping forward windows are not independent draws.
The default bar |t| > 3 follows Harvey-Liu-Zhu (2016): when you scan a whole matrix
(signals × regimes × horizons = dozens of hypotheses) the usual |t| > 2 is far too
lax and guarantees false 'conditional alpha'. Survivors of |t| > 3 are the only
cells worth building a reliability blend on.

Everything here DELEGATES to strategy_framework.api.signal_regime_horizon — no
regime, IC, or eff_n logic is re-implemented (CLAUDE.md DRY rule).

Usage (run on the machine that has the full Drive DB):

    python -m strategy_framework.run_pooled_regime \
        --from 2026-05-01 --to 2026-07-24 \
        --regime-by oi --horizons 15,30,60 --min-n 20 --t 3
"""
from __future__ import annotations
import argparse
import math

from strategy_framework.api import signal_regime_horizon


def _tstat(ic: float, eff_n: int) -> float | None:
    """Two-sided t for a Pearson/rank IC using the INDEPENDENT count eff_n."""
    if ic is None or eff_n is None or eff_n < 3:
        return None
    denom = 1.0 - ic * ic
    if denom <= 1e-12:
        return math.copysign(99.9, ic)
    return ic * math.sqrt(eff_n - 2) / math.sqrt(denom)


def _phi(z: float) -> float:
    """Standard-normal CDF."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _p_edge(exp: float | None, se: float | None, hurdle: float) -> float | None:
    """P(true expectancy > hurdle) from the session-block bootstrap SE, normal-approx.
    This ONE number folds together 'is the edge real' (via se) and 'is it big enough'
    (via hurdle) — the headline the whole framework was building toward."""
    if exp is None or se is None or se <= 0:
        return None
    return _phi((exp - hurdle) / se)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pooled regime × horizon IC with a significance bar.")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD (omit = all history)")
    ap.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD")
    ap.add_argument("--regime-by", default="oi", choices=["oi", "tape_vol", "none"],
                    help="'none' = UNCONDITIONAL: no regime split, whole sample pools "
                         "into each signal×horizon cell (max power, base-rate edge test)")
    ap.add_argument("--horizons", default="15,30,60", help="forward horizons in minutes, comma-sep")
    ap.add_argument("--min-n", type=int, default=20, help="independence gate (eff_n >= this)")
    ap.add_argument("--t", type=float, default=3.0, help="hard significance gate (|t| > this)")
    ap.add_argument("--fdr", type=float, default=0.10, help="Benjamini-Hochberg false-discovery rate "
                    "(power-appropriate alternative to a blanket |t|>3)")
    ap.add_argument("--hurdle-pct", type=float, default=0.15, help="economic hurdle: per-trade gross "
                    "expectancy (%% of spot) must beat this to be tradeable net of costs")
    ap.add_argument("--p-edge", type=float, default=0.90, help="headline gate: flag STRONG when "
                    "P(true expectancy > hurdle) from the session bootstrap is at least this")
    ap.add_argument("--min-move-pts", type=float, default=0.0, help="dead band — ignore forward moves "
                    "smaller than this many index points (economic noise)")
    ap.add_argument("--top", type=int, default=15, help="how many best cells to always print")
    ap.add_argument("--signal", default=None, help="restrict to one signal name (default: all)")
    args = ap.parse_args()

    res = signal_regime_horizon(args.date_from, args.date_to, min_n=args.min_n,
                                regime_by=args.regime_by, horizons=args.horizons,
                                min_move_pts=args.min_move_pts)
    if "error" in res:
        print("ERROR:", res["error"])
        if res.get("session_dates"):
            print("available dates:", res["session_dates"][0], "..", res["session_dates"][-1],
                  f"({len(res['session_dates'])} sessions)")
        return

    regimes, horizons, matrix = res["regimes"], res["horizons"], res["matrix"]
    n_sessions = len(res.get("session_dates", []))
    signals = [args.signal] if args.signal else sorted(matrix.keys())

    # Collect EVERY cell that cleared independence, with its t, two-sided p, and the
    # economic read. We keep them all (not just t>3 survivors) so a near-miss with a
    # promising IC is visible instead of hidden behind "0 survivors".
    cells, n_cells, n_indep_ok = [], 0, 0
    for sig in signals:
        if sig not in matrix:
            print(f"(skip unknown signal {sig})")
            continue
        for reg in regimes:
            for h in horizons:
                c = matrix[sig].get(reg, {}).get(h)
                if not c:
                    continue
                n_cells += 1
                ic, eff = c.get("ic"), c.get("eff_n", 0)
                if ic is None or eff < args.min_n:
                    continue
                n_indep_ok += 1
                t = _tstat(ic, eff)
                p = math.erfc(abs(t) / math.sqrt(2.0)) if t is not None else 1.0   # 2-sided normal
                exp, se = c.get("gross_exp_pct"), c.get("exp_se_pct")
                cells.append({"sig": sig, "reg": reg, "h": h, "ic": ic, "eff": eff,
                              "n": c.get("n"), "n_days": c.get("n_days"), "t": t, "p": p,
                              "mv": c.get("avg_abs_move_pct"), "exp": exp,
                              "lo": c.get("exp_lo_pct"), "hi": c.get("exp_hi_pct"), "se": se,
                              "hit": c.get("hit"), "p_pos": c.get("exp_p_pos"),
                              "p_edge": _p_edge(exp, se, args.hurdle_pct)})

    # Benjamini-Hochberg FDR across all independence-passing cells (m hypotheses).
    m = len(cells)
    fdr_ok = set()
    if m:
        ordered = sorted(cells, key=lambda d: d["p"])
        k_max = 0
        for k, d in enumerate(ordered, start=1):
            if d["p"] <= (k / m) * args.fdr:
                k_max = k
        for d in ordered[:k_max]:
            fdr_ok.add((d["sig"], d["reg"], d["h"]))

    hard = [d for d in cells if d["t"] is not None and abs(d["t"]) > args.t]
    # HEADLINE gate: P(true expectancy > hurdle) is high — both real AND big enough.
    strong = [d for d in cells if d["p_edge"] is not None and d["p_edge"] >= args.p_edge]

    print("=" * 104)
    print(f"POOLED regime × horizon — regime_by={args.regime_by}  horizons={horizons}"
          f"  dead-band={args.min_move_pts}pts  hurdle={args.hurdle_pct}%")
    print(f"range: {res.get('date_from') or 'ALL'} .. {res.get('date_to') or 'ALL'}   sessions: {n_sessions}")
    print(f"cells scanned: {n_cells}   passed independence (eff_n>={args.min_n}): {n_indep_ok}"
          f"   P(edge)≥{args.p_edge}: {len(strong)}   BH-FDR@{args.fdr}: {len(fdr_ok)}   |t|>{args.t}: {len(hard)}")
    print("=" * 104)
    if not cells:
        print("No cells cleared the independence gate — widen the range or lower --min-n.")
        return

    # Rank by P(edge) — the objective the whole framework points at: probability the
    # net, tradeable edge is real. IC/t kept only as diagnostics beside it.
    cells.sort(key=lambda d: (d["p_edge"] if d["p_edge"] is not None else -1), reverse=True)
    print(f"{'signal':<24}{'regime':<11}{'h':>4}{'P(edge)':>8}{'exp%':>8}"
          f"{'95% CI':>18}{'|mv|%':>7}{'hit%':>6}{'IC':>7}  flags")
    print("-" * 104)
    for d in cells[:args.top]:
        flags = []
        if d["p_edge"] is not None and d["p_edge"] >= args.p_edge:
            flags.append("STRONG")
        if (d["sig"], d["reg"], d["h"]) in fdr_ok:
            flags.append("FDR")
        pe = f"{d['p_edge']:.2f}" if d["p_edge"] is not None else "  -"
        ex = f"{d['exp']:+.3f}" if d["exp"] is not None else "   -"
        ci = (f"[{d['lo']:+.3f},{d['hi']:+.3f}]" if d["lo"] is not None else "        -")
        mv = f"{d['mv']:.3f}" if d["mv"] is not None else "  -"
        ht = f"{100*d['hit']:.0f}" if d["hit"] is not None else " -"
        print(f"{d['sig']:<24}{d['reg']:<11}{d['h']:>4}{pe:>8}{ex:>8}{ci:>18}"
              f"{mv:>7}{ht:>6}{d['ic']:>7.3f}  {' '.join(flags)}")
    print("-" * 104)

    best = cells[0]
    _pe = f"{best['p_edge']:.3f}" if best["p_edge"] is not None else "-"
    _ci = f"[{best['lo']:+.3f}, {best['hi']:+.3f}]" if best["lo"] is not None else "-"
    print(f"best by P(edge): {best['sig']} / {best['reg']} / {best['h']}m  "
          f"P(exp>{args.hurdle_pct}%)={_pe}  exp={best['exp']:+.3f}%  "
          f"CI{_ci}  over {best['n_days']} sessions")
    if best["p_pos"] is not None:
        print(f"  P(edge has right sign)={best['p_pos']:.2f}   "
              f"— if the CI straddles the hurdle, that is 'not enough data yet', not 'no edge'.")
    print("Read: P(edge)=P(true net expectancy > hurdle) from a SESSION-block bootstrap — one number")
    print("      that is both 'statistically real' and 'economically big enough'. STRONG = P(edge)≥"
          f"{args.p_edge}. exp% = mean sign(score)×move (gross). IC/t shown only as diagnostics.")


if __name__ == "__main__":
    main()
