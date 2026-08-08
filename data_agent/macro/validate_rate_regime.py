#!/usr/bin/env python3
"""
validate_rate_regime.py — Layer-1 (Macro Regime) → Layer-2/3 validation.

Does the India 10-Year rate regime actually carry information about bank returns
and about the shock-recovery bounce — or is "rates down ⇒ banks up" just folklore?
This script FITS and VALIDATES that relationship out-of-sample. It never asserts it.

SOURCE
  The rate signal is the Upstox India 10Y **price index** (IN10Y_INDEX, ~875).
  It is a CLEAN-PRICE index, so it is INVERSE to rates:
      price index UP   ⇒ yields/rates FALLING
      price index DOWN ⇒ yields/rates RISING
  Every feature below is defined in RATE space (we negate the price move), so a
  positive coefficient always reads as "when rates rise, X does Y".

DISCIPLINE (per SECTOR_INTELLIGENCE_FRAMEWORK.md)
  • POINT-IN-TIME — every feature at day t uses only data up to and including t
    (trailing windows). Targets are strictly forward (t → t+h). No look-ahead.
  • FIT, DON'T ASSERT — OLS with t-stats AND a chronological out-of-sample split.
    The in-sample number is never the verdict; the OOS number is. If the sign
    flips or the magnitude collapses on the held-out tail, we call it spurious.
  • HONEST OUTPUT — the script prints whichever way the data falls, including
    "no usable relationship." That is a valid — and likely — result.

WHAT IT TESTS
  A. BANKNIFTY forward return (1d, 5d) regressed on rate-regime features.
  B. Non-parametric: mean forward return by rate-regime tercile.
  C. Shock-recovery conditioning: does the next-day bounce after a NIFTY shock
     differ between a RISING-rate and a FALLING-rate regime? (Layer 1 gating Layer 3.)

USAGE
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python validate_rate_regime.py
"""
from __future__ import annotations
import os, sqlite3, sys, math

try:
    import numpy as np
    import pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")

try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")

# candidate symbol names (auto-detected against what's actually in the DB)
CAND_10Y   = ["IN10Y_INDEX", "IN10Y", "INDIA10Y", "IN_10Y", "IN10YR"]
CAND_BANK  = ["BANKNIFTY", "NIFTYBANK", "^NSEBANK", "NSEBANK", "BANKNIFTY_INDEX"]
CAND_NIFTY = ["NIFTY", "NIFTY50", "^NSEI", "NSEI", "NIFTY_INDEX"]
CAND_VIX   = ["INDIAVIX", "INDIA_VIX", "^INDIAVIX", "VIX"]

SHOCK_THRESH = -1.5   # NIFTY daily % that defines a macro shock (matches shock_recovery_v2)
HIVIX        = 20.0
OOS_FRAC     = 0.70   # train on first 70% of history, validate on the last 30%


# ---------- data access ------------------------------------------------------
def list_symbols(cur) -> set:
    try:
        return {r[0] for r in cur.execute(
            "SELECT DISTINCT symbol FROM price_bars WHERE timeframe='1d'").fetchall()}
    except Exception:
        return {r[0] for r in cur.execute("SELECT DISTINCT symbol FROM price_bars").fetchall()}


def pick(cands, have, label):
    for c in cands:
        if c in have:
            return c
    # case-insensitive fallback
    low = {s.lower(): s for s in have}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    print(f"  ⚠️  none of {label} found in price_bars. Tried {cands}.")
    return None


def load_series(cur, sym) -> pd.Series:
    """Daily close series indexed by date (ascending), de-duplicated."""
    rows = cur.execute(
        "SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
        "AND close IS NOT NULL ORDER BY ts ASC", (sym,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx, val = [], []
    for ts, c in rows:
        # ts may be epoch seconds/millis or an ISO string — normalise to a date
        try:
            if isinstance(ts, (int, float)):
                unit = "ms" if ts > 1e12 else "s"
                d = pd.to_datetime(ts, unit=unit)
            else:
                d = pd.to_datetime(str(ts))
        except Exception:
            continue
        idx.append(pd.Timestamp(d).normalize()); val.append(float(c))
    s = pd.Series(val, index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


# ---------- stats helpers ----------------------------------------------------
def ols_t(y: np.ndarray, x: np.ndarray):
    """Simple y = a + b·x. Returns (b, t_b, r2, n). Classical SE."""
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = n - 2
    if dof <= 0:
        return beta[1], float("nan"), float("nan"), n
    sigma2 = (resid @ resid) / dof
    XtX_inv = np.linalg.inv(X.T @ X)
    se_b = math.sqrt(sigma2 * XtX_inv[1, 1])
    t_b = beta[1] / se_b if se_b > 0 else float("nan")
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else float("nan")
    return beta[1], t_b, r2, n


def ic(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 5 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


# ---------- main -------------------------------------------------------------
def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    con = sqlite3.connect(SQLITE_DB)
    cur = con.cursor()
    have = list_symbols(cur)

    print("\n=== LAYER-1 RATE-REGIME VALIDATION (India 10Y → banks / shock) ===")
    s10  = pick(CAND_10Y,  have, "10Y")
    sbnk = pick(CAND_BANK, have, "BANKNIFTY")
    snif = pick(CAND_NIFTY, have, "NIFTY")
    svix = pick(CAND_VIX,  have, "INDIAVIX")
    print(f"  symbols → 10Y={s10}  BANK={sbnk}  NIFTY={snif}  VIX={svix}")
    if not s10 or not sbnk or not snif:
        con.close(); sys.exit("  missing a required series — cannot proceed.")

    y10  = load_series(cur, s10)
    bank = load_series(cur, sbnk)
    nif  = load_series(cur, snif)
    vix  = load_series(cur, svix) if svix else pd.Series(dtype=float)
    con.close()

    print(f"  10Y  {y10.index.min().date()}..{y10.index.max().date()}  {len(y10)} obs  "
          f"(last={y10.iloc[-1]:.2f})")
    print(f"  BANK {bank.index.min().date()}..{bank.index.max().date()}  {len(bank)} obs")
    if len(y10) < 250:
        print("  ⚠️  10Y history is short — OOS split will be weak; treat results as provisional.")

    # ---- build the point-in-time feature/target frame -----------------------
    df = pd.DataFrame({"y10": y10, "bank": bank, "nifty": nif})
    if len(vix):
        df["vix"] = vix
    df = df.sort_index()
    # forward-fill the 10Y only across market days it's missing (it can have gaps),
    # but NEVER fill bank/nifty forward (that would fabricate returns).
    df["y10"] = df["y10"].ffill(limit=5)
    df = df.dropna(subset=["y10", "bank", "nifty"])

    # RATE-SPACE features (price index is inverse → negate the price move) --------
    # rate_chg_h > 0  ⇔  yields rose over the last h sessions
    for h in (5, 20, 60):
        df[f"rate_chg_{h}"] = -(df["y10"] / df["y10"].shift(h) - 1.0) * 100.0
    # rate LEVEL state: high yield ⇔ low price ⇔ low price-percentile.
    # yield_state in [0,1], 1 = rates high vs the trailing year. Trailing-only (PIT).
    roll = df["y10"].rolling(252, min_periods=60)
    df["price_pctile"] = roll.apply(lambda w: (w.iloc[-1] > w).mean(), raw=False)
    df["yield_state"] = 1.0 - df["price_pctile"]           # 1 = high-rate regime
    # rate DIRECTION relative to its own 60d trend (momentum sign in rate space)
    df["rate_dir"] = np.sign(df["rate_chg_60"])

    # forward BANK returns (targets) — strictly forward, no look-ahead
    df["bank_fwd_1"] = df["bank"].shift(-1) / df["bank"] - 1.0
    df["bank_fwd_5"] = df["bank"].shift(-5) / df["bank"] - 1.0

    FEATURES = ["rate_chg_5", "rate_chg_20", "rate_chg_60", "yield_state"]

    # =====================================================================
    # TEST A — fitted regression, in-sample vs out-of-sample
    # =====================================================================
    print("\n--- TEST A: BANKNIFTY forward return ~ rate-regime feature ---")
    print("  (coeff read: '+' ⇒ higher when rates RISE. OOS is the verdict, not IS.)")
    for tgt, label in (("bank_fwd_1", "next-day"), ("bank_fwd_5", "5-day fwd")):
        print(f"\n  target = BANK {label} return")
        print(f"    {'feature':<13}{'IS beta':>9}{'t':>7}{'IS R²':>8}{'IS IC':>8}{'OOS IC':>8}   verdict")
        sub = df.dropna(subset=FEATURES + [tgt])
        if len(sub) < 120:
            print(f"    too few rows ({len(sub)}) — skip.")
            continue
        split = int(len(sub) * OOS_FRAC)
        tr, te = sub.iloc[:split], sub.iloc[split:]
        for f in FEATURES:
            b, t, r2, n = ols_t(sub[tgt].values * 100, sub[f].values)   # target in %
            is_ic = ic(tr[f].values, tr[tgt].values)
            oos_ic = ic(te[f].values, te[tgt].values)
            # verdict: survives only if OOS keeps the sign AND ≥ ~40% of IS magnitude
            surv = (not math.isnan(is_ic) and not math.isnan(oos_ic)
                    and np.sign(is_ic) == np.sign(oos_ic)
                    and abs(oos_ic) >= 0.4 * abs(is_ic) and abs(t) >= 2.0)
            verdict = "holds OOS ✓" if surv else ("sign-flip ✗" if np.sign(is_ic) != np.sign(oos_ic)
                                                  else "weak/insig")
            print(f"    {f:<13}{b:>9.4f}{t:>7.2f}{r2:>8.3f}{is_ic:>8.3f}{oos_ic:>8.3f}   {verdict}")
        print(f"    (train n={len(tr)}  →  {tr.index.min().date()}..{tr.index.max().date()};  "
              f"test n={len(te)}  →  {te.index.min().date()}..{te.index.max().date()})")

    # =====================================================================
    # TEST B — non-parametric: mean forward return by rate-regime tercile
    # =====================================================================
    print("\n--- TEST B: mean BANK next-day return by rate-regime tercile ---")
    print("  (rate_chg_20 terciles: T1 = rates falling most, T3 = rates rising most)")
    sub = df.dropna(subset=["rate_chg_20", "bank_fwd_1"]).copy()
    if len(sub) >= 90:
        try:
            sub["terc"] = pd.qcut(sub["rate_chg_20"], 3, labels=["T1 falling", "T2 flat", "T3 rising"])
            g = sub.groupby("terc", observed=True)["bank_fwd_1"]
            print(f"    {'regime':<14}{'n':>6}{'mean%':>9}{'hit%':>8}")
            for name, grp in g:
                print(f"    {str(name):<14}{grp.shape[0]:>6}{grp.mean()*100:>9.3f}{(grp>0).mean()*100:>8.1f}")
            spread = (sub.loc[sub.terc=="T1 falling","bank_fwd_1"].mean()
                      - sub.loc[sub.terc=="T3 rising","bank_fwd_1"].mean()) * 100
            print(f"    spread (falling − rising) = {spread:+.3f}%/day  "
                  f"{'(banks like falling rates)' if spread>0 else '(no rate-down tailwind)'}")
        except Exception as e:
            print(f"    tercile step failed: {e}")
    else:
        print("    too few rows — skip.")

    # =====================================================================
    # TEST C — does the rate regime GATE the shock-recovery bounce?
    # =====================================================================
    print("\n--- TEST C: shock-day next-day bounce, split by rate regime ---")
    print("  (shock = NIFTY < %.1f%%; regime from rate_chg_20 sign at the shock day)" % SHOCK_THRESH)
    d = df.copy()
    d["nifty_ret"] = d["nifty"] / d["nifty"].shift(1) - 1.0
    d["nifty_fwd_1"] = d["nifty"].shift(-1) / d["nifty"] - 1.0     # market bounce next day
    d["bank_fwd_1b"] = d["bank"].shift(-1) / d["bank"] - 1.0
    shocks = d[(d["nifty_ret"] * 100 < SHOCK_THRESH)].dropna(subset=["rate_chg_20", "nifty_fwd_1"])
    print(f"    shock days with a rate reading: {len(shocks)}")
    if len(shocks) >= 20:
        rising = shocks[shocks["rate_chg_20"] > 0]
        falling = shocks[shocks["rate_chg_20"] <= 0]
        print(f"    {'regime at shock':<18}{'n':>5}{'NIFTY bounce%':>15}{'hit%':>8}{'BANK bounce%':>15}")
        for name, grp in (("rates RISING", rising), ("rates FALLING", falling)):
            if len(grp):
                print(f"    {name:<18}{len(grp):>5}"
                      f"{grp['nifty_fwd_1'].mean()*100:>15.3f}"
                      f"{(grp['nifty_fwd_1']>0).mean()*100:>8.1f}"
                      f"{grp['bank_fwd_1b'].mean()*100:>15.3f}")
        if len(rising) and len(falling):
            diff = (falling["nifty_fwd_1"].mean() - rising["nifty_fwd_1"].mean()) * 100
            print(f"    bounce edge (falling − rising) = {diff:+.3f}%   "
                  f"{'→ rate regime looks like a gate' if abs(diff) >= 0.15 else '→ rate regime does NOT gate the bounce'}")
        # VIX cross-check for context, if VIX present
        if "vix" in shocks.columns:
            hv = shocks[shocks["vix"] >= HIVIX]; lv = shocks[shocks["vix"] < HIVIX]
            print(f"    (context — VIX split: hi≥{HIVIX:.0f} bounce {hv['nifty_fwd_1'].mean()*100:+.3f}% "
                  f"n={len(hv)}  vs lo {lv['nifty_fwd_1'].mean()*100:+.3f}% n={len(lv)})")
    else:
        print("    too few shock days with a rate reading — skip (need a longer 10Y history).")

    # ---- honest closing note ------------------------------------------------
    print("\n=== READ ===")
    print("  • OOS IC is the verdict. A big IS IC that collapses or flips on the test tail is")
    print("    an overfit, not a signal — do NOT wire it into Layer 1.")
    print("  • If Test A shows nothing but Test C shows a gate, the 10Y is a CONDITIONER")
    print("    (it changes when other edges work), not a direct return predictor. That's still useful.")
    print("  • Next only if something survives: add the 10Y regime as a lagged conditioner to the")
    print("    shock-recovery score, and re-test bank P/B re-rating vs rate LEVEL on the Postgres history.")


if __name__ == "__main__":
    main()
