"""
test_offline.py — smoke test with a mock snapshot (no network / no yfinance needed).

Injects a synthetic market snapshot, then exercises every core function and the MIO
assembler + schema validation. This proves the wiring to market_scan.py works and the
MIO conforms, without hitting live data.

Run:  NEWSINDEX_HOME=/path/to/newsindex python test_offline.py
"""
from __future__ import annotations
import sys
import core
import mio_builder

MOCK = {
    "live": False,
    "quotes_idx": [
        {"name": "Nifty 50", "symbol": "^NSEI", "last": 24500, "pct_change": -0.6},
        {"name": "Bank Nifty", "symbol": "^NSEBANK", "last": 52000, "pct_change": -0.8},
        {"name": "India VIX", "symbol": "^INDIAVIX", "last": 14.2, "pct_change": 6.0},
        {"name": "Nifty IT", "symbol": "^CNXIT", "last": 38000, "pct_change": -0.5},
    ],
    "quotes_macro": [
        {"name": "Brent Crude", "symbol": "BZ=F", "last": 92.3, "pct_change": 3.1},
        {"name": "US 10Y Yield", "symbol": "^TNX", "last": 4.3, "pct_change": 1.2},
        {"name": "Dollar Index", "symbol": "DX=F", "last": 105, "pct_change": 0.4},
        {"name": "USD/INR", "symbol": "INR=X", "last": 83.5, "pct_change": 0.3},
        {"name": "Phila Semi (SOX)", "symbol": "^SOX", "last": 5200, "pct_change": 2.0},
        {"name": "Kospi", "symbol": "^KS11", "last": 2700, "pct_change": 1.1},
        {"name": "Gold", "symbol": "GC=F", "last": 2400, "pct_change": 0.5},
        {"name": "Copper", "symbol": "HG=F", "last": 4.5, "pct_change": -0.2},
    ],
    "quotes_stk": [
        {"name": "Reliance", "symbol": "RELIANCE.NS", "last": 2900, "pct_change": 0.4},
        {"name": "HDFC Bank", "symbol": "HDFCBANK.NS", "last": 1650, "pct_change": -0.9},
        {"name": "ONGC", "symbol": "ONGC.NS", "last": 270, "pct_change": -0.7},
    ],
    "it_quotes": [
        {"name": "TCS", "symbol": "TCS.NS", "last": 3900, "pct_change": -0.6},
        {"name": "Infosys", "symbol": "INFY.NS", "last": 1500, "pct_change": -0.5},
    ],
    "sector_quotes": [
        {"name": "Nifty Auto", "symbol": "^CNXAUTO", "last": 22000, "pct_change": -0.4},
    ],
    "theme_quotes": [],
    "univ_quotes": [],
    "flows": [{"category": "FII", "net": -3200.0}, {"category": "DII", "net": 2100.0}],
    "news": [
        {"title": "Iran tensions rise near Strait of Hormuz", "macro": True,
         "tags": "war iran hormuz oil", "source": "Reuters", "link": "http://x"},
        {"title": "US CPI cools more than expected", "macro": True,
         "tags": "us cpi", "source": "Bloomberg", "link": "http://y"},
        {"title": "HDFC Bank Q1 profit beats estimates", "macro": False,
         "tags": "results", "source": "ET", "link": "http://z"},
    ],
    "earnings": [],
}


def main() -> int:
    core.load_snapshot(dict(MOCK))
    fails = []

    def check(name, fn):
        try:
            out = fn()
            print(f"  OK   {name}: {type(out).__name__}")
            return out
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            fails.append(name)
            return None

    print("snapshot:", core.snapshot_summary()["counts"])
    print("--- core functions ---")
    check("market_verdict", core.market_verdict)
    reg = check("detect_regime", core.detect_regime)
    check("causal_engine", core.causal_engine)
    dom = check("driver_dominance", core.driver_dominance)
    check("sector_intelligence", core.sector_intelligence)
    check("transmission_map", core.transmission_map)
    check("validate_relationships", core.validate_relationships)
    check("company_intelligence", core.company_intelligence)
    check("market_themes", core.market_themes)
    check("standout_movers", core.standout_movers)
    check("shock_type", core.shock_type)

    print("--- MIO assembly + validation ---")
    mio = check("build_mio", mio_builder.build_mio)
    if mio:
        # dominance sums to ~1.0
        ssum = round(sum(mio["driver_dominance"]["vector"].values()), 3)
        print(f"  dominance vector sums to {ssum} (expect ~1.0)")
        if not (0.98 <= ssum <= 1.02):
            fails.append("dominance_sum")
        v = mio_builder.validate_mio(mio)
        print(f"  schema valid: {v['valid']}")
        for e in v["errors"]:
            print("    -", e)
        if v["valid"] is False:
            fails.append("mio_schema")
        print(f"  regime: {reg['ai_regime']} | dominant driver: {dom['dominant_driver']} "
              f"({dom['dominant_driver_score']})")
        print(f"  event: {mio['event']['canonical_label']} [{mio['event']['class']}] "
              f"| sectors: {len(mio['affected_sectors'])} | companies: {len(mio['affected_companies'])}")

    print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
