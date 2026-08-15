"""
event_fetcher.py
----------------
Refreshes economic-calendar dates from the web instead of hardcoding them.

Design: each event source has a fetcher that returns a date (or None). We try
the fetchers; whatever they return overrides the static fallback in
event_calendar.upcoming_events(). Anything not found keeps the fallback date,
flagged stale=True so the UI shows "date unconfirmed — refresh".

The actual HTTP calls run in YOUR environment (the build sandbox can't reach
rbi.org.in / mospi.gov.in / news). The PARSERS are pure functions tested offline
so you can trust them before they ever hit the network.

Sources (in priority order per event):
  RBI_MPC  -> rbi.org.in MPC schedule page  (authoritative)
  IN_CPI / IN_WPI / IN_IIP -> mospi.gov.in release calendar (authoritative)
  US_CPI / US_FOMC / US_NFP -> a search/news fallback (or a calendar API)
Confirmed live this session: next RBI MPC = 2026-08-05 (3–5 Aug).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional
import json


# ── what a refreshed event looks like ────────────────────────────────────────
@dataclass
class FetchedDate:
    code: str
    date: Optional[date]
    source_url: Optional[str]
    stale: bool          # True => fell back to static, not web-confirmed
    note: str = ""


# ── PARSERS (pure, offline-testable) ─────────────────────────────────────────
# Each takes already-fetched text/HTML and returns the next date >= today.

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 0)}
_MON3 = {m[:3].lower(): i for m, i in _MONTHS.items() if m}


def _next_after(dates: list[date], today: date) -> Optional[date]:
    fut = sorted(d for d in dates if d >= today)
    return fut[0] if fut else (sorted(dates)[-1] if dates else None)


def parse_rbi_mpc(text: str, today: date) -> Optional[date]:
    """RBI MPC pages list ranges like '3-5 August, 2026' or '5 August 2026'.
    We take the LAST day of a range (decision day) and pick the next one."""
    found: list[date] = []
    # '3-5 August 2026'  or  '3–5 August, 2026'
    for m in re.finditer(
        r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", text):
        d2, mon, yr = int(m.group(2)), m.group(3).lower(), int(m.group(4))
        if mon in _MONTHS or mon in _MON3:
            mm = _MONTHS.get(mon) or _MON3.get(mon)
            try: found.append(date(yr, mm, d2))
            except ValueError: pass
    # single '5 August 2026'
    for m in re.finditer(r"(?<![-–]\s)(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", text):
        d1, mon, yr = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mm = _MONTHS.get(mon) or _MON3.get(mon)
        if mm:
            try: found.append(date(yr, mm, d1))
            except ValueError: pass
    return _next_after(found, today)


def parse_mospi_release(text: str, today: date, keyword: str) -> Optional[date]:
    """MoSPI release calendar lines pair an indicator with a date. We scan lines
    containing the keyword (e.g. 'Consumer Price Index') for a date."""
    out: list[date] = []
    for line in text.splitlines():
        if keyword.lower() not in line.lower():
            continue
        for m in re.finditer(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", line):
            d1, mon, yr = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            mm = _MONTHS.get(mon) or _MON3.get(mon)
            if mm:
                try: out.append(date(yr, mm, d1))
                except ValueError: pass
        for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", line):
            try: out.append(date(int(m[1]), int(m[2]), int(m[3])))
            except ValueError: pass
    return _next_after(out, today)

def parse_fmp_calendar(text: str, today: date, keyword: str) -> Optional[date]:
    """Parses FMP JSON economic calendar array for US events."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: list[date] = []
    for item in data:
        if item.get("country", "") == "US" and keyword.lower() in item.get("event", "").lower():
            date_str = item.get("date", "")
            # usually "YYYY-MM-DD HH:MM:SS"
            if len(date_str) >= 10:
                try:
                    d = date.fromisoformat(date_str[:10])
                    out.append(d)
                except ValueError:
                    pass
    return _next_after(out, today)


# ── FETCHERS (network — run in your env; injected http_get for testability) ──
def make_fetchers(http_get: Callable[[str], str], today: date) -> dict:
    """http_get(url)->text is YOURS (requests/httpx). Returns {code: FetchedDate}.
    Each fetcher is wrapped so a network/parse failure -> stale fallback, never a crash."""

    def safe(code, url, parser):
        try:
            txt = http_get(url)
            d = parser(txt)
            if d:
                return FetchedDate(code, d, url, stale=False, note="web-confirmed")
            return FetchedDate(code, None, url, stale=True, note="parsed-no-date")
        except Exception as e:
            return FetchedDate(code, None, url, stale=True, note=f"fetch-failed: {e}")

    return {
        "RBI_MPC": lambda: safe(
            "RBI_MPC", "https://www.rbi.org.in/scripts/annualpolicy.aspx",
            lambda t: parse_rbi_mpc(t, today)),
        "IN_CPI": lambda: safe(
            "IN_CPI", "https://www.mospi.gov.in/release-calendar",
            lambda t: parse_mospi_release(t, today, "Consumer Price Index")),
        "IN_WPI": lambda: safe(
            "IN_WPI", "https://eaindustry.nic.in/",
            lambda t: parse_mospi_release(t, today, "Wholesale Price Index")),
        "IN_IIP": lambda: safe(
            "IN_IIP", "https://www.mospi.gov.in/release-calendar",
            lambda t: parse_mospi_release(t, today, "Index of Industrial Production")),
        "US_CPI": lambda: safe(
            "US_CPI", f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today.isoformat()}&to={(today.replace(year=today.year+1)).isoformat()}&apikey=demo",
            lambda t: parse_fmp_calendar(t, today, "Core Inflation Rate YoY")),
        "US_FOMC": lambda: safe(
            "US_FOMC", f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today.isoformat()}&to={(today.replace(year=today.year+1)).isoformat()}&apikey=demo",
            lambda t: parse_fmp_calendar(t, today, "Fed Interest Rate Decision")),
        "US_NFP": lambda: safe(
            "US_NFP", f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today.isoformat()}&to={(today.replace(year=today.year+1)).isoformat()}&apikey=demo",
            lambda t: parse_fmp_calendar(t, today, "Non Farm Payrolls")),
        "US_PCE": lambda: safe(
            "US_PCE", f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today.isoformat()}&to={(today.replace(year=today.year+1)).isoformat()}&apikey=demo",
            lambda t: parse_fmp_calendar(t, today, "Core PCE Price Index")),
    }


def refresh_dates(http_get: Callable[[str], str], today: date | None = None) -> dict:
    """Run all fetchers. Returns {code: FetchedDate}. Codes not covered here
    (US events) just keep their static fallback in event_calendar."""
    today = today or date.today()
    fetchers = make_fetchers(http_get, today)
    return {code: fn() for code, fn in fetchers.items()}


# ── offline self-test of the PARSERS (no network) ────────────────────────────
if __name__ == "__main__":
    today = date(2026, 6, 27)

    rbi_html = """
        <table><tr><td>Sixth Bi-monthly Policy</td><td>3-5 August, 2026</td></tr>
        <tr><td>Seventh</td><td>6-8 October 2026</td></tr></table>
    """
    print("RBI MPC next:", parse_rbi_mpc(rbi_html, today), "(expect 2026-08-05)")

    mospi_txt = (
        "Consumer Price Index (CPI) for June 2026 | 13 July 2026\n"
        "Index of Industrial Production (IIP) for May 2026 | 31 July 2026\n"
        "Consumer Price Index (CPI) for July 2026 | 12 August 2026\n")
    print("CPI next:", parse_mospi_release(mospi_txt, today, "Consumer Price Index"),
          "(expect 2026-07-13)")
    print("IIP next:", parse_mospi_release(mospi_txt, today, "Index of Industrial Production"),
          "(expect 2026-07-31)")

    # simulate a wired http_get so refresh_dates() is exercised offline
    fmp_json = json.dumps([
        {"event": "Core Inflation Rate YoY", "date": "2026-07-14 08:30:00", "country": "US"},
        {"event": "Fed Interest Rate Decision", "date": "2026-07-29 14:00:00", "country": "US"},
    ])
    
    fake = {
        "https://www.rbi.org.in/scripts/annualpolicy.aspx": rbi_html,
        "https://www.mospi.gov.in/release-calendar": mospi_txt,
        "https://eaindustry.nic.in/": "Wholesale Price Index | 14 July 2026",
    }
    def mock_get(url):
        if "financialmodelingprep" in url: return fmp_json
        return fake.get(url, "")

    out = refresh_dates(mock_get, today)
    print("\nrefresh_dates():")
    for code, fd in out.items():
        print(f"  {code:8} {fd.date} stale={fd.stale} ({fd.note})")
