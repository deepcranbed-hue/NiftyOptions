"""
strategy_framework/run_morning_report.py
========================================
THE MORNING DECISION HIERARCHY — a market operating system, not a strategy picker.

    State  →  Fatal gates  →  Regime  →  Allowed families  →  Ranking (within
    allowed)  →  Execution constraints  →  Kill-list  →  Journal (with regret)

Design rules (all learned this month, all frozen):
  * No direction prediction anywhere.
  * Gates have PRIORITIES: L1 fatal (nothing overrides), L2 structure restriction,
    L3 execution restriction, L4 management advisory.
  * Ranking only happens INSIDE the family the regime allows.
  * Similarity carries a FAMILIARITY score — an unfamiliar state says so instead of
    pretending the neighbours mean something.
  * Every run appends to knowledge/decision_journal.csv and back-fills REGRET
    (best structure in hindsight − chosen) once outcomes exist. "What mistake did
    I make?" becomes a growing table, not a memory.
  * Where the user's priors and July's evidence diverge (low coverage: evidence says
    capped structures suffer the worst asymmetry, so G2 vetoes capped, not naked),
    the EVIDENCE version is implemented and the divergence is documented here.

    python -m strategy_framework.run_morning_report [--date YYYY-MM-DD]
"""
from __future__ import annotations
import argparse
import csv
import os

import numpy as np

from strategy_framework.run_analogue_days import _FEATS, _f

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_JOURNAL = os.path.join(_KNOW, "decision_journal.csv")
_STRUCTS = ["condor", "fly", "strangle", "straddle"]
_CAPPED = {"condor", "fly"}
# USER RISK POLICY (2026-08-05): naked short premium is BANNED — losses must be
# capped on both legs. Only defined-risk structures may be selected; naked ones
# remain in the ranking for reference. Accepting the profit cap is deliberate:
# July evidence — fly worst-mark −₹1,779 vs naked straddle −₹19,898.
_TRADABLE = {"fly", "condor"}

# regime → allowed structure families (frozen priors; the journal will grade them)
_REGIME_ALLOWED = {
    "expansion": [],                                    # no short premium; long gamma not in menu
    "pin": ["fly", "straddle"],                         # tight structures at the magnet
    "compression": ["straddle", "strangle", "fly", "condor"],
    "mixed": ["straddle", "strangle", "fly", "condor"],
    "compressed-gamma": [],                             # fatal zone anyway
}


def _classify_regime(cov, er, chop, schg, pin):
    if cov is not None and cov < 0.05:
        return "compressed-gamma", "coverage ~0 — maximum stored energy at the strikes"
    if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
        return "expansion", "straddle expanding / efficient directional travel"
    if pin is not None and pin >= 0.15 and (chop is not None and chop >= 50):
        return "pin", "concentrated ATM inventory on a choppy tape"
    if (chop is not None and chop >= 55) or (er is not None and er <= 0.20):
        return "compression", "inefficient tape, premium decaying"
    return "mixed", "no dominant signature"


def _journal_upsert(row, cs):
    rows = []
    if os.path.exists(_JOURNAL):
        rows = list(csv.DictReader(open(_JOURNAL)))
    by_date = {r["date"]: r for r in rows}
    by_date.setdefault(row["date"], row)
    # back-fill regret wherever outcomes now exist
    for d, r in by_date.items():
        lab = cs.get(d)
        if not lab or r.get("regret") not in (None, "", "None"):
            continue
        pnls = {s: _f(lab, f"{s}_pnl") for s in _STRUCTS}
        pnls = {k: v for k, v in pnls.items() if v is not None}
        if not pnls:
            continue
        best = max(pnls, key=pnls.get)
        chosen = r.get("chosen") or ""
        r["best_structure"] = best
        r["best_pnl"] = round(pnls[best], 0)
        if chosen in pnls:
            r["chosen_pnl"] = round(pnls[chosen], 0)
            r["regret"] = round(pnls[best] - pnls[chosen], 0)
        elif chosen == "STAND_ASIDE":
            r["chosen_pnl"] = 0
            r["regret"] = round(max(0.0, pnls[best]), 0)     # what standing aside forwent
    cols = ["date", "regime", "gates", "familiarity_pct", "chosen",
            "chosen_pnl", "best_structure", "best_pnl", "regret"]
    with open(_JOURNAL, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for d in sorted(by_date):
            w.writerow(by_date[d])
    return by_date


_POSITIONS = os.path.join(_KNOW, "positions.csv")


def _build_legs(db, date, structure, wing=100):
    """Price the chosen structure's legs from that morning's chain (10:00). Returns
    (legs_str, credit_pts, expiry) — legs encoded 'side:strike:sign:entry_px;…'
    (sign +1 = short/credit leg). None if the chain can't price it."""
    import sqlite3
    from strategy_framework.signals.data_access import DataAccess
    from strategy_framework.signals import option_oi
    da = DataAccess(db)
    con = sqlite3.connect(db)
    exps = sorted({r[0] for r in con.execute("SELECT DISTINCT expiry FROM chain_rows")},
                  key=lambda e: e[:10])
    con.close()
    exp = next((e for e in exps if e[:10] >= date), None)
    ch = da.chain_as_of(f"{date}T04:30:00Z", exp) if exp else None
    if not ch or ch.ts[:10] != date:
        return None
    K = min(ch.strikes, key=lambda k: abs(k - ch.spot))
    below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
    above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
    if not below or not above:
        return None
    kp = max(below, key=lambda x: x[1])[0]
    kc = max(above, key=lambda x: x[1])[0]
    legs = {"condor": [("C", kc, 1), ("C", kc + wing, -1), ("P", kp, 1), ("P", kp - wing, -1)],
            "fly": [("C", K, 1), ("P", K, 1), ("C", K + wing, -1), ("P", K - wing, -1)],
            "strangle": [("C", kc, 1), ("P", kp, 1)],
            "straddle": [("C", K, 1), ("P", K, 1)]}.get(structure)
    if not legs:
        return None
    out, credit = [], 0.0
    for cp, k, sgn in legs:
        px = (ch.call_ltp if cp == "C" else ch.put_ltp).get(k)
        if not px or px <= 0:
            return None
        out.append(f"{cp}:{k:.0f}:{sgn}:{px}")
        credit += sgn * px
    return ";".join(out), round(credit, 1), exp


def _record_position(date, structure, db):
    """Append the entered position to knowledge/positions.csv (idempotent per date)."""
    rows = list(csv.DictReader(open(_POSITIONS))) if os.path.exists(_POSITIONS) else []
    if any(r["entry_date"] == date for r in rows):
        return None
    built = _build_legs(db, date, structure)
    if not built:
        return None
    legs, credit, exp = built
    rows.append({"entry_date": date, "expiry": exp[:10], "expiry_full": exp,
                 "structure": structure, "legs": legs, "credit_pts": credit,
                 "lots": 1, "status": "open"})
    cols = ["entry_date", "expiry", "expiry_full", "structure", "legs",
            "credit_pts", "lots", "status"]
    with open(_POSITIONS, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return legs, credit


def main():
    ap = argparse.ArgumentParser(description="Morning decision hierarchy.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--k", type=int, default=7)
    ap.add_argument("--db", default="option_chains_full.db",
                    help="chain DB, used to price + record the entered position")
    args = ap.parse_args()

    ms = list(csv.DictReader(open(os.path.join(_KNOW, "market_state.csv"))))
    cs = {r["entry"]: r for r in csv.DictReader(open(os.path.join(_KNOW, "chain_structure.csv")))}
    dates = [r["date"] for r in ms]
    target = args.date or dates[-1]
    if target not in dates:
        print(f"no state row for {target}")
        return
    ti = dates.index(target)
    t = ms[ti]
    cov, er = _f(t, "coverage_ratio"), _f(t, "er30")
    chop, schg = _f(t, "chop_index"), _f(t, "straddle_chg_30m_pct")
    pin = _f(t, "pin_share")

    print("=" * 74)
    print(f"MORNING DECISION  {target}   (1 lot · allow/caution/veto)")
    print("=" * 74)
    print(f"state   er30={er} chop={chop} straddle={t.get('atm_straddle_pts')} "
          f"chg30m={schg}% pcr={t.get('pcr')} coverage={cov} "
          f"covAdjPrem={t.get('cov_adj_premium')} pin={pin}")

    # ---- gates, by priority level -----------------------------------------
    fatal, restrict, execu = [], [], []
    if cov is not None and cov < 0.05:
        fatal.append(f"L1 FATAL: coverage {cov} < 0.05 → NO TRADE (nothing overrides)")
    elif cov is not None and cov < 0.10:
        restrict.append(f"L2: coverage {cov} < 0.10 → capped-profit structures vetoed "
                        f"(evidence: worst asymmetry lives there)")
    if (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55):
        fatal.append(f"L3→L1: trend-expansion (chg {schg}% / er {er}) → no short premium today")
    execu.append("L3: minimise overnight holds — July P&L: days +₹218, nights −₹230")
    execu.append("L4: defend only the tested side, only on regime-confirmed touches")

    regime, why = _classify_regime(cov, er, chop, schg, pin)
    print(f"\nregime  {regime.upper()} — {why}")
    for g in fatal:
        print(f"gate    {g}")
    for g in restrict:
        print(f"gate    {g}")

    # ---- familiarity + ranking within the allowed family -------------------
    # L2 no longer REMOVES capped structures — it penalises them in the utility
    # (soft rule; the hard L1 veto is untouched). Regime still shapes the family list.
    allowed = [] if fatal else list(_REGIME_ALLOWED.get(regime, _STRUCTS))
    lab_idx = [i for i, r in enumerate(ms) if r["date"] in cs and i != ti]
    X = np.array([[(_f(r, c) if _f(r, c) is not None else np.nan) for c in _FEATS] for r in ms], float)
    mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    sims = []
    for j in lab_idx:
        m = ~np.isnan(Z[ti]) & ~np.isnan(Z[j])
        if m.sum() >= 8:
            sims.append((float(np.sqrt(np.mean((Z[ti][m] - Z[j][m]) ** 2))), j))
    sims.sort()
    top = sims[:args.k]
    avg_d = float(np.mean([d for d, _ in top])) if top else None
    familiarity = max(0.0, min(100.0, (1.6 - avg_d) / 1.6 * 100)) if avg_d is not None else 0.0
    fam_note = ("UNFAMILIAR STATE — historical guidance weak; trust gates, not ranking"
                if familiarity < 40 else "")
    print(f"\nfamiliarity {familiarity:.0f}%  (avg z-distance {avg_d:.2f} over {len(top)} "
          f"neighbours)  {fam_note}")

    chosen = "STAND_ASIDE"
    if allowed and top and familiarity >= 40:
        w = np.array([1.0 / (d + 0.25) for d, _ in top]); w /= w.sum()
        # ---- "WHICH STRUCTURES DESERVE CAPITAL?" — utility, not raw EV ---------
        # utility = EV − λ·|analogue worst| (λ=0.5 frozen; capped structures under an
        # L2 coverage restriction carry λ=1.0 as a SOFT PENALTY instead of removal —
        # hard rules are brittle; the journal arbitrates). L1 stays a hard veto.
        rank = []
        for s in allowed:
            pnl = np.array([_f(cs[ms[j]['date']], f"{s}_pnl") or 0.0 for _, j in top])
            wst = np.array([_f(cs[ms[j]['date']], f"{s}_worst") or 0.0 for _, j in top])
            ev, ws = float((w * pnl).sum()), float((w * wst).sum())
            lam = 1.0 if (restrict and s in _CAPPED) else 0.5
            rank.append({"s": s, "ev": ev, "worst": ws,
                         "util": ev - lam * abs(ws), "pen": lam > 0.5})
        rank.sort(key=lambda r: -r["util"])
        print(f"\nranking (utility = EV − λ·|worst|; λ=0.5, penalised {1.0}):")
        for r in rank:
            tag = (" [L2 penalty]" if r["pen"] else "") + \
                  ("" if r["s"] in _TRADABLE else " (naked — reference only, POLICY BAN)")
            print(f"   {r['s']:<10} EV ₹{r['ev']:>6.0f}  worst ₹{r['worst']:>6.0f}  "
                  f"utility ₹{r['util']:>6.0f}{tag}")
        # ---- selection restricted to DEFINED-RISK structures (user policy) ----
        trad = [r for r in rank if r["s"] in _TRADABLE]
        if not trad:
            print("   no defined-risk structure eligible → STAND ASIDE")
        else:
            # NB: fly and condor are ~0.92-correlated twins — a tie BETWEEN them says
            # nothing about whether to trade (the old all-tied→stand-aside band was
            # designed for a menu that included naked structures and misfires here).
            # Whether to trade is answered by gates + familiarity + a positive-utility
            # floor; ties among twins just break toward the smaller tail.
            top_u = trad[0]["util"]
            gap = top_u - trad[1]["util"] if len(trad) > 1 else float("inf")
            if top_u <= 0:
                print(f"   band: best defined-risk utility ₹{top_u:.0f} ≤ 0 — analogue "
                      f"days say the capped premium doesn't pay here → STAND ASIDE")
            else:
                near = [r for r in trad if top_u - r["util"] <= 0.15 * max(abs(top_u), 1.0)]
                if len(near) > 1:
                    pick = min(near, key=lambda r: abs(r["worst"]))
                    print(f"   band: near-tie (gap ₹{gap:.0f}) — broken toward the "
                          f"smaller tail: {pick['s']}")
                else:
                    pick = trad[0]
                    print(f"   band: CLEAR among defined-risk (gap ₹{gap:.0f})")
                chosen = pick["s"]
        # ---- EXPECTATION PRIOR: what kind of week does entry expect? ----------
        # Handed to the Management AI: if the realised tape diverges from this
        # prior, management should turn aggressive early. Frozen ex-post labels:
        # trend = |day move| ≥ 0.5%; pin = range ≤ 0.6×straddle; else mixed.
        probs = {"pin": 0.0, "trend": 0.0, "mixed": 0.0}
        for wt, (_, j) in zip(w, top):
            r = ms[j]
            dr = _f(r, "day_ret_1000_close_pct")
            rng, S = _f(r, "day_range_pts"), _f(r, "atm_straddle_pts")
            lab = ("trend" if dr is not None and abs(dr) >= 0.5 else
                   "pin" if rng is not None and S and rng <= 0.6 * S else "mixed")
            probs[lab] += float(wt)
        print("   expectation prior → " + "  ".join(f"{k} {100 * v:.0f}%"
                                                    for k, v in probs.items()) +
              "   (management inherits this; divergence = act)")
    elif not allowed:
        print("\nno structure families allowed in this regime/gates → STAND ASIDE")

    # ---- kill-list: what invalidates this AFTER entry ----------------------
    print("\nwhat could kill this:")
    if chosen != "STAND_ASIDE":
        print("   • ATM straddle expansion > +5% intraday → exit (repricing has begun)")
        print("   • wall migration > 30pts toward spot → coverage is deteriorating")
        print("   • overnight gap (unhedgeable): size so max loss is a normal Tuesday")
        if chosen not in _CAPPED:
            print("   • naked tail: a 400pt gap ≈ −₹20k+/lot — July 29 rate: ~1 week in 5")
    else:
        print("   • standing aside risks only forgone premium — the cheap mistake")

    print(f"\ndecision: {chosen if chosen != 'STAND_ASIDE' else 'STAND ASIDE'} (1 lot)")
    if chosen != "STAND_ASIDE":
        # CENTER: stays ATM until a drift predictor validates. The offset family was
        # tested (ATM+50 won one bullish month — drift artifact risk) and the entry-time
        # feature scan found no significant drift predictor (best |r|=0.56, n=20,
        # multiple-comparison-unsafe; morning momentum r≈0). Candidates under study:
        # pin_share, ΔPCR, dealer_center. Discretionary ±50 tilt costs only ~2% premium.
        print("center:   ATM (offset rule not yet validated — drift predictors "
              "pin_share/ΔPCR/dealer_center accumulating evidence)")
    if chosen != "STAND_ASIDE":
        rec = _record_position(target, chosen, args.db)
        if rec:
            print(f"position recorded → {rec[0]}   credit {rec[1]}pts  (knowledge/positions.csv)")
        else:
            print("position already recorded or not priceable from the 10:00 chain")

    j = _journal_upsert({"date": target, "regime": regime,
                         "gates": "; ".join(fatal + restrict) or "none",
                         "familiarity_pct": round(familiarity, 0), "chosen": chosen}, cs)
    graded = [r for r in j.values() if r.get("regret") not in (None, "", "None")]
    if graded:
        tot = sum(float(r["regret"]) for r in graded)
        print(f"\njournal: {len(j)} decisions, {len(graded)} graded — cumulative regret "
              f"₹{tot:.0f} (0 = always picked the hindsight-best) → {_JOURNAL}")


if __name__ == "__main__":
    main()
