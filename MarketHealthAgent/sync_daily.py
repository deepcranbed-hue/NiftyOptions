"""
MarketHealthAgent/sync_daily.py
===============================
Import DAILY (`timeframe='1d'`) constituent bars from a SOURCE SQLite DB (e.g. the
fuller copy synced from Google Drive) into the working DB's `price_bars`, so the
market-health Trend-Breadth layer can activate locally.

The market-health code already reads whatever daily symbols exist in `price_bars`;
this just moves the rows in. It is idempotent: for each imported symbol it clears
that symbol's existing 1d rows first, then re-inserts, so running twice is safe.

Usage (from the repo root):
    # copy the Drive DB somewhere local first, then:
    python -m MarketHealthAgent.sync_daily --source /path/to/drive_option_chains.db
    python -m MarketHealthAgent.sync_daily --source X.db --symbols RELIANCE,HDFCBANK
    python -m MarketHealthAgent.sync_daily --source X.db --dry-run

By default it imports every Nifty-50 constituent (from config.constituents) plus
NIFTY. Only the daily timeframe is touched; 1-minute data is never modified.
"""
from __future__ import annotations
import argparse
import sqlite3

from strategy_framework.config.settings import FrameworkConfig
from strategy_framework.config import constituents as K

DAILY_TF = "1d"
_COLS = ("exchange", "symbol", "timeframe", "ts", "open", "high", "low", "close", "volume")


def _has_daily_price_bars(con: sqlite3.Connection) -> bool:
    t = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='price_bars'").fetchone()
    if not t:
        return False
    cols = {r[1] for r in con.execute("PRAGMA table_info(price_bars)")}
    return {"symbol", "timeframe", "ts", "close"} <= cols


def sync(source_db: str, target_db: str, symbols: list[str] | None = None,
         dry_run: bool = False) -> dict:
    want = symbols or ([s for s in K.symbols()] + ["NIFTY"])
    want = sorted(set(want))
    src = sqlite3.connect(source_db)
    src.execute("PRAGMA query_only=1")
    if not _has_daily_price_bars(src):
        src.close()
        return {"error": f"{source_db} has no usable price_bars table"}

    tgt = sqlite3.connect(target_db)
    imported: dict[str, int] = {}
    skipped: list[str] = []
    try:
        for sym in want:
            rows = src.execute(
                "SELECT exchange, symbol, timeframe, ts, open, high, low, close, "
                "COALESCE(volume,0) FROM price_bars WHERE symbol=? AND timeframe=? ORDER BY ts",
                (sym, DAILY_TF)).fetchall()
            if not rows:
                skipped.append(sym)
                continue
            imported[sym] = len(rows)
            if dry_run:
                continue
            # idempotent: clear this symbol's 1d rows, then re-insert
            tgt.execute("DELETE FROM price_bars WHERE symbol=? AND timeframe=?", (sym, DAILY_TF))
            tgt.executemany(
                f"INSERT INTO price_bars ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                rows)
        if not dry_run:
            tgt.commit()
    finally:
        src.close()
        tgt.close()

    return {"source": source_db, "target": target_db, "dry_run": dry_run,
            "symbols_imported": len(imported), "rows_imported": sum(imported.values()),
            "per_symbol": imported,
            "not_in_source": skipped[:20] + (["…"] if len(skipped) > 20 else [])}


def main():
    cfg = FrameworkConfig()
    ap = argparse.ArgumentParser(description="Import daily constituent bars into the working DB")
    ap.add_argument("--source", required=True, help="path to the source SQLite DB (the Drive copy)")
    ap.add_argument("--target", default=cfg.db_path, help=f"working DB (default: {cfg.db_path})")
    ap.add_argument("--symbols", default=None, help="comma list (default: all Nifty-50 + NIFTY)")
    ap.add_argument("--dry-run", action="store_true", help="report what WOULD import, change nothing")
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    res = sync(args.source, args.target, symbols=syms, dry_run=args.dry_run)
    if "error" in res:
        print("ERROR:", res["error"]); return
    print(f"{'DRY-RUN — ' if res['dry_run'] else ''}imported {res['rows_imported']} daily bars "
          f"across {res['symbols_imported']} symbols")
    if res["not_in_source"]:
        print(f"  not present in source ({len(res['not_in_source'])}): {', '.join(res['not_in_source'])}")
    # show the resulting coverage so the user sees breadth activate
    try:
        from strategy_framework.market_health.trend import market_health
        r = market_health(args.target)
        print(f"\nmarket-health now: {r['score']}/100 ({r['band']}) · coverage {r['coverage_pct']:.0f}%")
        tb = r["layers"]["trend_breadth"]
        print("  trend_breadth:", "ACTIVE" if tb["data_ready"] else "still pending (need ≥10 members w/ 200 sessions)")
    except Exception as e:
        print("(coverage check skipped:", e, ")")


if __name__ == "__main__":
    main()
