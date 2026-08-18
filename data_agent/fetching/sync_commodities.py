"""sync_commodities.py — MCX commodities, USDINR and GIFTNIFTY, from Upstox.

Fetches from Upstox. Writes through the store. It was the last script in the repo
holding raw `INSERT INTO price_bars`, and every convention the store enforces had to
be re-remembered here by hand.

WHAT CHANGED, AND WHY IT WAS NOT COSMETIC
-----------------------------------------
1d now goes through `daily_bars.write_daily()` and 1m through `bar_store.save_bars()`,
which is the split `DATA_AGENT_ARCHITECTURE.md` specifies. That removed three real
defects, not just three copies of code:

  * THE DAILY DELETE DESTROYED HISTORY. Each run did
        DELETE FROM price_bars WHERE symbol=? AND timeframe='1d'
                               AND ts >= '2025-07-30T00:00:00Z'
    and then re-inserted only what Upstox returned in that window. When an MCX
    contract rolls, the new instrument key has only its own short history — so the
    delete removed a year of bars and the insert put back a few weeks. This is
    exactly how GOLD went from 249 bars to 12. `write_daily` uses INSERT OR REPLACE
    and deletes nothing, so a short vendor response now updates the sessions it
    covers and leaves the rest alone.

  * BOTH DELETES OMITTED `exchange`. The primary key is
    (exchange, symbol, timeframe, ts). A delete without the exchange predicate
    reaches across venues — and because the index is keyed leftmost on exchange, it
    also full-scans a 300MB+ table to do it.

  * THE DAILY DELETE COMPARED AGAINST A 'Z' TIMESTAMP while inserting the canonical
    unsuffixed form. Those are different strings, so the boundary session
    2025-07-30 was never actually cleared. A latent duplicate, of precisely the kind
    that took a day to clean out of the index symbols.

Going through `write_daily` also picks up the things it does for every other daily
sync: the foreign-ts-format purge, exchange resolution from what is already stored,
and the known vendor corrections.

TIMESTAMPS
----------
This file used to convert Upstox's "2026-07-27T23:29:00+05:30" to UTC itself. The
store's `to_db_ts` does the same job; the two were verified byte-identical across
IST midnight, year end and the 05:29 boundary before the local copy was deleted. The
raw vendor string is now handed to the store, which is the only place that decides
what a timestamp looks like on disk.

Daily bars keep the canonical '%Y-%m-%dT00:00:00' — the IST trading date, with no
conversion and no trailing Z. A Z here is a SECOND row for the same session beside
anything `daily_bars` wrote, which is what duplicated 13 index symbols.
"""
import os
import sys
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(HERE)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))

from bar_store import DB_PATH as _DEFAULT_DB, save_bars
from continuous import PER_CONTRACT, contract_symbol
from continuous import write as write_continuous
from daily_bars import PLAUSIBLE_1M_FLOOR, write_daily
from upstox_auth import get_upstox_token

# Honour OPTION_CHAINS_DB like every other script. This was hardcoded to the Google
# Drive copy, so pointing a run at the local mirror silently wrote to Drive instead.
DB_PATH = os.environ.get("OPTION_CHAINS_DB", _DEFAULT_DB)

UPSTOX_ACCESS_TOKEN = get_upstox_token()

# Mapping of symbols in DB to Upstox keys and their exchange codes
SYMBOLS_MAP = {
    # CRUDEOIL_MCX, not CRUDEOIL: this is the INR MCX contract. CRUDEOIL is the
    # USD NYMEX series from Yahoo CL=F (sync_crudeoil_yf.py). Sharing one symbol
    # produced an 84x currency "move" on 2026-02-20. See daily_bars.NATIVE_CCY.
    "CRUDEOIL_MCX": {"key": "MCX_FO|560977", "exchange": "MCX"},
    "USDINR": {"key": "GLOBAL_INDICATOR|USDINR", "exchange": "CDS"},
    "GOLD": {"key": "MCX_FO|466583", "exchange": "MCX"},
    "SILVER": {"key": "MCX_FO|471725", "exchange": "MCX"},
    "COPPER": {"key": "MCX_FO|562048", "exchange": "MCX"},
    "GIFTNIFTY": {"key": "GLOBAL_INDEX|SGX NIFTY", "exchange": "NSEIX"},
}

# db symbol -> the EXACT MCX product code.
#
# Matching on the instrument master's `name` field is not enough. Upstox reports
# name='GOLD' for GOLD, GOLDM, GOLDTEN, GOLDGUINEA and GOLDPETAL alike, so a
# nearest-expiry match by name picks whichever VARIANT expires soonest — which is
# almost always the mini or the micro. A live resolve returned GOLDTEN26AUGFUT for
# GOLD and SILVERMIC26AUGFUT for SILVER.
#
# That failure is worse than the option one, because it is invisible: SILVERMIC
# quotes in the same INR-per-kg units as SILVER, around 230,000, so the plausibility
# floor accepts it happily. A wrong contract at a right-looking price passes every
# check we have. Only the tradingsymbol distinguishes them.
MCX_PRODUCTS = {"GOLD": "GOLD", "SILVER": "SILVER", "COPPER": "COPPER",
                "CRUDEOIL_MCX": "CRUDEOIL"}

# GOLD26AUGFUT matches; GOLDTEN26AUGFUT does not, because the product code must be
# followed immediately by the two-digit year.
TRADINGSYMBOL_RE = "^{code}[0-9]{{2}}[A-Z]{{3}}FUT$"

# WHAT A RUPEE IS WORTH, NOT WHAT UPSTOX HAPPENS TO SEND TODAY.
#
# This used to be `SCALE = {"USDINR": 10.0}` with the comment "Upstox quotes the USDINR
# indicator 10x scaled", and the divisor was applied unconditionally. On 2026-08-16 Upstox
# STOPPED scaling the GLOBAL_INDICATOR|USDINR feed, our /10 stayed, and six daily bars plus
# every minute bar after that landed at 9.55 for a 95.5 rupee. The boundary is unambiguous:
# the last 1m close of 14-Aug was 95.415 and the first of 16-Aug was 9.5415 — the same price,
# ratio 10.0000 to four places, on a rate that moves 0.05% a day.
#
# The lesson is not "the constant was wrong". It is that a CONSTANT was the wrong shape for
# the problem: a compensation for someone else's convention has to be re-derived every run,
# because the only party who can change that convention is the one we cannot see. So instead
# of asserting the divisor, state what the series IS — a rupee is tens of rupees to a dollar,
# not units and not hundreds — and let each response find the power of ten that satisfies it.
# Powers of ten only, and only into a band this narrow, so this can correct a decimal point
# and can NOT quietly rescale a wrong instrument into looking right.
PLAUSIBLE_BAND = {
    # symbol: (low, high) for the MEDIAN close of a response, in stored units
    "USDINR": (50.0, 150.0),        # INR per USD; 63.3 in 2018, 96.6 at the 2026 high
}

DAILY_FROM = "2025-07-30"
INTRADAY_FROM = "2026-06-29"


def resolve_mcx_keys(wanted, log=print, n=1):
    """Resolve MCX futures to the CURRENT contract from Upstox's instrument master.

    The hardcoded keys in SYMBOLS_MAP point at a SPECIFIC contract. When it expires
    the key simply stops returning data, which is why GOLD stalled at 2026-08-04 and
    COPPER at 2026-07-30 while SILVER and CRUDEOIL_MCX — whose contracts were still
    live — stayed current. Nothing errors; the feed just goes quiet.

    Returns {db_symbol: instrument_key} for whatever it could resolve. Anything it
    cannot resolve is left to the hardcoded fallback, so a bad instrument dump
    degrades to today's behaviour instead of breaking the run.
    """
    import pandas as pd
    out = {}
    try:
        df = pd.read_csv(
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz")
        # FUTURES ONLY. 'MCX_FO' is Futures AND Options, and options expire sooner —
        # so a nearest-expiry match by name alone picks an OPTION. That is exactly
        # what happened on the first run: GOLD resolved to a call contract and, since
        # the old code DELETEd before writing, 249 gold bars were replaced by 12 bars
        # of option premium (close 49-600, against gold's ~150,000). Filter on
        # instrument_type before anything else.
        df = df[df["instrument_key"].astype(str).str.startswith("MCX_FO|", na=False)].copy()
        itype = df.get("instrument_type")
        if itype is None:
            log("   instrument master has no instrument_type column — refusing to roll")
            return {}
        itype = itype.astype(str).str.upper().str.strip()
        log(f"   MCX_FO instrument_type values present: "
            f"{', '.join(sorted(itype.unique())[:12])}")

        # MATCH ON PREFIX, NOT EQUALITY.
        #
        # This tested `== "FUT"` and matched nothing, then correctly refused to roll
        # — but reported only "no FUT rows", which said nothing about what WAS there
        # and left the wrong key in place. Commodity futures are typed FUTCOM (as
        # index and stock futures are FUTIDX and FUTSTK), so the equality test could
        # never have matched on MCX.
        #
        # A prefix match is safe against the failure that matters: MCX options on
        # futures are typed OPTFUT, which does not start with FUT. That is the
        # contamination this filter exists to stop — it is how GOLD's key ended up
        # on a call and wrote five weeks of option premium.
        fut = df[itype.str.startswith("FUT")].copy()

        # Belt and braces. instrument_type is one vendor field and vendors relabel;
        # a futures contract has no strike and no CE/PE, whatever it is called. Two
        # independent reasons to reject an option is the right number here, given
        # what one missed filter already cost.
        if "strike_price" in fut.columns:
            strike = pd.to_numeric(fut["strike_price"], errors="coerce").fillna(0)
            fut = fut[strike == 0]
        if "option_type" in fut.columns:
            ot = fut["option_type"].astype(str).str.upper().str.strip()
            fut = fut[~ot.isin(("CE", "PE"))]

        if fut.empty:
            log("   no futures rows survived the filter — refusing to roll. "
                "Check the instrument_type values printed above.")
            return {}
        log(f"   {len(fut):,} MCX futures rows after filtering")
        fut["expiry"] = pd.to_datetime(fut["expiry"], errors="coerce")
        today = pd.Timestamp(datetime.now().date())
        live = fut[fut["expiry"] >= today]
        has_ts = "tradingsymbol" in live.columns
        if not has_ts:
            log("   instrument master has no tradingsymbol column — refusing to roll, "
                "because `name` alone cannot tell GOLD from GOLDTEN")
            return {}
        tsym = live["tradingsymbol"].astype(str).str.upper().str.strip()
        for db_sym, code in wanted.items():
            pattern = TRADINGSYMBOL_RE.format(code=code.upper())
            m = live[tsym.str.match(pattern, na=False)]
            if m.empty:
                # Show the variants that DID match on name, so a rename or a code
                # change is diagnosable instead of just "no live contract".
                near = live[live["name"].astype(str).str.upper() == code.upper()]
                seen = sorted(near["tradingsymbol"].astype(str).unique())[:8]
                log(f"   {db_sym}: no contract matching {pattern} — keeping fallback key")
                if seen:
                    log(f"      variants listed under name='{code}': {', '.join(seen)}")
                continue
            picked = m.sort_values("expiry").head(n)
            out[db_sym] = [
                {"key": r["instrument_key"], "expiry": str(r["expiry"])[:10],
                 "tradingsymbol": r.get("tradingsymbol", "")}
                for _, r in picked.iterrows()]
            for con_ in out[db_sym]:
                log(f"   {db_sym}: {con_['key']} "
                    f"(expiry {con_['expiry']}, {con_['tradingsymbol']})")
    except Exception as e:
        log(f"   instrument master unavailable ({str(e)[:60]}) — keeping fallback keys")
    return out


def _get(url):
    r = requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}",
                                   "Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        print(f"   Upstox {r.status_code} for {url.split('/historical-candle/')[-1][:60]}")
        return []
    return r.json().get("data", {}).get("candles", [])


def fetch_1m(key, frm, to):
    return _get(f"https://api.upstox.com/v2/historical-candle/{key}/1minute/{to}/{frm}")


def fetch_1m_today(key):
    return _get(f"https://api.upstox.com/v2/historical-candle/intraday/{key}/1minute")


def fetch_1d(key, frm, to):
    return _get(f"https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}")


def _divisor(candles, symbol, log=print):
    """The power of ten that puts THIS response inside the symbol's plausible band.

    Re-derived per response, on purpose. See PLAUSIBLE_BAND. A symbol with no band gets
    1.0 and is untouched, so this changes nothing for the MCX contracts.

    Returns 1.0 when no power of ten fits — deliberately NOT an exception. The refusal
    belongs to implausible(), which is already called at all four write sites and already
    knows how to say why; handing back the raw values lets it see and report them. Two
    places that can reject a response is one place too many.
    """
    band = PLAUSIBLE_BAND.get(symbol)
    if not band or not candles:
        return 1.0
    low, high = band
    try:
        closes = sorted(float(c[4]) for c in candles)
    except (TypeError, ValueError, IndexError):
        return 1.0
    med = closes[len(closes) // 2]
    if med <= 0:
        return 1.0
    for k in range(-3, 4):                       # 0.001x .. 1000x, powers of ten only
        d = 10.0 ** k
        if low <= med / d <= high:
            log(f"   [scale] {symbol}: vendor median {med:,.4f} -> /{d:g} = "
                f"{med / d:,.4f}, inside the {low:g}-{high:g} band")
            return d
    log(f"   [scale] {symbol}: vendor median {med:,.4f} does not reach the "
        f"{low:g}-{high:g} band at ANY power of ten — passing it through unscaled so "
        f"implausible() can refuse it")
    return 1.0


def _rows(candles, symbol, ts_of):
    """Upstox candles -> store rows (ts, o, h, l, c, v, oi), deduped by ts.

    `ts_of` decides the timestamp convention: raw vendor string for 1m (the store
    converts), canonical trading date for 1d.
    """
    scale = _divisor(candles, symbol)
    seen, out = set(), []
    for c in candles:
        try:
            ts = ts_of(c[0])
            if ts in seen:
                continue
            seen.add(ts)
            out.append((ts, float(c[1]) / scale, float(c[2]) / scale,
                        float(c[3]) / scale, float(c[4]) / scale, float(c[5]),
                        float(c[6]) if len(c) > 6 and c[6] is not None else None))
        except (TypeError, ValueError, IndexError) as e:
            print(f"   skipped a malformed candle {c}: {e}")
    return out



def implausible(rows, symbol):
    """Is this vendor response the wrong instrument? -> reason, or None.

    Checks the MEDIAN close, not the minimum: a single bad print is a bad print, but
    a whole response an order of magnitude below the floor means the instrument key
    is no longer naming what we think it names.

    This is the fix that matters. Correcting GOLD's key patches today; refusing to
    write prices that cannot be the instrument stops the next wrong key — expired
    contract, mistyped edit, a roll that resolves to an option — from filing five
    weeks of someone else's prices under a metal's name before anyone notices.

    A FLOOR IS HALF A GUARD. That is not a style point — it is why the USDINR flip was
    written and not refused. PLAUSIBLE_1M_FLOOR covers GOLD, SILVER, COPPER and
    CRUDEOIL_MCX and has no USDINR entry, so this function returned None for USDINR and
    the store wrote 9.55 for six sessions. A one-sided test also cannot catch the OTHER
    direction of the same failure: if Upstox starts sending USDINR 100x scaled tomorrow,
    a floor is satisfied by 9,550. So band symbols are checked at both ends.
    """
    if not rows:
        return None
    closes = sorted(r[4] for r in rows)
    med = closes[len(closes) // 2]

    band = PLAUSIBLE_BAND.get(symbol)
    if band:
        low, high = band
        if not (low <= med <= high):
            return (f"median close {med:,.4f} is outside the {low:g}-{high:g} band for "
                    f"{symbol}, and no power of ten brought it inside — this is either a "
                    f"vendor scale change this code cannot express or the wrong "
                    f"instrument. Refusing rather than guessing")
        return None

    floor = PLAUSIBLE_1M_FLOOR.get(symbol)
    if not floor:
        return None
    if med < floor:
        return (f"median close {med:,.1f} is below the {floor:,} floor for {symbol} "
                f"— this key is not returning {symbol}, it is returning something "
                f"else (an option contract prints in this range)")
    return None



def _resume_from(db, symbol, timeframe, floor, overlap_days):
    """Where to start fetching: just before what we already hold.

    The floors below were absolute — every run re-pulled a year of daily and six
    weeks of minutes for every contract, then upserted the lot. Harmless to the data
    (the writes are upserts, nothing is lost) but it re-downloaded ~20 contracts'
    full history daily and rewrote rows that had not changed.

    A small overlap is deliberate, not laziness: the last few sessions are the ones
    a vendor revises, and re-pulling them costs almost nothing. Same rule
    daily_bars.sync_symbols already follows for Yahoo.
    """
    import sqlite3 as _sq
    try:
        con = _sq.connect(db)
        row = con.execute("select max(ts) from price_bars where symbol=? and "
                          "timeframe=?", (symbol, timeframe)).fetchone()
        con.close()
    except Exception:                                        # noqa: BLE001
        return floor
    if not row or not row[0]:
        return floor                    # nothing stored yet — take the whole window
    last = datetime.strptime(row[0][:10], "%Y-%m-%d")
    start = last - timedelta(days=overlap_days)
    return max(start, datetime.strptime(floor, "%Y-%m-%d")).strftime("%Y-%m-%d")


def sync_symbol(symbol, config, db, floor_symbol=None,
                timeframes=("1m", "1d"), full=False):
    print(f"\n--- {symbol} ({config['key']}) ---")
    key, exchange = config["key"], config["exchange"]

    rows_1m = []
    # ---- 1m: weekly batches back to INTRADAY_FROM, plus today ----
    candles = []
    if "1m" not in timeframes:
        print("   1m: skipped (far-dated contract — daily only until it nears the front)")
        candles = None
    frm_1m = INTRADAY_FROM if full else _resume_from(db, symbol, "1m",
                                                     INTRADAY_FROM, 1)
    cur, now = datetime.strptime(frm_1m, "%Y-%m-%d"), datetime.now()
    while candles is not None and cur < now:
        nxt = min(cur + timedelta(days=7), now)
        candles += fetch_1m(key, cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d"))
        cur = nxt + timedelta(days=1)
    if candles is not None:
        candles += fetch_1m_today(key)

    # The vendor's own "+05:30" string goes to the store untouched — see the module
    # docstring. No DELETE: the primary key makes this an upsert, and deleting a
    # range the vendor may return less of is how history gets thrown away.
    rows_1m = _rows(candles, symbol, lambda t: t) if candles is not None else []
    bad = implausible(rows_1m, floor_symbol or symbol)
    if bad:
        raise ValueError(f"refusing to write 1m for {symbol}: {bad}")
    if rows_1m:
        save_bars(rows_1m, exchange=exchange, symbol=symbol, timeframe="1m", db=db)
        print(f"   1m: {len(rows_1m)} bars "
              f"({rows_1m[0][0][:16]} .. {rows_1m[-1][0][:16]})")
    else:
        print("   1m: nothing returned")

    # ---- 1d ----
    frm_1d = DAILY_FROM if full else _resume_from(db, symbol, "1d", DAILY_FROM, 5)
    daily = fetch_1d(key, frm_1d, datetime.now().strftime("%Y-%m-%d"))
    rows_1d = _rows(daily, symbol,
                    lambda t: datetime.strptime(t[:10], "%Y-%m-%d")
                                      .strftime("%Y-%m-%dT00:00:00"))
    bad = implausible(rows_1d, floor_symbol or symbol)
    if bad:
        raise ValueError(f"refusing to write 1d for {symbol}: {bad}")
    if rows_1d:
        n, _ = write_daily(rows_1d, symbol, db, exchange=exchange)
        print(f"   1d: {n} bars ({rows_1d[0][0][:10]} .. {rows_1d[-1][0][:10]})")
    else:
        print("   1d: nothing returned")
    return len(rows_1m), len(rows_1d)


def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database file not found at: {DB_PATH}")
        return 1
    print(f"database: {DB_PATH}")

    # OPT-IN roll. Rolling to a new contract does not extend the old series — the new
    # key only has its own short history. write_daily no longer deletes, so a roll is
    # far less destructive than it was, but the two contracts are still different
    # instruments stored under one name. Until per-contract storage exists, the
    # hardcoded keys stay the default: they go stale, which the audit reports, rather
    # than quietly splicing two contracts into one series.
    # --backfill-expired: fetch contracts that have already expired.
    #
    # These are the contracts that were FRONT MONTH for the periods our live
    # contracts were not. Without them, strict front-month selection leaves GOLD
    # with two days: October only took over on 2026-08-06, and the August contract
    # that preceded it is not in the live instrument master any more.
    #
    # Reachable only because the token was recorded. The Expired Instruments API
    # cannot list MCX expiries — commodities have no permanent underlying key — so
    # the registry's expired_instrument_key is the whole mechanism.
    if "--backfill-expired" in sys.argv:
        sys.path.insert(0, os.path.dirname(HERE))
        import contract_registry as _reg
        from upstox_expired import candles as _expired_candles
        doc = _reg.load()
        gone = [c for c in _reg.expired(doc) if c["product"] in PER_CONTRACT]
        if not gone:
            print("No expired contracts recorded. Nothing to backfill.")
            return 0
        print(f"{len(gone)} expired contracts recorded\n")
        failed = []
        for c in sorted(gone, key=lambda x: x["expiry"]):
            sym = contract_symbol(c["product"], c["expiry"])
            ek = c["expired_instrument_key"]
            print(f"--- {sym}   {ek}")
            for interval, tf, frm in (("day", "1d", DAILY_FROM),
                                      ("1minute", "1m", INTRADAY_FROM)):
                rows, err = _expired_candles(ek, interval, frm, c["expiry"])
                if err:
                    print(f"    {tf}: {err}")
                    failed.append(f"{sym}:{tf}")
                    continue
                if not rows:
                    print(f"    {tf}: no candles returned")
                    continue
                if tf == "1d":
                    built = _rows(rows, c["product"],
                                  lambda t: datetime.strptime(t[:10], "%Y-%m-%d")
                                                    .strftime("%Y-%m-%dT00:00:00"))
                    bad = implausible(built, c["product"])
                    if bad:
                        print(f"    1d: REFUSED — {bad}")
                        failed.append(f"{sym}:1d")
                        continue
                    n, _ = write_daily(built, sym, DB_PATH,
                                       exchange=SYMBOLS_MAP[c["product"]]["exchange"])
                else:
                    built = _rows(rows, c["product"], lambda t: t)
                    bad = implausible(built, c["product"])
                    if bad:
                        print(f"    1m: REFUSED — {bad}")
                        failed.append(f"{sym}:1m")
                        continue
                    n = save_bars(built, exchange=SYMBOLS_MAP[c["product"]]["exchange"],
                                  symbol=sym, timeframe=tf, db=DB_PATH)
                print(f"    {tf}: {n:,} bars "
                      f"({built[0][0][:10]} .. {built[-1][0][:10]})")
        print()
        if failed:
            print(f"FAILED: {', '.join(failed)}")
            return 1
        print("Backfill complete. Now rebuild:")
        print("   python data_agent/fetching/continuous.py --apply")
        print("   python data_agent/fetching/continuous.py --timeframe 1m --apply")
        return 0

    # --list-contracts: every contract Upstox still lists, expired ones included.
    #
    # Decides whether a clean wipe-and-rebuild is affordable. Per-contract storage can
    # only reconstruct history from contracts we can still FETCH. If the master drops
    # contracts at expiry, a rebuild starts when the current ones went live and the
    # older history is gone for good; if it keeps them, we can rebuild the lot.
    if "--list-contracts" in sys.argv:
        import pandas as pd
        df = pd.read_csv(
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
            low_memory=False)
        df = df[df["instrument_key"].astype(str).str.startswith("MCX_FO|", na=False)]
        it = df["instrument_type"].astype(str).str.upper().str.strip()
        fut = df[it.str.startswith("FUT")].copy()
        if "strike_price" in fut.columns:
            fut = fut[pd.to_numeric(fut["strike_price"], errors="coerce").fillna(0) == 0]
        fut["expiry"] = pd.to_datetime(fut["expiry"], errors="coerce")
        today = pd.Timestamp(datetime.now().date())
        tsym = fut["tradingsymbol"].astype(str).str.upper().str.strip()
        print(f"today: {today.date()}\n")
        for db_sym, code in sorted(MCX_PRODUCTS.items()):
            m = fut[tsym.str.match(TRADINGSYMBOL_RE.format(code=code.upper()), na=False)]
            m = m.sort_values("expiry")
            past = m[m["expiry"] < today]
            live = m[m["expiry"] >= today]
            print(f"{db_sym}:  {len(past)} EXPIRED still listed, {len(live)} live")
            for _, r in m.iterrows():
                tag = "expired" if r["expiry"] < today else "live"
                print(f"     {str(r['expiry'])[:10]}  {r['tradingsymbol']:<22} "
                      f"{r['instrument_key']:<16} {tag}")
            print()
        print("EXPIRED contracts still listed can be fetched, so history can be")
        print("rebuilt from them. If that count is 0, a wipe loses the old history.")
        return 0

    # --resolve-only: print what the instrument master says and stop.
    #
    # Worth having separately, because --roll writes. Until the fetch side stores
    # per contract, rolling to a new key appends the new contract's bars onto the
    # old series under one name — an unlabelled splice, which is the thing
    # continuous.py exists to stop. Look before writing.
    if "--resolve-only" in sys.argv:
        print("Resolving current MCX contracts (read-only, nothing will be written)\n")
        found = resolve_mcx_keys(MCX_PRODUCTS)
        print()
        for db_sym in sorted(MCX_PRODUCTS):
            cur = SYMBOLS_MAP[db_sym]["key"]
            picked = found.get(db_sym) or []
            new = picked[0]["key"] if picked else None
            if not new:
                print(f"   {db_sym:<14} unresolved — keeps {cur}")
            elif new == cur:
                print(f"   {db_sym:<14} unchanged  {cur}   ({picked[0]['tradingsymbol']}, "
                      f"expiry {picked[0]['expiry']})")
            else:
                print(f"   {db_sym:<14} STALE      {cur}  ->  {new}   "
                      f"({picked[0]['tradingsymbol']}, expiry {picked[0]['expiry']})")
        print("\nNothing written. Re-run with --roll to fetch using these keys.")
        return 0

    if "--roll" in sys.argv:
        print("Resolving current MCX contracts (--roll)...")
        for db_sym, picked in resolve_mcx_keys(MCX_PRODUCTS).items():
            if not picked:
                continue
            k = picked[0]["key"]
            if db_sym in SYMBOLS_MAP and SYMBOLS_MAP[db_sym]["key"] != k:
                print(f"   {db_sym}: contract rolled "
                      f"{SYMBOLS_MAP[db_sym]['key']} -> {k}")
                SYMBOLS_MAP[db_sym]["key"] = k

    failed = []

    # ---- PER-CONTRACT products: one series per contract, then derive the roll ----
    #
    # The instrument key is not a durable identity. MCX_FO|466583 returned gold until
    # 2026-07-01 13:40 and option premium from 13:41, with nobody touching the config,
    # and five weeks of it landed under the name GOLD. A contract expiry IS durable —
    # it is a property of the contract, not of the vendor's database — so that is what
    # the symbol is keyed on now.
    #
    # Near AND next are fetched. The next contract is not needed today; it is needed
    # so that when the roll happens there is an overlap to measure the ratio on.
    # Without it continuous.py has to join raw and the step shows up as a real move.
    if PER_CONTRACT:
        print("\nResolving contracts for per-contract storage...")
        # EVERY live contract, not the nearest two.
        #
        # Upstox drops a contract from the instrument master the moment it expires —
        # 0 expired contracts are listed — so any history we do not capture while a
        # contract is alive is gone permanently. Fetching all of them means that when
        # October rolls to December we already hold December's liquid history instead
        # of starting it from that day.
        #
        # Daily for all of them; one-minute only for the nearest two. A far-dated
        # contract's minutes are untraded marks, and 26,000 bars per contract per
        # sync is real time for data the liquid-window rule would discard anyway.
        resolved = resolve_mcx_keys({k: v for k, v in MCX_PRODUCTS.items()
                                     if k in PER_CONTRACT}, n=99)

        # RECORD EVERY TOKEN, EVERY RUN, BEFORE FETCHING ANYTHING.
        #
        # The numeric token is the perishable part. Upstox delists a contract at
        # expiry and the Expired Instruments API cannot list MCX expiries — there is
        # no permanent underlying key for a commodity — so a token not written down
        # while the contract lived is unrecoverable at any price. That is precisely
        # why history before 2026-07-28 is gone: nothing recorded it.
        #
        # This ran as a standalone script for exactly one day, which is to say it
        # would have been forgotten. It belongs in the path that already knows the
        # answer, on every sync, before the work that might fail.
        try:
            sys.path.insert(0, os.path.dirname(HERE))
            import contract_registry as _reg
            _doc = _reg.load()
            _new = 0
            for _p, _cons in resolved.items():
                for _c in _cons:
                    if _reg.record(_doc, _p, _c["expiry"], _c["key"].split("|")[-1],
                                   extra={"tradingsymbol": _c.get("tradingsymbol"),
                                          "source": "sync_commodities"}):
                        _new += 1
            _reg.save(_doc)
            print(f"   contract registry: {len(_doc['contracts'])} known"
                  f"{f', {_new} new this run' if _new else ''}")
        except Exception as _e:                              # noqa: BLE001
            # Never fail the sync over bookkeeping — but say so loudly, because a
            # silent failure here is invisible until the day it costs history.
            print(f"   !! CONTRACT REGISTRY NOT UPDATED: {type(_e).__name__}: {_e}")
            print("      tokens seen in this run were not recorded — fix before the "
                  "next expiry or that contract becomes unrecoverable.")
        for db_sym, contracts in sorted(resolved.items()):
            for i, con_ in enumerate(contracts):
                sym = contract_symbol(db_sym, con_["expiry"])
                try:
                    sync_symbol(sym, {"key": con_["key"],
                                      "exchange": SYMBOLS_MAP[db_sym]["exchange"]},
                                DB_PATH, floor_symbol=db_sym,
                                timeframes=("1m", "1d") if i < 2 else ("1d",),
                                full="--full" in sys.argv)
                except Exception as e:
                    failed.append(sym)
                    print(f"   !! {sym} failed: {type(e).__name__}: {e}")
        # The rolling name is now GENERATED, never fetched into directly.
        #
        # --no-continuous exists for the FIRST run. Rebuilding overwrites legacy bars
        # under the bare product name with generated, ratio-adjusted ones wherever the
        # contract series reaches back that far. That is the intended end state, but
        # it is not something to discover afterwards — fetch the contracts, inspect
        # `continuous.py --product GOLD`, then let it write.
        if "--no-continuous" in sys.argv:
            print("\n--no-continuous: contracts stored; rolling series NOT rebuilt.")
            print("   inspect with: python data_agent/fetching/continuous.py --product GOLD")
            return 1 if failed else 0
        print("\nRebuilding continuous series from contracts...")
        for db_sym in sorted(resolved):
            for tf in ("1d", "1m"):
                try:
                    write_continuous(DB_PATH, db_sym, tf, dry=False)
                except Exception as e:
                    failed.append(f"{db_sym}:continuous:{tf}")
                    print(f"   !! {db_sym} continuous {tf}: {type(e).__name__}: {e}")

    # ---- everything else keeps its single-series name ----
    for symbol, config in SYMBOLS_MAP.items():
        if symbol in PER_CONTRACT:
            continue
        try:
            sync_symbol(symbol, config, DB_PATH, full="--full" in sys.argv)
        except Exception as e:
            failed.append(symbol)
            print(f"   !! {symbol} failed: {type(e).__name__}: {e}")

    print()
    if failed:
        # Non-zero exit so sync_all reports this as a failed step. The old version
        # printed "[SUCCESS] All commodities ... synced successfully!" unconditionally.
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("All commodities, USDINR and GIFTNIFTY synced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
