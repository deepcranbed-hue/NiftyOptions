#!/usr/bin/env python3
"""
audit.py — INSTRUMENTED audit trail: how every conclusion was reached.

Design decision that matters
----------------------------
This is NOT an LLM narrating the model's reasoning after the fact. It is a trace
emitted BY the computation, as it happens.

That distinction is the whole point. A post-hoc explainer is free to confabulate a
tidy story that doesn't match what the code did — which is the exact failure mode
we already hit twice in this engine: the report said "Banks lagged" when the model
only knew "model scores Banks −0.35", and the decoupling section said "vs the tape"
when it meant "vs the model". A narrated audit would have cheerfully explained the
wrong thing. An instrumented one cannot: every line is written at the moment the
arithmetic executes, carrying the operands with it.

What a trail records
--------------------
  inputs    — every value, its SOURCE, timestamp, staleness, prior-vs-fitted
  steps     — the actual arithmetic: operands, formula, result, and why
  decisions — threshold crossings ("0.087 < 0.10 ⇒ Neutral, not Bullish")
  caveats   — coverage gaps, caps applied, suppressed drivers

Why this is the backtesting layer
---------------------------------
Each trail is a replayable snapshot keyed by date. Save them daily, later attach
what actually happened, and you can ask the question the model cannot currently
answer: *which factors were actually right?* That is precisely the loop that turns
our PRIOR weights into fitted posteriors. Explainability and calibration are the
same artifact here.

Usage
-----
    from audit import AuditTrail
    t = AuditTrail(as_of="2026-07-19")
    t.input("credit_growth", 18.6, source="RBI WSS", asof="2026-07-11", kind="fitted")
    t.step("Banks", "credit_growth", formula="budget×w×sign×signal",
           operands={"budget":0.25,"w":0.40,"sign":1,"signal":1.0}, result=0.10)
    t.decision("Banks", "Neutral", 0.087, {"bullish":0.10,"bearish":-0.10})
    print(t.to_markdown());  t.save()

CLI:
    python3 audit.py --list                     # saved trails
    python3 audit.py --show <run_id>            # replay one trail
    python3 audit.py --attribute realized.json  # per-factor hit rates
"""

from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

_HERE = Path(__file__).resolve().parent
TRAIL_DIR = _HERE / "audit_trails"


# ===========================================================================
class AuditTrail:
    """Records how a conclusion was reached. Deterministic; no LLM involved."""

    def __init__(self, as_of: str | None = None, run_id: str | None = None,
                 engine: str = "sector_factor_model"):
        self.as_of = as_of or dt.date.today().isoformat()
        self.run_id = run_id or f"{self.as_of}_{dt.datetime.now():%H%M%S}"
        self.engine = engine
        self.inputs: list[dict] = []
        self.steps: list[dict] = []
        self.decisions: list[dict] = []
        self.caveats: list[dict] = []

    # ---------------------------------------------------------- recording
    def input(self, name, value, source="", asof="", stale=False, kind="observed",
              note=""):
        """kind: observed | fitted | prior | flag — so a reader can tell measured
        data from judgement. This is the distinction the engine kept blurring."""
        self.inputs.append({"name": name, "value": value, "source": source,
                            "asof": asof, "stale": bool(stale), "kind": kind,
                            "note": note})
        return self

    def step(self, scope, name, result, formula="", operands=None, kind="prior",
             note=""):
        """One arithmetic step, with its operands, so it can be recomputed by hand."""
        self.steps.append({"scope": scope, "name": name, "formula": formula,
                           "operands": operands or {}, "result": result,
                           "kind": kind, "note": note})
        return self

    def decision(self, scope, verdict, value, thresholds=None, note=""):
        """A threshold crossing — WHY this label and not the neighbouring one."""
        th = thresholds or {}
        why = note
        if not why and th:
            hi, lo = th.get("bullish"), th.get("bearish")
            if hi is not None and lo is not None:
                if value > hi:
                    why = f"{value:+.3f} > {hi:+.2f} ⇒ Bullish"
                elif value < lo:
                    why = f"{value:+.3f} < {lo:+.2f} ⇒ Bearish"
                else:
                    why = (f"{lo:+.2f} ≤ {value:+.3f} ≤ {hi:+.2f} ⇒ Neutral "
                           f"(needs {hi - value:+.3f} more to turn Bullish)")
        self.decisions.append({"scope": scope, "verdict": verdict, "value": value,
                               "thresholds": th, "why": why})
        return self

    def caveat(self, scope, text, severity="info"):
        self.caveats.append({"scope": scope, "text": text, "severity": severity})
        return self

    # ------------------------------------------------------------- output
    def to_dict(self) -> dict:
        return {"schema": "audit_trail/v1", "run_id": self.run_id, "as_of": self.as_of,
                "engine": self.engine, "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "inputs": self.inputs, "steps": self.steps,
                "decisions": self.decisions, "caveats": self.caveats}

    def save(self, directory: Path | None = None) -> Path:
        d = Path(directory or TRAIL_DIR)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"trail_{self.run_id}.json"
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p

    def to_markdown(self, scope: str | None = None) -> str:
        L = [f"### 🔍 Audit trail — how this conclusion was reached",
             f"_run `{self.run_id}` · as of {self.as_of} · engine `{self.engine}` · "
             f"instrumented (emitted by the computation, not narrated afterwards)_\n"]

        ins = [i for i in self.inputs if not scope or i.get("name")]
        if ins:
            L.append("**① Inputs** — what went in, and whether it was measured or assumed\n")
            L.append("| Input | Value | Kind | Source | As of |")
            L.append("|---|---:|---|---|---|")
            for i in ins:
                mark = {"observed": "📈 observed", "fitted": "📊 fitted",
                        "prior": "🧠 prior", "flag": "🚩 flag"}.get(i["kind"], i["kind"])
                stale = " ⚠️stale" if i["stale"] else ""
                v = i["value"]
                vs = f"{v:+.3f}" if isinstance(v, float) else str(v)
                L.append(f"| {i['name']} | {vs} | {mark} | {i['source'] or '—'} | "
                         f"{i['asof'] or '—'}{stale} |")
            L.append("")

        sc_steps = [s for s in self.steps if not scope or s["scope"] == scope]
        if sc_steps:
            L.append("**② Derivation** — the actual arithmetic, recomputable by hand\n")
            L.append("| Scope | Factor | Formula | Operands | Result |")
            L.append("|---|---|---|---|---:|")
            for s in sc_steps:
                ops = ", ".join(f"{k}={v}" for k, v in (s["operands"] or {}).items())
                L.append(f"| {s['scope']} | {s['name']} | `{s['formula'] or '—'}` | "
                         f"{ops or '—'} | {s['result']:+.3f} |")
            L.append("")

        sc_dec = [d for d in self.decisions if not scope or d["scope"] == scope]
        if sc_dec:
            L.append("**③ Decision** — why this label and not the neighbouring one\n")
            for d in sc_dec:
                L.append(f"- **{d['scope']} → {d['verdict']}** · {d['why']}")
            L.append("")

        sc_cav = [c for c in self.caveats if not scope or c["scope"] == scope]
        if sc_cav:
            L.append("**④ Caveats** — what would make this wrong\n")
            for c in sc_cav:
                icon = {"warn": "⚠️", "error": "❌"}.get(c["severity"], "ℹ️")
                L.append(f"- {icon} _{c['scope']}_: {c['text']}")
            L.append("")
        return "\n".join(L)


# ===========================================================================
# BACKTEST ATTRIBUTION — turn saved trails + realised outcomes into hit rates.
# This is how the PRIOR weights eventually become fitted posteriors.
# ===========================================================================
def load_trails(directory: Path | None = None) -> list[dict]:
    d = Path(directory or TRAIL_DIR)
    out = []
    for p in sorted(d.glob("trail_*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def attribute(trails: list[dict], realized: dict) -> dict:
    """
    realized: {as_of: {scope: actual_pct_move}}  e.g. {"2026-07-19": {"Banks": 0.84}}

    For each FACTOR, ask: when this factor pushed positive, did the sector actually
    rise? That yields a per-factor hit rate — the empirical check our priors have
    never had. A factor at ~50% is noise regardless of how sound its story is.
    """
    stats: dict[str, dict] = {}
    for t in trails:
        day = realized.get(t.get("as_of"))
        if not day:
            continue
        for s in t.get("steps", []):
            actual = day.get(s["scope"])
            if actual is None or not s.get("result"):
                continue
            rec = stats.setdefault(s["name"], {"n": 0, "hits": 0, "sum_contrib": 0.0,
                                               "kind": s.get("kind", "prior")})
            rec["n"] += 1
            rec["sum_contrib"] += abs(s["result"])
            if (s["result"] > 0) == (actual > 0):
                rec["hits"] += 1
    for k, r in stats.items():
        r["hit_rate"] = round(r["hits"] / r["n"] * 100, 1) if r["n"] else None
        r["avg_contrib"] = round(r["sum_contrib"] / r["n"], 3) if r["n"] else None
        lo, hi = _wilson(r["hits"], r["n"])
        r["ci95"] = [lo, hi]
        # A raw hit rate is NOT evidence: at n=40 a pure-noise factor lands at 65%
        # roughly 1 run in 20. Demand the 95% LOWER BOUND clear the coin-flip line
        # before calling anything predictive — same discipline as the scorecard's
        # reliability bands.
        r["verdict"] = ("insufficient data (need ~30+)" if r["n"] < 30 else
                        "🟢 predictive" if lo > 55 else
                        "🟡 promising — CI still spans chance" if lo > 50 else
                        "⚪ indistinguishable from chance" if hi > 50 else
                        "🔴 inverted — sign may be backwards")
    return stats


def _wilson(hits: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a proportion, in percent. Robust at small n."""
    if not n:
        return (None, None)
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1))


def attribution_markdown(stats: dict) -> str:
    if not stats:
        return ("_No attribution yet — save trails daily, then supply realised moves. "
                "Roughly 20+ observations per factor before the hit rate means anything._")
    L = ["### 📊 Factor attribution (backtest)\n",
         "_Per-factor hit rate: when this factor pushed positive, did the sector actually "
         "rise? This is the empirical test the PRIOR weights have never faced._\n",
         "| Factor | N | Hit rate | 95% CI | Avg |contrib| | Verdict |",
         "|---|---:|---:|---|---:|---|"]
    for k, r in sorted(stats.items(), key=lambda kv: -((kv[1].get("ci95") or [0])[0] or 0)):
        hr = f"{r['hit_rate']:.1f}%" if r["hit_rate"] is not None else "—"
        lo, hi = r.get("ci95", [None, None])
        ci = f"{lo:.0f}–{hi:.0f}%" if lo is not None else "—"
        L.append(f"| {k} | {r['n']} | {hr} | {ci} | {r['avg_contrib']} | {r['verdict']} |")
    L.append("\n_Ranked by the **lower** CI bound, not the point estimate — a 70% hit rate on "
             "12 observations is weaker evidence than 58% on 300. A factor whose interval "
             "still contains 50% has not demonstrated anything, however good its story._")
    return "\n".join(L)


# --------------------------------------------------------------------- CLI
if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        ts = load_trails()
        print(f"{len(ts)} trail(s) in {TRAIL_DIR}")
        for t in ts:
            print(f"  {t['run_id']:24} {t['as_of']}  steps={len(t.get('steps',[]))} "
                  f"decisions={len(t.get('decisions',[]))}")
    elif "--show" in args:
        rid = args[args.index("--show") + 1]
        for t in load_trails():
            if t["run_id"] == rid:
                a = AuditTrail(as_of=t["as_of"], run_id=t["run_id"], engine=t["engine"])
                a.inputs, a.steps = t["inputs"], t["steps"]
                a.decisions, a.caveats = t["decisions"], t["caveats"]
                print(a.to_markdown())
                break
        else:
            print("no such run_id")
    elif "--attribute" in args:
        rp = Path(args[args.index("--attribute") + 1])
        print(attribution_markdown(attribute(load_trails(), json.loads(rp.read_text()))))
    else:
        print(__doc__)
