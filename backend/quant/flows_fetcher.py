"""
flows_fetcher.py
----------------
Fetches FII/DII cash, F&O participant-wise OI, SIP and FPI sector data.

Design (unchanged): PURE parsers + THIN network + explicit STALE flag.
  * Parsers take already-fetched text/JSON and never touch the network — unit-testable offline.
  * Network functions warm an NSE session cookie (same pattern the option-chain fetch uses),
    fetch, parse, and ACCUMULATE daily rows into a local cache so flow_trend() has a window.
  * On ANY failure they fall back to the last cache (or a dummy) and return is_stale=True.

Why this file used to show stale values: fetch_nse_cash_sync() was a stub that returned
three hardcoded FlowDay rows and `True`. It never called NSE. It does now.

DATA SOURCES:
  * FII/DII cash (provisional, combined NSE/BSE/MSEI):
        page   https://www.nseindia.com/reports/fii-dii   (cookie warm-up)
        json   https://www.nseindia.com/api/fiidiiTradeReact   (undocumented; site's own endpoint)
  * FII derivatives (participant-wise OI, static archive — no cookie needed, preferred):
        https://archives.nseindia.com/content/nsccl/fao_participant_oi_<DDMMYYYY>.csv
  * SIP monthly: AMFI (amfiindia.com)      — TODO, still stubbed.
  * FPI sector : NSDL/CDSL (fortnightly)   — TODO, still stubbed.

Cadence: cash DAILY after ~18:30 IST; participant CSV after the post-close batch.
Provisional revises next day — reconcile if you keep history.

NOTE on ToS: NSE's /api/* is undocumented and its terms discourage automated use. You already
warm cookies for the option chain the same way; the participant archive CSV is a static file and
is the more robust route. Use the source you're comfortable with — both parsers below are pure.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import datetime, date as _date, timedelta as _timedelta
from typing import Optional

try:
    import requests  # proven working for NSE in this repo (backend/main.py)
except Exception:  # pragma: no cover
    requests = None

# Adjust imports to find flows
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flows import FlowDay, SIPMonth

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_NSE_PAGE = "https://www.nseindia.com/reports/fii-dii"
_NSE_CASH_API = "https://www.nseindia.com/api/fiidiiTradeReact"
_NSE_PART_CSV = "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATE_DIR = os.environ.get("NIFTY_STATE_DIR", os.path.join(_REPO_ROOT, ".state"))
_CASH_CACHE = os.path.join(_STATE_DIR, "flows_cash_cache.json")
_SIP_CONFIG = os.path.join(_STATE_DIR, "sip_data.json")  # you maintain this monthly (see fetch_amfi_sip_sync)
_NSDL_FPI_URL = (
    "https://www.fpi.nsdl.co.in/StaticReports/"
    "Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestmentSector_h.html"
)
_HTTP_TIMEOUT = float(os.environ.get("NSE_HTTP_TIMEOUT", "12"))
_CACHE_KEEP = 60  # sessions to retain for flow_trend windows

# Last failure reason per source, so "stale" is never a silent black box. Inspect after a fetch.
LAST_ERROR: Optional[str] = None       # cash
LAST_ERROR_SIP: Optional[str] = None
LAST_ERROR_FPI: Optional[str] = None
LAST_ERROR_DERIV: Optional[str] = None  # participant-wise F&O OI


# ── small helpers ─────────────────────────────────────────────────────────────
def _to_float(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("₹", "")
    if s in ("", "-", "--", "NA", "N.A."):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _iso(date_str: str) -> str:
    """NSE dates like '10-Jul-2026' -> '2026-07-10'; pass through if already ISO."""
    s = (date_str or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s or datetime.now().strftime("%Y-%m-%d")


def _nse_session():
    """Warm cookies on NSE, exactly like the option-chain fetch does.

    NSE is picky: it often 401s the API unless you first touch the homepage AND the
    referring report page in the same session. We warm both.
    """
    if requests is None:
        raise RuntimeError("requests not available (pip install requests)")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "application/json, text/plain, */*",
        "Referer": _NSE_PAGE,
    }
    s = requests.Session()
    s.headers.update(headers)
    s.get("https://www.nseindia.com/", timeout=_HTTP_TIMEOUT)  # base cookies
    s.get(_NSE_PAGE, timeout=_HTTP_TIMEOUT)                     # report-page cookies
    return s


def _load_cache() -> list[FlowDay]:
    try:
        with open(_CASH_CACHE, "r") as f:
            raw = json.load(f)
        return [FlowDay(**d) for d in raw]
    except Exception:
        return []


def _save_cache(days: list[FlowDay]) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_CASH_CACHE, "w") as f:
            json.dump([d.__dict__ for d in days], f, indent=2)
    except Exception:
        pass


def _merge(existing: list[FlowDay], new: list[FlowDay]) -> list[FlowDay]:
    """Upsert by date, keep sorted, retain the last _CACHE_KEEP sessions."""
    by_date = {d.date: d for d in existing}
    for d in new:
        by_date[d.date] = d  # newest wins (handles provisional->final revision)
    merged = sorted(by_date.values(), key=lambda d: d.date)
    return merged[-_CACHE_KEEP:]


# ── 1. NSE FII/DII Cash ──────────────────────────────────────────────────────
def parse_nse_fii_dii(data: dict | list | str) -> FlowDay:
    """PURE parser for the NSE fiidiiTradeReact payload (one trading day).

    Real shape is a list of category rows, e.g.:
      [{"category":"DII **","date":"10-Jul-2026","buyValue":"..","sellValue":"..","netValue":"2,057.80"},
       {"category":"FII/FPI **","date":"10-Jul-2026", ... ,"netValue":"-532.90"}]
    Also tolerates the old flat {"date","fii_net","dii_net"} test shape.
    """
    if isinstance(data, str):
        data = json.loads(data)

    # Old flat shape (keeps existing tests green)
    if isinstance(data, dict) and ("fii_net" in data or "dii_net" in data):
        return FlowDay(
            date=_iso(data.get("date", "")),
            fii_cash=_to_float(data.get("fii_net")),
            dii_cash=_to_float(data.get("dii_net")),
        )

    rows = data if isinstance(data, list) else data.get("data", data.get("result", []))
    fii = dii = 0.0
    date_str = ""
    for row in rows:
        cat = str(row.get("category", "")).upper()
        net = _to_float(row.get("netValue", row.get("net", row.get("netBuySell"))))
        date_str = date_str or row.get("date", "")
        if "FII" in cat or "FPI" in cat:
            fii = net
        elif "DII" in cat:
            dii = net
    return FlowDay(date=_iso(date_str), fii_cash=fii, dii_cash=dii)


def fetch_nse_cash_sync(enrich_derivatives: bool = True) -> tuple[list[FlowDay], bool]:
    """Fetch today's provisional FII/DII cash, accumulate into cache, return (days, is_stale).

    is_stale is False only when we actually reached NSE this call. On failure we return the
    cached history (still useful, but flagged stale) or a dummy if the cache is empty.
    """
    global LAST_ERROR
    LAST_ERROR = None
    cache = _load_cache()
    last_exc = None
    for attempt in range(2):  # NSE sometimes needs a second hit after cookies settle
        try:
            sess = _nse_session()
            resp = sess.get(_NSE_CASH_API, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            today = parse_nse_fii_dii(resp.json())
            if enrich_derivatives:
                deriv, ok = fetch_fii_derivatives_sync(today.date)
                if not ok:
                    today.fii_fut_net = deriv.fii_fut_net  # latest available (walk-back), may be prior session
            days = _merge(cache, [today])
            _save_cache(days)
            return days, False
        except Exception as e:  # capture so the caller can see WHY it's stale
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            LAST_ERROR = f"{type(e).__name__}: {e}" + (f" [HTTP {status}]" if status else "")

    if True:
        if cache:
            return cache, True  # last good history, flagged stale
        dummy = [
            FlowDay("2026-06-25", -500.0, 1200.0, fii_debt=-120.0),
            FlowDay("2026-06-26", -1500.0, 2000.0, fii_debt=-450.0),
            FlowDay("2026-06-27", 800.0, 500.0, fii_debt=210.0),
        ]
        return dummy, True


# ── 1b. One-time cash backfill (seed a real multi-day window today) ──────────
def parse_cash_history_csv(text: str) -> list[FlowDay]:
    """PURE parser for a FII/DII history CSV exported from the NSE fii-dii report
    (Reports -> FII/DII -> Historical, date-range download) or any CSV with a date
    column and FII/DII net columns. Column names are matched fuzzily.
    """
    rows = list(csv.DictReader(io.StringIO(text)))
    out: list[FlowDay] = []
    for r in rows:
        keys = {k.lower().strip(): k for k in r.keys() if k}
        def pick(*subs):
            for want in subs:
                for lk, orig in keys.items():
                    if want in lk:
                        return r[orig]
            return None
        date_raw = pick("date")
        fii_raw = pick("fii net", "fii/fpi net", "fpi net", "fii")
        dii_raw = pick("dii net", "dii")
        if not date_raw:
            continue
        out.append(FlowDay(date=_iso(date_raw), fii_cash=_to_float(fii_raw), dii_cash=_to_float(dii_raw)))
    return out


def backfill_cash(csv_path: str) -> tuple[int, str]:
    """One-time: merge a FII/DII history CSV into the cash cache so flow_trend has a
    real window immediately (instead of waiting 5 daily runs). Returns (total_rows, msg)."""
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            parsed = parse_cash_history_csv(f.read())
        if not parsed:
            return len(_load_cache()), "no rows parsed — check the CSV has date + FII/DII net columns"
        merged = _merge(_load_cache(), parsed)
        _save_cache(merged)
        return len(merged), f"backfilled {len(parsed)} rows; cache now spans {merged[0].date}..{merged[-1].date}"
    except FileNotFoundError:
        return len(_load_cache()), f"file not found: {csv_path}"


# ── 2. NSE F&O Participant-wise OI (the directional dataset) ──────────────────
def parse_participant_oi_csv(text: str, iso_date: str = "") -> FlowDay:
    """PURE parser for fao_participant_oi_<DDMMYYYY>.csv.

    Returns a FlowDay carrying fii_fut_net = FII (Future Index Long - Future Index Short),
    in *contracts* (net long positioning; positive = net long index futures).
    The file has a title line, then a header row, then one row per Client Type
    (Client, DII, FII, Pro, TOTAL).
    """
    reader = list(csv.reader(io.StringIO(text)))
    # find header row (the one containing 'Client Type')
    hdr_idx = next(
        (i for i, r in enumerate(reader) if any("client type" in c.strip().lower() for c in r)),
        None,
    )
    fii_net = 0.0
    if hdr_idx is not None:
        header = [c.strip() for c in reader[hdr_idx]]
        col = {name.lower(): j for j, name in enumerate(header)}
        li = col.get("future index long")
        si = col.get("future index short")
        for r in reader[hdr_idx + 1 :]:
            if not r:
                continue
            ctype = r[0].strip().upper()
            if ctype.startswith("FII"):
                lng = _to_float(r[li]) if li is not None and li < len(r) else 0.0
                sht = _to_float(r[si]) if si is not None and si < len(r) else 0.0
                fii_net = lng - sht
                break
    return FlowDay(date=iso_date or datetime.now().strftime("%Y-%m-%d"),
                   fii_cash=0.0, dii_cash=0.0, fii_fut_net=fii_net)


def fetch_fii_derivatives_sync(iso_date: Optional[str] = None, lookback: int = 5) -> tuple[FlowDay, bool]:
    """Download the participant-OI archive CSV and parse FII index-fut net.

    Walks back up to `lookback` days from the target date, because the file for
    'today' won't exist on weekends/holidays or before the post-close batch.
    Returns (FlowDay, is_stale). is_stale=False only on a genuine successful parse.
    """
    global LAST_ERROR_DERIV
    LAST_ERROR_DERIV = None
    if requests is None:
        LAST_ERROR_DERIV = "requests not available"
        return FlowDay(date=(iso_date or ""), fii_cash=0.0, dii_cash=0.0, fii_fut_net=0.0), True

    start = datetime.strptime(iso_date, "%Y-%m-%d").date() if iso_date else _date.today()
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.nseindia.com/"}
    last_err = None
    for back in range(lookback + 1):
        d = start - _timedelta(days=back)
        if d.weekday() >= 5:  # skip Sat/Sun (no report)
            continue
        url = _NSE_PART_CSV.format(ddmmyyyy=d.strftime("%d%m%Y"))
        try:
            resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
            if resp.status_code == 404:
                last_err = f"no file for {d.isoformat()} (404)"
                continue
            resp.raise_for_status()
            fd = parse_participant_oi_csv(resp.text, d.strftime("%Y-%m-%d"))
            if fd.fii_fut_net == 0.0 and "FII" not in resp.text.upper():
                last_err = f"file for {d.isoformat()} had no FII row (structure changed?)"
                continue
            return fd, False
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            last_err = f"{type(e).__name__}: {e}" + (f" [HTTP {status}]" if status else "")
    LAST_ERROR_DERIV = last_err or "no participant-OI file found in lookback window"
    return FlowDay(date=start.strftime("%Y-%m-%d"), fii_cash=0.0, dii_cash=0.0, fii_fut_net=0.0), True


# keep the old name as a thin alias (nothing else imports it, but be safe)
def parse_fii_derivatives(data: dict | str) -> FlowDay:
    if isinstance(data, str):
        data = json.loads(data)
    return FlowDay(date=_iso(data.get("date", "")), fii_cash=0.0, dii_cash=0.0,
                   fii_fut_net=_to_float(data.get("fii_fut_net")))


# ── 3. AMFI SIP ──────────────────────────────────────────────────────────────
def parse_amfi_sip(data: dict | str) -> SIPMonth:
    if isinstance(data, str):
        data = json.loads(data)
    return SIPMonth(month=data.get("month", ""), sip_inflow_cr=_to_float(data.get("sip_cr", data.get("sip_inflow_cr"))))


_SIP_TEMPLATE = {
    "_comment": "AMFI monthly SIP contribution (₹ cr). Update from the AMFI Monthly Note "
                "(amfiindia.com, ~8th-10th working day). Newest last. This is monthly + ~10d lagged; "
                "no reliable auto-URL exists (PDF filename carries a random hash), so it's maintained by hand.",
    "months": [
        {"month": "2026-03", "sip_cr": 19200.0},
        {"month": "2026-04", "sip_cr": 19500.0},
        {"month": "2026-05", "sip_cr": 20200.0},
    ],
}


def fetch_amfi_sip_sync() -> tuple[list[SIPMonth], bool]:
    """Read SIP from a hand-maintained config (.state/sip_data.json).

    is_stale=False when the config exists and its latest month is recent (<=45 days old),
    else True. Writes a template on first run so you know where to put the number.
    """
    global LAST_ERROR_SIP
    LAST_ERROR_SIP = None
    try:
        if not os.path.exists(_SIP_CONFIG):
            os.makedirs(_STATE_DIR, exist_ok=True)
            with open(_SIP_CONFIG, "w") as f:
                json.dump(_SIP_TEMPLATE, f, indent=2)
            LAST_ERROR_SIP = f"no config yet — template written to {_SIP_CONFIG}; fill in the latest month"
        with open(_SIP_CONFIG) as f:
            cfg = json.load(f)
        months = [parse_amfi_sip(m) for m in cfg.get("months", []) if m.get("month")]
        months.sort(key=lambda m: m.month)
        if not months:
            LAST_ERROR_SIP = LAST_ERROR_SIP or "config has no months"
            return [], True
        # freshness: latest month within ~45 days of today
        try:
            latest = datetime.strptime(months[-1].month, "%Y-%m")
            fresh = (datetime.now() - latest).days <= 45
        except ValueError:
            fresh = False
        if not fresh:
            LAST_ERROR_SIP = LAST_ERROR_SIP or f"latest SIP month {months[-1].month} is old — update {_SIP_CONFIG}"
        return months, (not fresh)
    except Exception as e:
        LAST_ERROR_SIP = f"{type(e).__name__}: {e}"
        return [SIPMonth("2026-05", 20200.0)], True


# ── 4. NSDL Sector FPI ───────────────────────────────────────────────────────
def parse_nsdl_fpi(data: str | dict) -> dict:
    """PURE parser for the NSDL fortnightly sector-wise HTML table -> {sector: net_cr}.

    Uses the latest fortnight's net-investment column. Defensive about column names:
    picks the sector-name column and the right-most numeric column as 'latest net'.
    Also accepts a plain dict (test shape).
    """
    if isinstance(data, dict):
        return {k: _to_float(v) for k, v in data.items()}
    try:
        import pandas as pd
    except Exception:
        return {}
    out: dict = {}
    try:
        tables = pd.read_html(io.StringIO(data))
    except Exception:
        return {}
    for t in tables:
        cols = [str(c) for c in t.columns]
        # find a sector/name column
        name_col = next((c for c in t.columns if any(k in str(c).lower()
                        for k in ("sector", "particular", "name"))), None)
        if name_col is None:
            continue
        # numeric columns (net investment values); take the right-most as most recent
        num_cols = [c for c in t.columns if c != name_col
                    and pd.to_numeric(t[c].astype(str).str.replace(",", "").str.replace("−", "-"),
                                      errors="coerce").notna().any()]
        if not num_cols:
            continue
        val_col = num_cols[-1]
        for _, row in t.iterrows():
            sector = str(row[name_col]).strip()
            if not sector or sector.lower() in ("nan", "total", "grand total"):
                continue
            out[sector] = _to_float(str(row[val_col]).replace("−", "-"))
        if out:
            break
    return out


def fetch_sector_fpi_sync() -> tuple[dict, bool]:
    """Fetch NSDL fortnightly sector-wise FPI static report and parse it. Fortnightly + lagged."""
    global LAST_ERROR_FPI
    LAST_ERROR_FPI = None
    try:
        if requests is None:
            raise RuntimeError("requests not available")
        resp = requests.get(_NSDL_FPI_URL, headers={"User-Agent": USER_AGENT}, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        parsed = parse_nsdl_fpi(resp.text)
        if not parsed:
            raise ValueError("NSDL page fetched but no sector table parsed (structure changed?)")
        return parsed, False
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        LAST_ERROR_FPI = f"{type(e).__name__}: {e}" + (f" [HTTP {status}]" if status else "")
        return {"IT": -500.0, "Banks": 1200.0}, True


# ── 5. Index Quotes ─────────────────────────────────────────────────────────
def fetch_index_quotes() -> list:
    from backend.quant.index_attribution import Quote
    # Still mock heavyweight quotes to drive breadth. TODO: wire to live feed.
    return [
        Quote("HDFCBANK", -0.4),
        Quote("RELIANCE", 0.5),
        Quote("ICICIBANK", 0.2),
        Quote("INFY", -1.2),
        Quote("TCS", -0.8),
        Quote("ITC", 0.1),
        Quote("LT", 0.6),
        Quote("SBIN", 1.1),
        Quote("BHARTIARTL", 0.3),
        Quote("KOTAKBANK", -0.5),
    ]


if __name__ == "__main__":
    # Standalone diagnostic — runs the CURRENT code fresh, bypassing any running server.
    print(f"requests available : {requests is not None}")
    print(f"cache file         : {_CASH_CACHE}  (exists={os.path.exists(_CASH_CACHE)})")
    days, stale = fetch_nse_cash_sync()
    print(f"stale={stale}  rows={len(days)}")
    if stale:
        print(f"REASON             : {LAST_ERROR or 'no network error — returned dummy/cache'}")
        print("  -> If REASON shows HTTP 401/403, NSE is blocking this IP (try from India / off-VPN).")
        print("  -> If it shows a real fetch but your APP still looks stale, the server is running")
        print("     the OLD module in memory: restart uvicorn (or launch with --reload).")
    for d in days[-5:]:
        print(f"  {d.date}  FII_cash={d.fii_cash:>10.2f}  DII_cash={d.dii_cash:>10.2f}  FII_fut_net={d.fii_fut_net:>10.2f}")
    if len(days) < 5:
        print(f"  NOTE: only {len(days)} day(s) cached — the 5d window fills over 5 daily runs, or")
        print("        run:  backfill_cash('fii_dii_history.csv')  to seed it now.")

    print("\n--- FII derivatives (participant-wise OI) ---")
    deriv, dstale = fetch_fii_derivatives_sync()
    print(f"stale={dstale}  date={deriv.date}  FII_fut_net={deriv.fii_fut_net:,.0f} contracts")
    if LAST_ERROR_DERIV:
        print(f"REASON: {LAST_ERROR_DERIV}")

    print("\n--- SIP (AMFI, monthly, manual config) ---")
    sip, sip_stale = fetch_amfi_sip_sync()
    print(f"stale={sip_stale}  months={len(sip)}  config={_SIP_CONFIG}")
    if LAST_ERROR_SIP:
        print(f"REASON: {LAST_ERROR_SIP}")
    for m in sip[-3:]:
        print(f"  {m.month}  SIP=₹{m.sip_inflow_cr:,.0f} cr")

    print("\n--- SECTOR FPI (NSDL, fortnightly) ---")
    fpi, fpi_stale = fetch_sector_fpi_sync()
    print(f"stale={fpi_stale}  sectors={len(fpi)}")
    if LAST_ERROR_FPI:
        print(f"REASON: {LAST_ERROR_FPI}")
    for k, v in list(fpi.items())[:8]:
        print(f"  {k:<24} ₹{v:,.0f} cr")
