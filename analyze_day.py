#!/usr/bin/env python3
"""
analyze_day.py — one-day "shock" post-mortem for the NIFTY tape.
================================================================
Answers: on a given session, WHICH sector drove the index, was the move
concentrated (a sector shock) or broad (everything moved), and what did the
cross-asset complex (crude, USDINR, gold) do?

Runs against the SAME database the framework uses (Google-Drive live copy on
your machine, repo-local option_chains.db in a sandbox). Override with
    NIFTY_DB=/path/to/option_chains.db  python analyze_day.py 2026-07-08

All moves are DAY-OVER-DAY (vs the previous session's close), so the overnight
gap is included — the same convention as the Index Move Attribution panel.
Index-points contribution = stock%move x free-float index weight x prev index level.
"""
from __future__ import annotations
import os, sys, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_framework.config import constituents as K
from strategy_framework.config.settings import resolve_db_path

ENERGY_SECTORS = {"Oil & Gas", "Power"}          # the "energy complex"
CROSS = ["CRUDEOIL", "USDINR", "GOLD", "SILVER", "GIFTNIFTY"]
OPEN_UTC = "03:45:00"                              # 09:15 IST session open


def _prev_date(c, date):
    r = c.execute("SELECT MAX(SUBSTR(ts,1,10)) FROM price_bars WHERE timeframe='1m' "
                  "AND SUBSTR(ts,1,10) < ?", (date,)).fetchone()
    return r[0] if r else None


def _close(c, sym, date, after_open=False):
    q = ("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' "
         "AND SUBSTR(ts,1,10)=? " + ("AND SUBSTR(ts,12,8) >= ? " if after_open else "") +
         "ORDER BY ts DESC LIMIT 1")
    args = (sym, date, OPEN_UTC) if after_open else (sym, date)
    r = c.execute(q, args).fetchone()
    return r[0] if r else None


def _open(c, sym, date):
    r = c.execute("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' "
                  "AND SUBSTR(ts,1,10)=? AND SUBSTR(ts,12,8) >= ? ORDER BY ts ASC LIMIT 1",
                  (sym, date, OPEN_UTC)).fetchone()
    return r[0] if r else None


def analyze(date, db=None):
    db = db or resolve_db_path()
    c = sqlite3.connect(db)
    pd = _prev_date(c, date)
    if not pd:
        print(f"No prior session before {date} in {db}"); return
    n_prev = _close(c, "NIFTY", pd)
    n_open = _open(c, "NIFTY", date)
    n_close = _close(c, "NIFTY", date, after_open=True)
    if not (n_prev and n_close):
        print(f"No NIFTY 1m data for {date} in {db}"); return
    lvl = n_prev                                   # base-period level for pts
    day_pts = n_close - n_prev
    day_pct = (n_close / n_prev - 1) * 100
    gap_pts = (n_open - n_prev) if n_open else 0.0
    intra_pts = (n_close - n_open) if n_open else day_pts

    print("=" * 74)
    print(f"  NIFTY SHOCK ANALYSIS — {date}   (vs prev close {pd})")
    print("=" * 74)
    print(f"  Prev close {n_prev:,.1f}  ->  open {n_open:,.1f}  ->  close {n_close:,.1f}")
    print(f"  Whole day: {day_pts:+.1f} pts ({day_pct:+.2f}%)   "
          f"=  gap {gap_pts:+.1f}  +  intraday {intra_pts:+.1f}")

    # ---- per-stock day-over-day contribution -----------------------------
    rows = []
    for sym in K.symbols():
        if sym == "NIFTY":
            continue
        pc = _close(c, sym, pd)
        cl = _close(c, sym, date, after_open=True)
        if not (pc and cl):
            continue
        ret = (cl / pc - 1) * 100
        w = K.weight_of(sym)
        pts = ret / 100 * w / 100 * lvl
        rows.append({"sym": sym, "sec": K.sector_of(sym), "ret": ret,
                     "w": w, "pts": pts, "close": cl})
    if not rows:
        print("  (no constituent data for this date)"); return

    tot_pts = sum(r["pts"] for r in rows)

    # ---- sector roll-up ---------------------------------------------------
    secs = {}
    for r in rows:
        s = secs.setdefault(r["sec"], {"pts": 0.0, "rets": [], "w": 0.0, "n": 0})
        s["pts"] += r["pts"]; s["rets"].append(r["ret"]); s["w"] += r["w"]; s["n"] += 1
    print("\n  SECTOR CONTRIBUTION TO THE INDEX MOVE  (sorted by impact)")
    print(f"  {'sector':<22}{'pts':>9}{'% of move':>11}{'avg Δ%':>9}{'wt%':>7}{'n':>4}")
    print("  " + "-" * 60)
    for sec, s in sorted(secs.items(), key=lambda kv: -abs(kv[1]["pts"])):
        avg = sum(s["rets"]) / len(s["rets"])
        share = (s["pts"] / tot_pts * 100) if tot_pts else 0
        tag = "  <-- ENERGY" if sec in ENERGY_SECTORS else ""
        print(f"  {sec:<22}{s['pts']:>+9.1f}{share:>10.0f}%{avg:>+9.2f}{s['w']:>7.1f}{s['n']:>4}{tag}")

    # ---- energy complex vs rest ------------------------------------------
    energy = [r for r in rows if r["sec"] in ENERGY_SECTORS]
    rest = [r for r in rows if r["sec"] not in ENERGY_SECTORS]
    e_avg = sum(r["ret"] for r in energy) / len(energy) if energy else 0
    r_avg = sum(r["ret"] for r in rest) / len(rest) if rest else 0
    e_pts = sum(r["pts"] for r in energy)
    print("\n  ENERGY COMPLEX (Oil & Gas + Power)")
    for r in sorted(energy, key=lambda x: x["ret"]):
        print(f"    {r['sym']:<12} {r['ret']:>+7.2f}%   {r['pts']:>+6.1f} pts   (wt {r['w']:.1f}%, close {r['close']:,.1f})")
    print(f"    energy avg move {e_avg:+.2f}%  |  contributed {e_pts:+.1f} pts "
          f"({(e_pts/tot_pts*100 if tot_pts else 0):.0f}% of the total)")

    # ---- top movers -------------------------------------------------------
    print("\n  TOP MOVERS BY INDEX POINTS")
    for r in sorted(rows, key=lambda x: -abs(x["pts"]))[:8]:
        print(f"    {r['sym']:<12} {r['ret']:>+7.2f}%   {r['pts']:>+6.1f} pts   ({r['sec']})")

    # ---- breadth: was it broad or concentrated? --------------------------
    up = [r for r in rows if r["ret"] > 0]
    dn = [r for r in rows if r["ret"] < 0]
    big = [r for r in rows if abs(r["ret"]) >= 1.0]
    allavg = sum(r["ret"] for r in rows) / len(rows)
    print("\n  BREADTH  (was the move broad or just energy?)")
    print(f"    advancers {len(up)} / decliners {len(dn)} of {len(rows)}")
    print(f"    names moving >=1%: {len(big)} of {len(rows)}")
    print(f"    avg move — ALL {allavg:+.2f}%  |  ENERGY {e_avg:+.2f}%  |  NON-ENERGY {r_avg:+.2f}%")
    verdict = ("ENERGY-LED but BROAD" if abs(r_avg) >= 0.5 and abs(e_avg) > abs(r_avg)
               else "CONCENTRATED in energy" if abs(e_avg) > 2 * abs(r_avg)
               else "BROAD-BASED")
    print(f"    -> read: {verdict}  (energy moved {abs(e_avg)/max(abs(r_avg),1e-9):.1f}x the rest, "
          f"but non-energy still averaged {r_avg:+.2f}%)")

    # ---- cross-asset drivers ---------------------------------------------
    print("\n  CROSS-ASSET DRIVERS  (day-over-day)")
    any_x = False
    for sym in CROSS:
        pc = _close(c, sym, pd); cl = _close(c, sym, date)
        if pc and cl:
            any_x = True
            print(f"    {sym:<12} {(cl/pc-1)*100:>+7.2f}%   ({pc:,.2f} -> {cl:,.2f})")
    if not any_x:
        print("    (no cross-asset bars for this date)")
    print("=" * 74)
    c.close()


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
    analyze(d, os.environ.get("NIFTY_DB"))
