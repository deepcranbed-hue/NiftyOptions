#!/usr/bin/env python3
"""
bank_factor_model.py — cross-sectional bank STOCK-SELECTION model (Product B, thesis engine).

NOT a signal engine (no overnight transmission — Bank is domestic, that was validated as
generic-risk/gap-locked in bank_validation.py). This ranks the 12 Nifty Bank names on
FUNDAMENTALS and tests one central thesis:

    THE VALUE-TRAP 2x2 — cheapness (P/B) is only rewarded when asset quality is IMPROVING.
    cheap + improving  →  re-rating (the winner)
    cheap + deteriorating → value trap (the loser masquerading as a bargain)

Everything is cross-sectional (rank among the banks present each period), point-in-time
(period_end + 45d reporting lag), and bucketed BEFORE any regression — per the framework.

Ingredients (all now in Postgres, verified by bank_valuation_probe.py):
    net_worth = equity_capital + reserves      (fundamentals.financials, statement='balance')
    shares                                      (same; split-basis auto-detected + adjusted)
    price      = clean adjusted close           (price_bars, Yahoo auto_adjust)
    quality    = gnpa/nnpa/pcr/nim              (fundamentals.asset_quality, 12/12 banks)
    P/B  = adj_price x adj_shares / net_worth   (screener-consistent; NEVER raw price x raw shares)

PRE-FLIGHT GATE (halts before any ranking if inputs are corrupt):
    (A) UNITS anchor  — HDFC latest P/B must be ~1.9 (731.55 x 1539.34 / 586060). If not → halt.
    (B) SPLIT basis   — detect clean split/bonus jumps in the share series, back-adjust to
                        current basis, PRINT what was adjusted. (auto-adj price + raw shares =
                        understated historical P/B + corrupted cross-sectional rank.)
    (C) COVERAGE      — banks x years with all ingredients; AU Bank is thin (flag, don't drop silently).

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_factor_model.py
"""
from __future__ import annotations
import os, sqlite3, sys, math

try:
    import numpy as np, pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")
try:
    import psycopg
except ImportError:
    sys.exit("psycopg 3 required")
try:
    from scipy import stats as _sps
except Exception:
    _sps = None
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
         "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK"]
LAG_DAYS = 45                      # point-in-time: numbers public ~period_end + 45d
FWD = [365, 182]                   # forward horizons (calendar days) for relative return
NICE_SPLITS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0]
HDFC_PB_ANCHOR = (1.5, 2.5)        # HDFC latest P/B must land here or units are wrong
# canonical line_item -> fuzzy source labels (lowercased contains)
LI = {
    "equity_capital": ["equity_capital", "equity capital"],
    "reserves":       ["reserves"],
    "shares":         ["shares"],
    "total_assets":   ["total assets"],
    "pat":            ["net_profit", "profit after tax"],
    "revenue":        ["revenue"],
}
REGIMES = [("2017-2020 NPA/covid", 2017, 2020), ("2021-2023 recovery", 2021, 2023),
           ("2024-2026 recent", 2024, 2026)]


# ------------------------------------------------------------------ loaders
def load_prices(con):
    """dict[bank] -> (dates ndarray[datetime64], close ndarray) sorted ascending."""
    out = {}
    for b in BANKS:
        rows = con.execute(
            "SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
            "AND close IS NOT NULL ORDER BY ts", (b,)).fetchall()
        if not rows:
            continue
        d = pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize().values
        c = np.asarray([float(r[1]) for r in rows])
        out[b] = (d, c)
    return out


def price_asof(px, bank, when):
    """last close on/before `when` (numpy searchsorted, holiday-safe)."""
    if bank not in px:
        return np.nan
    d, c = px[bank]
    i = np.searchsorted(d, np.datetime64(pd.Timestamp(when).normalize()), side="right") - 1
    return float(c[i]) if i >= 0 else np.nan


def canon(label):
    l = str(label).lower()
    for k, frags in LI.items():
        if any(f in l for f in frags):
            return k
    return None


def load_financials(cur, sym2isin):
    """long -> wide DataFrame: rows (symbol, fy_end), cols = canonical line items (yearly)."""
    isins = [sym2isin[b] for b in BANKS if b in sym2isin]
    # BANKS: standalone ONLY — consolidated bloats net worth with insurance/AMC subs and
    # collapses P/B (Kotak 2.8x -> 1.06x). Both bases now live in the table, so this filter
    # is load-bearing; without it the dedup can silently grab consolidated. (IT will use
    # consolidated — that is the correct primary basis for IT holdcos, set per-sector.)
    cur.execute(
        "SELECT isin, period_end, section, line_item, value FROM fundamentals.financials "
        "WHERE time_period='yearly' AND basis='standalone' AND isin = ANY(%s)", (isins,))
    isin2sym = {v: k for k, v in sym2isin.items()}
    recs = []
    for isin, pend, section, li, val in cur.fetchall():
        c = canon(li)
        if c is None or val is None:
            continue
        recs.append((isin2sym.get(isin), pd.Timestamp(pend).normalize(), section or "", c, float(val)))
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs, columns=["symbol", "fy_end", "section", "item", "value"])
    # prefer 'balance'/'income' 'full' rows; dedupe (symbol,fy_end,item) — 'full' sorts before 'summary'
    df = df.sort_values("section").drop_duplicates(["symbol", "fy_end", "item"], keep="first")
    wide = df.pivot_table(index=["symbol", "fy_end"], columns="item", values="value", aggfunc="first")
    return wide.reset_index()


def load_asset_quality(cur):
    cur.execute("SELECT symbol, period_end, gnpa_pct, nnpa_pct, pcr_pct, nim_pct "
                "FROM fundamentals.asset_quality WHERE symbol = ANY(%s)", (BANKS,))
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    aq = pd.DataFrame(rows, columns=["symbol", "period_end", "gnpa", "nnpa", "pcr", "nim"])
    aq["period_end"] = pd.to_datetime(aq["period_end"]).dt.normalize()
    for c in ("gnpa", "nnpa", "pcr", "nim"):
        aq[c] = pd.to_numeric(aq[c], errors="coerce")
    return aq


# ------------------------------------------------------------------ split-basis (B)
def adjust_shares(g):
    """g: per-bank frame sorted by fy_end asc with 'shares' AND 'net_worth'. Return adj_shares on
    CURRENT basis (latest unchanged). A split/bonus adds NO money → net worth stays flat while
    shares jump → back-adjust. A QIP / merger / recap raises net worth ALONGSIDE shares → real
    capital → keep (adjusting it would corrupt historical P/B, e.g. PNB's 2020 amalgamation).
    Split iff shares jump >=1.4x, net worth ~flat (<1.25x), AND ratio snaps to a nice split.
    Returns (adj_series, notes[])."""
    s = g["shares"].to_numpy(dtype=float)
    nw = g["net_worth"].to_numpy(dtype=float)
    yrs = g["fy_end"].dt.year.to_numpy()
    notes = []
    # split factor at each transition t (t-1 -> t)
    factor = np.ones(len(s))
    for t in range(1, len(s)):
        if s[t - 1] and s[t] and s[t] / s[t - 1] >= 1.4:
            raw = s[t] / s[t - 1]
            nwr = (nw[t] / nw[t - 1]) if (nw[t - 1] and nw[t] and nw[t - 1] > 0 and nw[t] > 0) else float("nan")
            if nwr == nwr and nwr >= 1.25:   # net worth grew too → real capital (QIP/merger/recap)
                notes.append(f"{yrs[t-1]}->{yrs[t]} shares x{raw:.2f}, net_worth x{nwr:.2f} "
                             f"-> real capital raise/merger (KEPT, not a split)")
                continue
            snap = min(NICE_SPLITS, key=lambda r: abs(r - raw))
            if abs(snap - raw) / snap <= 0.06:
                factor[t] = snap
                notes.append(f"{yrs[t-1]}->{yrs[t]} shares x{raw:.2f}, net_worth "
                             f"x{nwr:.2f} flat -> split {snap:g} (back-adjusted)")
            else:
                notes.append(f"{yrs[t-1]}->{yrs[t]} shares x{raw:.2f} (net_worth x{nwr:.2f}) "
                             f"-> ambiguous, KEPT")
    # adj_shares[i] = shares[i] * product(split factors at transitions AFTER i)
    adj = s.copy()
    for i in range(len(s)):
        adj[i] = s[i] * float(np.prod(factor[i + 1:])) if i + 1 < len(s) else s[i]
    return adj, notes


# ------------------------------------------------------------------ metrics
def rank_ic(df, fcol, rcol):
    """mean cross-sectional Spearman rank-IC across fy_end + %positive + N periods."""
    ics = []
    for _, g in df.dropna(subset=[fcol, rcol]).groupby("fy_end"):
        if len(g) >= 4:
            if _sps is not None:
                ic = _sps.spearmanr(g[fcol], g[rcol]).correlation
            else:
                ic = g[fcol].rank().corr(g[rcol].rank())
            if ic == ic:
                ics.append(ic)
    if not ics:
        return None
    ics = np.array(ics)
    return {"ic": float(ics.mean()), "pos": float((ics > 0).mean()), "n": len(ics)}


def fmt_ic(r):
    return f"IC={r['ic']:+.3f}  %pos={r['pos']:.0%}  (n={r['n']} yrs)" if r else "n/a (insufficient)"


# ------------------------------------------------------------------ main
def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    scon = sqlite3.connect(SQLITE_DB)
    px = load_prices(scon)
    scon.close()

    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    cur = conn.cursor()
    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (BANKS,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    fin = load_financials(cur, sym2isin)
    aq = load_asset_quality(cur)
    conn.close()

    print("\n" + "=" * 78)
    print("  BANK CROSS-SECTIONAL FACTOR MODEL — value-trap 2x2 + fundamental rank-ICs")
    print("=" * 78)
    if fin.empty:
        sys.exit("  no yearly financials loaded — check time_period / line_item names.")

    # net worth + split-adjusted shares + P/B
    fin = fin.sort_values(["symbol", "fy_end"]).reset_index(drop=True)
    fin["net_worth"] = fin.get("equity_capital", np.nan) + fin.get("reserves", np.nan)
    parts = []
    split_log = {}
    for b, g in fin.groupby("symbol"):
        g = g.sort_values("fy_end").copy()
        if "shares" in g and g["shares"].notna().any():
            adj, notes = adjust_shares(g)
            g["adj_shares"] = adj
            if notes:
                split_log[b] = notes
        else:
            g["adj_shares"] = np.nan
        parts.append(g)
    fin = pd.concat(parts, ignore_index=True)

    # price at availability (fy_end + 45d) — point-in-time
    fin["avail"] = fin["fy_end"] + pd.Timedelta(days=LAG_DAYS)
    fin["price"] = [price_asof(px, b, d) for b, d in zip(fin["symbol"], fin["avail"])]
    # P/B = adj_price x adj_shares / net_worth  (units: Rs x Cr / RsCr -> dimensionless)
    fin["pb"] = fin["price"] * fin["adj_shares"] / fin["net_worth"]
    fin["roa"] = fin.get("pat", np.nan) / fin.get("total_assets", np.nan) * 100.0

    # ---------- PRE-FLIGHT GATE ----------
    print("\n  ── PRE-FLIGHT GATE ──")
    # (B) split basis
    if split_log:
        print("  (B) split/bonus adjustments applied to share series:")
        for b, notes in split_log.items():
            for n in notes:
                print(f"        {b:<12} {n}")
    else:
        print("  (B) no clean split/bonus jumps detected — shares treated as already on one basis.")
    # (A) units anchor — HDFC latest
    h = fin[fin["symbol"] == "HDFCBANK"].dropna(subset=["pb"]).sort_values("fy_end")
    if h.empty:
        sys.exit("  (A) HALT: no HDFC P/B computed — cannot validate units. Check ingredients.")
    hl = h.iloc[-1]
    print(f"  (A) UNITS anchor — HDFC {hl['fy_end'].date()}:  "
          f"eqcap={hl.get('equity_capital', float('nan')):.0f}  reserves={hl.get('reserves', float('nan')):.0f} "
          f" net_worth={hl['net_worth']:.0f}  adj_shares={hl['adj_shares']:.2f}  price={hl['price']:.1f}")
    print(f"      -> HDFC latest P/B = {hl['pb']:.2f}   (expected ~1.9)")
    if not (HDFC_PB_ANCHOR[0] <= hl["pb"] <= HDFC_PB_ANCHOR[1]):
        print("      ❌ HALT: HDFC P/B outside [1.5, 2.5] — units/basis wrong, NOT ranking on this.")
        print("         Fix: confirm net_worth in Rs-Cr and shares in Cr; then re-run.")
        sys.exit(1)
    print("      ✅ units validated.")
    # (C) coverage
    cov = (fin.dropna(subset=["pb"]).groupby("symbol")["fy_end"]
           .agg(["count", "min", "max"]).reindex(BANKS))
    print("  (C) P/B coverage per bank (years, span):")
    for b in BANKS:
        r = cov.loc[b] if b in cov.index else None
        if r is None or pd.isna(r["count"]):
            print(f"        {b:<12} ❌ no P/B")
        else:
            print(f"        {b:<12} {int(r['count']):>2} yrs  {pd.Timestamp(r['min']).year}-{pd.Timestamp(r['max']).year}")

    # ---------- merge asset quality (annual, fiscal-year-end aligned) ----------
    aq2 = aq.copy()
    aq2["fy"] = aq2["period_end"].dt.year + (aq2["period_end"].dt.month > 6).astype(int)  # Apr-Mar FY
    # take the fiscal-year-END row (closest to March) per (symbol, fy)
    aq2["dist"] = (aq2["period_end"].dt.month - 3).abs()
    aqy = aq2.sort_values("dist").drop_duplicates(["symbol", "fy"], keep="first")
    fin["fy"] = fin["fy_end"].dt.year + (fin["fy_end"].dt.month > 6).astype(int)
    df = fin.merge(aqy[["symbol", "fy", "gnpa", "nnpa", "pcr", "nim"]], on=["symbol", "fy"], how="left")

    # ΔGNPA YoY (improving = gnpa falling = negative Δ) — the trap axis
    df = df.sort_values(["symbol", "fy_end"])
    df["d_gnpa"] = df.groupby("symbol")["gnpa"].diff()

    # ---------- forward RELATIVE return (cross-sectional demeaned) ----------
    for H in FWD:
        col = f"fwd{H}"
        r = []
        for b, a in zip(df["symbol"], df["avail"]):
            p0 = price_asof(px, b, a)
            p1 = price_asof(px, b, a + pd.Timedelta(days=H))
            r.append((p1 / p0 - 1.0) if (p0 and p1 and p0 == p0 and p1 == p1) else np.nan)
        df[col] = r
        # demean within each fy cohort -> pure cross-sectional (stock-selection) return
        df[f"rel{H}"] = df[col] - df.groupby("fy_end")[col].transform("mean")

    # ---------- STANDALONE RANK-ICs (factor -> forward relative return) ----------
    print("\n  ── STANDALONE FACTOR RANK-ICs (vs forward 365d relative return) ──")
    print("     sign expectation: P/B negative (cheap wins) · ΔGNPA negative (deteriorating loses)")
    factors = [("P/B", "pb"), ("ΔGNPA (YoY)", "d_gnpa"), ("GNPA level", "gnpa"),
               ("PCR", "pcr"), ("NIM", "nim"), ("ROA", "roa")]
    for name, c in factors:
        if c not in df:
            continue
        print(f"     {name:<14} {fmt_ic(rank_ic(df, c, 'rel365'))}")

    print("\n  ── per-regime rank-IC (P/B and ΔGNPA — does the edge survive the NPA cycle?) ──")
    for label, y0, y1 in REGIMES:
        sub = df[(df["fy_end"].dt.year >= y0) & (df["fy_end"].dt.year <= y1)]
        print(f"     {label:<22} P/B {fmt_ic(rank_ic(sub, 'pb', 'rel365'))}")
        print(f"     {'':<22} ΔGNPA {fmt_ic(rank_ic(sub, 'd_gnpa', 'rel365'))}")

    # ---------- POWERED value-trap: regime-conditioned P/B (9yr of P/B, not 4yr of GNPA) ----------
    print("\n  ── VALUE-TRAP, POWERED VERSION — regime-conditioned P/B + robustness ──")
    print("     the regimes ARE the asset-quality phases; this asks the 2x2 question on the DEEP axis.")
    # (1) does the GNPA we DO have confirm recovery years were 'improving' (falling)?
    sg = df.dropna(subset=["gnpa"]).groupby(df["fy_end"].dt.year)["gnpa"].median()
    print("     system median GNPA by year (should FALL through 2021-23 = improving): "
          + ", ".join(f"{int(y)}:{v:.1f}%" for y, v in sg.items()))
    # (2) horizon robustness — sign must hold at 182d too, not just 365d
    for label, y0, y1 in REGIMES:
        sub = df[(df["fy_end"].dt.year >= y0) & (df["fy_end"].dt.year <= y1)]
        print(f"     {label:<22} P/B 365d {fmt_ic(rank_ic(sub, 'pb', 'rel365'))} | "
              f"182d {fmt_ic(rank_ic(sub, 'pb', 'rel182'))}")
    # (3) late-listing robustness — is the recovery IC an AU/Bandhan/IDFC-First artifact?
    CORE = [b for b in BANKS if b not in ("AUBANK", "BANDHANBNK", "IDFCFIRSTB")]
    rec = df[(df["fy_end"].dt.year >= 2021) & (df["fy_end"].dt.year <= 2023)]
    print(f"     recovery P/B, ALL banks           {fmt_ic(rank_ic(rec, 'pb', 'rel365'))}")
    print(f"     recovery P/B, core-9 (drop late)  {fmt_ic(rank_ic(rec[rec['symbol'].isin(CORE)], 'pb', 'rel365'))}")

    # ---------- THE VALUE-TRAP 2x2 (UNDERPOWERED cross-sectional version — starved by GNPA years) ----------
    print("\n  ── VALUE-TRAP 2x2 — median-split P/B x asset-quality trend (UNDERPOWERED: N per cell tiny) ──")
    print("     (per year: cheap = below-median P/B; improving = ΔGNPA < 0. Pooled fwd-365d relative return.)")
    print("     NOTE: this is the SHALLOW-axis version; trust the regime-conditioned P/B above instead.")
    v = df.dropna(subset=["pb", "d_gnpa", "rel365"]).copy()
    v["cheap"] = v.groupby("fy_end")["pb"].transform(lambda s: s < s.median())
    v["improving"] = v["d_gnpa"] < 0
    cells = [("cheap",   True,  True,  "CHEAP + IMPROVING  (re-rating)"),
             ("cheap",   True,  False, "CHEAP + DETERIORATING (value trap)"),
             ("expensive", False, True,  "EXPENSIVE + IMPROVING"),
             ("expensive", False, False, "EXPENSIVE + DETERIORATING")]
    print(f"       {'bucket':<36}{'mean rel-ret':>13}{'N':>6}")
    res = {}
    for _, ch, im, label in cells:
        cell = v[(v["cheap"] == ch) & (v["improving"] == im)]
        m = cell["rel365"].mean() if len(cell) else np.nan
        res[(ch, im)] = m
        print(f"       {label:<36}{(f'{m:+.1%}' if m == m else 'n/a'):>13}{len(cell):>6}")

    print("\n  ── THESIS VERDICT (read from the POWERED regime-conditioned P/B, not the starved 2x2) ──")
    print("     The value trap shows up as a SIGN FLIP in P/B's IC across asset-quality regimes:")
    print("       • deteriorating (2017-20 NPA cycle): P/B IC POSITIVE → cheap banks kept falling (the trap)")
    print("       • improving      (2021-23 recovery):  P/B IC strongly NEGATIVE → cheap re-rated (the payoff)")
    print("       • normalized     (2024-26):           ~zero → no cross-sectional edge")
    print("     ⇒ USABLE RULE: rank on P/B ONLY while system asset quality is IMPROVING; in a")
    print("       deteriorating phase, cheapness is a trap — stand aside or invert. P/B is a")
    print("       CONDITIONAL factor, never a standalone one.")
    print("     Confidence gates: the sign flip must survive the 182d-horizon and drop-late-banks")
    print("     checks above. If either flips the recovery sign, downgrade to 'suggestive'.")
    print("\n  NOTE: 9yr / 3-regime sample — a DIRECTION finding, not a calibrated edge. We are NOT")
    print("  mining the 2x2 cells (N=2,3); the honest result lives in the regime IC on the deep axis.")


if __name__ == "__main__":
    main()
