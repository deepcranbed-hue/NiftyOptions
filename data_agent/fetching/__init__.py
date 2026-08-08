"""
data_agent.fetching — WHERE the data comes from.

Modules:
  universe.py      — expiry-aware instrument selection (BUILT + TESTED):
                       futures = near + next; options = current expiry, plus the
                       next expiry once within 2 days of current; expired dropped.
  fo_bars.py       — typed futures/options bar store (BUILT + TESTED): fo_price_bars
                       with expiry/strike/right columns, futures sentinel-keyed,
                       idempotent save + fast strike-range queries. Cash stays in
                       price_bars (bar_store), untouched.
  broker.py        — ONE broker per run over Breeze + Kite (BUILT + TESTED):
                     common Broker interface, get_broker(kind), identical
                     normalized Bar (IST->UTC, OI). Live fetch needs the vendor
                     SDK + token; pure logic (factory/normalizers/resolver) tested.
  orchestrator.py  — BUILT + TESTED: build_plan() (universe rules -> cash+FUT+OPT
                     targets) and run(broker, plan, db) — watermark-incremental,
                     writes cash->price_bars & F&O->fo_price_bars, per-target error
                     isolation + retry, then data_health. Broker is injected.

Reuses existing plumbing: bar_store, active_schedulers, resolve_exchange,
/api/sync-* endpoints. Nothing here scrapes — data only via authenticated broker APIs.
"""
from .universe import (  # noqa: F401
    active_future_expiries, active_option_expiries, build_universe,
    is_expired, ROLL_AHEAD_DAYS,
)
from .fo_bars import (  # noqa: F401
    save_fo_bars, get_option_chain, get_strike_range, get_atm_chain,
    get_future, near_next, stored_expiries, contract_symbol, ExpiryResolver,
    FUT, OPT,
)
from .broker import (  # noqa: F401
    get_broker, Broker, BreezeBroker, KiteBroker,
    normalize_breeze, normalize_kite,
)
from .orchestrator import build_plan, run, run_and_check  # noqa: F401
