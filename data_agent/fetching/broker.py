"""
data_agent/fetching/broker.py
=============================
ONE broker per run, over two backends — Breeze (ICICI) and Zerodha Kite — behind a
single interface so the orchestrator never branches on vendor.

    b = get_broker("breeze", session_token=tok)          # or:
    b = get_broker("kite", access_token=tok, api_key=k)
    bars = b.fetch_cash("RELIANCE", frm, to)             # -> [Bar, ...]
    bars = b.fetch_future("NIFTY", "2026-07-31", frm, to)
    bars = b.fetch_option("NIFTY", "2026-07-14", 24000, "CE", frm, to)

Every method returns the SAME normalized shape:
    Bar = (ts_utc_iso, open, high, low, close, volume, open_interest|None)
so it drops straight into fo_bars.save_fo_bars / bar_store.save_bars.

The two vendors differ in three ways this module hides:
  * AUTH      — Breeze: session_token (+ api_key/secret); Kite: access_token + api_key.
  * IDENTITY  — Breeze: short stock codes (RELIANCE->RELIND); Kite: numeric
                instrument_token resolved from the NFO/NSE instrument dump.
  * CALL      — Breeze: get_historical_data_v2(product_type, expiry_date, right,
                strike_price); Kite: historical_data(instrument_token, ..., oi=True).

Vendor SDKs are GUARDED imports, so this module loads (and its pure logic tests)
even where breeze_connect / kiteconnect aren't installed. Connections are lazy —
constructing a broker doesn't hit the network; the first fetch does.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import sys
try:
    from breeze_connect import BreezeConnect
except Exception:
    import os
    # Fallback to local breeze_env site-packages
    env_site_packages = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scratch_scripts", "breeze_env", "lib", "python3.9", "site-packages")
    if env_site_packages not in sys.path:
        sys.path.append(env_site_packages)
    try:
        from breeze_connect import BreezeConnect
    except Exception:
        BreezeConnect = None
try:
    from kiteconnect import KiteConnect
except Exception:
    KiteConnect = None

_IST = timezone(timedelta(hours=5, minutes=30))
Bar = tuple  # (ts_utc_iso, o, h, l, c, v, oi|None)

# Breeze short-code map — SINGLE SOURCE OF TRUTH is the generated
# strategy_framework/config/breeze_symbol_map.json (kept in lock-step with the CSV
# / constituents.py by validate_constituents_alignment.py). The small built-in dict
# is only an offline fallback so this module still imports if the JSON is absent.
_BREEZE_CODE_FALLBACK = {
    "RELIANCE": "RELIND", "INFY": "INFTEC", "TCS": "TCS", "HDFCBANK": "HDFBAN",
    "ICICIBANK": "ICIBAN", "SBIN": "STABAN", "LT": "LARTOU", "AXISBANK": "AXIBAN",
    "KOTAKBANK": "KOTMAH", "BHARTIARTL": "BHAAIR", "M&M": "MAHMAH", "NIFTY": "NIFTY",
}


def _load_breeze_codes() -> dict:
    p = os.path.join(os.path.dirname(__file__), "..", "..",
                     "strategy_framework", "config", "breeze_symbol_map.json")
    try:
        import json
        with open(os.path.abspath(p)) as f:
            return {str(k).upper(): v for k, v in json.load(f).items()}
    except Exception:
        return {}


# authoritative JSON wins; fallback fills any gap (and keeps NIFTY present)
BREEZE_CODE = {**_BREEZE_CODE_FALLBACK, **_load_breeze_codes()}


def _ist_to_utc_iso(x) -> str:
    """Broker timestamps are IST. Return UTC ISO 'YYYY-MM-DDTHH:MM:SSZ'."""
    if isinstance(x, datetime):
        dt = x if x.tzinfo else x.replace(tzinfo=_IST)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(x).strip()
    try:
        naive = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        naive = datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    return (naive - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _i(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


# ── normalizers (pure, unit-tested) ─────────────────────────────────────────
def normalize_breeze(rows) -> list[Bar]:
    """Breeze get_historical_data_v2 'Success' rows -> Bars.
    Cash rows have no OI; F&O rows carry 'open_interest'."""
    out = []
    for it in rows or []:
        ts = it.get("datetime")
        if not ts:
            continue
        out.append((_ist_to_utc_iso(ts), _f(it.get("open")), _f(it.get("high")),
                    _f(it.get("low")), _f(it.get("close")), _f(it.get("volume")),
                    _i(it.get("open_interest"))))
    return out


def normalize_kite(rows) -> list[Bar]:
    """Kite historical_data rows (dict with tz-aware 'date' + optional 'oi')."""
    out = []
    for it in rows or []:
        d = it.get("date")
        if d is None:
            continue
        out.append((_ist_to_utc_iso(d), _f(it.get("open")), _f(it.get("high")),
                    _f(it.get("low")), _f(it.get("close")), _f(it.get("volume")),
                    _i(it.get("oi"))))
    return out


# ── interface ───────────────────────────────────────────────────────────────
class Broker:
    kind = "base"
    def fetch_cash(self, symbol, frm, to, interval="1minute") -> list[Bar]: raise NotImplementedError
    def fetch_future(self, underlying, expiry, frm, to, interval="1minute") -> list[Bar]: raise NotImplementedError
    def fetch_option(self, underlying, expiry, strike, right, frm, to, interval="1minute") -> list[Bar]: raise NotImplementedError


# ── Breeze ──────────────────────────────────────────────────────────────────
class BreezeBroker(Broker):
    kind = "breeze"

    def __init__(self, session_token: str, api_key: str | None = None,
                 api_secret: str | None = None):
        self.session_token = session_token
        self.api_key = api_key or os.getenv("BREEZE_API_KEY")
        self.api_secret = api_secret or os.getenv("BREEZE_API_SECRET")
        self._b = None

    def _session(self):
        if self._b is None:
            b = BreezeConnect(api_key=self.api_key)
            b.generate_session(api_secret=self.api_secret, session_token=self.session_token)
            self._b = b
        return self._b

    @staticmethod
    def code(symbol: str) -> str:
        return BREEZE_CODE.get(symbol.upper(), symbol.upper())

    def fetch_cash(self, symbol, frm, to, interval="1minute"):
        r = self._session().get_historical_data_v2(
            interval=interval, from_date=frm, to_date=to,
            stock_code=self.code(symbol), exchange_code="NSE", product_type="cash")
        return normalize_breeze((r or {}).get("Success", []))

    def fetch_future(self, underlying, expiry, frm, to, interval="1minute"):
        r = self._session().get_historical_data_v2(
            interval=interval, from_date=frm, to_date=to, stock_code=self.code(underlying),
            exchange_code="NFO", product_type="futures", expiry_date=expiry)
        if r and r.get("Status") == 500:
            raise Exception(f"Breeze API Error: {r.get('Error')}")
        return normalize_breeze((r or {}).get("Success", []))

    def fetch_option(self, underlying, expiry, strike, right, frm, to, interval="1minute"):
        rt = "call" if str(right).upper().startswith("C") else "put"
        r = self._session().get_historical_data_v2(
            interval=interval, from_date=frm, to_date=to, stock_code=self.code(underlying),
            exchange_code="NFO", product_type="options", expiry_date=expiry,
            right=rt, strike_price=str(int(strike)))
        if r and r.get("Status") == 500:
            raise Exception(f"Breeze API Error: {r.get('Error')}")
        return normalize_breeze((r or {}).get("Success", []))



# ── Kite ────────────────────────────────────────────────────────────────────
class KiteBroker(Broker):
    kind = "kite"

    def __init__(self, access_token: str, api_key: str | None = None):
        self.access_token = access_token
        self.api_key = api_key or os.getenv("KITE_API_KEY")
        self._k = None
        self._instruments = {}   # exchange -> list[dict] (cached)

    def _kite(self):
        if self._k is None:
            if KiteConnect is None:
                raise RuntimeError("kiteconnect not installed (run inside breeze_env)")
            self._k = KiteConnect(api_key=self.api_key)
            self._k.set_access_token(self.access_token)
        return self._k

    def _dump(self, exchange):
        if exchange not in self._instruments:
            self._instruments[exchange] = self._kite().instruments(exchange)
        return self._instruments[exchange]

    @staticmethod
    def resolve_token(instruments, *, underlying=None, expiry=None, strike=None,
                      right=None, instrument_type=None, tradingsymbol=None):
        """Match a Kite instrument by STRUCTURED fields (robust to tradingsymbol
        format changes across weeklies/monthlies). Returns instrument_token or None."""
        want_r = None
        if right:
            want_r = "CE" if str(right).upper().startswith("C") else "PE"
        for ins in instruments:
            if tradingsymbol and ins.get("tradingsymbol") == tradingsymbol:
                return ins.get("instrument_token")
            if underlying and str(ins.get("name", "")).upper() != underlying.upper():
                continue
            if instrument_type and ins.get("instrument_type") != instrument_type:
                continue
            if expiry and str(ins.get("expiry")) != str(expiry):
                continue
            if strike is not None and float(ins.get("strike") or 0) != float(strike):
                continue
            if want_r and ins.get("instrument_type") != want_r:
                continue
            return ins.get("instrument_token")
        return None

    def _hist(self, token, frm, to, interval, oi=False):
        rows = self._kite().historical_data(token, frm, to, interval, oi=oi)
        return normalize_kite(rows)

    def fetch_cash(self, symbol, frm, to, interval="minute"):
        tok = self.resolve_token(self._dump("NSE"), tradingsymbol=symbol.upper())
        if tok is None:
            return []
        return self._hist(tok, frm, to, interval, oi=False)

    def fetch_future(self, underlying, expiry, frm, to, interval="minute"):
        tok = self.resolve_token(self._dump("NFO"), underlying=underlying,
                                 expiry=expiry, instrument_type="FUT")
        if tok is None:
            return []
        return self._hist(tok, frm, to, interval, oi=True)

    def fetch_option(self, underlying, expiry, strike, right, frm, to, interval="minute"):
        it = "CE" if str(right).upper().startswith("C") else "PE"
        tok = self.resolve_token(self._dump("NFO"), underlying=underlying, expiry=expiry,
                                 strike=strike, instrument_type=it)
        if tok is None:
            return []
        return self._hist(tok, frm, to, interval, oi=True)


# ── factory: one broker per run ─────────────────────────────────────────────
def get_broker(kind: str, **creds) -> Broker:
    k = (kind or "").lower()
    if k == "breeze":
        if not creds.get("session_token"):
            raise ValueError("breeze requires session_token")
        return BreezeBroker(**{x: creds[x] for x in ("session_token", "api_key", "api_secret") if x in creds})
    if k in ("kite", "zerodha"):
        if not creds.get("access_token"):
            raise ValueError("kite requires access_token")
        return KiteBroker(**{x: creds[x] for x in ("access_token", "api_key") if x in creds})
    raise ValueError(f"unknown broker: {kind!r} (use 'breeze' or 'kite')")


if __name__ == "__main__":
    # offline: prove normalization + token resolution without hitting either API.
    bz = normalize_breeze([{"datetime": "2026-07-14 09:16:00", "open": 100, "high": 101,
                            "low": 99, "close": 100.5, "volume": 1200, "open_interest": 45000}])
    print("breeze bar:", bz[0])
    kt = normalize_kite([{"date": datetime(2026, 7, 14, 9, 16, tzinfo=_IST), "open": 100,
                          "high": 101, "low": 99, "close": 100.5, "volume": 1200, "oi": 45000}])
    print("kite bar:  ", kt[0])
    insts = [{"name": "NIFTY", "expiry": "2026-07-14", "strike": 24000,
              "instrument_type": "CE", "instrument_token": 111, "tradingsymbol": "NIFTY2571424000CE"}]
    print("kite token:", KiteBroker.resolve_token(insts, underlying="NIFTY",
          expiry="2026-07-14", strike=24000, right="CE"))
