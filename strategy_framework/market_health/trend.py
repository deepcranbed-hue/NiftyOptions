"""
strategy_framework/market_health/trend.py
=========================================
The DAILY market-health score — a 0-100 "where are we in the trend/cycle" gauge,
built ONLY from the daily data we actually have (index + constituent daily bars).
Macro, Fundamentals and Institutional-Flow layers are deliberately OMITTED because
there is no trusted feed for them yet; when one arrives it becomes a new component
here, exactly like a signal joining the registry.

Two components, each a set of smooth 0..1 sub-scores times a point budget:

  A. INDEX TREND  (from the NIFTY daily series)
       price vs 200-DMA · price vs 50-DMA · 50/200 regime (golden/death) ·
       200-DMA slope · momentum (RSI + MACD histogram)
  B. TREND BREADTH  (from constituent daily series — lights up when they sync)
       % of members above their 200-DMA · % above 50-DMA · cap-weighted share
       above the 200-DMA (are the heavyweights participating)

Honesty (the whole point of doing this carefully)
-------------------------------------------------
* Every sub-score carries `data_ready`. A 200-DMA on < 200 sessions is not
  computed — it reports INSUFFICIENT_HISTORY, never a partial number dressed as a
  verdict (matches the framework's PRIOR-until-data rule / D-MA-04).
* The headline score is normalised over the points that HAVE data, and `coverage`
  states how much of the intended model was actually available. A score built on
  the index alone says so, rather than pretending the breadth layer contributed.
* The point weights are PRIOR — a reasonable prior, not a calibrated truth. They
  live in one place (`POINTS`) so a future calibration step can revise them without
  hunting through the code.

`market_health(db_path)` -> the full report dict (what the API / agent serve).
"""
from __future__ import annotations

from . import daily_bars as D

# ── PRIOR point budget (one place; calibratable later) ────────────────────────
# Proportions echo the user's framework once Macro/Fundamentals/Flows are dropped:
# price "technical" ≈ 40%, market "internals" (breadth + rotation + leadership) ≈ 60%.
POINTS = {
    # A. INDEX TREND — price structure from the NIFTY daily series
    "px_vs_200dma": 20.0,
    "px_vs_50dma": 10.0,
    "cross_50_200": 12.0,
    "slope_200dma": 10.0,
    "momentum": 8.0,
    # B. TREND BREADTH — participation (constituents)
    "breadth_above_200": 20.0,
    "breadth_above_50": 10.0,
    "breadth_weighted_200": 10.0,
    # C. SECTOR ROTATION — is the leadership risk-on (cyclicals) or risk-off (defensives)
    "sector_rotation": 14.0,
    "sector_breadth": 6.0,
    # D. LEADERSHIP QUALITY — are the heavyweight leaders making highs or breaking down
    "leaders_near_high": 12.0,
    "leaders_uptrend": 8.0,
}
_INDEX_KEYS = ("px_vs_200dma", "px_vs_50dma", "cross_50_200", "slope_200dma", "momentum")
_BREADTH_KEYS = ("breadth_above_200", "breadth_above_50", "breadth_weighted_200")
_SECTOR_KEYS = ("sector_rotation", "sector_breadth")
_LEADERSHIP_KEYS = ("leaders_near_high", "leaders_uptrend")

# Cyclical (risk-on) vs defensive (risk-off) sector map — a documented PRIOR
# convention. Cyclicals lead when growth/risk appetite is rising; defensives lead
# when the market turns cautious. Ambiguous names (IT as a USD/export play, Oil &
# Gas) are placed by their usual Indian-market behaviour; revise here if you disagree.
CYCLICAL = {"Financial Services", "Metals & Mining", "Automobile", "Consumer Durables",
            "Cement", "Oil & Gas", "Capital Goods", "Construction", "Services",
            "Consumer Services"}
DEFENSIVE = {"FMCG", "Healthcare", "Information Technology", "Power", "Telecommunication"}

# interpretation bands on the normalised 0-100 score (from the user's framework)
BANDS = [(80, "Strong uptrend"), (65, "Healthy uptrend"), (50, "Neutral / consolidation"),
         (35, "Weakening"), (0, "Defensive / downtrend")]

# ── display metadata served to the UI (single source — the panel hardcodes none) ──
# Friendly names for each scored component.
COMPONENT_LABELS = {
    "px_vs_200dma": "Price vs 200-day MA",
    "px_vs_50dma": "Price vs 50-day MA",
    "cross_50_200": "50/200 cross (regime)",
    "slope_200dma": "200-day MA slope",
    "momentum": "Momentum (RSI + MACD)",
    "breadth_above_200": "Breadth above 200-DMA",
    "breadth_above_50": "Breadth above 50-DMA",
    "breadth_weighted_200": "Weighted breadth (200-DMA)",
    "sector_rotation": "Sector rotation (risk on/off)",
    "sector_breadth": "Sectors participating",
    "leaders_near_high": "Leaders near highs",
    "leaders_uptrend": "Leaders above 50-DMA",
}

LAYER_LABELS = {
    "index_trend": "Index Trend", "trend_breadth": "Trend Breadth",
    "sector_rotation": "Sector Rotation", "leadership_quality": "Leadership Quality",
}

# Friendly label + one-line meaning for every raw number that appears in a
# component's detail. The UI reads THIS instead of printing `ma200:24867.7`.
FIELD_GLOSSARY = {
    "ma200": {"label": "200-day MA", "help": "Average closing price over the last 200 trading days — the long-term trend line."},
    "ma50": {"label": "50-day MA", "help": "Average closing price over the last 50 trading days — the intermediate trend line."},
    "px": {"label": "Spot", "help": "Latest NIFTY closing level."},
    "dist_pct": {"label": "Distance from MA", "help": "How far spot is above (+) or below (−) the moving average, in percent."},
    "gap_pct": {"label": "50/200 gap", "help": "How far the 50-day MA is above (+) or below (−) the 200-day MA, in percent."},
    "regime": {"label": "Regime", "help": "'golden' = 50-DMA above 200-DMA (bullish structure); 'death' = 50-DMA below 200-DMA (bearish)."},
    "slope_pct_20d": {"label": "200-MA slope (20d)", "help": "Percent change of the 200-day MA over the last 20 sessions — is the long trend itself rising or falling."},
    "turning": {"label": "Long trend", "help": "Whether the 200-day MA is currently rising or falling."},
    "rsi14": {"label": "RSI (14)", "help": "Relative Strength Index over 14 days: 0-100. >70 overbought/strong, <30 oversold/weak, 50 neutral."},
    "macd_hist": {"label": "MACD histogram", "help": "MACD line minus its signal line. Positive = upward momentum building, negative = fading."},
    "pct": {"label": "Share", "help": "Percent of members meeting the condition."},
    "weighted_pct": {"label": "Weighted share", "help": "Cap-weighted percent — heavyweights count more than small names."},
    "n": {"label": "Members counted", "help": "How many constituents had enough history to include."},
    "cyclical_strength": {"label": "Cyclical strength", "help": "Cap-weighted share of risk-on (cyclical) sectors above their 200-DMA, 0-1."},
    "defensive_strength": {"label": "Defensive strength", "help": "Cap-weighted share of risk-off (defensive) sectors above their 200-DMA, 0-1."},
    "tilt": {"label": "Rotation tilt", "help": "Cyclical strength minus defensive strength. Positive = risk-on leadership, negative = risk-off."},
    "leaning": {"label": "Leaning", "help": "Which side is leading: risk-on (cyclicals) is healthy, risk-off (defensives) is cautious."},
    "sectors_participating_pct": {"label": "Sectors participating", "help": "Percent of sectors where most members (by weight) are above their 200-DMA."},
    "sectors": {"label": "Sectors seen", "help": "Number of sectors with usable daily data."},
    "weighted_pct_near_high": {"label": "Leaders near high", "help": "Cap-weighted percent of the heavyweights within 5% of their trailing 100-day high."},
    "weighted_pct_above_50dma": {"label": "Leaders above 50-DMA", "help": "Cap-weighted percent of the heavyweights trading above their 50-day MA."},
    "n_leaders": {"label": "Leaders counted", "help": "How many of the heavyweight leaders had enough history."},
    "window_days": {"label": "High window", "help": "Look-back window (trading days) used for the 'near high' test."},
    "note": {"label": "Note", "help": ""},
}

# Short definitions of the recurring concepts, for a legend / help panel.
CONCEPTS = {
    "200-day MA (200-DMA)": "The average of the last 200 daily closes. The single most-watched long-term trend line — price above it is a structural uptrend, below it a downtrend.",
    "50-day MA (50-DMA)": "The average of the last 50 daily closes — the intermediate trend. Reacts faster than the 200-DMA.",
    "Golden / death cross": "When the 50-DMA crosses above the 200-DMA it is a 'golden cross' (bullish regime); crossing below is a 'death cross' (bearish regime).",
    "RSI (14)": "Relative Strength Index over 14 days, 0-100. Above 70 is strong/overbought, below 30 weak/oversold, 50 is neutral.",
    "MACD": "Moving Average Convergence Divergence. Its histogram (MACD minus signal line) turning positive signals building upward momentum.",
    "Breadth": "How many stocks — not just the index — are in an uptrend. Strong breadth means the advance is broad; weak breadth means a few names are carrying it.",
    "Cyclical vs defensive": "Cyclical sectors (banks, autos, metals) lead when risk appetite rises; defensives (FMCG, pharma, IT) lead when the market turns cautious.",
}


def display_meta() -> dict:
    """All the labels / definitions the UI needs, in one place, so no .tsx file
    hardcodes what a metric is called or means (CLAUDE.md DRY rule)."""
    return {"components": COMPONENT_LABELS, "layers": LAYER_LABELS,
            "fields": FIELD_GLOSSARY, "concepts": CONCEPTS}


def _lin(x: float, lo: float, hi: float) -> float:
    """Map x from [lo,hi] onto [0,1], clamped. lo may exceed hi to invert."""
    if hi == lo:
        return 0.5
    t = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def band(score: float) -> str:
    for thr, label in BANDS:
        if score >= thr:
            return label
    return BANDS[-1][1]


# ── component A: index trend ──────────────────────────────────────────────────
def index_trend(db_path: str, symbol: str = "NIFTY", as_of: str | None = None) -> dict:
    s = D.series(db_path, symbol, as_of=as_of)
    n = len(s)
    px = s.last_close
    subs: dict[str, dict] = {}

    def put(key, ready, unit01, detail):
        subs[key] = {"points": POINTS[key], "data_ready": ready,
                     "score01": (round(unit01, 4) if ready else None),
                     "awarded": (round(unit01 * POINTS[key], 2) if ready else None),
                     **detail}

    ma200 = D.sma(s.close, 200)
    ma50 = D.sma(s.close, 50)

    # price vs 200-DMA: -6%..+6% of the DMA maps to 0..1 (structural trend)
    if ma200 and px:
        d = (px / ma200 - 1.0) * 100.0
        put("px_vs_200dma", True, _lin(d, -6.0, 6.0),
            {"dist_pct": round(d, 2), "ma200": round(ma200, 1), "px": round(px, 1)})
    else:
        put("px_vs_200dma", False, 0.0, {"note": "need 200 daily sessions"})

    # price vs 50-DMA: -4%..+4%
    if ma50 and px:
        d = (px / ma50 - 1.0) * 100.0
        put("px_vs_50dma", True, _lin(d, -4.0, 4.0),
            {"dist_pct": round(d, 2), "ma50": round(ma50, 1)})
    else:
        put("px_vs_50dma", False, 0.0, {"note": "need 50 daily sessions"})

    # 50/200 regime: golden (50>200) vs death (50<200), scaled by the gap
    if ma50 and ma200:
        gap = (ma50 / ma200 - 1.0) * 100.0
        put("cross_50_200", True, _lin(gap, -3.0, 3.0),
            {"gap_pct": round(gap, 2), "regime": "golden" if gap >= 0 else "death"})
    else:
        put("cross_50_200", False, 0.0, {"note": "need 200 daily sessions"})

    # 200-DMA slope over ~20 sessions: -1%..+1% (is the long trend itself turning)
    sl = D.slope_pct(s.close, 200, look=20)
    if sl is not None:
        put("slope_200dma", True, _lin(sl, -1.0, 1.0),
            {"slope_pct_20d": round(sl, 3),
             "turning": "rising" if sl >= 0 else "falling"})
    else:
        put("slope_200dma", False, 0.0, {"note": "need 220 daily sessions"})

    # momentum: RSI(14) mapped 30..70 -> 0..1, blended with the MACD histogram sign
    # WHEN available. RSI alone (needs ~15 bars) is enough to score; MACD (needs ~35)
    # refines it if present — so momentum lights up as soon as RSI can be computed,
    # rather than waiting on MACD.
    r = D.rsi(s.close, 14)
    mac = D.macd(s.close)
    if r is not None:
        rsi01 = _lin(r, 30.0, 70.0)
        if mac is not None:
            hist01 = 1.0 if mac["hist"] > 0 else 0.0
            put("momentum", True, 0.7 * rsi01 + 0.3 * hist01,
                {"rsi14": round(r, 1), "macd_hist": round(mac["hist"], 2)})
        else:
            put("momentum", True, rsi01,
                {"rsi14": round(r, 1), "macd_hist": None, "note": "RSI only (MACD needs ~35 sessions)"})
    else:
        put("momentum", False, 0.0, {"note": "need ~15 daily sessions for RSI"})

    return {"symbol": symbol, "sessions": n, "as_of": s.last_date, "sub": subs}


# ── shared constituent loader (read each member's daily series ONCE) ───────────
def _load_constituents(db_path: str, as_of: str | None = None) -> dict:
    """{symbol: DailySeries} for every Nifty-50 member that has daily bars — loaded
    once and shared by the breadth / rotation / leadership modules so we don't read
    each series three times."""
    from ..config import constituents as K
    have = (set(D.available_daily_symbols(db_path)) & set(K.symbols())) - {"NIFTY"}
    out = {}
    for sym in sorted(have):
        s = D.series(db_path, sym, as_of=as_of)
        if len(s):
            out[sym] = s
    return out


def _wfrac(members: dict, predicate) -> tuple[float, float]:
    """(cap-weighted share, unweighted share) of `members` satisfying predicate(series)."""
    from ..config import constituents as K
    w_hit = w_tot = 0.0
    n_hit = n_tot = 0
    for sym, s in members.items():
        p = predicate(s)
        if p is None:
            continue
        w = K.weight_of(sym)
        w_tot += w
        n_tot += 1
        if p:
            w_hit += w
            n_hit += 1
    return ((w_hit / w_tot) if w_tot else 0.0), ((n_hit / n_tot) if n_tot else 0.0)


# ── component B: constituent trend breadth ────────────────────────────────────
def constituent_breadth(db_path: str, as_of: str | None = None, members: dict | None = None) -> dict:
    """% of Nifty-50 members trading above their own 200-/50-DMA, plus a
    cap-weighted share above the 200-DMA (heavyweight participation)."""
    members = _load_constituents(db_path, as_of) if members is None else members

    def above200(s):
        ma, px = D.sma(s.close, 200), s.last_close
        return (px >= ma) if (ma and px) else None

    def above50(s):
        ma, px = D.sma(s.close, 50), s.last_close
        return (px >= ma) if (ma and px) else None

    usable = sum(1 for s in members.values() if D.sma(s.close, 200) and s.last_close)
    ready = usable >= 10
    subs: dict[str, dict] = {}

    def put(key, unit01, detail):
        subs[key] = {"points": POINTS[key], "data_ready": ready,
                     "score01": (round(unit01, 4) if ready else None),
                     "awarded": (round(unit01 * POINTS[key], 2) if ready else None),
                     **detail}

    w200, u200 = _wfrac(members, above200)
    _, u50 = _wfrac(members, above50)
    put("breadth_above_200", u200, {"pct": round(u200 * 100, 1), "n": usable})
    put("breadth_above_50", u50, {"pct": round(u50 * 100, 1)})
    put("breadth_weighted_200", w200, {"weighted_pct": round(w200 * 100, 1)})

    return {"members_usable": usable, "ready": ready, "sub": subs,
            "note": None if ready else
            f"only {usable} constituents have ≥200 daily sessions — breadth pending "
            f"the constituent daily sync (index trend still scores)"}


# ── component C: sector rotation (risk-on vs risk-off leadership) ──────────────
def sector_rotation(db_path: str, as_of: str | None = None, members: dict | None = None) -> dict:
    """Are CYCLICAL (risk-on) sectors leading, or DEFENSIVE (risk-off) ones? For each
    sector, strength = cap-weighted share of its members above their 200-DMA; the
    rotation tilt is cyclical-strength − defensive-strength. Cyclicals leading reads
    healthy; a flight to defensives reads cautious."""
    from ..config import constituents as K
    members = _load_constituents(db_path, as_of) if members is None else members

    # per-sector cap-weighted "above 200-DMA" strength
    sec_w_above: dict[str, float] = {}
    sec_w_tot: dict[str, float] = {}
    sec_members: dict[str, int] = {}
    for sym, s in members.items():
        ma, px = D.sma(s.close, 200), s.last_close
        if not (ma and px):
            continue
        sec = K.sector_of(sym)
        w = K.weight_of(sym)
        sec_w_tot[sec] = sec_w_tot.get(sec, 0.0) + w
        sec_members[sec] = sec_members.get(sec, 0) + 1
        if px >= ma:
            sec_w_above[sec] = sec_w_above.get(sec, 0.0) + w
    sectors_seen = list(sec_w_tot.keys())
    usable = sum(sec_members.values())
    ready = usable >= 10 and len(sectors_seen) >= 4

    def _side_strength(side_set):
        num = sum(sec_w_above.get(sec, 0.0) for sec in sectors_seen if sec in side_set)
        den = sum(sec_w_tot[sec] for sec in sectors_seen if sec in side_set)
        return (num / den) if den else None

    cyc = _side_strength(CYCLICAL)
    dfn = _side_strength(DEFENSIVE)
    # tilt in [-1,1]: +1 cyclicals fully up & defensives fully down (risk-on)
    tilt = ((cyc if cyc is not None else 0.0) - (dfn if dfn is not None else 0.0))
    # sectors actually participating (majority of members above 200-DMA)
    part = sum(1 for sec in sectors_seen
               if sec_w_above.get(sec, 0.0) >= 0.5 * sec_w_tot[sec]) / max(1, len(sectors_seen))

    subs: dict[str, dict] = {}

    def put(key, unit01, detail):
        subs[key] = {"points": POINTS[key], "data_ready": ready,
                     "score01": (round(unit01, 4) if ready else None),
                     "awarded": (round(unit01 * POINTS[key], 2) if ready else None),
                     **detail}

    put("sector_rotation", _lin(tilt, -0.5, 0.5),
        {"cyclical_strength": (round(cyc, 3) if cyc is not None else None),
         "defensive_strength": (round(dfn, 3) if dfn is not None else None),
         "tilt": round(tilt, 3),
         "leaning": "risk-on (cyclicals)" if tilt >= 0 else "risk-off (defensives)"})
    put("sector_breadth", part,
        {"sectors_participating_pct": round(part * 100, 1), "sectors": len(sectors_seen)})

    return {"ready": ready, "sub": subs,
            "note": None if ready else "sector rotation pending constituent daily bars"}


# ── component D: leadership quality (are the leaders making highs?) ────────────
def leadership_quality(db_path: str, as_of: str | None = None, members: dict | None = None,
                       hi_window: int = 100) -> dict:
    """Are the market's LEADERS (the heavyweights) making higher highs, or breaking
    down? Two cap-weighted reads over the leaders: share within ~5% of their trailing
    `hi_window`-day high, and share above their 50-DMA. Leaders rolling over while the
    index holds up is a classic late-stage warning."""
    from ..config import constituents as K
    members = _load_constituents(db_path, as_of) if members is None else members
    leaders = {sym: s for sym, s in members.items() if sym in set(K.HEAVYWEIGHTS)}

    def near_high(s):
        if len(s) < hi_window:
            return None
        hi = max(s.close[-hi_window:])
        px = s.last_close
        if not (hi and px):
            return None
        return (px / hi - 1.0) >= -0.05        # within 5% of the trailing high

    def above50(s):
        ma, px = D.sma(s.close, 50), s.last_close
        return (px >= ma) if (ma and px) else None

    usable = sum(1 for s in leaders.values() if len(s) >= hi_window)
    ready = usable >= 5                          # need a handful of leaders

    subs: dict[str, dict] = {}

    def put(key, unit01, detail):
        subs[key] = {"points": POINTS[key], "data_ready": ready,
                     "score01": (round(unit01, 4) if ready else None),
                     "awarded": (round(unit01 * POINTS[key], 2) if ready else None),
                     **detail}

    w_hi, u_hi = _wfrac(leaders, near_high)
    w_up, _ = _wfrac(leaders, above50)
    put("leaders_near_high", w_hi,
        {"weighted_pct_near_high": round(w_hi * 100, 1), "n_leaders": usable,
         "window_days": hi_window})
    put("leaders_uptrend", w_up, {"weighted_pct_above_50dma": round(w_up * 100, 1)})

    return {"ready": ready, "sub": subs,
            "note": None if ready else "leadership quality pending heavyweight daily bars"}


# ── headline assembly ─────────────────────────────────────────────────────────
def market_health(db_path: str, as_of: str | None = None) -> dict:
    idx = index_trend(db_path, "NIFTY", as_of=as_of)
    members = _load_constituents(db_path, as_of)          # read each series ONCE
    brd = constituent_breadth(db_path, as_of=as_of, members=members)
    sec = sector_rotation(db_path, as_of=as_of, members=members)
    led = leadership_quality(db_path, as_of=as_of, members=members)
    all_sub = {**idx["sub"], **brd["sub"], **sec["sub"], **led["sub"]}

    awarded = sum(v["awarded"] for v in all_sub.values() if v["data_ready"])
    avail_pts = sum(v["points"] for v in all_sub.values() if v["data_ready"])
    total_pts = sum(POINTS.values())
    # normalise over AVAILABLE points → a 0-100 comparable across coverage levels
    score = round(100.0 * awarded / avail_pts, 1) if avail_pts else None
    coverage = round(100.0 * avail_pts / total_pts, 0)

    layer_score = {
        "index_trend": _layer(all_sub, _INDEX_KEYS),
        "trend_breadth": _layer(all_sub, _BREADTH_KEYS),
        "sector_rotation": _layer(all_sub, _SECTOR_KEYS),
        "leadership_quality": _layer(all_sub, _LEADERSHIP_KEYS),
    }
    thin = idx["sessions"] < 200
    layer_notes = [x["note"] for x in (brd, sec, led) if x.get("note")]
    return {
        "as_of": idx["as_of"], "sessions": idx["sessions"],
        "score": score, "band": band(score) if score is not None else None,
        "coverage_pct": coverage,
        "layers": layer_score,
        "components": all_sub,
        "bands": [{"min": t, "label": l} for t, l in BANDS],
        "meta": display_meta(),          # labels + definitions for the UI (single source)
        "omitted_layers": ["macro (no trusted feed)", "fundamentals (no earnings feed)",
                           "institutional_flows (no FII/DII feed)"],
        "prior": True,
        "notes": [n for n in [
                  (layer_notes[0] if layer_notes else None),
                  "point weights are PRIOR (a reasonable prior, not calibrated)",
                  ("index history < 200 sessions — 200-DMA reads unavailable" if thin else None)] if n],
        "disclaimer": "Descriptive market-health gauge, not financial advice.",
    }


def _layer(sub: dict, keys) -> dict:
    ks = [k for k in keys if k in sub]
    aw = sum(sub[k]["awarded"] for k in ks if sub[k]["data_ready"])
    ap = sum(sub[k]["points"] for k in ks if sub[k]["data_ready"])
    tp = sum(sub[k]["points"] for k in ks)
    return {"awarded": round(aw, 1), "available_points": round(ap, 1),
            "max_points": round(tp, 1),
            "pct": (round(100.0 * aw / ap, 1) if ap else None),
            "data_ready": ap > 0}
