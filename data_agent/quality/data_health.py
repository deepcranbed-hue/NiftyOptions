"""
data_health.py
==============
The data agent's quality eye. Per symbol per day it answers three questions:

  1. COVERAGE   — did we get enough bars for the session? (count vs expected)
  2. FREQUENCY  — are the bars actually spaced at the symbol's expected frequency?
                  (1-min by default, or a per-symbol USER-DEFINED frequency)
  3. GAPS       — are there holes bigger than one interval mid-session?

"Enough bars" alone isn't enough: a symbol can have ~375 rows but at the wrong
spacing (5-min data masquerading as 1-min, or clustered bursts with holes). So we
check the actual timestamp spacing, not just the count.

PER-SYMBOL FREQUENCY (user-defined). Every symbol is expected at 1 minute unless
you override it in a config (`{symbol: minutes}`, e.g. an illiquid name you only
pull at 5m). Then the checker judges that symbol against *your* frequency and flags
`WRONG_FREQ` only when the data doesn't match what you defined — not against a blind
1-min assumption. Config loads from `.state/data_freq_config.json` or is passed in.

Statuses: OK | DEGRADED (low coverage) | WRONG_FREQ (spacing ≠ expected) |
GAPS (holes) | NO_DATA. Expired instruments are excluded up front.

Pure/DB-only — no broker, no network — so it runs and tests offline.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

# Expected minutes in a full session, by exchange (tune to your vendor).
SESSION_MINUTES = {
    "NSE": 375, "NFO": 375, "BSE": 375,
    "MCX": 780, "CDS": 480, "NSEIX": 1260,
}
DEFAULT_MINUTES = 375
DEFAULT_MIN_COVERAGE = 0.95
DEFAULT_FREQ_MIN = 1              # every symbol is 1-minute unless the user says otherwise

# Regular-session window as minute-of-day in UTC (bars outside it — e.g. the 09:07
# IST pre-open auction bar — are excluded so a normal pre-open boundary isn't
# mistaken for a mid-session hole). NSE 09:15–15:30 IST = 03:45–10:00 UTC = [225,600).
# Exchanges without a window here are analysed over all their bars.
SESSION_WINDOW = {
    "NSE": (225, 600), "NFO": (225, 600), "BSE": (225, 600),
}


def _window(exchange: str):
    return SESSION_WINDOW.get((exchange or "").upper())


def session_minutes(exchange: str) -> int:
    return SESSION_MINUTES.get((exchange or "").upper(), DEFAULT_MINUTES)


# kept for backward-compat (was the old expected-bars-per-day for 1m)
def expected_minutes(exchange: str) -> int:
    return session_minutes(exchange)


def load_freq_config(path: str | None = None) -> dict[str, int]:
    """User-defined per-symbol frequency in minutes: {'RELIANCE':1, 'SOMEILLIQUID':5}.
    Missing file -> empty (everything defaults to 1-minute)."""
    if not path:
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
        return {str(k).upper(): int(v) for k, v in raw.items() if int(v) > 0}
    except Exception:
        return {}


def freq_for(symbol: str, config: dict[str, int]) -> tuple[int, str]:
    """Return (minutes, source) — source is 'user' if the symbol was overridden."""
    sym = (symbol or "").upper()
    if sym in config:
        return config[sym], "user"
    return DEFAULT_FREQ_MIN, "default"


def _minute_of_day(ts: str) -> int:
    """'2026-07-03T09:59:00Z' -> 599 (minutes since midnight UTC)."""
    try:
        hh = int(ts[11:13]); mm = int(ts[14:16])
        return hh * 60 + mm
    except Exception:
        return -1


def analyze_spacing(minute_ints: list[int], freq_min: int) -> dict:
    """Diagnose the actual bar spacing vs the expected frequency.

    Returns modal/median/max gaps, whether the dominant spacing matches `freq_min`
    (else WRONG_FREQ), and how many holes exceed one interval (GAPS). Pure + unit-
    testable with synthetic minute lists.
    """
    ms = sorted(set(m for m in minute_ints if m >= 0))
    if len(ms) < 2:
        return {"modal_gap": None, "median_gap": None, "max_gap": None,
                "wrong_freq": False, "gap_count": 0}
    gaps = [ms[i + 1] - ms[i] for i in range(len(ms) - 1)]
    gaps_sorted = sorted(gaps)
    modal_gap = Counter(gaps).most_common(1)[0][0]
    median_gap = gaps_sorted[len(gaps_sorted) // 2]
    max_gap = max(gaps)
    wrong_freq = modal_gap != freq_min
    gap_count = sum(1 for g in gaps if g > freq_min * 1.5)   # holes larger than one interval
    return {"modal_gap": modal_gap, "median_gap": median_gap, "max_gap": max_gap,
            "wrong_freq": wrong_freq, "gap_count": gap_count}


def coverage_report(db_path: str, *, timeframe: str = "1m",
                    min_coverage: float = DEFAULT_MIN_COVERAGE,
                    is_expired=None, only_dates: set[str] | None = None,
                    freq_config: dict[str, int] | None = None) -> dict:
    """Per (exchange, symbol, date) coverage + frequency-maintained check.

    freq_config : {symbol: minutes} user-defined expected frequency (default 1m).
    is_expired  : callable(symbol, date_str)->bool, expired series are skipped.
    only_dates  : restrict the scan to these 'YYYY-MM-DD' dates.
    """
    freq_config = {k.upper(): v for k, v in (freq_config or {}).items()}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT exchange, symbol, ts FROM price_bars WHERE timeframe = ? ORDER BY symbol, ts",
        (timeframe,)).fetchall()
    con.close()

    # group minute-of-day per (symbol, exchange, date)
    groups: dict[tuple, list[int]] = defaultdict(list)
    exch_of: dict[str, str] = {}
    for r in rows:
        sym, exch, ts = r["symbol"], r["exchange"], r["ts"]
        d = ts[:10]
        if only_dates and d not in only_dates:
            continue
        if is_expired and is_expired(sym, d):
            continue
        groups[(sym, exch, d)].append(_minute_of_day(ts))
        exch_of[sym] = exch

    per_symbol: dict[str, dict] = {}
    flagged: list[dict] = []

    for (sym, exch, d), minutes in groups.items():
        freq_min, freq_src = freq_for(sym, freq_config)
        win = _window(exch)
        mins = [m for m in minutes if m >= 0]
        if win:                                        # keep only regular-session bars
            mins = [m for m in mins if win[0] <= m < win[1]]
            span = win[1] - win[0]
        else:
            span = session_minutes(exch)
        distinct = sorted(set(mins))                   # distinct in-session minutes
        exp = max(1, round(span / freq_min))
        n = len(distinct)
        cov = min(1.0, n / exp)
        sp = analyze_spacing(distinct, freq_min)

        # status precedence: wrong frequency is the loudest, then coverage, then holes
        if n == 0:
            status, reason = "NO_DATA", "no bars"
        elif sp["wrong_freq"]:
            status, reason = "WRONG_FREQ", (
                f"spacing ~{sp['modal_gap']}m but expected {freq_min}m"
                + (" (user-defined)" if freq_src == "user" else ""))
        elif cov < min_coverage:
            status, reason = "DEGRADED", f"{n}/{exp} bars ({_pct(cov)})"
        elif sp["gap_count"] > 0:
            status, reason = "GAPS", f"{sp['gap_count']} hole(s), max {sp['max_gap']}m"
        else:
            status, reason = "OK", ""

        s = per_symbol.setdefault(sym, {
            "symbol": sym, "exchange": exch, "freq_min": freq_min, "freq_source": freq_src,
            "expected_per_day": exp, "days": 0, "bad_days": 0,
            "latest_date": None, "latest_coverage": None, "latest_status": None, "worst": None})
        s["days"] += 1
        if status != "OK":
            s["bad_days"] += 1
            flagged.append({"symbol": sym, "date": d, "bars": n, "expected": exp,
                            "coverage": round(cov, 3), "freq_min": freq_min,
                            "freq_source": freq_src, "status": status, "reason": reason,
                            "modal_gap": sp["modal_gap"], "max_gap": sp["max_gap"]})
        if s["latest_date"] is None or d > s["latest_date"]:
            s["latest_date"] = d; s["latest_coverage"] = round(cov, 3); s["latest_status"] = status
        if s["worst"] is None or cov < s["worst"]["coverage"]:
            s["worst"] = {"date": d, "coverage": round(cov, 3), "bars": n, "status": status}

    flagged.sort(key=lambda x: (x["status"] != "WRONG_FREQ", x["coverage"], x["symbol"]))
    n_bad = sum(1 for s in per_symbol.values() if s["bad_days"] > 0)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe, "min_coverage": min_coverage,
        "n_symbols": len(per_symbol), "n_symbols_flagged": n_bad,
        "symbols": per_symbol, "flagged": flagged,
    }
    report["summary"] = _summary(report)
    return report


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _summary(report: dict) -> str:
    flagged = report["flagged"]
    n = report["n_symbols"]
    if not flagged:
        return f"All {n} symbols meet their expected frequency and coverage. Data is up to the mark."
    worst = flagged[:6]
    bits = [f"{f['symbol']} {f['date']} [{f['status']}: {f['reason']}]" for f in worst]
    more = "" if len(flagged) <= 6 else f" +{len(flagged) - 6} more"
    return (f"{report['n_symbols_flagged']} of {n} symbols flagged "
            f"({len(flagged)} symbol-days): " + "; ".join(bits) + more + ".")


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def trading_days_from_index(db_path: str, *, index_symbol: str = "NIFTY",
                            timeframe: str = "1m") -> list[str]:
    """The set of dates the market was open, inferred from the index's OWN bars in the
    EXISTING price_bars table. We don't hardcode a holiday calendar — a day the index
    has bars is a day that should have had data. Returns sorted 'YYYY-MM-DD'."""
    con = sqlite3.connect(db_path)
    try:
        if not _table_exists(con, "price_bars"):
            return []
        rows = con.execute(
            "SELECT DISTINCT substr(ts,1,10) FROM price_bars WHERE symbol=? AND timeframe=? ORDER BY 1",
            (index_symbol, timeframe)).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


_IST = timezone(timedelta(hours=5, minutes=30))
_SESSION_CLOSE_MIN = 15 * 60 + 30        # 15:30 IST


def _ist_today_and_complete(now_utc_iso: str | None):
    """Return (today_ist 'YYYY-MM-DD', session_complete_bool) as of `now`.
    A day is 'complete' for auditing once the 15:30 IST close has passed."""
    if now_utc_iso:
        n = datetime.fromisoformat(now_utc_iso.replace("Z", "+00:00"))
        if n.tzinfo is None:
            n = n.replace(tzinfo=timezone.utc)
    else:
        n = datetime.now(timezone.utc)
    ist = n.astimezone(_IST)
    return ist.strftime("%Y-%m-%d"), (ist.hour * 60 + ist.minute) >= _SESSION_CLOSE_MIN


def chain_coverage_report(db_path: str, *, min_coverage: float = DEFAULT_MIN_COVERAGE,
                          index_symbol: str = "NIFTY",
                          expected_minutes: int = 375,
                          now: str | None = None) -> dict:
    """NOT PART OF THE EOD AUDIT (kept for reference/analysis only). Per the data rule,
    options are audited by LAST TIMESTAMP only (see inventory_report); sample-size/coverage
    is shown, never judged, because expiries change. This per-day coverage view remains
    available for ad-hoc inspection but is no longer called by missing_report.

    Gap audit over the EXISTING option store (captures + chain_rows). No new tables.

    For every expiry we've ever stored, per date it counts DISTINCT snapshot minutes
    (one capture = one minute's full chain for that expiry) and compares to the expected
    session. It flags:
      * MISSING  — an INTERIOR hole: a trading day BETWEEN two days we did capture for
                   this expiry that has NO snapshots. Leading/trailing absence (e.g. a day
                   you simply haven't collected yet) is NOT flagged — only real drops.
      * THIN     — a COMPLETED-session day with snapshots but coverage below min_coverage.
                   The current day is skipped until the 15:30 IST close has passed, so an
                   in-progress session never looks thin (this runs clean at the 5 PM EOD audit).
    "Trading day" is inferred from the index's own bars, so 'missing N days' means N days
    the market was open (between your captures) but we have no chain for that expiry.
    """
    today_ist, session_complete = _ist_today_and_complete(now)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if not (_table_exists(con, "captures") and _table_exists(con, "chain_rows")):
            return {"available": False, "detail": "no captures/chain_rows in this DB",
                    "expiries": {}, "flagged": [], "summary": "Option store not present."}
        # distinct (expiry, snapshot-minute) — a capture counts for an expiry only if it
        # actually has rows for that expiry (calendar captures carry several expiries).
        rows = con.execute("""
            SELECT r.expiry AS expiry,
                   substr(c.captured_at,1,10) AS d,
                   substr(c.captured_at,12,5) AS hhmm
            FROM captures c JOIN chain_rows r ON r.capture_id = c.capture_id
            WHERE c.status='complete'
            GROUP BY r.expiry, c.capture_id
        """).fetchall()
    finally:
        con.close()

    trading_days = trading_days_from_index(db_path, index_symbol=index_symbol)
    td_set = set(trading_days)

    # expiry -> date -> set(minutes present)
    per: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        per[r["expiry"]][r["d"]].add(r["hhmm"])

    expiries: dict[str, dict] = {}
    flagged: list[dict] = []
    for expiry, bydate in per.items():
        exp_day = (expiry or "")[:10]
        observed_dates = sorted(bydate.keys())
        first = observed_dates[0]
        last = observed_dates[-1]
        present_days = set(observed_dates)
        # INTERIOR holes only: index-open days strictly inside [first, last] (and not past
        # the expiry) that we didn't capture. Trailing days you haven't collected yet, and
        # leading days before you started, are never flagged.
        hi = min(last, exp_day)
        interior = [d for d in trading_days if first < d < hi] if td_set else []
        missing_days = [d for d in interior if d not in present_days]
        thin_days = []
        for d in observed_dates:
            # skip the current day until its session has closed (no mid-session 'thin')
            if d == today_ist and not session_complete:
                continue
            n = len(bydate[d])
            cov = min(1.0, n / expected_minutes)
            if cov < min_coverage:
                thin_days.append({"date": d, "snapshots": n, "coverage": round(cov, 3)})
        expiries[expiry] = {
            "expiry": expiry, "first_seen": first, "last_seen": last,
            "days_present": len(observed_dates),
            "days_expected": len(observed_dates) + len(missing_days),
            "missing_days": missing_days, "thin_days": thin_days,
        }
        for d in missing_days:
            flagged.append({"expiry": expiry, "date": d, "status": "MISSING",
                            "reason": "no chain snapshot (market was open)"})
        for t in thin_days:
            flagged.append({"expiry": expiry, "date": t["date"], "status": "THIN",
                            "reason": f"{t['snapshots']}/{expected_minutes} snapshots ({_pct(t['coverage'])})"})

    flagged.sort(key=lambda x: (x["status"] != "MISSING", x["expiry"], x["date"]))
    n_missing = sum(1 for f in flagged if f["status"] == "MISSING")
    n_thin = sum(1 for f in flagged if f["status"] == "THIN")
    if not flagged:
        summary = f"All {len(expiries)} stored expiries have complete daily chain coverage."
    else:
        head = []
        if n_missing:
            head.append(f"{n_missing} missing day(s)")
        if n_thin:
            head.append(f"{n_thin} thin day(s)")
        bits = [f"{f['expiry'][:10]} {f['date']} [{f['status']}]" for f in flagged[:6]]
        more = "" if len(flagged) <= 6 else f" +{len(flagged) - 6} more"
        summary = f"Option chain: {', '.join(head)} across {len(expiries)} expiries — " + "; ".join(bits) + more + "."
    return {"available": True, "checked_at": datetime.now(timezone.utc).isoformat(),
            "n_expiries": len(expiries), "n_missing": n_missing, "n_thin": n_thin,
            "expiries": expiries, "flagged": flagged, "summary": summary}


def _is_future(symbol: str) -> bool:
    """Rolling/continuous or per-expiry futures live in price_bars (e.g. NIFTY_FUT_1,
    NIFTY_FUT_2, NIFTY26JUL_FUT). Audited by last timestamp only — never by sample size."""
    return "FUT" in (symbol or "").upper()


def _reference_last_day(trading_days: list[str], today_ist: str, session_complete: bool) -> str | None:
    """The most recent trading day that data is EXPECTED to be current through: today if
    its 15:30 close has passed and the index has today's bars, else the prior trading day."""
    for d in reversed(trading_days):
        if d < today_ist or (d == today_ist and session_complete):
            return d
    return None


def _days_behind(trading_days: list[str], last_day: str | None, ref_day: str | None) -> int:
    """How many trading days elapsed AFTER an instrument's last data, up to ref_day."""
    if not last_day or not ref_day:
        return 0
    return sum(1 for d in trading_days if last_day < d <= ref_day)


def inventory_report(db_path: str, *, index_symbol: str = "NIFTY",
                     now: str | None = None) -> dict:
    """Freshness/inventory audit over the EXISTING tables. For every cash symbol and every
    option expiry it reports, in ONE pass:
        first_day, last_day, last_ts (the watermark), sample_size, n_days, days_behind
    and a status: CURRENT (up to the last complete trading day) | STALE (behind) |
    EXPIRED (an option expiry now in the past — expected to stop updating).

    This is the 'what is the last timestamp for every symbol, and is it up to date' gate.
    """
    today_ist, session_complete = _ist_today_and_complete(now)
    trading_days = trading_days_from_index(db_path, index_symbol=index_symbol)
    ref_day = _reference_last_day(trading_days, today_ist, session_complete)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cash_rows = con.execute("""
            SELECT symbol,
                   MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                   COUNT(*) AS sample_size,
                   COUNT(DISTINCT substr(ts,1,10)) AS n_days
            FROM price_bars WHERE timeframe='1m' GROUP BY symbol ORDER BY symbol
        """).fetchall()
        has_chain = _table_exists(con, "captures") and _table_exists(con, "chain_rows")
        chain_rows = con.execute("""
            SELECT r.expiry AS expiry,
                   MIN(c.captured_at) AS first_ts, MAX(c.captured_at) AS last_ts,
                   COUNT(*) AS sample_size,
                   COUNT(DISTINCT c.capture_id) AS n_snapshots,
                   COUNT(DISTINCT substr(c.captured_at,1,10)) AS n_days
            FROM captures c JOIN chain_rows r ON r.capture_id=c.capture_id
            WHERE c.status='complete' GROUP BY r.expiry ORDER BY r.expiry
        """).fetchall() if has_chain else []
    finally:
        con.close()

    cash = []
    for r in cash_rows:
        last_day = (r["last_ts"] or "")[:10]
        behind = _days_behind(trading_days, last_day, ref_day)
        cash.append({
            "symbol": r["symbol"], "instrument_type": "future" if _is_future(r["symbol"]) else "stock",
            "first_day": (r["first_ts"] or "")[:10], "last_day": last_day,
            "last_ts": r["last_ts"], "sample_size": r["sample_size"], "n_days": r["n_days"],
            "days_behind": behind, "status": "CURRENT" if behind == 0 else "STALE",
        })

    chain = []
    for r in chain_rows:
        exp_day = (r["expiry"] or "")[:10]
        last_day = (r["last_ts"] or "")[:10]
        if exp_day and exp_day < today_ist:
            status, behind = "EXPIRED", 0          # a past expiry is meant to stop updating
        else:
            behind = _days_behind(trading_days, last_day, ref_day)
            status = "CURRENT" if behind == 0 else "STALE"
        chain.append({
            "expiry": r["expiry"], "first_day": (r["first_ts"] or "")[:10], "last_day": last_day,
            "last_ts": r["last_ts"], "sample_size": r["sample_size"],
            "n_snapshots": r["n_snapshots"], "n_days": r["n_days"],
            "days_behind": behind, "status": status,
        })

    stale_cash = [c for c in cash if c["status"] == "STALE"]
    stale_chain = [c for c in chain if c["status"] == "STALE"]
    parts = []
    if stale_cash:
        parts.append(f"{len(stale_cash)} cash symbol(s) stale")
    if stale_chain:
        parts.append(f"{len(stale_chain)} active expiry(ies) stale")
    summary = (f"All current as of {ref_day}." if not parts
               else f"As of {ref_day}: " + "; ".join(parts) + ".")
    return {"reference_day": ref_day, "checked_at": datetime.now(timezone.utc).isoformat(),
            "cash": cash, "chain": chain,
            "stale_cash": stale_cash, "stale_chain": stale_chain, "summary": summary}


def missing_report(db_path: str, *, freq_config: dict[str, int] | None = None,
                   index_symbol: str = "NIFTY", now: str | None = None) -> dict:
    """One unified 'what's missing' answer over the EXISTING tables — the thing the
    data agent suggests to the user, designed to run at the 5 PM EOD audit. Combines:
      * cash  symbol-days short of their expected 1-min coverage (price_bars)
      * option expiry-days missing/thin in the chain store (captures/chain_rows)
    The current day is excluded until its 15:30 IST session has closed, so a run before
    close never reports today as incomplete.
    """
    today_ist, session_complete = _ist_today_and_complete(now)
    inv = inventory_report(db_path, index_symbol=index_symbol, now=now)   # freshness (last timestamp)

    # FRESHNESS (last-timestamp) audit — applies to stocks, FUTURES and OPTION expiries.
    stale_stocks = [c for c in inv["cash"] if c["status"] == "STALE" and c["instrument_type"] == "stock"]
    stale_futures = [c for c in inv["cash"] if c["status"] == "STALE" and c["instrument_type"] == "future"]
    stale_chain = inv["stale_chain"]                # option expiries, already EXPIRED-aware

    # COVERAGE (sample-size per day) audit — CASH STOCKS ONLY. Options & futures are NEVER
    # judged by sample size (expiries change; sample size is shown, not audited).
    # We also explicitly ignore commodities and currencies here because their irregular weekend ticks trigger false DEGRADED alerts.
    IGNORE_COVERAGE = {"CRUDEOIL", "GOLD", "SILVER", "COPPER", "USDINR", "GIFTNIFTY"}
    cov = coverage_report(db_path, freq_config=freq_config)
    stock_thin = [
        f for f in cov.get("flagged", []) 
        if not _is_future(f.get("symbol", "")) and f.get("symbol", "") not in IGNORE_COVERAGE
    ]
    if not session_complete:            # drop the in-progress day
        stock_thin = [f for f in stock_thin if f.get("date") != today_ist]

    n_stale = len(stale_stocks) + len(stale_futures) + len(stale_chain)
    parts = []
    if n_stale:
        bits = []
        if stale_stocks:  bits.append(f"{len(stale_stocks)} stock(s)")
        if stale_futures: bits.append(f"{len(stale_futures)} future(s)")
        if stale_chain:   bits.append(f"{len(stale_chain)} expiry(ies)")
        parts.append("stale by last timestamp: " + ", ".join(bits))
    if stock_thin:
        parts.append(f"{len(stock_thin)} stock-day(s) thin (incomplete session)")
    headline = (f"Data current as of {inv['reference_day']}." if not parts
                else "Issues — " + "; ".join(parts) + ".")
    level = "ok" if not parts else ("alert" if n_stale else "warn")
    return {
        "level": level, "headline": headline, "reference_day": inv["reference_day"],
        "inventory": {"summary": inv["summary"], "cash": inv["cash"], "chain": inv["chain"]},
        # freshness = the last-timestamp audit for stocks + futures + option expiries
        "freshness": {"stale_stocks": stale_stocks, "stale_futures": stale_futures,
                      "stale_chain": stale_chain},
        # coverage = sample-size audit, STOCKS ONLY
        "coverage": {"summary": cov.get("summary"), "stock_thin": stock_thin[:20]},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def alert_message(report: dict, when: str = "") -> dict:
    """Compact payload for the sidebar health badge / morning-evening alert."""
    flagged = report["flagged"]
    level = "ok" if not flagged else ("warn" if report["n_symbols_flagged"] <= 3 else "alert")
    head = "Data OK" if level == "ok" else f"{report['n_symbols_flagged']} symbol(s) need attention"
    return {
        "level": level,
        "headline": (f"{when} — " if when else "") + head,
        "detail": report["summary"],
        "flagged": report["flagged"][:12],
        "checked_at": report["checked_at"],
    }


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    db = os.path.join(root, "option_chains.db")
    cfg = load_freq_config(os.path.join(root, ".state", "data_freq_config.json"))
    rep = coverage_report(db, freq_config=cfg)
    print(json.dumps({k: rep[k] for k in ("n_symbols", "n_symbols_flagged", "summary")},
                     indent=2, ensure_ascii=False))
    print("\nalert:", json.dumps(alert_message(rep, when="Morning"), ensure_ascii=False)[:300])
