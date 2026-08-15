"""
strategy_framework/run_oi_check.py
==================================
Coverage check for the NIFTY_FUT_1 open-interest feed — how much history the
OI-regime study actually has to work with. Run on the machine with the Drive DB.

    python -m strategy_framework.run_oi_check
"""
from __future__ import annotations
import sqlite3


def main() -> None:
    from strategy_framework.api import _CFG
    p = _CFG.db_path
    con = sqlite3.connect(p)
    cols = [r[1] for r in con.execute("PRAGMA table_info(price_bars)")]
    print("db:", p)
    if "open_interest" not in cols:
        print("open_interest column: ABSENT — the OI feed is not landing in price_bars yet.")
        con.close()
        return
    rows_total = con.execute("SELECT COUNT(*) FROM price_bars WHERE symbol='NIFTY_FUT_1' "
                             "AND timeframe='1m'").fetchone()[0]
    rows_oi = con.execute("SELECT COUNT(*) FROM price_bars WHERE symbol='NIFTY_FUT_1' "
                          "AND timeframe='1m' AND open_interest IS NOT NULL").fetchone()[0]
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT substr(ts,1,10) FROM price_bars WHERE symbol='NIFTY_FUT_1' "
        "AND timeframe='1m' AND open_interest IS NOT NULL ORDER BY 1")]
    con.close()
    print(f"NIFTY_FUT_1 1m bars: {rows_total}   with OI populated: {rows_oi}"
          f"  ({100*rows_oi/rows_total:.0f}%)" if rows_total else "no NIFTY_FUT_1 bars")
    if days:
        print(f"OI-covered sessions: {len(days)}   {days[0]} .. {days[-1]}")
        print("→ the regime_by='oi' study (conviction/hollow/coiled/churn) is backed by these "
              f"{len(days)} sessions. Rare regimes (conviction/coiled) still need many to fill.")
    else:
        print("No sessions carry OI yet — capture must populate open_interest before the study "
              "is meaningful.")


if __name__ == "__main__":
    main()
