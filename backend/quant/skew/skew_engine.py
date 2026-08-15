"""
skew_engine.py — reference implementation of minute_analytics_v1_brief §3.4 / §3.4a.

Design rules enforced in code (not comments):
  - IV is ALWAYS inverted from mids (Black-76 on parity forward). Any stored iv_mid
    column is ignored by this engine.
  - Parity gate (T-I) actually filters strikes; flagged strikes never enter the smile.
  - 25Δ points are computed PER WING (calls and puts never share an array) and are
    NEVER extrapolated: an unbracketed wing returns status="UNBRACKETED", not a number.
  - artifact_share carries its three guards (quiet / mixed-regime / negative-raw).
  - Configuration classifier: dead-banded inputs, five named states, "unclassified"
    fallback. There is no nearest-match code path.
  - DTE < 2: skew outputs gap (status="EXPIRY_DEGENERATE") unless next-expiry chain
    is supplied.
All thresholds carry PRIOR provenance tags per D-MA-04.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import math

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq

# ----------------------------- threshold registry ----------------------------------
# Every parameter carries provenance (D-MA-04). Tags:
#   PRIOR   — judgment value; must graduate via the stated path or stay visibly PRIOR
#   DERIVED — computed from the data at run time (the value here is only a fallback
#             used when the derivation inputs are unavailable, and is flagged as such)
#   STRUCT  — structural constant with a stated non-market rationale
# The full registry is emitted on every payload as `thresholds_used` so consumers
# see which parameter shaped which output. Overridable via the `pr` argument.
THRESHOLDS = {
    "parity_gap_tol_vpt": {
        "value": 0.50, "tag": "DERIVED",
        "rationale": "same-strike IV gap tolerance; derived per strike from the quotes' "
                     "own bid-ask width in IV space (see spread_implied_tol). The 0.50 "
                     "here is the FALLBACK when bid/ask are missing, and is then PRIOR.",
        "graduation": "n/a — self-calibrating from spreads"},
    "parity_gap_tol_ltp_vpt": {
        "value": 3.0, "tag": "PRIOR",
        "rationale": "parity tolerance for LTP-era rows (no quotes -> spread-implied tol "
                     "unavailable). LTP async/close-mark noise widens same-strike gaps; "
                     "observed body distribution 0.5-1.4 vpt, broken strikes >= 5. "
                     "3.0 passes sync noise, still excludes genuine pollution (D-MA-13)",
        "graduation": "set from P95 of body-strike gap distribution after 30 sessions"},
    "leg_identity_tol_vpt": {
        "value": 0.05, "tag": "STRUCT",
        "rationale": "numerical tolerance on an accounting identity (interp + root-find "
                     "epsilon); not a market judgment",
        "graduation": "tighten if interpolation scheme changes"},
    "iv_change_bound_vpt": {
        "value": 10.0, "tag": "PRIOR",
        "rationale": "T-D sanity bound on any leg change per window",
        "graduation": "replace with quantile of observed |leg Δ| after 60 sessions"},
    "rr_quiet_deadband_vpt": {
        "value": 0.10, "tag": "PRIOR",
        "rationale": "|ΔRR_floating| below this -> artifact_share QUIET",
        "graduation": "z-score vs trailing ΔRR vol after 60 sessions"},
    "spot_deadband_pct": {
        "value": 0.15, "tag": "PRIOR",
        "rationale": "config-chip spot input dead-band. KNOWN TENSION with house rule "
                     "(fixed % band, same pattern criticized in Global Cues)",
        "graduation": "REQUIRED: z-score vs trailing spot vol once minute history >= 30 sessions"},
    "atm_deadband_vpt": {
        "value": 0.30, "tag": "PRIOR",
        "rationale": "config-chip ATM-IV input dead-band",
        "graduation": "z-score vs trailing ATM-IV-change vol, 30+ sessions"},
    "leg_deadband_vpt": {
        "value": 0.30, "tag": "PRIOR",
        "rationale": "config-chip leg input dead-band",
        "graduation": "z-score vs trailing leg-change vol, 30+ sessions"},
    "artifact_share_extreme": {
        "value": 2.0, "tag": "PRIOR",
        "rationale": "|share| above this -> ratio denominator too small to interpret; "
                     "display EXTREME state with raw deltas primary, share secondary",
        "graduation": "revisit against observed share distribution after 60 sessions"},
    "dte_hard_floor_days": {
        "value": 0.20, "tag": "PRIOR",
        "rationale": "below this (~final hours) sigma*sqrt(T) puts the 25D points within "
                     "~1 strike of ATM - measure genuinely degenerate, series gaps hard. "
                     "Between here and dte_splice_days: computed, EXPIRY_REGIME-flagged (D-MA-12)",
        "graduation": "set from observed unbracketed-wing rate vs dte after 10 expiries"},
    "dte_splice_days": {
        "value": 2.0, "tag": "STRUCT",
        "rationale": "delta degenerates toward step function as T->0; 25Δ points collapse "
                     "into ATM (§3.4a). Structural, not calibratable",
        "graduation": "n/a"},
}
PRIOR = {k: v["value"] for k, v in THRESHOLDS.items()}   # runtime values (back-compat)

def thresholds_manifest(pr: dict) -> dict:
    """Per-parameter provenance block for the emission. Overridden values are tagged."""
    out = {}
    for k, meta in THRESHOLDS.items():
        v = pr.get(k, meta["value"])
        out[k] = {"value": v, "tag": meta["tag"] if v == meta["value"] else "OVERRIDE",
                  "graduation": meta["graduation"]}
    return out

def spread_implied_tol(chain: pd.DataFrame, F: float, T: float, K: int) -> Optional[float]:
    """
    DERIVED parity tolerance at strike K: the same-strike CE/PE IV gap the quotes
    themselves permit = half the IV-space width of each side's bid-ask, summed.
    Returns None when quote columns are absent entirely (LTP-era stores).
    """
    if "bid" not in chain.columns or "ask" not in chain.columns:
        return None
    try:
        row_c = chain[(chain.strike == K) & (chain.cp == "CE")].iloc[0]
        row_p = chain[(chain.strike == K) & (chain.cp == "PE")].iloc[0]
        w = 0.0
        for row, cp in ((row_c, "CE"), (row_p, "PE")):
            iv_b = implied_vol(F, K, T, float(row.bid), cp)
            iv_a = implied_vol(F, K, T, float(row.ask), cp)
            if iv_b is None or iv_a is None:
                return None
            w += abs(iv_a - iv_b) / 2.0
        return w * 100.0   # vpt
    except (IndexError, KeyError, TypeError, ValueError):
        return None


# ----------------------------- emission vocabulary (single source of truth) ---------
# One-line meaning for every code the engine can emit. The UI renders these as
# tooltips; the brief's vocabulary appendix is generated from this dict. Do not
# duplicate these strings elsewhere - import VOCABULARY.
VOCABULARY = {
    "flow.state": {
        "NEW_BUYING":       "IV up + OI up - fresh contracts at rising prices: buyers aggressing (real demand).",
        "WRITER_BUYBACK":   "IV up + OI down - shorts paying up to close: often the most urgent flow state.",
        "REPRICING":        "IV up + OI flat - quotes marked up with no position change: no flow behind it.",
        "SUPPLY_OR_UNWIND": "IV down - options sold down: fresh writing (OI up) or long liquidation (OI down).",
        "QUIET":            "IV unchanged - nothing at these strikes worth classifying.",
    },
    "flow.flags": {
        "NO_SPREAD_DATA":   "No bid/ask in store - spread-quality filter could not run; IV move unfiltered for quote noise.",
        "SPREAD_WIDENED":   "Bid-ask widened materially - part of the IV move is quote sloppiness; discount the state.",
    },
    "status": {
        "OK":                 "All requested measures computed; read normally.",
        "PARTIAL":            "Some measure could not be computed; present fields valid, absent ones honestly absent.",
        "EXPIRY_DEGENERATE":  "DTE below hard floor - 25D points collapse into ATM; skew is noise, series gaps.",
        "SPLICE_INCOMPLETE":  "Next-expiry splice requires BOTH its open and current snapshots; half-splice refused (D-MA-14).",
    },
    "flags": {
        "EXPIRY_REGIME":      "DTE in caution band (0.2-2d) - numbers valid but expiry-dominated; excluded from calibration.",
        "SPLICED":            "Measured on the next expiry (front contract too close); one contract, both snapshots.",
        "FLOATING_25D_UNAVAILABLE": "A floating 25D wing could not be computed this snapshot.",
        "FIXED_ANCHOR_OUT_OF_SMILE_RANGE": "A session-open anchor strike fell outside today's usable smile.",
    },
    "wing.status": {
        "BRACKETED":   "25D point sits between two listed strikes - interpolated cleanly, trust it.",
        "UNBRACKETED": "25D point lies beyond available strikes - no number returned (extrapolation forbidden).",
        "NO_WING":     "No usable OTM strikes on that side at all.",
    },
    "artifact_share.status": {
        "OK":           "Ratio stable and interpretable - headline share, deltas beneath.",
        "QUIET":        "Floating RR barely moved - nothing to decompose.",
        "MIXED_REGIME": "Fixed and floating moved opposite ways - no single share exists; read both deltas.",
        "EXTREME":      "Denominator near zero, ratio uninterpretable - read the raw deltas; share demoted.",
        "UNAVAILABLE":  "An input measure missing - share not computed.",
    },
    "parity.reason": {
        "parity_iv_gap":              "Same-strike call/put IVs disagree beyond tolerance - polluted quotes, strike excluded.",
        "mid_at_or_below_intrinsic":  "Price at or under intrinsic - no time value to invert, strike excluded.",
    },
    "threshold.tag": {
        "PRIOR":    "Judgment value awaiting calibration - graduation path in the registry.",
        "STRUCT":   "Structural constant with non-market rationale - not calibratable.",
        "DERIVED":  "Computed from the data at runtime.",
        "OVERRIDE": "Caller replaced the registry value - substitution made visible.",
    },
    "price_source": {
        "MID_2S":     "Two-sided bid/ask mid - full-quality price.",
        "LTP_RECENT": "Last traded price within recency gate - fallback quality, tagged.",
        "EXCLUDED":   "Row unusable - no acceptable price source.",
    },
    "invariant.result": {
        "PASSED":  "Identity held on the computed values.",
        "FAILED":  "Identity violated - measured values and rule attached; card blanks to DATA_INCONSISTENT.",
        "SKIPPED": "Required input not wired - named explicitly, never silently counted as passed.",
    },
}

# ----------------------------- pricing primitives ----------------------------------
def _b76_call(F: float, K: float, T: float, s: float) -> float:
    sq = s * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s * T) / sq
    return F * norm.cdf(d1) - K * norm.cdf(d1 - sq)

def implied_vol(F: float, K: float, T: float, mid: float, cp: str) -> Optional[float]:
    """Invert Black-76 from the mid. Returns None if mid is at/below intrinsic."""
    target = mid if cp == "CE" else mid + (F - K)          # parity: C = P + (F-K) (r*T folded into F)
    intrinsic = max(F - K, 0.0)
    if target <= intrinsic + 1e-6:
        return None
    try:
        return brentq(lambda s: _b76_call(F, K, T, s) - target, 1e-3, 5.0)
    except ValueError:
        return None

def delta(F: float, K: float, T: float, s: float, cp: str) -> float:
    """Per-strike delta using THAT strike's sigma. Callers must pass sigma(K)."""
    d1 = (math.log(F / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    return norm.cdf(d1) if cp == "CE" else norm.cdf(d1) - 1.0

# ----------------------------- forward & parity gate -------------------------------
def forward_from_parity(chain: pd.DataFrame) -> float:
    """F = K + (C_mid - P_mid), median across strikes with both sides quoted."""
    c = chain[chain.cp == "CE"].set_index("strike")["mid"]
    p = chain[chain.cp == "PE"].set_index("strike")["mid"]
    common = c.index.intersection(p.index)
    if len(common) == 0:
        raise ValueError("no strike has both CE and PE mids — cannot form forward")
    return float(np.median(common.values + (c[common] - p[common]).values))

def parity_gate(chain: pd.DataFrame, F: float, T: float,
                tol_vpt: float = PRIOR["parity_gap_tol_vpt"],
                ltp_tol_vpt: float = PRIOR["parity_gap_tol_ltp_vpt"]):
    """
    T-I, real: invert BOTH sides at every dual-quoted strike; flag strikes whose
    same-strike IV gap exceeds tolerance. Returns (clean_strikes, flagged) where
    flagged carries the measured gap — never silently dropped.
    """
    c = chain[chain.cp == "CE"].set_index("strike")["mid"]
    p = chain[chain.cp == "PE"].set_index("strike")["mid"]
    clean, flagged = [], []
    for K in sorted(c.index.intersection(p.index)):
        iv_c = implied_vol(F, K, T, float(c[K]), "CE")
        iv_p = implied_vol(F, K, T, float(p[K]), "PE")
        if iv_c is None or iv_p is None:
            flagged.append({"strike": int(K), "reason": "mid_at_or_below_intrinsic"})
            continue
        gap_vpt = abs(iv_c - iv_p) * 100.0
        tol_k = spread_implied_tol(chain, F, T, int(K))
        if tol_k is not None:
            tol_used, tol_src = tol_k, "DERIVED:spread"
        else:                                  # no quotes at this strike -> LTP tier (D-MA-13)
            tol_used, tol_src = ltp_tol_vpt, "PRIOR:ltp_tier"
        if gap_vpt > tol_used:
            flagged.append({"strike": int(K), "reason": "parity_iv_gap",
                            "gap_vpt": round(gap_vpt, 3),
                            "tol_vpt": round(tol_used, 3), "tol_source": tol_src})
        else:
            clean.append(int(K))
    return clean, flagged

# ----------------------------- smile ------------------------------------------------
def build_otm_smile(chain: pd.DataFrame, F: float, T: float,
                    clean_strikes: list[int]) -> dict[int, float]:
    """sigma(K) from OTM mids only: puts for K < F, calls for K > F."""
    c = chain[chain.cp == "CE"].set_index("strike")["mid"]
    p = chain[chain.cp == "PE"].set_index("strike")["mid"]
    smile = {}
    for K in clean_strikes:
        cp = "PE" if K < F else "CE"
        mid = float(p[K]) if cp == "PE" else float(c[K])
        iv = implied_vol(F, K, T, mid, cp)
        if iv is not None:
            smile[K] = iv
    return smile

# ----------------------------- 25Δ point, per wing ---------------------------------
@dataclass
class WingPoint:
    status: str                      # "BRACKETED" | "UNBRACKETED" | "NO_WING"
    strike: Optional[float] = None
    iv: Optional[float] = None
    wing: list = field(default_factory=list)   # [(K, |delta|, iv)] evidence

def iv_at_delta(smile: dict[int, float], F: float, T: float, cp: str,
                target: float = 0.25) -> WingPoint:
    Ks = [K for K in sorted(smile) if (K > F if cp == "CE" else K < F)]
    pts = [(K, abs(delta(F, K, T, smile[K], cp)), smile[K]) for K in Ks]
    if not pts:
        return WingPoint("NO_WING")
    pts.sort(key=lambda x: x[1])
    ds = [d for _, d, _ in pts]
    if not (ds[0] <= target <= ds[-1]):
        return WingPoint("UNBRACKETED", wing=pts)         # never clamp, never extrapolate
    for (K1, d1, iv1), (K2, d2, iv2) in zip(pts, pts[1:]):
        if d1 <= target <= d2:
            w = (target - d1) / (d2 - d1)
            return WingPoint("BRACKETED", K1 + w * (K2 - K1), iv1 + w * (iv2 - iv1), pts)
    return WingPoint("UNBRACKETED", wing=pts)

def iv_at_strike(smile: dict[int, float], K: float) -> Optional[float]:
    """Fixed-strike readout, linear in strike, in-range only."""
    Ks = sorted(smile)
    for K1, K2 in zip(Ks, Ks[1:]):
        if K1 <= K <= K2:
            w = (K - K1) / (K2 - K1)
            return smile[K1] + w * (smile[K2] - smile[K1])
    return None

# ----------------------------- z±1 cross-check measure ------------------------------
def z_skew(smile: dict[int, float], F: float, T: float) -> Optional[float]:
    """IV(z=+1) - IV(z=-1), z = ln(K/F)/(sigma_atm * sqrt(T)). In-range only."""
    atm = iv_at_strike(smile, F)
    if atm is None:
        return None
    up = F * math.exp(+atm * math.sqrt(T))
    dn = F * math.exp(-atm * math.sqrt(T))
    iu, idn = iv_at_strike(smile, up), iv_at_strike(smile, dn)
    return None if iu is None or idn is None else (iu - idn) * 100.0

# ----------------------------- flow join (D-MA-08) ----------------------------------
def flow_join(d_iv_vpt: float, d_oi_pct: float, d_spread_pct: Optional[float],
              oi_flat_band: float = 2.0, spread_widen_pct: float = 30.0) -> dict:
    """Three OI states + spread gate. Falling OI with rising IV is writer buy-back.
    d_spread_pct=None (no quote data) -> spread gate skipped, NO_SPREAD_DATA flagged (D-MA-11)."""
    flags = []
    if d_spread_pct is None:
        flags.append("NO_SPREAD_DATA")
    elif d_spread_pct > spread_widen_pct:
        flags.append("SPREAD_WIDENED")
    if d_iv_vpt > 0:
        if d_oi_pct > oi_flat_band:
            state = "NEW_BUYING"
        elif d_oi_pct < -oi_flat_band:
            state = "WRITER_BUYBACK"           # aggressive demand — NOT 'no flow'
        else:
            state = "REPRICING"
    else:
        state = "QUIET" if abs(d_iv_vpt) < 1e-9 else "SUPPLY_OR_UNWIND"
    return {"state": state, "flags": flags,
            "measured": {"d_iv_vpt": d_iv_vpt, "d_oi_pct": d_oi_pct,
                         "d_spread_pct": d_spread_pct}}


# ----------------------------- spread & OI change at leg strikes --------------------
def leg_flow_inputs(open_chain: pd.DataFrame, curr_chain: pd.DataFrame,
                    anchor_strike: float, cp: str, n_strikes: int = 2) -> Optional[dict]:
    """
    Compute d_spread_pct and d_oi_pct at the leg's nearest listed strikes,
    from the chains' own bid/ask/oi columns. Same strike set for both (T-F).
    Returns None (checked-and-absent) if required columns are missing.
    """
    # D-MA-11: OI is the hard requirement; spreads are optional (LTP-era stores
    # have no quotes). Missing bid/ask -> d_spread_pct None + NO_SPREAD_DATA flag,
    # never a fabricated 0.0 and never a dropped flow block.
    if "oi" not in open_chain.columns or "oi" not in curr_chain.columns:
        return None
    has_quotes = {"bid", "ask"}.issubset(open_chain.columns) and \
                 {"bid", "ask"}.issubset(curr_chain.columns)
    side_o = open_chain[open_chain.cp == cp].set_index("strike")
    side_c = curr_chain[curr_chain.cp == cp].set_index("strike")
    common = side_o.index.intersection(side_c.index)
    if len(common) == 0:
        return None
    Ks = sorted(common, key=lambda K: abs(K - anchor_strike))[:n_strikes]
    oi_o = float(side_o.loc[Ks, "oi"].sum()); oi_c = float(side_c.loc[Ks, "oi"].sum())
    if oi_o <= 0:
        return None
    out = {"strikes": [int(k) for k in Ks], "cp": cp,
           "d_oi_pct": (oi_c / oi_o - 1.0) * 100.0,
           "d_spread_pct": None, "spread_flag": "NO_SPREAD_DATA"}
    if has_quotes:
        sp_o = float((side_o.loc[Ks, "ask"] - side_o.loc[Ks, "bid"]).mean())
        sp_c = float((side_c.loc[Ks, "ask"] - side_c.loc[Ks, "bid"]).mean())
        if sp_o > 0:
            out.update({"d_spread_pct": (sp_c / sp_o - 1.0) * 100.0,
                        "spread_open": sp_o, "spread_curr": sp_c, "spread_flag": None})
    return out

# ----------------------------- configuration classifier -----------------------------
def classify_configuration(spot_chg_pct: float, atm_chg_vpt: float,
                           put_leg_vpt: float, call_leg_vpt: float,
                           pr: dict = PRIOR) -> dict:
    """Five named states, dead-banded inputs, honest fallback. Forcing to the closest row is forbidden."""
    def band(x, b): return 0 if abs(x) < b else (1 if x > 0 else -1)
    s  = band(spot_chg_pct, pr["spot_deadband_pct"])
    v  = band(atm_chg_vpt,  pr["atm_deadband_vpt"])
    pu = band(put_leg_vpt,  pr["leg_deadband_vpt"])
    ca = band(call_leg_vpt, pr["leg_deadband_vpt"])
    inputs = {"spot": s, "atm_vol": v, "put_leg": pu, "call_leg": ca,
              "measured": {"spot_chg_pct": spot_chg_pct, "atm_chg_vpt": atm_chg_vpt,
                           "put_leg_vpt": put_leg_vpt, "call_leg_vpt": call_leg_vpt}}
    table = [
        ((+1, +1, +1, None), "hedged rally (fragile)"),
        ((+1, -1, None, -1), "overwriting grind"),
        ((+1, None, None, +1), "call chase — upside tail risk"),
        ((-1, +1, +1, None), "orderly hedging"),
        ((-1, +1, None, +1), "squeeze-risk-into-weakness"),
    ]
    for (rs, rv, rp, rc), label in table:
        if ((rs is None or rs == s) and (rv is None or rv == v)
                and (rp is None or rp == pu) and (rc is None or rc == ca)):
            return {"configuration": label, "inputs": inputs}
    return {"configuration": "unclassified — mixed tape", "inputs": inputs}

# ----------------------------- orchestrator ----------------------------------------
def decompose_skew(open_chain: pd.DataFrame, curr_chain: pd.DataFrame,
                   T_open: float, T_curr: float, dte_days: float,
                   spot_open: float, spot_curr: float,
                   next_expiry_open_chain: Optional[pd.DataFrame] = None,
                   next_expiry_curr_chain: Optional[pd.DataFrame] = None,
                   pr: dict = PRIOR) -> dict:
    """Full §3.4 emission. Every number computed from the supplied chains.

    Splice contract (D-MA-14): measuring the next expiry requires BOTH its
    session-open and current snapshots — anchors, legs, and both RR series must
    come from ONE contract. A half-splice (current only) would compare IVs
    across expiries and report term structure as skew change; it is refused.
    When splicing, the caller MUST pass T_open/T_curr computed against the
    SPLICED expiry's settlement (10:00Z), and stamps expiry_measured.
    """
    # ---- DTE validity (D-MA-06 + D-MA-12 two-tier) ----
    expiry_regime = False
    spliced = False
    if dte_days < pr["dte_splice_days"]:
        if next_expiry_open_chain is not None and next_expiry_curr_chain is not None:
            open_chain, curr_chain = next_expiry_open_chain, next_expiry_curr_chain
            spliced = True                        # both snapshots swapped — one contract
        elif (next_expiry_open_chain is not None) != (next_expiry_curr_chain is not None):
            return {"status": "SPLICE_INCOMPLETE",
                    "detail": "half-splice refused: next-expiry OPEN and CURRENT snapshots "
                              "are both required — cross-expiry RR deltas report term "
                              "structure as skew change (D-MA-14).",
                    "provenance": "D-MA-14"}
        elif dte_days < pr["dte_hard_floor_days"]:
            return {"status": "EXPIRY_DEGENERATE",
                    "detail": f"DTE={dte_days:.2f} < hard floor {pr['dte_hard_floor_days']}; "
                              "25D points degenerate — series gaps.",
                    "provenance": "PRIOR:dte_hard_floor_days"}
        else:
            expiry_regime = True                  # compute, but the regime is different

    out = {"status": "OK", "thresholds_used": thresholds_manifest(pr), "flags": []}
    if spliced:
        out["flags"].append("SPLICED")            # measured on next expiry; caller stamps which
    if expiry_regime:
        out["flags"].append("EXPIRY_REGIME")      # canonical D-MA-06 vocabulary: valid data,
                                                  # tagged; excluded from threshold calibration

    # ---- surfaces ----
    F_o = forward_from_parity(open_chain);  F_c = forward_from_parity(curr_chain)
    clean_o, flag_o = parity_gate(open_chain, F_o, T_open,
                                  pr["parity_gap_tol_vpt"], pr["parity_gap_tol_ltp_vpt"])
    clean_c, flag_c = parity_gate(curr_chain, F_c, T_curr,
                                  pr["parity_gap_tol_vpt"], pr["parity_gap_tol_ltp_vpt"])
    out["parity_flags"] = {"open": flag_o, "current": flag_c}
    smile_o = build_otm_smile(open_chain, F_o, T_open, clean_o)
    smile_c = build_otm_smile(curr_chain, F_c, T_curr, clean_c)

    # ---- floating 25Δ, per wing, both snapshots ----
    c_o = iv_at_delta(smile_o, F_o, T_open, "CE"); p_o = iv_at_delta(smile_o, F_o, T_open, "PE")
    c_c = iv_at_delta(smile_c, F_c, T_curr, "CE"); p_c = iv_at_delta(smile_c, F_c, T_curr, "PE")
    for nm, wp in [("call25_open", c_o), ("put25_open", p_o),
                   ("call25_curr", c_c), ("put25_curr", p_c)]:
        out[nm] = {"status": wp.status, "strike": wp.strike,
                   "iv_vpt": None if wp.iv is None else round(wp.iv * 100, 3)}
    if any(w.status != "BRACKETED" for w in (c_o, p_o, c_c, p_c)):
        out["status"] = "PARTIAL"
        out["flags"].append("FLOATING_25D_UNAVAILABLE")
        out["rr_floating"] = None
        d_rr_float = None
    else:
        rr_o = (c_o.iv - p_o.iv) * 100; rr_c = (c_c.iv - p_c.iv) * 100
        out["rr_floating"] = {"open_vpt": round(rr_o, 3), "curr_vpt": round(rr_c, 3),
                              "d_vpt": round(rr_c - rr_o, 3),
                              "window": "session_open→current"}
        d_rr_float = rr_c - rr_o

    # ---- fixed-strike series at the open anchors (T-C basis) ----
    out["anchors"] = None
    d_rr_fixed = d_call_fix = d_put_fix = None
    if c_o.status == "BRACKETED" and p_o.status == "BRACKETED":
        out["anchors"] = {"call": round(c_o.strike, 1), "put": round(p_o.strike, 1)}
        ivc_f = iv_at_strike(smile_c, c_o.strike); ivp_f = iv_at_strike(smile_c, p_o.strike)
        if ivc_f is not None and ivp_f is not None:
            d_call_fix = (ivc_f - c_o.iv) * 100; d_put_fix = (ivp_f - p_o.iv) * 100
            d_rr_fixed = d_call_fix - d_put_fix
            out["rr_fixed"] = {"open_vpt": round((c_o.iv - p_o.iv) * 100, 3),
                               "curr_vpt": round((ivc_f - ivp_f) * 100, 3),
                               "d_vpt": round(d_rr_fixed, 3),
                               "window": "session_open→current"}
            out["legs_fixed_vpt"] = {"d_call": round(d_call_fix, 3),
                                     "d_put": round(d_put_fix, 3)}
            legs = {"d_call": d_call_fix, "d_put": d_put_fix}
            out["dominant_leg"] = max(legs, key=lambda k: abs(legs[k]))
            # flow join at the dominant leg (D-MA-08): spread & OI from the chains themselves
            cp_leg = "CE" if out["dominant_leg"] == "d_call" else "PE"
            anchor = c_o.strike if cp_leg == "CE" else p_o.strike
            d_iv = legs[out["dominant_leg"]]
            fi = leg_flow_inputs(open_chain, curr_chain, anchor, cp_leg)
            if fi is None:
                out["flow"] = {"status": "UNAVAILABLE",
                               "missing": "bid/ask/oi columns or common strikes"}
            else:
                out["flow"] = flow_join(d_iv, fi["d_oi_pct"], fi["d_spread_pct"])
                out["flow"]["strikes"] = fi["strikes"]; out["flow"]["cp"] = fi["cp"]
                if fi.get("spread_flag") and fi["spread_flag"] not in out["flow"]["flags"]:
                    out["flow"]["flags"].append(fi["spread_flag"])
        else:
            out["flags"].append("FIXED_ANCHOR_OUT_OF_SMILE_RANGE")

    # ---- artifact_share with three guards ----
    if d_rr_float is None or d_rr_fixed is None:
        out["artifact_share"] = {"status": "UNAVAILABLE"}
    elif abs(d_rr_float) < pr["rr_quiet_deadband_vpt"]:
        out["artifact_share"] = {"status": "QUIET",
                                 "d_rr_floating_vpt": round(d_rr_float, 3)}
    elif abs(d_rr_fixed) > pr["leg_identity_tol_vpt"] and np.sign(d_rr_float) != np.sign(d_rr_fixed):
        out["artifact_share"] = {"status": "MIXED_REGIME",
                                 "d_rr_fixed_vpt": round(d_rr_fixed, 3),
                                 "d_rr_floating_vpt": round(d_rr_float, 3)}
    else:
        share = 1.0 - d_rr_fixed / d_rr_float
        if abs(share) > pr["artifact_share_extreme"]:
            out["artifact_share"] = {"status": "EXTREME",
                                     "note": "denominator too small for the ratio to be "
                                             "interpretable - read the raw deltas",
                                     "d_rr_fixed_vpt": round(d_rr_fixed, 3),
                                     "d_rr_floating_vpt": round(d_rr_float, 3),
                                     "value_raw": round(share, 3)}   # kept, never headline
        else:
            out["artifact_share"] = {"status": "OK", "value": round(share, 3),  # negative allowed raw
                                     "d_rr_fixed_vpt": round(d_rr_fixed, 3),
                                     "d_rr_floating_vpt": round(d_rr_float, 3)}

    # ---- z±1 cross-check ----
    z_o, z_c = z_skew(smile_o, F_o, T_open), z_skew(smile_c, F_c, T_curr)
    out["z1_skew_vpt"] = {"open": None if z_o is None else round(z_o, 3),
                          "curr": None if z_c is None else round(z_c, 3)}

    # ---- configuration ----
    spot_chg = (spot_curr / spot_open - 1.0) * 100
    atm_o, atm_c = iv_at_strike(smile_o, F_o), iv_at_strike(smile_c, F_c)
    atm_chg = None if atm_o is None or atm_c is None else (atm_c - atm_o) * 100
    if atm_chg is None or d_call_fix is None:
        out["configuration"] = {"configuration": "unclassified — inputs unavailable"}
    else:
        out["configuration"] = classify_configuration(spot_chg, atm_chg,
                                                      d_put_fix, d_call_fix, pr)
    out["forward"] = {"open": round(F_o, 2), "curr": round(F_c, 2)}
    return out
