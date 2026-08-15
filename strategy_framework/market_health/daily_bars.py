"""
strategy_framework/market_health/daily_bars.py
==============================================
THE single source of truth for DAILY OHLC and the daily indicator primitives
(moving averages, RSI, MACD, slope). Everything on the daily clock reads from
here rather than re-querying price_bars or re-implementing an SMA.

Daily bars live in `price_bars` under `timeframe='1d'` — the same table and DB as
the 1-minute data, just a different timeframe (that's why the DB copy in Drive and
the local one share a schema). NIFTY has a full year (~250 sessions); constituents
have daily bars in the fuller Drive copy and light up here automatically once those
rows are present locally.

Design notes
------------
* No lookahead: `series(sym, as_of=...)` returns only bars at/before `as_of`, so a
  backtest at date D never sees D+1. Live callers pass as_of=None (latest).
* Primitives return None when there is not enough history (e.g. a 200-DMA on 120
  bars) rather than a misleading partial value — the caller degrades to
  INSUFFICIENT_HISTORY, matching the framework's PRIOR-until-data convention.
* Absolute volume is passed through untouched (daily volume IS real for the index
  proxy and for stocks), unlike the reconstructed intraday index volume.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass

DAILY_TF = "1d"


@dataclass
class DailySeries:
    symbol: str
    dates: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)

    @property
    def last_close(self) -> float | None:
        return self.close[-1] if self.close else None

    @property
    def last_date(self) -> str | None:
        return self.dates[-1] if self.dates else None


def series(db_path: str, symbol: str, as_of: str | None = None,
           limit: int = 400) -> DailySeries:
    """Daily OHLCV for `symbol`, oldest→newest, only bars at/before `as_of`
    (ISO date or datetime; None = latest). `limit` caps how many trailing bars
    load (400 > any MA we use)."""
    con = sqlite3.connect(db_path)
    try:
        q = ("SELECT ts, open, high, low, close, COALESCE(volume,0) "
             "FROM price_bars WHERE symbol=? AND timeframe=?")
        args: list = [symbol, DAILY_TF]
        if as_of:
            q += " AND ts <= ?"
            args.append(as_of if len(as_of) > 10 else as_of + "T23:59:59Z")
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        rows = con.execute(q, args).fetchall()
    finally:
        con.close()
    rows.reverse()   # back to oldest→newest
    return DailySeries(symbol,
                       [r[0][:10] for r in rows],
                       [float(r[1]) for r in rows], [float(r[2]) for r in rows],
                       [float(r[3]) for r in rows], [float(r[4]) for r in rows],
                       [float(r[5]) for r in rows])


def available_daily_symbols(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM price_bars WHERE timeframe=? ORDER BY symbol",
            (DAILY_TF,))]
    finally:
        con.close()


def coverage(db_path: str, symbol: str = "NIFTY") -> dict:
    """How much daily history exists for `symbol` — the honesty input for every
    MA read (a 200-DMA needs ≥200 sessions)."""
    s = series(db_path, symbol)
    n = len(s)
    return {"symbol": symbol, "sessions": n,
            "from": s.dates[0] if n else None, "to": s.dates[-1] if n else None,
            "has_50dma": n >= 50, "has_200dma": n >= 200}


# ── indicator primitives (daily) — canonical home, imported not re-implemented ──
def sma(values: list[float], n: int) -> float | None:
    """Simple moving average of the last n values; None if fewer than n exist."""
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def ema(values: list[float], n: int) -> float | None:
    """Exponential moving average (last value of the EMA series); None if short."""
    if len(values) < n or n <= 0:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n            # seed with the SMA of the first window
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def slope_pct(values: list[float], n: int, look: int = 20) -> float | None:
    """Percent change of the n-period SMA vs where that SMA sat `look` bars earlier
    — i.e. is the moving average itself rising or falling. Needs n+look bars (a
    200-DMA slope over 20 sessions needs 220, not 400). None if history is short."""
    if n <= 0 or look <= 0 or len(values) < n + look:
        return None
    now = sum(values[-n:]) / n
    prev = sum(values[-n - look:-look]) / n
    return (now / prev - 1.0) * 100.0 if prev else None


def rsi(values: list[float], n: int = 14) -> float | None:
    """Wilder RSI over the last n deltas; None if fewer than n+1 closes."""
    if len(values) < n + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    window = deltas[-n:]
    gains = sum(d for d in window if d > 0) / n
    losses = sum(-d for d in window if d < 0) / n
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    """MACD line, signal line and histogram (daily). None if history is short.
    Signal is an EMA of the MACD line, so we need `slow + signal` bars."""
    if len(values) < slow + signal:
        return None
    def _ema_series(vals, span):
        k = 2.0 / (span + 1)
        out = []
        e = sum(vals[:span]) / span
        for i, v in enumerate(vals):
            e = v if i == 0 else (v * k + e * (1 - k))
            out.append(e)
        return out
    ef, es = _ema_series(values, fast), _ema_series(values, slow)
    macd_line = [a - b for a, b in zip(ef, es)]
    sig = _ema_series(macd_line, signal)
    return {"macd": macd_line[-1], "signal": sig[-1],
            "hist": macd_line[-1] - sig[-1]}
