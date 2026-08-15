"""Nifty 50 scan (/api/nifty50-view) — returns + fundamentals-relative pricing for all
index constituents.

Everything is computed ON DEMAND (the frontend button): nothing runs at import or app
start. One batch yfinance download gives 1Y of daily bars for all 50 names + ^NSEI,
from which 1D/1W/6M/1Y returns and the 52-week-range position are derived; a threaded
pass over Ticker.info collects trailing/forward P/E and P/B. The whole result is cached
in .state/nifty50_view_cache.json for 30 minutes (force=true bypasses).

"Priced high or low" is deliberately CATEGORICAL (per SECTOR_INTELLIGENCE_FRAMEWORK.md —
no invented fair values): each stock's trailing P/E (P/B fallback when P/E is missing or
negative) is compared to the MEDIAN of its own Nifty-50 sector peers; >+25% = rich,
<-25% = cheap, else in-line. Sectors with fewer than 3 valued peers fall back to the
whole-index median and are flagged. This is a cross-sectional heuristic — it ignores
growth and quality differences — and the payload says so.

Constituents come from nifty-50-stock-list.csv at the repo root (Symbol, Company Name,
Sector, Weight) — update that file when the index rebalances.
"""
from __future__ import annotations  # backend runs on py3.9 — keep `X | None` lazy

import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV_PATH = os.path.join(_REPO_ROOT, "nifty-50-stock-list.csv")
_DRIVERS_PATH = os.path.join(_REPO_ROOT, "nifty50_drivers.json")  # curated tailwinds/headwinds
_REACTIONS_PATH = os.path.join(_REPO_ROOT, "earnings_reactions.json")  # MEASURED earnings reactions
_EXPECT_PATH = os.path.join(_REPO_ROOT, "expectation_snapshots.json")  # pre-announcement expectations
_STATE_DIR = os.path.join(_REPO_ROOT, ".state")
_CACHE = os.path.join(_STATE_DIR, "nifty50_view_cache_v17.json")  # v17: gap self-calibration

# Heuristic Nifty trailing-P/E band (PRIOR, not a measurement — post-2021 NSE
# consolidated-earnings methodology; calibrate from history when wired to a P/E series):
# <18 cheap · 18-21 fair · 21-24 mildly rich · >24 rich
_PE_BAND = (18.0, 21.0, 24.0)
_TTL_S = 30 * 60

# vs-sector-median thresholds for the categorical verdict (display convention)
_RICH = 1.25
_CHEAP = 0.75

# A reversion-to-peer-median number is a reference point only while the stock is in the
# same postcode as its peers. At 670x trailing against a 30x median the arithmetic still
# resolves (-95.5%) but nobody should read that as a scenario, so we withhold it outside
# this band rather than printing a number that looks like a target.
_REVERSION_BAND = (1.0 / 3.0, 3.0)

# One trading year, and the floor below which we refuse to call anything a "1-year"
# return. yfinance's period="1y" yields ~248 sessions; a name with less history than
# _MIN_YEAR_BARS (a recent listing, or a demerged entity after the trim below) gets
# y1_pct=None instead of a shorter window silently labelled 1Y — which would also be
# differenced against the index's true year to make a meaningless rel_1y.
_YEAR_BARS = 248
_MIN_YEAR_BARS = 230

# Tickers whose Yahoo series splices the PRE-demerger parent's price onto the
# post-demerger entity. auto_adjust=True handles splits and dividends but NOT a
# demerger: the step on the record date is a change in what the share IS, not a return.
# Left in, TMPV reads -46% over "1Y" (the worst name in the index) with a "52-week high"
# of ~714 that belongs to undivided Tata Motors, and an "upside to 52-week high" of
# +107% that is pure arithmetic on a corporate action.
#
# We cut at the step itself rather than hardcoding a record date, so this needs no
# maintenance when the next demerger lands — add the ticker and nothing else.
_DEMERGED = {"TMPV"}
_DEMERGER_STEP = -0.25  # a single-session drop this large in one of these names IS the event

# Above this, a trailing P/E stops measuring "expensive" and starts measuring "the
# earnings denominator is near zero". Eternal at 670x on ~Rs 433 cr of TTM profit
# against a Rs 2.9 lakh cr market cap is the live case: the same company is ~3.6x
# sales and 9.3x book. Ranking that against a 30x peer median produces a confident
# number with no content, so these are marked non-comparable and kept out of the
# medians — the verdict still renders, flagged, but nothing else is polluted.
_PE_COMPARABLE_MAX = 100.0

# A Nifty 50 constituent essentially never moves this much in one session on news. When
# it appears, the overwhelmingly likely cause is a corporate action the price series has
# not been adjusted for yet — a bonus, split, demerger or special dividend going ex.
#
# yfinance's auto_adjust DOES back-adjust splits and bonuses, but only once Yahoo has
# applied the factor, and there is routinely a lag of a session or two. In that window a
# 1:1 bonus reads as a -50% "return". Nestle India's first-ever 1:1 bonus went ex on
# 2026-08-08, which is exactly this situation; the TMPV demerger was the same failure
# with a different label. Rather than enumerate every action, flag the shape.
_MAX_PLAUSIBLE_1D_PCT = 20.0

# NSE symbol changes (renames / demergers). The constituents CSV may carry EITHER the
# old or the new symbol depending on when it was generated, and Yahoo only resolves the
# current one — a stale symbol silently returns an empty row. We try the canonical
# ticker first and fall back to the alternate, so both CSV vintages work.
#   ZOMATO  -> ETERNAL : Zomato Ltd renamed Eternal Ltd (2025)
#   TATAMOTORS -> TMPV : 2025 demerger; the passenger-vehicle entity carries the index seat
# Add a row here whenever NSE renames a constituent; nothing else needs to change.
_TICKER_ALTS = {
    "ZOMATO": ["ETERNAL", "ZOMATO"],
    "ETERNAL": ["ETERNAL", "ZOMATO"],
    "TATAMOTORS": ["TMPV", "TATAMOTORS"],
    "TMPV": ["TMPV", "TATAMOTORS"],
}


def _candidates(symbol: str) -> list:
    """Yahoo ticker candidates for a CSV symbol, most-current first."""
    return _TICKER_ALTS.get(symbol, [symbol])


def load_constituents() -> list[dict]:
    if not os.path.exists(_CSV_PATH):
        raise HTTPException(status_code=500, detail=f"nifty-50-stock-list.csv not found at {_CSV_PATH}")
    out = []
    with open(_CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            if not sym:
                continue
            out.append({
                "symbol": sym,
                "name": (row.get("Company Name") or sym).strip(),
                "sector": (row.get("Sector") or "—").strip(),
                "weight": float(row.get("Weight") or 0) or None,
            })
    return out


def _read_cache() -> dict | None:
    try:
        with open(_CACHE, "r") as f:
            cached = json.load(f)
        if time.time() - cached.get("fetched_at", 0) <= _TTL_S:
            return cached
    except Exception:
        pass
    return None


def _trim_demerger(close, ticker: str):
    """Cut a flagged ticker's series at its demerger step. Returns (close, note|None).

    Scoped to _DEMERGED so a genuine -25% session in an ordinary name is never mistaken
    for a corporate action. We cut at the MOST RECENT qualifying step and keep the post
    -event series only if what remains is long enough to say anything at all.
    """
    if ticker not in _DEMERGED or len(close) < 2:
        return close, None
    try:
        steps = close.pct_change()
        hits = steps[steps <= _DEMERGER_STEP]
        if hits.empty:
            return close, None
        cut = hits.index[-1]
        trimmed = close[close.index >= cut]
        if len(trimmed) < 2:
            return close, None
        return trimmed, (
            f"Bars before {cut.date()} belong to the pre-demerger parent and are excluded — "
            f"returns and the 52-week range are measured from the demerger only "
            f"({len(trimmed)} sessions)."
        )
    except Exception:
        return close, None


def _returns_block(close) -> dict:
    """1D/1W/6M/1Y returns + 52w range position from a 1y daily close series.

    Every window is None unless the series is actually long enough to fill it — a short
    history yields honest gaps rather than a 3-month move printed in the 1Y column.
    Note that hi/lo are DIVIDEND-ADJUSTED closes (auto_adjust=True upstream), so for a
    high-yield name they sit below NSE's published 52-week extremes; the payload says so.
    """
    n = len(close)
    last = float(close.iloc[-1])

    def pct_back(k: int):
        if k <= 0 or n <= k:
            return None
        base = float(close.iloc[-1 - k])
        return round((last / base - 1.0) * 100.0, 2) if base else None

    lo, hi = float(close.min()), float(close.max())
    d1 = pct_back(1)
    return {
        "last": round(last, 2),
        "d1_pct": d1,
        # See _MAX_PLAUSIBLE_1D_PCT: almost certainly an unadjusted corporate action
        # rather than a return. Reported, not silently swallowed — the UI says so and
        # stops the figure being read as performance.
        "suspect_corporate_action": bool(d1 is not None and abs(d1) >= _MAX_PLAUSIBLE_1D_PCT),
        "w1_pct": pct_back(5),
        "m6_pct": pct_back(126),
        # A near-full year is still a year (period="1y" lands around 248 sessions); less
        # than _MIN_YEAR_BARS is not, and says so rather than shortening the window.
        "y1_pct": pct_back(min(n - 1, _YEAR_BARS)) if n >= _MIN_YEAR_BARS else None,
        "pos_52w": round((last - lo) / (hi - lo), 2) if hi > lo else None,
        "hi_52w": round(hi, 2),
        "lo_52w": round(lo, 2),
        # Range SCENARIOS (not forecasts): the move if price revisits its own 52w extremes.
        "up_to_high_pct": round((hi / last - 1.0) * 100.0, 1) if last else None,
        "down_to_low_pct": round((lo / last - 1.0) * 100.0, 1) if last else None,
        "as_of": str(close.index[-1].date()),
        "bars": n,
        # True when the "52-week" range and the return windows cover less than a year.
        "partial_history": n < _MIN_YEAR_BARS,
    }


_MAX_PLAUSIBLE_YIELD = 15.0   # no Nifty 50 constituent yields more than this
_FRACTION_CEILING = 0.25      # ...so a value under this can only be fraction-encoded


def _div_yield(info: dict):
    """Dividend yield in PERCENT, or None.

    Yahoo has served `dividendYield` as both a fraction (0.035) and a percent (3.5), and
    the value alone cannot disambiguate: 0.99 is Maruti's real 0.99% yield, not 99%. The
    old `x*100 if x < 1` rule inflated every sub-1% yield by 100x — 26 of the 50 names,
    with Maruti printing "div yield ~99%".

    So we derive it from dividendRate / price, which has no encoding ambiguity, and fall
    back to the raw field only when the rate is missing. That fallback keys on 0.25
    rather than 1.0: real equity yields between 0.25% and 1% are common, while a
    FRACTION above 0.25 would mean a 25% yield, which no constituent has. Anything that
    still lands beyond _MAX_PLAUSIBLE_YIELD is dropped rather than displayed.
    """
    rate = info.get("dividendRate")
    px = (info.get("currentPrice") or info.get("regularMarketPrice")
          or info.get("previousClose"))
    if isinstance(rate, (int, float)) and isinstance(px, (int, float)) and px:
        pct = float(rate) / float(px) * 100.0
    else:
        dy = info.get("dividendYield")
        if not isinstance(dy, (int, float)) or float(dy) <= 0:
            return None
        dy = float(dy)
        pct = dy * 100.0 if dy < _FRACTION_CEILING else dy
    if pct <= 0 or pct > _MAX_PLAUSIBLE_YIELD:
        return None
    return round(pct, 2)


def _fetch_info(sym: str) -> dict:
    """trailing/forward P/E + P/B via yfinance Ticker.info — slow, so threaded upstream.

    `fundamentals_ok` distinguishes "this company has no P/E because it loses money"
    from "Yahoo threw and we have nothing" — the two used to be indistinguishable, and
    the second silently drops a sector under the 3-peer threshold and flips every name
    in it to the whole-index median with no signal that anything failed.
    """
    import yfinance as yf
    try:
        info = yf.Ticker(f"{sym}.NS").info or {}
        def num(k):
            v = info.get(k)
            return round(float(v), 2) if isinstance(v, (int, float)) else None
        def pct(k):
            v = info.get(k)
            return round(float(v) * 100.0, 1) if isinstance(v, (int, float)) else None
        return {"pe": num("trailingPE"), "fwd_pe": num("forwardPE"), "pb": num("priceToBook"),
                "div_yield": _div_yield(info),
                # DELIVERED growth — the other half of the expectation gap. Yahoo reports
                # these as fractions (0.272 = +27.2%) and they are SINGLE year-on-year
                # readings, not trends.
                "earnings_growth_pct": pct("earningsGrowth"),
                "earnings_q_growth_pct": pct("earningsQuarterlyGrowth"),
                "revenue_growth_pct": pct("revenueGrowth"),
                "fundamentals_ok": True}
    except Exception:
        return {"pe": None, "fwd_pe": None, "pb": None, "div_yield": None,
                "earnings_growth_pct": None, "earnings_q_growth_pct": None,
                "revenue_growth_pct": None, "fundamentals_ok": False}


def _median(vals: list[float]) -> float | None:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _verdicts(rows: list[dict]) -> None:
    """Categorical rich/in-line/cheap vs sector-median P/E (P/B fallback), in place."""
    index_pe = _median([r["pe"] for r in rows if r.get("pe") and r["pe"] > 0])
    index_pb = _median([r["pb"] for r in rows if r.get("pb") and r["pb"] > 0])
    by_sector: dict = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)

    for sector, members in by_sector.items():
        pes = [m["pe"] for m in members if m.get("pe") and m["pe"] > 0]
        use_index = len(pes) < 3
        med_pe = index_pe if use_index else _median(pes)
        pbs = [m["pb"] for m in members if m.get("pb") and m["pb"] > 0]
        # Same index-median fallback as P/E: without it, a loss-maker in a small
        # sector (e.g. IndiGo in a 2-name Services bucket) got NO verdict at all.
        use_index_pb = len(pbs) < 3
        med_pb = index_pb if use_index_pb else _median(pbs)

        for m in members:
            metric, val, med = "pe", m.get("pe"), med_pe
            if not val or val <= 0:  # loss-maker or missing → P/B fallback
                metric, val, med = "pb", m.get("pb"), med_pb
            if not val or val <= 0 or not med:
                m["verdict"] = None
                continue
            ratio = val / med
            label = "rich" if ratio >= _RICH else "cheap" if ratio <= _CHEAP else "in-line"
            basis = "index" if ((metric == "pe" and use_index) or
                                (metric == "pb" and use_index_pb)) else "sector"
            rev_lo, rev_hi = _REVERSION_BAND
            m["verdict"] = {
                "label": label,
                "metric": metric,
                "value": round(val, 2),
                "vs_median_pct": round((ratio - 1.0) * 100.0, 1),
                "sector_median": round(med, 2),
                "basis": basis,
                # How many peers the median actually rests on — a "sector median" over
                # 3 names and one over 11 are not the same claim, and a 1-member sector
                # on the index fallback is barely a claim at all.
                "peers": len(pes) if metric == "pe" else len(pbs),
                # Reversion SCENARIO (not a forecast): the price move implied if this
                # stock's multiple went to the peer median with earnings/book unchanged.
                # Withheld outside _REVERSION_BAND — see the constant.
                "reversion_pct": (round((med / val - 1.0) * 100.0, 1)
                                  if rev_lo <= ratio <= rev_hi else None),
                "reversion_note": (None if rev_lo <= ratio <= rev_hi else
                                   "Withheld: this multiple is too far from the peer median "
                                   "for reversion to be a meaningful reference point."),
            }


# Every metric the UI shows a number for, with the direction that counts as "good" and
# the word to use when this name leads its peers on it. The superlative travels in the
# payload so the phrasing lives with the metric definition rather than being re-guessed
# in the UI: a low P/E is "cheapest", a low P/B is "cheapest", a high 6M return is
# "strongest", a high yield is "highest-yielding".
#   direction +1 → higher value ranks first;  -1 → lower value ranks first.
_CONTEXT_FIELDS = {
    "d1_pct":      {"dir": +1, "sup": "strongest today",   "label": "1-day return"},
    "w1_pct":      {"dir": +1, "sup": "strongest",         "label": "1-week return"},
    "m6_pct":      {"dir": +1, "sup": "strongest",         "label": "6-month return"},
    "y1_pct":      {"dir": +1, "sup": "strongest",         "label": "1-year return"},
    "pe":          {"dir": -1, "sup": "cheapest",          "label": "trailing P/E"},
    "fwd_pe":      {"dir": -1, "sup": "cheapest",          "label": "forward P/E"},
    "pb":          {"dir": -1, "sup": "cheapest",          "label": "P/B"},
    "div_yield":   {"dir": +1, "sup": "highest-yielding",  "label": "dividend yield"},
    "pos_52w":     {"dir": +1, "sup": "closest to its high", "label": "52-week range position"},
}


def _rank(values: list, target, direction: int) -> int | None:
    """1-based rank of `target` among `values`, counting from the favourable end."""
    vals = sorted((v for v in values if v is not None), reverse=(direction > 0))
    try:
        return vals.index(target) + 1
    except ValueError:
        return None


def _context(rows: list[dict]) -> None:
    """Where each number sits vs its sector peers and vs the 50, in place.

    This is what turns a bare "20.1" into "2nd cheapest of the 11 financials; index
    median 30.1". Everything here is derived from the scan that already ran — no extra
    fetch — and it is deliberately RANK + MEDIAN rather than a score: a rank says where
    the name sits without implying the gap between ranks means anything.

    P/E entries carry `comparable: false` above _PE_COMPARABLE_MAX. A 670x trailing P/E
    is a near-zero earnings denominator, not an expensive stock, and letting it rank
    against 30x peers produces confident nonsense — so the read says so and the number
    is excluded from the medians it would otherwise drag.
    """
    by_sector: dict = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)

    for r in rows:
        ctx: dict = {}
        peers = by_sector[r["sector"]]
        for field, cfg in _CONTEXT_FIELDS.items():
            val = r.get(field)
            if val is None:
                continue
            comparable = not (field in ("pe", "fwd_pe") and val > _PE_COMPARABLE_MAX)

            def pool(rs):
                # A non-comparable multiple is excluded from everyone else's medians too.
                return [x.get(field) for x in rs
                        if x.get(field) is not None
                        and not (field in ("pe", "fwd_pe") and x[field] > _PE_COMPARABLE_MAX)]

            sec_vals, idx_vals = pool(peers), pool(rows)
            ctx[field] = {
                "value": val,
                "comparable": comparable,
                "sector": r["sector"],
                "sector_rank": _rank(sec_vals, val, cfg["dir"]) if comparable else None,
                "sector_n": len(sec_vals),
                "sector_median": _median(sec_vals),
                "index_rank": _rank(idx_vals, val, cfg["dir"]) if comparable else None,
                "index_n": len(idx_vals),
                "index_median": _median(idx_vals),
                "superlative": cfg["sup"],
                "label": cfg["label"],
            }
        # Returns get one extra comparator the others don't need: the index's own move
        # over the identical window, which is what makes "lagged" meaningful.
        r["context"] = ctx


# India 10-year G-sec, the discount-rate anchor for the earnings-yield gap below.
# Dated FALLBACK, used only when the live cue cache can't supply one. Source:
# tradingeconomics.com/india/government-bond-yield
_GSEC_10Y_PCT = 6.78
_GSEC_10Y_AS_OF = "2026-08-07"
_GSEC_10Y_YOY_PP = 0.40   # up this much in a year — the de-rating force, quantified

_CUES_CACHE = os.path.join(_REPO_ROOT, "global_cues_cache.json")
# Any 10-year sovereign yield outside this band is a unit error or a bad parse, not a
# market move. Cheap guard against silently importing a price index as a percentage.
_GSEC_PLAUSIBLE = (4.0, 12.0)


def _gsec_10y() -> dict:
    """The 10-year yield, preferring the app's own cue cache over the dated constant.

    IMPORTANT — do NOT reach for macro.factor_series IN10Y_INDEX here. That series is
    the Upstox 'Nifty GS 10Yr Cln' CLEAN PRICE index (level ~875), not a yield: bond
    price up means yield DOWN, so using it as a rate inverts the sign and the unit.
    The repo already treats it correctly (bank_factor_regression consumes it as a pct
    CHANGE; validate_rate_regime calls it a price index in so many words) — this note
    exists so the next person wiring a discount rate doesn't grab the wrong series.

    The real yield lives in global_cues_cache.json -> close_levels['India 10Y'].
    """
    out = {"pct": _GSEC_10Y_PCT, "as_of": _GSEC_10Y_AS_OF,
           "source": "dated constant (tradingeconomics)", "live": False, "disagreement_bp": None}
    try:
        with open(_CUES_CACHE, "r") as f:
            cues = json.load(f)
        y = (cues.get("close_levels") or {}).get("India 10Y")
        if isinstance(y, (int, float)) and _GSEC_PLAUSIBLE[0] < float(y) < _GSEC_PLAUSIBLE[1]:
            y = float(y)
            out.update({
                "pct": y, "live": True,
                "source": "global_cues_cache.json · close_levels['India 10Y']",
                # The cue cache carries no as_of for the India curve and hardcodes its
                # daily change to 0.0, so freshness here is unknown by construction.
                "as_of": None,
                "daily_change_bp": (cues.get("cues") or {}).get("India 10Y"),
                "disagreement_bp": round((y - _GSEC_10Y_PCT) * 100),
                "fallback_pct": _GSEC_10Y_PCT, "fallback_as_of": _GSEC_10Y_AS_OF,
            })
    except Exception:
        pass
    return out


# Gap thresholds, in percentage POINTS of growth (implied minus delivered).
# Deliberately wide: a single year-on-year earnings reading is noisy, so a small gap
# means nothing at all.
_GAP_HIGH_BAR = 30.0     # priced for this much more than delivered
_GAP_LOW_BAR = -10.0     # delivering this much more than priced

# Growth-quality split points. Both sit close to the current cross-sectional medians
# (ROE ~15.2%, embedded growth ~31%), chosen as round numbers so the quadrant a name
# falls into does not flicker between scans as the medians drift.
_ROE_STRONG_PCT = 15.0
_GROWTH_HIGH_PCT = 30.0
# Above this, ROE is usually telling you about a thin equity base — a high payout, a
# buyback history, negative working capital — rather than superior operating returns.
# Nestle India prints ~74% on a book value of roughly Rs 27 a share. Flag, don't hide.
_ROE_THIN_EQUITY_PCT = 40.0

# Below this many names a cross-sectional correlation is an anecdote with a decimal
# point on it, so the calibration block is withheld entirely rather than shipped weak.
_CALIB_MIN_NAMES = 20
# |r| below this is indistinguishable from nothing at these sample sizes.
_CALIB_NOISE_R = 0.30


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Plain correlation. Returns None when either series is flat or too short."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / ((sxx ** 0.5) * (syy ** 0.5)), 3)


def _gap_calibration(out: list[dict]) -> dict | None:
    """Does the gap actually predict anything? Measured, and the answer is: barely.

    WHY THIS IS HERE AT ALL. Every other number in this tab describes what the market
    is assuming. None of them establishes that the gap is INFORMATIVE. A tool that
    ranks 47 names and stays silent about its own hit rate invites the reader to
    supply a confidence it never earned. So the tab tests itself against the one
    measured, non-judgmental dataset in this repo — earnings_reactions.json, where
    announcement days are identified by volume alone — and prints the result whether
    or not it flatters the idea. It does not.

    WHAT THIS IS NOT: a backtest. The gap is measured TODAY; the reactions run back to
    2018. Nothing here is aligned in time, so this cannot show that a large gap
    PRECEDED a bad results-day move. It answers only the weaker question: do the names
    the market is currently asking the most from also happen to be the names that have
    historically been punished on results? A strong relationship would have been
    suggestive. A null one — which is what comes back — closes off the reading that
    the gap is a signal, and that is the direction the evidence actually points.

    Reading it: r near 0 means the ranking carries no cross-sectional information about
    results-day behaviour. Compare the band means too — if the high-bar group's average
    reaction is not worse than the low-bar group's, the thresholds are drawing a line
    through noise.
    """
    try:
        pairs = [(x["gap_pp"], x["_rx_full"], x["_rx_recent"]) for x in out
                 if x.get("_rx_full") is not None]
        if len(pairs) < _CALIB_MIN_NAMES:
            return None
        r_full = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        rec = [(g, rr) for g, _, rr in pairs if rr is not None]
        r_recent = (_pearson([p[0] for p in rec], [p[1] for p in rec])
                    if len(rec) >= _CALIB_MIN_NAMES else None)

        def band_mean(band: str):
            vals = [x["_rx_full"] for x in out
                    if x["band"] == band and x.get("_rx_full") is not None]
            return (round(sum(vals) / len(vals), 2), len(vals)) if vals else (None, 0)

        hi, hi_n = band_mean("high bar")
        lo, lo_n = band_mean("low bar")
        mid, mid_n = band_mean("in line")
        # The test the thresholds have to pass: high-bar names should react WORSE.
        ordered = (hi is not None and lo is not None and hi < lo)
        strong = bool(r_full is not None and abs(r_full) >= _CALIB_NOISE_R)
        return {
            "names": len(pairs),
            "events": sum(x["_rx_n"] for x in out if x.get("_rx_n")),
            "r_gap_vs_full_reaction": r_full,
            "r_gap_vs_recent_reaction": r_recent,
            "band_mean_rel_pct": {"high bar": hi, "in line": mid, "low bar": lo},
            "band_n": {"high bar": hi_n, "in line": mid_n, "low bar": lo_n},
            "bands_ordered_as_theory_predicts": ordered,
            "informative": bool(strong and ordered),
            "noise_threshold_r": _CALIB_NOISE_R,
            "verdict": (
                "The gap ranks questions. On this repo's own measured data it does not "
                "rank outcomes." if not (strong and ordered) else
                "A relationship is present in the cross-section — still not a backtest."),
            "note": (
                "SELF-TEST, and it is deliberately unflattering. Correlation between each "
                "name's current gap and how it has actually traded the session after results "
                "(versus NIFTY), from earnings_reactions.json. This is NOT a backtest and "
                "cannot be one: the gap is measured today while the reactions run back to "
                "2018, so nothing is aligned in time. It answers the weaker question of "
                "whether the names the market asks most from are the names results-day has "
                f"punished. |r| under {_CALIB_NOISE_R} is indistinguishable from zero at "
                "this sample size. If the band means are not ordered high-bar-worst, the "
                "thresholds are drawing a line through noise. Treat the gap as a way of "
                "generating questions to research, not as a signal to act on — the tab "
                "prints this whether or not it supports the idea."),
        }
    except Exception:
        return None


def _expectation_gap(rows: list[dict], snap_by_sym: dict, eps_hist: dict) -> dict | None:
    """Implied growth MINUS delivered growth — the bar against the track record.

    WHY THIS EXISTS: implied growth alone does not discriminate. Across the 47 valued
    names the median is ~31% and only 4 are priced for a decline, so reading a high
    number as "good potential" marks nine names in ten as attractive — which is a
    description of the market, not a screen. Worse, it reads backwards: a stock priced
    for +103% has to nearly double earnings to justify today's price. That is a bigger
    promise, not a bigger opportunity.

    The gap fixes the direction. A name priced for +103% while delivering +12% has a
    91pp bar to clear; one priced for +15% while delivering +40% is being asked for less
    than it has been producing.

    WHAT IT IS NOT: delivered growth here is Yahoo's earningsGrowth, a SINGLE
    year-on-year reading, not a trend — noisy, and enormous off a small base (Eternal
    prints +233%). And a cyclical at a trough will always show a huge gap: depressed
    trailing earnings inflate implied growth while delivered growth is negative. That is
    the cycle, not a mispricing. Tata Steel is the live example. So this ranks questions,
    not stocks, and the payload says so.
    """
    try:
        out = []
        for r in rows:
            pe, fwd = r.get("pe"), r.get("fwd_pe")
            if not (pe and fwd and pe > 0 and fwd > 0):
                continue
            if pe > _PE_COMPARABLE_MAX or fwd > _PE_COMPARABLE_MAX:
                continue  # same non-comparable rule as everywhere else
            implied = (pe / fwd - 1.0) * 100.0
            delivered = r.get("earnings_growth_pct")
            if delivered is None:
                snap = next((snap_by_sym[k] for k in _candidates(r["symbol"])
                             if k in snap_by_sym), None)
                if snap and isinstance(snap.get("earningsGrowth"), (int, float)):
                    delivered = round(float(snap["earningsGrowth"]) * 100.0, 1)
            if delivered is None:
                continue
            gap = implied - delivered

            # --- growth QUALITY: does the growth earn anything? --------------------
            # ROE falls out of two numbers already fetched, no new data required:
            #     P/B / P/E = (P/B) x (E/P) = E/B = ROE
            # This is the term a pure P/E comparison cannot see. Two companies priced
            # for the same growth are not equivalent if one earns 26% on equity and the
            # other 5% — growth funded at low returns consumes capital while EPS rises.
            #
            # LIMITATIONS, both real and both surfaced rather than buried:
            #  * ROE is a BACKWARD-LOOKING AVERAGE over all capital. What actually
            #    matters for growth is the return on the NEXT rupee invested — return on
            #    incremental capital — which needs a book-value history this scan does
            #    not carry. A legacy business can post 30% ROE while deploying new money
            #    at 8%.
            #  * ROE is not comparable across capital structures. Leverage inflates it,
            #    so a bank at 15% and an asset-light software firm at 15% are not the
            #    same fact, and a thin equity base inflates it further still.
            pb = r.get("pb")
            roe = round(pb / pe * 100.0, 1) if (pb and pb > 0) else None
            thin_equity = bool(roe is not None and roe >= _ROE_THIN_EQUITY_PCT)
            quadrant = None
            if roe is not None:
                hi_g = implied >= _GROWTH_HIGH_PCT
                hi_r = roe >= _ROE_STRONG_PCT
                # Deliberately DESCRIPTIVE, not a verdict. "Quality growth" and "value
                # trap" are conclusions; this app's job is to state the combination and
                # leave the judgment to the reader.
                quadrant = ("priced for growth · high return" if (hi_g and hi_r)
                            else "priced for growth · low return" if hi_g
                            else "priced for little · high return" if hi_r
                            else "priced for little · low return")

            # --- normalised delivered growth, and the UNITS FIX -------------------
            # Embedded growth is a TOTAL change from trailing EPS to the forward
            # estimate; a CAGR is PER YEAR. Differencing them raw overstates the gap.
            # The conventional reading of trailing-vs-forward is ~1 year, so the 1y
            # annualisation is the identity — but Yahoo's forward multiple can reach
            # into the following fiscal year, and at a 2-year horizon the annual rate
            # is sqrt(1+g)-1, which is much lower. Both are shipped so the sensitivity
            # is visible rather than buried in an assumption.
            hist = next((eps_hist[k] for k in _candidates(r["symbol"]) if k in eps_hist), None)
            norm = None
            norm_basis = None
            if hist:
                for key, label in (("cagr_3y_pct", "3-yr CAGR"),
                                   ("cagr_5y_pct", "5-yr CAGR"),
                                   ("cagr_full_pct", "full-history CAGR")):
                    if hist.get(key) is not None:
                        norm, norm_basis = hist[key], label
                        break
            ann_1y = implied
            ann_2y = ((1.0 + implied / 100.0) ** 0.5 - 1.0) * 100.0 if implied > -100 else None
            # Carried only so the tab can test ITSELF (see _gap_calibration). Leading
            # underscore = internal; stripped before the payload leaves this function so
            # the client never sees a field it has no contract for.
            rx = r.get("reaction") or {}
            out.append({
                "_rx_full": rx.get("full_mean_rel1d_pct"),
                "_rx_recent": rx.get("recent_mean_rel1d_pct"),
                "_rx_n": rx.get("n_events"),
                "roe_pct": roe,
                "roe_thin_equity": thin_equity,
                "quality_quadrant": quadrant,
                "normalized_growth_pct": norm,
                "normalized_basis": norm_basis,
                "normalized_sign_change": bool(hist.get("sign_change")) if hist else None,
                "implied_annualised_1y_pct": round(ann_1y, 1),
                "implied_annualised_2y_pct": round(ann_2y, 1) if ann_2y is not None else None,
                "gap_vs_normalized_pp": round(ann_1y - norm, 1) if norm is not None else None,
                "gap_vs_normalized_2y_pp": (round(ann_2y - norm, 1)
                                            if (norm is not None and ann_2y is not None) else None),
                "symbol": r["symbol"], "name": r["name"], "sector": r["sector"],
                "weight": r.get("weight"),
                "trailing_pe": pe, "forward_pe": fwd,
                "implied_growth_pct": round(implied, 1),
                "delivered_growth_pct": round(delivered, 1),
                "gap_pp": round(gap, 1),
                "revenue_growth_pct": r.get("revenue_growth_pct"),
                "band": ("high bar" if gap >= _GAP_HIGH_BAR
                         else "low bar" if gap <= _GAP_LOW_BAR else "in line"),
                # A trough cyclical manufactures a big gap mechanically. Flag rather than
                # hide: the reader needs to know WHY the gap is large.
                "cyclical_caution": bool(delivered < 0 and implied > _GAP_HIGH_BAR),
            })
        if not out:
            return None
        # Rank by the normalised gap when it exists — that is the better comparison —
        # falling back to the single-quarter gap for names without EPS history.
        out.sort(key=lambda x: -(x["gap_vs_normalized_pp"]
                                 if x["gap_vs_normalized_pp"] is not None else x["gap_pp"]))
        gaps = sorted(x["gap_pp"] for x in out)
        med = (gaps[len(gaps) // 2] if len(gaps) % 2
               else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2.0)
        # Measure BEFORE stripping the internal reaction fields, then drop them so the
        # row schema the client consumes stays exactly what nifty50Shared declares.
        calibration = _gap_calibration(out)
        for x in out:
            x.pop("_rx_full", None), x.pop("_rx_recent", None), x.pop("_rx_n", None)
        return {
            "calibration": calibration,
            "rows": out, "names": len(out),
            "median_gap_pp": round(med, 1),
            "median_implied_pct": round(_median([x["implied_growth_pct"] for x in out]) or 0, 1),
            "median_delivered_pct": round(_median([x["delivered_growth_pct"] for x in out]) or 0, 1),
            "high_bar": sum(1 for x in out if x["band"] == "high bar"),
            "low_bar": sum(1 for x in out if x["band"] == "low bar"),
            "in_line": sum(1 for x in out if x["band"] == "in line"),
            "thresholds_pp": [_GAP_LOW_BAR, _GAP_HIGH_BAR],
            "roe_strong_pct": _ROE_STRONG_PCT, "growth_high_pct": _GROWTH_HIGH_PCT,
            "quadrant_counts": {q: sum(1 for x in out if x["quality_quadrant"] == q)
                                for q in ("priced for growth · high return",
                                          "priced for growth · low return",
                                          "priced for little · high return",
                                          "priced for little · low return")},
            "normalized_available": sum(1 for x in out if x["normalized_growth_pct"] is not None),
            "note": (
                "STATUS: a guideline, not a signal — see the self-test above, which is run on "
                "this repo's own measured earnings reactions and reported whether or not it "
                "supports the idea. Use this to decide what to research next; do not size a "
                "position off it. "
                "Gap = growth PRICED IN minus growth RECENTLY DELIVERED, in percentage points. "
                "It measures the SIZE of the bet, NOT its direction. A forward P/E below trailing "
                "simply means earnings are expected to grow, which is not a warning: if consensus "
                "is met and the multiple holds, the holder earns roughly the growth. Tata Steel at "
                "+103% embedded returns +103% if delivered and re-rated at today's trailing "
                "multiple, 0% if delivered but the multiple compresses to the forward one, and "
                "-40% on a shortfall. A large gap means a WIDE range of outcomes both ways. "
                "Symmetrically, a low or negative gap is not safety — NTPC's -14% is low because "
                "earnings are forecast to FALL. What decides the outcome is whether growth "
                "persists beyond the forecast year, which is what lets a compounder hold a high "
                "trailing multiple indefinitely. Delivered growth is a single year-on-year earnings "
                "reading from the fundamentals snapshot: noisy, huge off a small base, and not a "
                "trend. A cyclical at a trough will always show a large gap because depressed "
                "trailing earnings inflate the implied figure — those rows are flagged. Where EPS "
                "history exists the table also carries a NORMALISED gap against a multi-year CAGR, "
                "which is the better comparison — but mind the units: embedded growth is a TOTAL "
                "change while a CAGR is per year, so both a 1-year and a 2-year annualisation of "
                "the embedded figure are shown. At a 2-year horizon the gap is roughly halved. "
                "This ranks QUESTIONS, not stocks, and it is not a fair-value model or investment "
                "advice. ROE here is derived as P/B divided by P/E and carries two limits worth "
                "stating: it is a backward-looking average over ALL capital rather than the return "
                "on the next rupee invested, and it is inflated by leverage and by a thin equity "
                "base — names above 40% are flagged for that reason. The quadrant labels are "
                "deliberately descriptive rather than verdicts."),
        }
    except Exception:
        return None


# India macro anchors — DATED INPUTS, refresh alongside the P/E band and the G-sec.
#   Nominal GDP FY26 actual: MoSPI, 2 Mar 2026 (real 7.6%, nominal 8.6%).
#   Nominal GDP FY27 forecast + Nifty earnings forecast: Motilal Oswal, Jun 2026.
#   Corporate profit-to-GDP: Nifty-500 profits over nominal GDP — FY26 record 5.2%
#   against ~2.0% in FY20.
_NOMINAL_GDP_FY26_PCT = 8.6
_NOMINAL_GDP_FY27_PCT = 11.25          # midpoint of an 11-11.5% forecast
_NIFTY_EPS_FORECAST_FY27_PCT = 15.5    # midpoint of the sell-side 15-16%
_PROFIT_TO_GDP_PCT = 5.2
_PROFIT_TO_GDP_FY20_PCT = 2.0
_MACRO_AS_OF = "2026-06"


def _macro_check(implied_growth_pct: float) -> dict:
    """Is the growth embedded in the index reachable for the economy underneath it?

    Aggregate corporate earnings cannot durably outgrow nominal GDP without profits
    taking an ever-larger share of it. India's have been doing exactly that: Nifty-500
    profit-to-GDP hit a RECORD 5.2% in FY26 against ~2.0% in FY20, with profits
    compounding 28.7% a year against 9.5% nominal GDP since FY20. So "earnings outgrow
    GDP" is not implausible here — it has been the regime, driven by formalisation and
    the listed sector taking share. But it compounds from a record, and five sectors
    carry three-quarters of it, so mean reversion is a live risk rather than a textbook one.

    THE HORIZON, RESOLVED EMPIRICALLY. Embedded growth from trailing/forward P/E is a
    total change across an ambiguous 1-2 year window, and that ambiguity has undermined
    every comparison in this file. Annualising it and checking against the PUBLISHED
    Nifty earnings forecast pins it down without assuming anything: whichever
    annualisation lands on the sell-side estimate is the horizon the forward multiple is
    actually using. That is a calibration, not a guess.
    """
    g = implied_growth_pct / 100.0

    def ann(years):
        return round(((1.0 + g) ** (1.0 / years) - 1.0) * 100.0, 1) if g > -1 else None

    a1, a15, a2 = ann(1.0), ann(1.5), ann(2.0)
    return {
        "as_of": _MACRO_AS_OF,
        "nominal_gdp_fy26_pct": _NOMINAL_GDP_FY26_PCT,
        "nominal_gdp_fy27_pct": _NOMINAL_GDP_FY27_PCT,
        "nifty_eps_forecast_fy27_pct": _NIFTY_EPS_FORECAST_FY27_PCT,
        "profit_to_gdp_pct": _PROFIT_TO_GDP_PCT,
        "profit_to_gdp_fy20_pct": _PROFIT_TO_GDP_FY20_PCT,
        "implied_annualised": {"1y": a1, "1_5y": a15, "2y": a2},
        # Positive = profits must keep taking share of the economy to justify the price.
        "excess_over_nominal_gdp_pp": {
            "1y": round((a1 or 0) - _NOMINAL_GDP_FY27_PCT, 1),
            "1_5y": round((a15 or 0) - _NOMINAL_GDP_FY27_PCT, 1),
            "2y": round((a2 or 0) - _NOMINAL_GDP_FY27_PCT, 1),
        },
        "best_fit_horizon": min(
            (("1y", a1), ("1.5y", a15), ("2y", a2)),
            key=lambda kv: abs((kv[1] or 0) - _NIFTY_EPS_FORECAST_FY27_PCT))[0],
        "note": (
            f"Nominal GDP grew {_NOMINAL_GDP_FY26_PCT}% in FY26 and is forecast near "
            f"{_NOMINAL_GDP_FY27_PCT}% for FY27, against a sell-side Nifty earnings forecast of "
            f"~{_NIFTY_EPS_FORECAST_FY27_PCT}%. Earnings outgrowing GDP is India's actual recent "
            f"regime — Nifty-500 profit-to-GDP ran from ~{_PROFIT_TO_GDP_FY20_PCT}% in FY20 to a "
            f"record {_PROFIT_TO_GDP_PCT}% in FY26 — so it is not implausible, but it compounds "
            "from a high base. Read the annualisations as calibration: the embedded figure only "
            "reconciles with the published earnings forecast at a horizon well beyond one year, "
            "which is the best evidence available that the forward multiple is not a 1-year number."),
    }


def _earnings_vs_valuation(rows: list[dict]) -> dict | None:
    """What earnings growth is the index priced for, and what would break the multiple.

    THE MATCHED-SAMPLE POINT, which is easy to get wrong: a naive weighted forward P/E
    covers 100% of index weight while the trailing one covers 98%, because loss-makers
    (TMPV, IndiGo) have a forward estimate but no trailing multiple. Including them on
    one side only deflates the forward figure and inflates implied growth — it read 27.4%
    that way against 24.6% on a like-for-like sample. Only names carrying BOTH multiples
    are used here, and the excluded ones are named in the payload.

    The three levers on a multiple, which is the taxonomy the UI leans on:
      DISCOUNT RATE (r) — G-sec yield and risk premium. Moves every multiple at once,
        within days, and is the only one you can observe directly today.
      GROWTH (g)        — competition, demand, capex cycle. Sector-specific and slow;
        shows up first as a falling forward estimate, not a falling price.
      EARNINGS LEVEL (E)— input costs, crude, supply. Changes the denominator, not the
        multiple: an oil spike cuts E and the P/E RISES even as the price falls.
    Flow effects (profit booking, FII, rebalancing) move price without touching any of
    the three, which is why they mean-revert and the other three do not.
    """
    try:
        def harm(rs, key):
            num = den = 0.0
            for r in rs:
                w, v = r.get("weight"), r.get(key)
                if w and v and v > 0:
                    num += w
                    den += w / v
            return (num / den, num) if den else (None, 0.0)

        matched = [r for r in rows
                   if (r.get("pe") or 0) > 0 and (r.get("fwd_pe") or 0) > 0
                   and (r.get("weight") or 0) > 0]
        excluded = [r["symbol"] for r in rows
                    if (r.get("weight") or 0) > 0 and not
                    ((r.get("pe") or 0) > 0 and (r.get("fwd_pe") or 0) > 0)]
        # Drop the non-comparable multiples for the same reason _context does.
        clean = [r for r in matched
                 if r["pe"] <= _PE_COMPARABLE_MAX and r["fwd_pe"] <= _PE_COMPARABLE_MAX]
        if len(clean) < 20:
            return None

        g = _gsec_10y()
        gsec = g["pct"]
        tp, cov = harm(clean, "pe")
        fp, _ = harm(clean, "fwd_pe")
        if not tp or not fp:
            return None
        implied_g = (tp / fp - 1.0) * 100.0
        ey, fey = 100.0 / tp, 100.0 / fp

        # Per-sector: which parts of the index are carrying the growth expectation.
        by_sector: dict = {}
        for r in clean:
            by_sector.setdefault(r["sector"], []).append(r)
        sectors = []
        for s, rs in by_sector.items():
            t, w = harm(rs, "pe")
            f, _ = harm(rs, "fwd_pe")
            if t and f:
                sectors.append({"sector": s, "weight": round(w, 2),
                                "trailing_pe": round(t, 2), "forward_pe": round(f, 2),
                                "implied_growth_pct": round((t / f - 1.0) * 100.0, 1)})
        sectors.sort(key=lambda x: -x["implied_growth_pct"])

        # Which names carry the index's growth bill, and which are priced to shrink.
        contrib = sorted(
            ({"symbol": r["symbol"], "weight": r["weight"],
              "implied_growth_pct": round((r["pe"] / r["fwd_pe"] - 1.0) * 100.0, 1)}
             for r in clean),
            key=lambda x: -(x["weight"] * x["implied_growth_pct"]))
        shrinking = [c for c in contrib if c["implied_growth_pct"] < 0]

        # One turn of multiple, as a share of index value. The cleanest sensitivity
        # there is: at a fixed multiple, index return simply EQUALS earnings growth,
        # so everything else is the multiple moving.
        per_turn = 100.0 / tp
        lo, mid, hi = _PE_BAND
        return {
            "trailing_pe": round(tp, 2), "forward_pe": round(fp, 2),
            "weight_covered_pct": round(cov, 1), "names": len(clean),
            "excluded": excluded,
            "implied_growth_pct": round(implied_g, 1),
            "earnings_yield_pct": round(ey, 2),
            "forward_earnings_yield_pct": round(fey, 2),
            "gsec_10y_pct": gsec, "gsec_as_of": g["as_of"],
            "gsec_yoy_pp": _GSEC_10Y_YOY_PP,
            "gsec_source": g["source"], "gsec_live": g["live"],
            "gsec_disagreement_bp": g.get("disagreement_bp"),
            "gsec_fallback_pct": g.get("fallback_pct"),
            # Negative = equities yield LESS than the risk-free rate, i.e. the multiple
            # is resting on growth rather than on current earnings.
            "yield_gap_pp": round(ey - gsec, 2),
            "forward_yield_gap_pp": round(fey - gsec, 2),
            "pct_per_pe_turn": round(per_turn, 2),
            "to_band_low_pct": round((lo / tp - 1.0) * 100.0, 1),
            "to_band_mid_pct": round((mid / tp - 1.0) * 100.0, 1),
            "parity_pe": round(100.0 / gsec, 1),
            "macro_check": _macro_check(implied_g),
            "sectors": sectors,
            "top_contributors": contrib[:8],
            "priced_to_shrink": shrinking,
            "note": (
                f"Priced for ~{implied_g:.0f}% earnings growth (trailing {tp:.1f}x over forward "
                f"{fp:.1f}x, {len(clean)} names, {cov:.0f}% of weight). Forward multiples are "
                "next-fiscal-year consensus and consensus runs optimistic, so treat this as the "
                "bar being set, not growth being forecast. At a constant multiple the index "
                f"returns exactly what earnings do; every 1 turn of P/E is {per_turn:.1f}% of "
                "index value, which is the whole de-rating risk in one number."),
        }
    except Exception:
        return None


def _index_read(rows: list[dict], nsei_close) -> dict | None:
    """Index-level cheap/rich + short-term trend read, from data already in the scan.

    VALUATION: bottom-up index trailing P/E = Σw / Σ(w/PE) (harmonic, weight-based —
    equivalent to total mcap / total earnings), judged against the heuristic _PE_BAND.
    TREND: last close vs 50-DMA / 200-DMA + distance from 52-week high.
    The combined lean is a categorical READ of those two states — a prior, not a
    prediction; the options-based Market State view is the desk's real short-term tool.
    """
    try:
        # -- valuation: weighted harmonic P/E over constituents with valid P/E --
        num = den = wtot = 0.0
        for r in rows:
            w = r.get("weight")
            if w:
                wtot += w
                if r.get("pe") and r["pe"] > 0:
                    num += w
                    den += w / r["pe"]
        wpe = round(num / den, 2) if den else None
        coverage = round(num / wtot * 100.0) if wtot else 0
        lo, mid, hi = _PE_BAND
        val_label = (None if wpe is None else
                     "cheap" if wpe < lo else "fair" if wpe < mid else
                     "mildly rich" if wpe < hi else "rich")

        # -- breadth: how the 50 verdicts split --
        breadth = {"rich": 0, "in-line": 0, "cheap": 0}
        for r in rows:
            v = r.get("verdict")
            if v:
                breadth[v["label"]] += 1

        # -- trend: DMAs + distance from 52w high --
        last = float(nsei_close.iloc[-1])
        dma50 = round(float(nsei_close.tail(50).mean()), 2) if len(nsei_close) >= 50 else None
        dma200 = round(float(nsei_close.tail(200).mean()), 2) if len(nsei_close) >= 200 else None
        hi_52w = float(nsei_close.max())
        off_high_pct = round((last / hi_52w - 1.0) * 100.0, 1)
        above50 = dma50 is not None and last > dma50
        above200 = dma200 is not None and last > dma200
        # A missing DMA is not a bearish DMA: without both averages `above == False`
        # would manufacture a "downtrend" out of insufficient history.
        trend_label = ("mixed" if (dma50 is None or dma200 is None) else
                       "uptrend" if above50 and above200 else
                       "downtrend" if not above50 and not above200 else "mixed")

        # -- combined categorical lean --
        if trend_label == "uptrend":
            lean, why = ("constructive", "price above both 50- and 200-DMA")
            if val_label in ("mildly rich", "rich"):
                lean = "constructive but stretched"
                why += f"; valuation {val_label} caps the margin for error"
        elif trend_label == "downtrend":
            lean, why = ("cautious", "price below both 50- and 200-DMA")
            if val_label == "cheap":
                lean = "cautious — value building"
                why += "; valuation turning cheap is where bottoms form, but trend must turn first"
        elif dma50 is None or dma200 is None:
            lean, why = ("neutral", "not enough history for a 50/200-DMA trend state")
        else:
            lean, why = ("neutral", "price between its 50- and 200-DMA — no trend edge")
            if val_label in ("mildly rich", "rich"):
                why += f"; valuation {val_label} argues against chasing strength"

        # How many of the 50 could not be valued at all — a low number here means the
        # breadth split below is a read on the index; a high one means it is a read on
        # Yahoo's uptime.
        unvalued = sum(1 for r in rows if not r.get("verdict"))
        stale_fundamentals = sum(1 for r in rows if r.get("fundamentals_ok") is False)

        return {
            "weighted_pe": wpe, "pe_coverage_pct": coverage, "pe_band": list(_PE_BAND),
            "val_label": val_label, "breadth": breadth,
            "unvalued": unvalued, "fundamentals_failed": stale_fundamentals,
            "dma50": dma50, "dma200": dma200, "above50": above50, "above200": above200,
            "off_high_pct": off_high_pct, "trend_label": trend_label,
            "lean": lean, "why": why,
            "note": ("Heuristic read: bottom-up weighted trailing P/E vs a PRIOR band "
                     f"(<{lo} cheap · {lo}-{mid} fair · {mid}-{hi} mildly rich · >{hi} rich; "
                     f"P/E data covers ~{coverage}% of index weight) + a 50/200-DMA trend state. "
                     "Valuation says almost nothing about the next 3 months — trend and flows "
                     "dominate short-term; see Market State for the options-based read. Not advice."),
        }
    except Exception:
        return None


_FLOWS_STATE = os.path.join(_STATE_DIR, "flows_state.json")
_PE_HISTORY_PATH = os.path.join(_REPO_ROOT, "pe_history.json")      # data_agent/fundamentals/pe_history_backfill.py
_FII_HOLDING_PATH = os.path.join(_REPO_ROOT, "fii_holdings.json")   # data_agent/fundamentals/fii_holding_backfill.py
# Researched, DATED context for names whose headline multiple doesn't mean what it looks
# like — the layer neither a rank nor a median can supply. Hand-written with sources,
# same decay discipline as nifty50_drivers.json.
_VALUATION_NOTES_PATH = os.path.join(_REPO_ROOT, "valuation_notes.json")
# Multi-year EPS CAGR — data_agent/fundamentals/eps_cagr_backfill.py. Optional: without
# it the gap falls back to the single-quarter comparison.
_EPS_HISTORY_PATH = os.path.join(_REPO_ROOT, "eps_history.json")


def _flows_block() -> dict | None:
    """FII/DII money, in the two layers we can actually stand behind.

    LAYER 1 — index cash flow. fii_dii_flows in the chain DB, written daily after close
    by the flows job. This is what FIIs and DIIs DID, not what news says they might do.

    LAYER 2 — sector FPI (NSDL, fortnightly). Read from .state/flows_state.json and
    ONLY when that file says fpi_stale is false. flows_fetcher.fetch_sector_fpi_sync()
    returns a hardcoded {"IT": -500, "Banks": 1200} placeholder when the NSDL fetch
    fails, and right now the state file is carrying exactly that. Rendering it would be
    inventing foreign-flow data — so a stale flag means the layer reports itself
    unavailable rather than showing a number.

    Layer 3 (per-stock FII holding) is per-row, not here — see _fii_holdings().
    """
    out: dict = {"available": False}
    try:
        import sqlite3
        from chain_store import DB_PATH
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT flow_date, fii_net, dii_net, fii_idx_fut_net, fii_idx_opt_net "
                "FROM fii_dii_flows ORDER BY flow_date DESC LIMIT 20"
            ).fetchall()
            # Same connection: does that money actually track the index, and which part.
            out["vs_index"] = _flows_vs_index(conn)
        finally:
            conn.close()
        recent = [{"date": str(d)[:10], "fii_net": f, "dii_net": dd,
                   "fii_idx_fut_net": ff, "fii_idx_opt_net": fo}
                  for d, f, dd, ff, fo in rows]
        def cum(key, n):
            vals = [x[key] for x in recent[:n] if x[key] is not None]
            return round(sum(vals), 0) if vals else None
        fii5, dii5 = cum("fii_net", 5), cum("dii_net", 5)
        # Consecutive sessions on the same side, newest first — a streak is the part of
        # a flow number that actually carries information about persistence.
        streak = 0
        if recent and recent[0]["fii_net"] is not None:
            sign = 1 if recent[0]["fii_net"] >= 0 else -1
            for x in recent:
                if x["fii_net"] is None or (1 if x["fii_net"] >= 0 else -1) != sign:
                    break
                streak += 1
            streak *= sign
        out.update({
            "available": bool(recent),
            "as_of": recent[0]["date"] if recent else None,
            "days": len(recent),
            "recent": list(reversed(recent)),      # chronological for a chart
            "fii_5d_cr": fii5, "dii_5d_cr": dii5,
            "fii_20d_cr": cum("fii_net", 20), "dii_20d_cr": cum("dii_net", 20),
            "fii_streak_days": streak,
        })
        if fii5 is not None and dii5 is not None:
            absorbed = fii5 < 0 and dii5 > 0 and (dii5 + fii5) > 0
            out["regime"] = ("FIIs selling, DIIs absorbing" if absorbed else
                             "FIIs selling, DIIs not covering" if fii5 < 0 and (dii5 + fii5) <= 0 else
                             "FIIs buying" if fii5 > 0 else "mixed")
    except Exception as e:
        # Layer 1 unavailable must not take layer 2 down with it — they come from
        # different sources and fail independently.
        out["cash_note"] = f"Index FII/DII cash flow unavailable ({type(e).__name__})."

    # Layer 2 — only if the state file vouches for it.
    try:
        with open(_FLOWS_STATE, "r") as f:
            st = json.load(f)
        if st.get("fpi_stale") is False and st.get("sector_fpi"):
            out["sector_fpi"] = st["sector_fpi"]
            out["sector_fpi_as_of"] = st.get("as_of")
        else:
            out["sector_fpi"] = None
            out["sector_fpi_note"] = (
                "Sector-wise FPI (NSDL, fortnightly) is stale — the fetcher falls back to a "
                "placeholder when NSDL is unreachable, so nothing is shown rather than a made-up "
                "number. Refresh via /api/update-flows from an Indian IP.")
    except Exception:
        out["sector_fpi"] = None
    return out


def _flows_vs_index(conn) -> dict | None:
    """Does FII money actually move the index — and which part of it?

    Joins daily FII net cash to the daily close of NIFTY, BANKNIFTY and NIFTYIT and
    reports, per index: correlation with the same-session move, the move implied per
    ₹1,000 cr of net flow (an OLS slope, not a forecast), and the average flow on up
    versus down days. Everything comes from tables the repo already fills.

    Read it as description, not causation. Flow and price are simultaneous — foreigners
    buy on strong days and strong days attract buying — so the slope measures how
    tightly the two move together, not how much the index would move if you injected
    ₹1,000 cr. The window is also short: fii_dii_flows starts 2026-06-18, so this is
    tens of sessions, not years, and one news-heavy month can dominate it. `sessions` is
    in the payload precisely so the number is never read without its sample size.
    """
    out = {"indices": {}, "note": None}
    try:
        import statistics
        for sym, label in (("NIFTY", "Nifty 50"), ("BANKNIFTY", "Bank Nifty"), ("NIFTYIT", "Nifty IT")):
            rows = conn.execute(
                "SELECT f.flow_date, f.fii_net, p.close FROM fii_dii_flows f "
                "JOIN price_bars p ON date(p.ts) = f.flow_date AND p.symbol = ? "
                "AND p.timeframe = '1d' ORDER BY f.flow_date", (sym,)).fetchall()
            if len(rows) < 10:
                continue
            fii = [r[1] for r in rows][1:]
            close = [r[2] for r in rows]
            rets = [(close[i] / close[i - 1] - 1.0) * 100.0 for i in range(1, len(close))]
            pair = [(f, r) for f, r in zip(fii, rets) if f is not None]
            if len(pair) < 10:
                continue
            f_, r_ = [p[0] for p in pair], [p[1] for p in pair]
            mf, mr = statistics.mean(f_), statistics.mean(r_)
            cov = sum((a - mf) * (b - mr) for a, b in zip(f_, r_)) / len(f_)
            sd = statistics.pstdev(f_) * statistics.pstdev(r_)
            # Mean-normalise the variance too — cov above already is, and mixing a
            # normalised numerator with a raw sum silently divides the slope by n.
            var = sum((a - mf) ** 2 for a in f_) / len(f_)
            up = [a for a, b in zip(f_, r_) if b > 0]
            dn = [a for a, b in zip(f_, r_) if b <= 0]
            out["indices"][sym] = {
                "label": label,
                "sessions": len(f_),
                "corr": round(cov / sd, 3) if sd else None,
                # % index move per ₹1,000 cr of net FII flow, same session.
                "pct_per_1000cr": round(cov / var * 1000.0, 3) if var else None,
                "avg_fii_up_day_cr": round(statistics.mean(up)) if up else None,
                "avg_fii_down_day_cr": round(statistics.mean(dn)) if dn else None,
                "first": rows[1][0], "last": rows[-1][0],
            }
        if out["indices"]:
            n = max(v["sessions"] for v in out["indices"].values())
            out["note"] = (
                f"Same-session co-movement over {n} sessions — description, not causation, and far "
                "too short a window to be a rule. Flow and price are simultaneous: foreigners buy "
                "strong days as much as they cause them.")
            return out
    except Exception:
        pass
    return None


def _load_optional(path: str, key: str) -> tuple[dict, dict | None]:
    """Read one of the optional per-stock JSON caches. Missing file = feature off.

    Same pattern as earnings_reactions.json: a backfill script owns the expensive part
    and writes a plain JSON, the route just reads it. Neither of these is required for
    the scan to work, and the UI hides the section when the map comes back empty.
    """
    try:
        with open(path, "r") as f:
            doc = json.load(f)
        return {k.upper(): v for k, v in (doc.get(key) or {}).items()}, {
            "as_of": doc.get("as_of"), "source": doc.get("source"),
            "names": len(doc.get(key) or {}), "note": doc.get("note"),
        }
    except Exception:
        return {}, None


def _compute() -> dict:
    import yfinance as yf

    cons = load_constituents()
    # Pass 1: batch-download the most-current ticker for every constituent.
    primary = {c["symbol"]: _candidates(c["symbol"])[0] for c in cons}
    data = yf.download(
        [f"{t}.NS" for t in primary.values()] + ["^NSEI"], period="1y", interval="1d",
        group_by="ticker", progress=False, threads=True, auto_adjust=True,
    )

    def _closes(yft: str):
        """Close series for a '<TICKER>.NS' key out of the batch frame, or None."""
        try:
            s = data[yft]["Close"].dropna()
            return s if not s.empty else None
        except Exception:
            return None

    rows: list[dict] = []
    resolved: dict = {}  # csv symbol -> ticker that actually returned bars
    for c in cons:
        sym = c["symbol"]
        row = dict(c)
        close = _closes(f"{primary[sym]}.NS")
        used = primary[sym] if close is not None else None

        # Pass 2: the primary ticker returned nothing (stale CSV symbol after a
        # rename/demerger, or a genuine Yahoo gap) — try the alternates one by one.
        if close is None:
            for alt in _candidates(sym)[1:]:
                try:
                    s = yf.Ticker(f"{alt}.NS").history(period="1y", auto_adjust=True)["Close"].dropna()
                    if not s.empty:
                        close, used = s, alt
                        break
                except Exception:
                    continue

        # Drop pre-demerger parent bars before ANY return is measured off them.
        history_note = None
        if close is not None and used:
            close, history_note = _trim_demerger(close, used)

        if close is not None:
            row.update(_returns_block(close))
        else:
            row.update({"last": None, "d1_pct": None, "w1_pct": None, "m6_pct": None,
                        "y1_pct": None, "pos_52w": None, "as_of": None,
                        "bars": 0, "partial_history": True})
        row["yahoo_symbol"] = used
        row["history_note"] = history_note
        # Flag the substitution so the UI can explain a symbol that isn't the CSV's.
        row["symbol_note"] = (f"CSV symbol {sym} is stale — resolved via {used} "
                              f"(NSE rename/demerger)") if used and used != sym else None
        resolved[sym] = used
        rows.append(row)

    # Fundamentals use whichever ticker actually resolved.
    live = {s: t for s, t in resolved.items() if t}
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = dict(zip(live.keys(), ex.map(_fetch_info, live.values())))
    for row in rows:
        row.update(infos.get(row["symbol"], {"pe": None, "fwd_pe": None, "pb": None,
                                             "div_yield": None, "earnings_growth_pct": None,
                                             "earnings_q_growth_pct": None,
                                             "revenue_growth_pct": None,
                                             "fundamentals_ok": False}))

    _verdicts(rows)

    # Curated qualitative drivers (tailwinds/headwinds/position) — judgment layer,
    # kept in nifty50_drivers.json so it can be hand-edited without touching code.
    drivers_meta = None
    try:
        with open(_DRIVERS_PATH, "r") as f:
            drv = json.load(f)
        companies = drv.get("companies", {})
        for r in rows:
            # Look up by every alias too — the drivers file is keyed on current NSE
            # symbols, so a stale CSV symbol would otherwise find no drivers.
            r["drivers"] = next((companies[k] for k in _candidates(r["symbol"])
                                 if k in companies), None)
        drivers_meta = {"as_of": drv.get("as_of"), "note": drv.get("note")}
    except Exception:
        for r in rows:
            r["drivers"] = None

    # MEASURED earnings reactions — the one layer here that is neither a live quote
    # nor a hand-written judgment. earnings_reactions.json is rebuilt from price_bars
    # by data_agent/fundamentals/earnings_reaction_backfill.py: announcement days are
    # picked by VOLUME ONLY (never by the size of the move, which would make the
    # result circular), then the next session's move is measured against NIFTY and
    # the stock's sector index.
    #
    # What earns its place in the table is the DIVERGENCE: full-sample bias is what
    # this stock usually does on results, recent_bias is the last 8. When those
    # disagree, the market has changed how it reads this name — that is a question
    # worth opening the row for, and 13 of the 50 currently disagree.
    reactions_meta = None
    try:
        with open(_REACTIONS_PATH, "r") as f:
            rx = json.load(f)
        summary = rx.get("summary", {})
        for r in rows:
            rec = next((summary[k] for k in _candidates(r["symbol"]) if k in summary), None)
            if rec:
                rec = dict(rec)
                rec["diverges"] = rec.get("full_bias") != rec.get("recent_bias")
            r["reaction"] = rec
        reactions_meta = {
            "as_of": rx.get("as_of"),
            "events": len(rx.get("events", [])),
            "names": len(summary),
            "diverging": sum(1 for r in rows
                             if (r.get("reaction") or {}).get("diverges")),
        }
    except Exception:
        for r in rows:
            r["reaction"] = None

    # EXPECTATION — what the market is priced FOR, as distinct from what the company
    # has delivered. Two sources, deliberately:
    #
    #   implied_eps_growth = trailing P/E / forward P/E - 1, computed LIVE from the
    #   multiples already fetched above. It is not a score and carries no weights:
    #   if a stock trades at 96x trailing and 59x forward, consensus is priced for
    #   ~63% EPS growth. That is arithmetic, not judgment. Trent is the worked case
    #   — priced for +63%, delivered +22%, and the "good" quarter sold off.
    #
    #   target/dispersion/coverage come from expectation_snapshots.json, an
    #   append-only log captured BEFORE each print, because consensus is revised
    #   continuously and the pre-announcement values cannot be recovered later.
    #
    # Dispersion is target spread over mean. It only means anything after corporate
    # actions are normalised — a stale pre-bonus target next to a post-bonus price
    # manufactures disagreement that nobody actually has.
    expectation_meta = None
    snap_by_sym = {}
    try:
        with open(_EXPECT_PATH, "r") as f:
            snaps = json.load(f).get("snapshots", [])
        if snaps:
            latest = snaps[-1]
            for row in latest.get("rows", []):
                snap_by_sym[row.get("symbol", "").upper()] = row
            expectation_meta = {"captured_at": latest.get("captured_at"),
                                "source": latest.get("source"),
                                "snapshots": len(snaps)}
    except Exception:
        pass

    for r in rows:
        pe, fwd = r.get("pe"), r.get("fwd_pe")
        implied = round((pe / fwd - 1.0) * 100.0, 1) if (pe and fwd and pe > 0 and fwd > 0) else None
        snap = next((snap_by_sym[k] for k in _candidates(r["symbol"])
                     if k in snap_by_sym), None)
        exp = {"implied_eps_growth_pct": implied}
        if snap:
            tgt = snap.get("targetMeanPrice")
            lo, hi = snap.get("targetLowPrice"), snap.get("targetHighPrice")
            last = r.get("last")
            exp.update({
                "target_mean": tgt,
                "target_low": lo,
                "target_high": hi,
                "target_upside_pct": round((tgt / last - 1.0) * 100.0, 1)
                                     if (tgt and last) else None,
                "dispersion_pct": round((hi - lo) / tgt * 100.0, 0)
                                  if (lo and hi and tgt) else None,
                "analysts": int(snap["numberOfAnalystOpinions"])
                            if snap.get("numberOfAnalystOpinions") else None,
                "next_earnings": snap.get("next_earnings_date"),
                "as_of": expectation_meta["captured_at"] if expectation_meta else None,
            })
        r["expectation"] = exp

    # Own-history comparators + per-stock FII holding. Both optional: if the backfill
    # hasn't been run the maps are empty, every row gets None, and the UI drops those
    # lines rather than showing a gap.
    pe_hist, pe_hist_meta = _load_optional(_PE_HISTORY_PATH, "history")
    fii_hold, fii_hold_meta = _load_optional(_FII_HOLDING_PATH, "holdings")
    val_notes, val_notes_meta = _load_optional(_VALUATION_NOTES_PATH, "notes")
    eps_hist, eps_hist_meta = _load_optional(_EPS_HISTORY_PATH, "history")
    for r in rows:
        r["pe_history"] = next((pe_hist[k] for k in _candidates(r["symbol"]) if k in pe_hist), None)
        r["fii_holding"] = next((fii_hold[k] for k in _candidates(r["symbol"]) if k in fii_hold), None)
        r["valuation_note"] = next((val_notes[k] for k in _candidates(r["symbol"]) if k in val_notes), None)

    if expectation_meta:
        vals = [r["expectation"]["implied_eps_growth_pct"] for r in rows
                if r.get("expectation", {}).get("implied_eps_growth_pct") is not None]
        if vals:
            vals.sort()
            expectation_meta["median_implied_eps_growth_pct"] = vals[len(vals) // 2]

    index_block = None
    index_read = None
    try:
        nsei_close = data["^NSEI"]["Close"].dropna()
        index_block = _returns_block(nsei_close)
        index_read = _index_read(rows, nsei_close)
    except Exception:
        pass

    # Relative performance vs the index — turns "is it still underperforming?" from a
    # narrative into a number: stock return minus index return over the same window.
    # (Same convention as the Sector View's rel_1m/rel_6m residuals.)
    if index_block:
        for r in rows:
            for key, rel in (("w1_pct", "rel_1w"), ("m6_pct", "rel_6m"), ("y1_pct", "rel_1y")):
                s, i = r.get(key), index_block.get(key)
                r[rel] = round(s - i, 2) if (s is not None and i is not None) else None

    # Rank/median context for every displayed number — last, so it sees the final rows.
    _context(rows)

    return {
        "fetched_at": time.time(),
        "index": index_block,
        "index_read": index_read,
        "earnings_vs_valuation": _earnings_vs_valuation(rows),
        "expectation_gap": _expectation_gap(rows, snap_by_sym, eps_hist),
        "rows": rows,
        "flows": _flows_block(),
        "drivers_meta": drivers_meta,
        "reactions_meta": reactions_meta,
        "expectation_meta": expectation_meta,
        "pe_history_meta": pe_hist_meta,
        "fii_holding_meta": fii_hold_meta,
        "valuation_notes_meta": val_notes_meta,
        "eps_history_meta": eps_hist_meta,
        "note": ("Verdicts are categorical (rich / in-line / cheap) vs the sector-median "
                 "trailing P/E within the Nifty 50 (P/B fallback for loss-makers; index-median "
                 "fallback when a sector has <3 valued peers; thresholds ±25%). A cross-sectional "
                 "heuristic that ignores growth/quality differences — a starting point for "
                 "questions, not a fair-value model, and not investment advice."),
        "mechanism": [
            "1 · RETURNS — one batch download of 1 year of daily adjusted closes (Yahoo/yfinance, ~15-min delayed) for all 50 constituents + ^NSEI; 1D/1W/6M/1Y returns and the 52-week-range position are computed from those bars. A window is left blank rather than shortened when the history can't fill it, so a recently listed or newly demerged name shows no 1Y figure instead of a 3-month move in the 1Y column. Bars before a demerger are dropped for the affected ticker (auto-adjustment handles splits and dividends but NOT a demerger, where the price step is a change in what the share IS). Note that the 52-week high/low are dividend-ADJUSTED closes, so for a high-yield name they sit a little below NSE's published extremes.",
            "2 · FUNDAMENTALS — trailing P/E, forward P/E and P/B per stock from Yahoo's fundamentals snapshot (Ticker.info); occasionally lags a fresh quarterly result.",
            "3 · VERDICT — each stock's trailing P/E is divided by the MEDIAN P/E of its own Nifty-50 sector peers: ≥1.25× = rich, ≤0.75× = cheap, else in-line. Loss-makers/missing P/E fall back to P/B vs sector-median P/B; sectors with <3 valued peers fall back to the whole-index median (flagged).",
            "4 · SCENARIOS (not forecasts) — upside/downside are mechanical reference points: the % move to the stock's own 52-week high and low, and the % move implied if its multiple reverted to the peer median with earnings/book unchanged. They say where price HAS been and where valuation WOULD be at peer parity — they carry no probability. The reversion figure is withheld entirely once a multiple sits beyond 3× or below ⅓ of the peer median: the arithmetic still resolves at 670× against a 30× median, but −95% is not a scenario anyone should read as one.",
            "5 · DRIVERS (judgment, not computation) — each stock's tailwinds (why investors hold: dividend support, deposit/NIM strength, low competition, capex pipeline) and headwinds (what can hurt) come from curated research in nifty50_drivers.json, dated and hand-editable. This is the qualitative layer the numbers can't see — and it goes stale: refresh it each earnings season.",
            "6 · INDEX READ — the overall cheap/fair/rich call is the bottom-up weighted trailing P/E (Σweight ÷ Σ(weight/P/E), i.e. total mcap over total earnings) judged against a heuristic band (<18 cheap · 18-21 fair · 21-24 mildly rich · >24 rich — a PRIOR, not a calibrated measurement). The short-term lean combines that with a 50/200-DMA trend state. Valuation is a poor 3-month timer — the lean weights trend over valuation, and the options-based Market State view remains the desk's real short-term instrument.",
            "7 · EARNINGS REACTION (measured, not judged) — how this stock has actually traded the session after a results announcement, from data_agent/fundamentals/earnings_reaction_backfill.py over price_bars back to 2018. Announcement days are identified by VOLUME ONLY — never by the size of the move, which would make the finding circular. Each reaction is measured against NIFTY and against the stock's own sector index, so a 'positive' bias means it beat the market on results day, not merely that it rose. The badge shows the RECENT bias (last 8 events); the arrow marks names whose recent behaviour has diverged from their full-sample habit, which is where the market has changed its mind about a name.",
            "8 \u00b7 EXPECTATION (arithmetic, not a score) \u2014 implied EPS growth is trailing P/E \u00f7 forward P/E \u2212 1: a stock at 96\u00d7 trailing and 59\u00d7 forward is priced for ~63% earnings growth. No weights are set by hand \u2014 it is what the two multiples already say. Read it as the growth already embedded in the price, NOT as a next-quarter hurdle: Yahoo's forward P/E is a next-fiscal-year consensus against a trailing-twelve-month denominator, so the figure spans one to two years. It is why a company can report +22% profit and still fall: good in absolute terms, short of what was priced in. Analyst target, dispersion (spread \u00f7 mean) and coverage come from expectation_snapshots.json, captured BEFORE each print because consensus is revised continuously and the pre-announcement value cannot be recovered afterwards. Dispersion is only meaningful once corporate actions are normalised \u2014 a stale pre-bonus target beside a post-bonus price manufactures disagreement nobody has.",
            "A rich stock can stay rich for years (quality/growth premium) and a cheap one can be cheap for a reason (value trap) — the verdict is a question to investigate, not a signal to trade.",
        ],
    }


_FACTORS_PATH = os.path.join(_REPO_ROOT, "nifty_factors.json")
_TODAY_PATH = os.path.join(_REPO_ROOT, "nifty_today.json")


@router.get("/api/nifty-today")
def nifty_today():
    """Curated 'today's market' brief: ranked drivers, sector-weight math, what to
    monitor tomorrow. Plain file read of nifty_today.json — decays in a day; the
    frontend shows the as_of date prominently so a stale brief is visibly stale."""
    if not os.path.exists(_TODAY_PATH):
        raise HTTPException(status_code=500, detail=f"nifty_today.json not found at {_TODAY_PATH}")
    with open(_TODAY_PATH, "r") as f:
        return {"success": True, "today": json.load(f)}


@router.get("/api/nifty-factors")
def nifty_factors():
    """Curated macro-factor library: status, transmission, episode-based scenario priors,
    and the dated today/this-week expectation. A plain file read (nifty_factors.json at
    repo root) — no market data fetched; edit the file to update the factors."""
    if not os.path.exists(_FACTORS_PATH):
        raise HTTPException(status_code=500, detail=f"nifty_factors.json not found at {_FACTORS_PATH}")
    with open(_FACTORS_PATH, "r") as f:
        return {"success": True, "factors": json.load(f)}


_QUALITY_GROWTH_PATH = os.path.join(_REPO_ROOT, "quality_growth.json")


@router.get("/api/nifty-quality-growth")
def nifty_quality_growth():
    """The weight-ordered quality screen and its two backtests.

    Plain file read of quality_growth.json, written by
    data_agent/fundamentals/quality_growth.py. Same convention as pe_history.json and
    fii_holdings.json: the expensive part (reading 47 Screener workbooks, walking the
    index, pricing two portfolios off price_bars) belongs to a script that runs on
    demand, and the route only serves the artifact. No new table, and nothing here
    recomputes on request — a screen that silently changed between two page loads
    would be unciteable.

    404 rather than an empty payload when the file is absent: "the backfill has not
    been run" and "no name passed the screen" are different answers and the UI has to
    be able to tell them apart.
    """
    if not os.path.exists(_QUALITY_GROWTH_PATH):
        raise HTTPException(
            status_code=404,
            detail=("quality_growth.json not found — run "
                    "`python3 data_agent/fundamentals/quality_growth.py` "
                    "(it needs delivery_history.json and expectation_snapshots.json)."))
    with open(_QUALITY_GROWTH_PATH, "r") as f:
        return {"success": True, "quality": json.load(f)}


_COMPUTE_LOCK = threading.Lock()

# A scan is only worth caching if it actually came back. Below this share of priced
# rows we treat the result as an upstream failure, not as a picture of the market.
_MIN_PRICED_SHARE = 0.8


def _usable(view: dict) -> bool:
    """Did this scan return enough to be worth serving for the next 30 minutes?

    Without this, one yfinance rate-limit produced 50 rows of `last: None`, wrote them
    to the cache, and served them as `cached: true` for the full TTL — a transient
    upstream blip became half an hour of an empty screen.
    """
    rows = view.get("rows") or []
    if not rows or view.get("index") is None:
        return False
    priced = sum(1 for r in rows if r.get("last") is not None)
    return priced >= len(rows) * _MIN_PRICED_SHARE


def _write_cache(view: dict) -> None:
    """Atomic best-effort cache write — a torn file from two racing scans would be
    silently swallowed by _read_cache and cost a needless recompute every time."""
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = f"{_CACHE}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(view, f)
        os.replace(tmp, _CACHE)
    except Exception:
        pass


@router.get("/api/nifty50-view")
def nifty50_view(force: bool = False, cached_only: bool = False):
    """Nifty 50 constituents scan — computed on request, cached 30 min.

    cached_only=true NEVER computes: it serves a warm cache or reports a cold one. The
    per-stock page (/intel/nifty50/<SYMBOL>) opens in a fresh tab with no user gesture
    behind it, so it reads this way — a deep link is instant when the desk has already
    run the scan, and can never kick off a 30-second fetch nobody asked for. The
    no-auto-run convention holds on the stock page exactly as it does on the panel.
    """
    if not force:
        cached = _read_cache()
        if cached is not None:
            return {"success": True, "view": cached, "cached": True}
    if cached_only:
        return {"success": True, "view": None, "cached": False, "cold": True}

    # Serialise the scan: it is a 50-name download plus an 8-thread Ticker.info fan-out,
    # and two clients pressing Run inside the same TTL gap used to run it twice and race
    # on the cache file. Whoever waits re-checks the cache the winner just wrote.
    with _COMPUTE_LOCK:
        if not force:
            cached = _read_cache()
            if cached is not None:
                return {"success": True, "view": cached, "cached": True}
        view = _compute()

    if _usable(view):
        _write_cache(view)
        return {"success": True, "view": view, "cached": False}

    priced = sum(1 for r in (view.get("rows") or []) if r.get("last") is not None)
    view["degraded"] = (
        f"Upstream returned prices for only {priced} of {len(view.get('rows') or [])} "
        "constituents, so this scan was NOT cached — press Run again in a moment."
    )
    return {"success": True, "view": view, "cached": False, "degraded": view["degraded"]}
