"""
news_fundamentals.py
--------------------
The bridge between the fundamentals layer and the news tagger.

WHY THIS EXISTS
    The tagger's rubric grades an earnings beat as clear positive news. The market
    does not. On 2026-08-12 Apollo Hospitals reported PAT +38.4% YoY and the stock
    fell 3.49%; Grasim reported +51% and fell 3.79%. Neither was a miss against
    zero — both were misses against the growth the multiple had already embedded.

    Apollo traded at 66.4x trailing / 41.7x forward, which implies ~59.2% earnings
    growth. It delivered 38.4%. The gap is -20.8pp, and that is the number that
    predicted the sign, not the +38.4%.

    So: a print is scored against what is ALREADY IN THE PRICE, never against zero.

THE DIVISION OF LABOUR
    The LLM EXTRACTS (what number did the company report?).
    This module COMPARES (what number was already assumed?).

    Never ask the model to do the arithmetic. It cannot see the multiple, it will
    hallucinate a hurdle, and the result is unauditable. Extraction is a language
    task; the comparison is division.

MISSING DATA RETURNS None, NEVER 0
    Tata Motors has no usable trailing P/E in the snapshot. The gap is therefore
    UNCOMPUTABLE, and this module says so. It does not emit a 0.0 gap, because a
    0.0 gap reads as "met expectations exactly" — a substantive claim nobody made.
    Four separate bugs in this repo (see StrategyBacktesting/Hypotheses.md §2,
    corrections C1/C3/C5/C14) were a missing value silently replaced by a plausible
    one. This module refuses to add a fifth.

SOURCES (all dated; staleness is reported, not hidden)
    expectation_snapshots.json  trailing/forward P/E, analyst targets. Captured
                                BEFORE each print on purpose — the pre-announcement
                                consensus IS the hurdle and cannot be recovered
                                afterwards, since consensus is revised continuously.
    earnings_reactions.json     measured session-after behaviour, 2018->, identified
                                by VOLUME only (never by the size of the move, which
                                would make the finding circular). rel1d_pct is
                                relative to NIFTY; sect_rel1d_pct to the sector index.
    pe_history.json             where the current multiple sits in its own history.
    nifty-50-stock-list.csv     index weights, for converting a stock move into
                                index points.
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _p(name: str) -> str:
    return os.path.join(_ROOT, name)


# Recent-bias window. 8 events ~ two years of quarters: long enough to average out
# one odd print, short enough to notice the market changing its mind about a name.
_RECENT_EVENTS = 8

# Below this many analysts the forward P/E is one or two opinions wearing a
# consensus label, and the implied-growth hurdle is not meaningful.
_MIN_ANALYSTS = 3


def _load(path: str, default=None):
    try:
        with open(_p(path)) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _positive(x) -> Optional[float]:
    """A P/E of 0, negative, or None all mean 'no usable multiple' — a loss-making
    trailing twelve months, or a data gap. All three must degrade to None so the
    caller is forced to handle absence rather than divide by a placeholder."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def index_weights() -> dict[str, float]:
    try:
        with open(_p("nifty-50-stock-list.csv")) as f:
            return {r["Symbol"]: float(r["Weight"]) for r in csv.DictReader(f)}
    except Exception:
        return {}


def fundamental_context(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """Everything a tagger needs to judge a print about a constituent.

    Returns per symbol:
        index_weight_pct      share of the index
        trailing_pe / forward_pe
        implied_eps_growth_pct   trailing/forward - 1: the growth the price assumes.
                                 None when either multiple is unusable.
        analyst_n                how many opinions the forward P/E rests on
        target_upside_pct        consensus target vs the price at capture
        pe_percentile_now        where the multiple sits in its own 3y history
        reaction_recent_rel      mean rel1d over the last 8 results (vs NIFTY)
        reaction_full_rel        mean rel1d over all recorded results
        reaction_n
        as_of / stale_days       the snapshot is deliberately pre-print; report age
    """
    snaps = _load("expectation_snapshots.json", {}).get("snapshots") or []
    snap = snaps[-1] if snaps else {}
    rows = {r.get("symbol"): r for r in (snap.get("rows") or [])}
    captured = snap.get("captured_at")
    stale_days = None
    if captured:
        try:
            dt = datetime.fromisoformat(captured)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stale_days = round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 1)
        except Exception:
            pass

    pe_hist = _load("pe_history.json", {}).get("history") or {}
    events = _load("earnings_reactions.json", {}).get("events") or []
    weights = index_weights()

    by_sym: dict[str, list] = {}
    for e in events:
        by_sym.setdefault(e.get("symbol"), []).append(e)

    wanted = symbols or list(weights) or list(rows)
    out: dict[str, dict] = {}
    for s in wanted:
        r = rows.get(s, {})
        tp = _positive(r.get("trailingPE"))
        fp = _positive(r.get("forwardPE"))
        implied = (tp / fp - 1.0) * 100.0 if (tp and fp) else None

        ev = sorted(by_sym.get(s, []), key=lambda x: x.get("date", ""))
        rel = [x.get("rel1d_pct") for x in ev if isinstance(x.get("rel1d_pct"), (int, float))]
        recent = rel[-_RECENT_EVENTS:]

        tgt = r.get("targetMeanPrice")
        px = r.get("currentPrice") or r.get("regularMarketPrice")
        upside = ((tgt / px - 1.0) * 100.0) if (tgt and px) else None

        out[s] = {
            "index_weight_pct": weights.get(s),
            "trailing_pe": tp,
            "forward_pe": fp,
            "implied_eps_growth_pct": round(implied, 1) if implied is not None else None,
            "analyst_n": r.get("numberOfAnalystOpinions"),
            "target_upside_pct": round(upside, 1) if upside is not None else None,
            "pe_percentile_now": (pe_hist.get(s) or {}).get("percentile_now"),
            "reaction_recent_rel": round(sum(recent) / len(recent), 2) if recent else None,
            "reaction_full_rel": round(sum(rel) / len(rel), 2) if rel else None,
            "reaction_n": len(rel),
            "as_of": captured,
            "stale_days": stale_days,
        }
    return out


def hurdle_gap(symbol: str, reported_growth_pct: Optional[float],
               ctx: Optional[dict] = None) -> dict:
    """Score a reported earnings growth against the growth already in the price.

    gap = reported - implied.  A NEGATIVE gap means the company beat zero and
    missed the multiple — the configuration that produced Apollo (-20.8pp, stock
    -3.49%) and Grasim (-50.0pp, -3.79%) on 2026-08-12.

    Returns `computable: False` with a reason whenever any input is missing. The
    caller must not treat that as a neutral reading.
    """
    c = (ctx or fundamental_context([symbol])).get(symbol) or {}
    implied = c.get("implied_eps_growth_pct")
    n_an = c.get("analyst_n")

    if reported_growth_pct is None:
        return {"symbol": symbol, "computable": False, "reason": "no reported growth extracted"}
    if implied is None:
        return {"symbol": symbol, "computable": False,
                "reason": "no usable trailing/forward P/E (loss-making TTM or data gap)",
                "reported_growth_pct": reported_growth_pct}
    if n_an is not None and n_an < _MIN_ANALYSTS:
        return {"symbol": symbol, "computable": False,
                "reason": f"forward P/E rests on {int(n_an)} analyst(s); hurdle not meaningful",
                "implied_eps_growth_pct": implied, "reported_growth_pct": reported_growth_pct}

    gap = reported_growth_pct - implied
    # Deliberately coarse. The inputs are a stale-by-days consensus multiple and a
    # headline growth number; a finer scale would imply precision that is not there.
    if gap >= 15:
        verdict, lean = "BEAT the priced-in hurdle", +1
    elif gap <= -15:
        verdict, lean = "MISSED the priced-in hurdle", -1
    else:
        verdict, lean = "roughly met the priced-in hurdle", 0

    return {
        "symbol": symbol,
        "computable": True,
        "reported_growth_pct": round(reported_growth_pct, 1),
        "implied_eps_growth_pct": implied,
        "hurdle_gap_pp": round(gap, 1),
        "verdict": verdict,
        "lean": lean,
        "index_weight_pct": c.get("index_weight_pct"),
        "pe_percentile_now": c.get("pe_percentile_now"),
        "reaction_recent_rel": c.get("reaction_recent_rel"),
        "consensus_as_of": c.get("as_of"),
        "consensus_stale_days": c.get("stale_days"),
    }


def index_points(symbol: str, move_pct: float, index_level: float,
                 weights: Optional[dict] = None) -> Optional[float]:
    """Convert a stock move into index points — the unit that decides whether a
    story matters. On 2026-08-13 Max Healthcare fell 4.61% for -4.0 points while
    TCS fell 2.95% for -29.0. Sentiment cannot express that difference; weight can.
    """
    w = (weights or index_weights()).get(symbol)
    if w is None:
        return None
    return round(move_pct / 100.0 * w / 100.0 * index_level, 1)


def prompt_context(symbols: list[str], ctx: Optional[dict] = None) -> str:
    """Compact per-symbol block to inject into the tagger prompt.

    Gives the model the hurdle so it stops grading beats against zero — while the
    authoritative comparison still happens in hurdle_gap(), not in the model.
    """
    c = ctx or fundamental_context(symbols)
    lines = []
    for s in symbols:
        d = c.get(s) or {}
        if not d.get("index_weight_pct"):
            continue
        bits = [f"weight {d['index_weight_pct']}%"]
        if d.get("implied_eps_growth_pct") is not None:
            bits.append(f"price already assumes ~{d['implied_eps_growth_pct']}% EPS growth")
        else:
            bits.append("no usable P/E — do not infer a hurdle")
        if d.get("pe_percentile_now") is not None:
            bits.append(f"P/E at {d['pe_percentile_now']:.0%} of its 3y range")
        if d.get("reaction_recent_rel") is not None:
            bits.append(f"last {_RECENT_EVENTS} results averaged "
                        f"{d['reaction_recent_rel']:+.2f}% vs NIFTY next session")
        lines.append(f"- {s}: " + "; ".join(bits))
    if not lines:
        return ""
    return ("FUNDAMENTAL CONTEXT for the constituents named below. Grade an earnings "
            "print against the growth ALREADY ASSUMED, not against zero: a company can "
            "report +38% and still fall because the multiple assumed +59%.\n" + "\n".join(lines))


# Indian earnings headlines state growth in a small number of regular shapes:
#   "Net profit jumps 83% YoY", "PAT rises 38.4%", "profit surges 51% to Rs 2,146 cr".
# Regex handles these deterministically and auditably. The LLM is only consulted
# when it has already supplied `reported_growth_pct` itself — extraction by rule
# where the pattern is regular, model only where language genuinely varies.
_GROWTH_RE = re.compile(
    r"\b(?:net\s+|consolidated\s+|standalone\s+)?(?P<metric>profit|pat|earnings)\b"
    r"(?P<mid>[^.]{0,40}?)"
    r"\b(?P<verb>jump|surge|rise|soar|climb|grow|zoom|spike|"
    r"fall|drop|decline|slump|plunge|sink)\w*\s+"
    r"(?:by\s+)?(?P<num>\d{1,3}(?:\.\d)?)\s*%",
    re.I)
_NEGATIVE_VERBS = {"fall", "drop", "decline", "slump", "plunge", "sink"}
# If any of these sit between the profit token and the number, the percentage
# belongs to a DIFFERENT line item. "Grasim swings to profit in Q1, revenue rises
# 21% YoY" must not report 21% profit growth — that is revenue, and feeding it to
# a profit hurdle is a category error that would silently invert the verdict.
_OTHER_METRIC_RE = re.compile(
    r"\b(revenue|sales|topline|income|ebitda|margin|turnover|volume|aum|nii)\b", re.I)


def extract_reported_growth(text: str) -> Optional[float]:
    """Pull a reported PROFIT-growth percentage out of a headline.

    Returns None when the shape is not recognised or when the percentage plainly
    attaches to another metric — never a guess. A wrong number here is worse than
    no number, because the hurdle comparison downstream looks equally confident
    either way.
    """
    for m in _GROWTH_RE.finditer(text or ""):
        if _OTHER_METRIC_RE.search(m.group("mid")):
            continue          # the % belongs to revenue/EBITDA/margin, not profit
        try:
            v = float(m.group("num"))
        except ValueError:
            continue
        return -v if m.group("verb").lower() in _NEGATIVE_VERBS else v
    return None


def enrich_articles(articles: list[dict], ctx: Optional[dict] = None) -> list[dict]:
    """Attach fundamental context and the hurdle verdict to tagged articles.

    Runs AFTER constituent resolution and is deliberately independent of the LLM:
    if every provider is down and the keyword fallback ran, the hurdle arithmetic
    still executes. Mutates and returns the same list.

    Adds per article:
        fundamentals   per-named-symbol context (weight, implied growth, P/E percentile)
        hurdle         the comparison, or {computable: False, reason} when it cannot run
    """
    ctx = ctx if ctx is not None else fundamental_context()
    for a in articles:
        syms = [c.get("symbol") for c in (a.get("constituents") or []) if c.get("symbol")]
        if not syms:
            continue
        a["fundamentals"] = {s: ctx[s] for s in syms if s in ctx}
        reported = a.get("reported_growth_pct")
        if reported is None:
            reported = extract_reported_growth(
                f"{a.get('title','')} {a.get('description','') or a.get('body','')}")
        if reported is None:
            continue
        # Attribute the print to the single largest-weight name mentioned. A results
        # headline naming several companies is a market wrap, not one company's print;
        # weight picks the one whose number the headline is most likely quoting.
        primary = max(syms, key=lambda s: (ctx.get(s, {}).get("index_weight_pct") or 0))
        a["hurdle"] = hurdle_gap(primary, reported, ctx)
    return articles


if __name__ == "__main__":
    ctx = fundamental_context()
    cov = sum(1 for v in ctx.values() if v["implied_eps_growth_pct"] is not None)
    print(f"context built for {len(ctx)} symbols; implied-growth computable for {cov}")
    for sym, rep in [("APOLLOHOSP", 38.4), ("GRASIM", 51.0), ("TATAMOTORS", 83.3)]:
        print(" ", json.dumps(hurdle_gap(sym, rep, ctx)))

    print("\nheadline extraction (deterministic, no LLM):")
    for h in ["Apollo Hospitals Q1 Results: Net profit jumps 38% YoY to Rs 610 crore",
              "Grasim Industries swings to profit in Q1, revenue rises 21% YoY",
              "Tata Motors Q1 Results: Net profit surges 83% YoY to Rs 2,560 crore",
              "Senco Gold shares plunge over 14% to 4-week low after Q1 results",
              "IRCTC Q1 Results: Revenue rises 18%, profit remains largely flat",
              "Marksans Pharma Q1 profit spikes 174% YoY to Rs 159 crore",
              "XYZ Q1: revenue up 30%, net profit declines 12% on higher costs"]:
        print(f"   {extract_reported_growth(h)!s:>7}  <- {h[:64]}")
