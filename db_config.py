"""
db_config.py — THE single source of truth for which databases this project uses.

================================================================================
MANDATORY RULE
================================================================================
No module may hardcode a database path or DSN, and no module may call
`sqlite3.connect()` / `psycopg.connect()` on a literal. Import from here:

    # market data — chains, price bars, captures  (SQLite)
    from db_config import DB_PATH, connect, resolve_db_path
    from db_config import resolve_writable_db_path      # downloads / backfills

    # macro + fundamentals                        (PostgreSQL)
    from db_config import PG_DSN, connect_pg, resolve_pg_dsn

If you find a path or DSN literal anywhere else, it is a bug — point it here.

================================================================================
TWO STORES, BY DOMAIN — this split is deliberate
================================================================================
  SQLite  (Google Drive)   market/time-series: `captures`, `chain_rows`,
                           `price_bars`. The download pipeline writes here.
  Postgres (localhost)     macro + fundamentals. `data_agent/macro/` and
                           `data_agent/fundamentals/` read/write here instead of
                           SQLite; many of those scripts touch BOTH stores in one
                           run (read chains from SQLite, write fundamentals to PG).

Do not "consolidate" one into the other without a decision — see
POSTGRES_MIGRATION_PLAN.md, whose scope is exactly this split.

================================================================================
SQLITE RESOLUTION ORDER
================================================================================
  1. $NIFTY_DB              explicit override — tests, CI, one-off runs.
  2. $OPTION_CHAINS_DB      the name 35 existing call sites already use.
  3. Google Drive copy      THE PRIMARY. Every download writes here.
  4. repo-local             a daily copy of (3), for sandboxes and tests.
                            NEVER written to by the download pipeline.

The Drive copy wins whenever present, so a developer machine always reads
production. The repo-local file is a read-only convenience, not a second master —
if the two diverge, Drive is correct by definition.

================================================================================
A NOTE ON SQLITE PRAGMAS (do not "optimise" this away)
================================================================================
The SQLite file lives on a Google-Drive-synced directory, and the sync process
takes file locks during active writes. Per POSTGRES_MIGRATION_PLAN.md §1.1 the
project therefore runs in **rollback-journal mode with a long busy-timeout** —
NOT WAL. WAL keeps `-wal`/`-shm` sidecar files that a file-sync client will
happily upload, reorder, or lock independently of the main file, which is how a
synced SQLite database gets corrupted. `connect()` sets the busy timeout and
deliberately leaves journal_mode at the default.
"""
from __future__ import annotations

import os
import sqlite3

# ---- SQLite (market data) -----------------------------------------------------
_DRIVE_DB = ("/Users/deepak/Library/CloudStorage/"
             "GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOCAL_DB = os.path.join(_REPO_ROOT, "option_chains.db")

_ENV_VARS = ("NIFTY_DB", "OPTION_CHAINS_DB")

# Google Drive holds locks during sync; 30s matches POSTGRES_MIGRATION_PLAN.md §1.1.
BUSY_TIMEOUT_MS = 30_000

# ---- PostgreSQL (macro + fundamentals) ----------------------------------------
# Two different defaults were in circulation across data_agent/ — this is the one.
_PG_DSN_DEFAULT = "postgresql://localhost/niftyoptions"
_PG_ENV_VARS = ("DATABASE_URL", "NIFTY_PG_DSN")


# ==============================================================================
# SQLite
# ==============================================================================
def resolve_db_path(*, require: bool = False) -> str:
    """First existing SQLite candidate, in documented priority order (readers)."""
    for var in _ENV_VARS:
        p = os.environ.get(var)
        if p and os.path.exists(p):
            return p
    if os.path.exists(_DRIVE_DB):
        return _DRIVE_DB
    if os.path.exists(_LOCAL_DB):
        return _LOCAL_DB
    if require:
        raise FileNotFoundError(
            "No option_chains.db found. Looked at: "
            + ", ".join([f"${v}" for v in _ENV_VARS] + [_DRIVE_DB, _LOCAL_DB]))
    return _DRIVE_DB          # the primary — so the error names the real target


def resolve_writable_db_path() -> str:
    """The SQLite path a WRITER may open. Never the repo-local copy.

    Downloads and backfills must land in the primary. If the Drive mount is
    missing — signed out, not yet synced, running in a sandbox — a writer must
    FAIL rather than quietly write into the local read-only copy and manufacture
    the divergence this module exists to prevent.
    """
    for var in _ENV_VARS:
        p = os.environ.get(var)
        if p and os.path.exists(p):
            return p
    if os.path.exists(_DRIVE_DB):
        return _DRIVE_DB
    raise FileNotFoundError(
        "Primary database not reachable — refusing to write to the local copy.\n"
        f"  expected: {_DRIVE_DB}\n"
        f"  (a repo-local copy at {_LOCAL_DB} exists but is READ-ONLY by policy)\n"
        "  set $NIFTY_DB to override deliberately.")


def connect(db: str | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    """SQLite connection with `sqlite3.Row` and the Drive-safe busy timeout set.

    `db` overrides resolution entirely — pass it only from tests, or from a tool
    that genuinely targets a different file.
    """
    path = db or resolve_db_path()
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # journal_mode is deliberately NOT set — see the module docstring. Do not
    # switch this to WAL while the file lives on a synced directory.
    return con


# ==============================================================================
# PostgreSQL
# ==============================================================================
def resolve_pg_dsn() -> str:
    """DSN for the macro + fundamentals store.

    $DATABASE_URL is the name the existing data_agent scripts already read, so it
    stays authoritative. $NIFTY_PG_DSN is accepted as an unambiguous alias.
    """
    for var in _PG_ENV_VARS:
        v = os.environ.get(var)
        if v:
            return v
    return _PG_DSN_DEFAULT


def resolve_pg_admin_dsn() -> str:
    """DSN for the scripts that run DDL (`CREATE SCHEMA`, `CREATE TABLE`).

    `CREATE SCHEMA` needs CREATE privilege on the database, which the `postgres`
    superuser has unconditionally and a plain login role may not. Four scripts
    were already connecting as `postgres` for exactly this reason
    (`macro/us10y.py`, `macro/ingest_india_rates.py`, `macro/download_us_stocks.py`,
    `fundamentals/download_fundamentals.py`) — that split is preserved here rather
    than flattened, so single-sourcing the DSN cannot silently remove a privilege
    those scripts depend on.

    THE BETTER FIX, once the cluster is up: grant the ordinary role properly
        GRANT CREATE ON DATABASE niftyoptions TO <role>;
        ALTER SCHEMA macro OWNER TO <role>;   -- and fundamentals
    then delete this function and let everything use resolve_pg_dsn().
    Objects created by `postgres` are OWNED by postgres, so a writer connecting as
    the ordinary role fails on them until grants exist — that failure looks like a
    flaky script, not a permissions problem.
    """
    for var in ("DATABASE_URL_ADMIN", "NIFTY_PG_ADMIN_DSN"):
        v = os.environ.get(var)
        if v:
            return v
    return os.environ.get("DATABASE_URL") or "postgresql://postgres@localhost:5432/niftyoptions"


def connect_pg(dsn: str | None = None, *, admin: bool = False):
    """psycopg 3 connection to the macro/fundamentals store.

    Raises a clear error when psycopg is absent rather than an opaque ImportError:
    it is NOT in backend/requirements.txt because the backend and
    strategy_framework are SQLite-only — Postgres is a data_agent dependency.
    """
    try:
        import psycopg
    except ImportError as e:
        raise ImportError(
            "psycopg 3 is required for the macro/fundamentals store "
            "(`pip install 'psycopg[binary]'`). It is intentionally absent from "
            "backend/requirements.txt — only data_agent/ needs it.") from e
    return psycopg.connect(dsn or (resolve_pg_admin_dsn() if admin else resolve_pg_dsn()))


# ==============================================================================
def describe() -> dict:
    """Which candidates exist and which won, for both stores. For health cards and
    debugging — 'checked-and-absent != silently zero' applies to paths too."""
    envs = {v: os.environ.get(v) for v in _ENV_VARS}
    pg_envs = {v: os.environ.get(v) for v in _PG_ENV_VARS}
    return {
        "sqlite": {
            "resolved": resolve_db_path(),
            "source": ("env" if any(p and os.path.exists(p) for p in envs.values())
                       else "drive" if os.path.exists(_DRIVE_DB)
                       else "local" if os.path.exists(_LOCAL_DB)
                       else "missing"),
            "env": {k: {"set": bool(v), "exists": bool(v and os.path.exists(v))}
                    for k, v in envs.items()},
            "drive": {"path": _DRIVE_DB, "exists": os.path.exists(_DRIVE_DB)},
            "local": {"path": _LOCAL_DB, "exists": os.path.exists(_LOCAL_DB)},
            "busy_timeout_ms": BUSY_TIMEOUT_MS,
        },
        "postgres": {
            "resolved": resolve_pg_dsn(),
            "source": "env" if any(pg_envs.values()) else "default",
            "env": {k: bool(v) for k, v in pg_envs.items()},
            "admin_dsn": resolve_pg_admin_dsn(),
            "psycopg_installed": _has_psycopg(),
            "domain": "macro + fundamentals",
        },
    }


def _has_psycopg() -> bool:
    try:
        import psycopg  # noqa: F401
        return True
    except ImportError:
        return False


DB_PATH = resolve_db_path()      # SQLite, resolved once at import
PG_DSN = resolve_pg_dsn()        # Postgres DSN, resolved once at import


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=2))
