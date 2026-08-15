"""
test_data_health.py
====================
Proves the data-health coverage checker against the real bar DB (offline, no broker):

  1. Report shape + per-symbol coverage computed.
  2. Full days (~375-381 bars) count as OK.
  3. Partial days flagged DEGRADED — the known 2026-06-29 NIFTY 3-bar day must show up.
  4. Expiry exclusion hook drops expired instruments.
  5. alert_message() emits a sidebar-ready payload with the right level.

Run:  python test_data_health.py
"""
from __future__ import annotations
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "option_chains.db")
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m

dh = _load(HERE + "/data_agent/quality/data_health.py", "data_health")
llm = _load(HERE + "/data_agent/agent/local_llm.py", "local_llm")

# ── 1. report ───────────────────────────────────────────────────────────────
hr("1. COVERAGE REPORT")
rep = dh.coverage_report(DB)
check("report has symbols + flagged + summary", all(k in rep for k in ("symbols", "flagged", "summary")))
check("found the known symbols", {"NIFTY", "HDFCBANK", "RELIANCE", "TCS"} <= set(rep["symbols"]))
print("  summary:", rep["summary"])

# ── 2. full days OK ─────────────────────────────────────────────────────────
hr("2. FULL DAYS = OK")
# TCS/RELIANCE/HDFCBANK have 376 bars/day on 07-01..07-03 -> should be OK latest
tcs = rep["symbols"]["TCS"]
check("TCS latest day is OK (376/375)", tcs["latest_status"] == "OK")
check("TCS coverage capped at 1.0", tcs["latest_coverage"] == 1.0)

# ── 3. partial day flagged ──────────────────────────────────────────────────
hr("3. PARTIAL DAY FLAGGED (2026-06-29 NIFTY = 3 bars)")
nifty_flags = [f for f in rep["flagged"] if f["symbol"] == "NIFTY"]
partial = [f for f in nifty_flags if f["date"] == "2026-06-29"]
check("2026-06-29 NIFTY is flagged", len(partial) == 1)
if partial:
    f = partial[0]
    print(f"  flagged: NIFTY {f['date']} bars={f['bars']} expected={f['expected']} cov={f['coverage']} status={f['status']}")
    check("flagged as DEGRADED with tiny coverage", f["status"] == "DEGRADED" and f["coverage"] < 0.1)

# ── 3b. frequency / spacing maintained ──────────────────────────────────────
hr("3b. FREQUENCY MAINTAINED (bar spacing, not just count)")
check("clean 1-min spacing -> not wrong_freq", dh.analyze_spacing([0, 1, 2, 3, 4], 1)["wrong_freq"] is False)
check("5-min data vs expected 1m -> WRONG_FREQ", dh.analyze_spacing([0, 5, 10, 15], 1)["wrong_freq"] is True)
check("5-min data vs expected 5m -> ok", dh.analyze_spacing([0, 5, 10, 15], 5)["wrong_freq"] is False)
gp = dh.analyze_spacing([0, 1, 2, 50], 1)
check("mid-session hole flagged as gap", gp["gap_count"] >= 1 and gp["max_gap"] == 48)
check("real TCS full day is NOT falsely gap-flagged (pre-open excluded)",
      rep["symbols"]["TCS"]["latest_status"] == "OK")

# ── 3c. per-symbol USER-DEFINED frequency ───────────────────────────────────
hr("3c. USER-DEFINED PER-SYMBOL FREQUENCY")
rep_freq = dh.coverage_report(DB, freq_config={"TCS": 5})
tcs_flag = [f for f in rep_freq["flagged"] if f["symbol"] == "TCS"]
check("TCS (1-min data) flagged WRONG_FREQ when user defines 5m",
      any(f["status"] == "WRONG_FREQ" and f["freq_source"] == "user" for f in tcs_flag))
check("RELIANCE still OK at default 1m", rep_freq["symbols"]["RELIANCE"]["latest_status"] == "OK")
if tcs_flag:
    print("  TCS@5m ->", tcs_flag[0]["reason"])

# ── 4. expiry exclusion hook ────────────────────────────────────────────────
hr("4. EXPIRY EXCLUSION")
rep_excl = dh.coverage_report(DB, is_expired=lambda sym, d: sym == "NIFTY")
check("NIFTY dropped when marked expired", "NIFTY" not in rep_excl["symbols"])
check("other symbols still present", "TCS" in rep_excl["symbols"])

# ── 5. alert payload ────────────────────────────────────────────────────────
hr("5. ALERT MESSAGE (sidebar badge)")
al = dh.alert_message(rep, when="Morning")
check("alert has level + headline + detail", all(k in al for k in ("level", "headline", "detail")))
check("level reflects flagged symbols", al["level"] in ("warn", "alert"))
print(f"  level={al['level']}  headline={al['headline']}")
print(f"  detail={al['detail'][:140]}")

# also: a clean subset (only full days) should report OK
hr("5b. CLEAN-SUBSET => OK ALERT")
rep_clean = dh.coverage_report(DB, only_dates={"2026-07-03"})
al_clean = dh.alert_message(rep_clean, when="Evening")
check("only-full-day scan is OK", al_clean["level"] == "ok")
print(f"  {al_clean['headline']} — {al_clean['detail']}")

# ── 6. local-LLM intent parser (keyword fallback path) ──────────────────────
hr("6. LOCAL-LLM INTENT PARSER (data_agent/agent)")
i1 = llm.parse_intent("start downloading with my breeze token abc123XYZ", prefer_llm=False)
check("'start ... breeze token' -> start+breeze+token", i1["action"] == "start" and i1["broker"] == "breeze" and i1["token"] == "abc123XYZ")
i2 = llm.parse_intent("sync TCS and its options", prefer_llm=False)
check("'sync TCS ... options' -> sync+TCS+include_options", i2["action"] == "sync" and "TCS" in i2["symbols"] and i2["include_options"])
i3 = llm.parse_intent("is the data up to the mark?", prefer_llm=False)
check("'up to the mark' -> health", i3["action"] == "health")
i4 = llm.parse_intent("backfill last 5 days for NIFTY", prefer_llm=False)
check("'backfill 5 days NIFTY' -> backfill+days=5", i4["action"] == "backfill" and i4["days"] == 5)
i5 = llm.parse_intent("stop the data agent", prefer_llm=False)
check("'stop' -> stop", i5["action"] == "stop")
print("  sample:", i1)

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print(f"  symbols scanned: {rep['n_symbols']} | flagged symbol-days: {len(rep['flagged'])}")
print(f"  reuses price_bars (bar_store) — no broker/network needed for the health read")
sys.exit(0 if n_pass == n else 1)
