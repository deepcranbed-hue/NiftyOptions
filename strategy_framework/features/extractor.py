"""
strategy_framework/features/extractor.py
========================================
Compute a rich, as-of feature vector for one option-chain snapshot.

This is the substrate for the attribution / feature-engineering direction: rather
than one "RND score", persist every underlying number per snapshot so any
hypothesis can be re-tested over history without recomputing. Everything here is
as-of `ts` (no lookahead) and degrades gracefully — a feature that can't be
computed (e.g. RND without scipy, IV-based features on IV=0 backfill) returns
None rather than failing the whole vector.

Feature groups (see the desk SKILL.md / user's spec):
  context      : spot, dte, minutes_since_open, session_phase
  rnd          : mean, median, mode, sd, skew, kurtosis, entropy, p_up, drift_z
  smile        : atm_iv, rr_proxy   (iv-slope/curvature/butterfly need real IV)
  open_interest: pcr, total_call_oi, total_put_oi, oi_change_net, max_pain, max_pain_dist
  market_state : realized_vol, vix, spot_ret_15m, spot_ret_60m, vwap_dist, breadth
Greeks-based dealer-positioning features (GEX/vanna/charm) are intentionally
omitted until real per-strike IV is captured — they'd be fabricated from IV=0.
"""
from __future__ import annotations
import math
import numpy as np
from datetime import datetime, timezone, timedelta

from ..signals.data_access import DataAccess, days_to_expiry
from ..bs import (            # single source for BS math (D: no second implementation)
    ncdf as _ncdf, npdf as _npdf, bs_delta as _bs_delta,
    bs_price as _bs_price, implied_vol,
)
from ..config import constituents as _K

IST = timezone(timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------
def _minutes_since_open(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
    return int((dt - dt.replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() // 60)


def _max_pain(chain):
    """Strike minimising total option-writer payout at expiry."""
    if not chain.strikes:
        return None
    best = None
    for k in chain.strikes:
        pay = sum(chain.call_oi.get(s, 0) * max(k - s, 0) +
                  chain.put_oi.get(s, 0) * max(s - k, 0) for s in chain.strikes)
        if best is None or pay < best[0]:
            best = (pay, k)
    return best[1] if best else None


def _oi_features(chain) -> dict:
    spot = chain.spot
    tot_call = sum(chain.call_oi.values())
    tot_put = sum(chain.put_oi.values())
    oi_chg = sum(chain.call_oi_chg.values()) - sum(chain.put_oi_chg.values())
    cvol = sum((chain.call_volume or {}).values())
    pvol = sum((chain.put_volume or {}).values())
    max_pain = _max_pain(chain)
    return {
        "pcr": round(tot_put / (tot_call + 1e-9), 4),                    # PCR by OI
        "pcr_volume": round(pvol / (cvol + 1e-9), 4) if cvol else None,  # PCR by volume
        "total_call_oi": tot_call, "total_put_oi": tot_put,
        "total_call_volume": cvol, "total_put_volume": pvol,
        "oi_change_net": oi_chg,
        "max_pain": max_pain,
        "max_pain_dist_pct": round((spot - max_pain) / spot * 100, 3) if max_pain else None,
    }


def _prev_deltas(da, chain, expiry) -> dict:
    """Changes vs the immediately-prior snapshot: max-pain shift + volume change."""
    caps = da.list_captures(expiry=expiry, end=chain.ts)
    if len(caps) < 2:
        return {}
    prev = da.chain_as_of(caps[-2]["captured_at"], expiry)
    if prev is None:
        return {}
    out = {}
    cur_mp, prev_mp = _max_pain(chain), _max_pain(prev)
    if cur_mp is not None and prev_mp is not None:
        out["max_pain_shift"] = round(cur_mp - prev_mp, 1)
    out["call_volume_chg"] = round(sum((chain.call_volume or {}).values()) -
                                   sum((prev.call_volume or {}).values()), 0)
    out["put_volume_chg"] = round(sum((chain.put_volume or {}).values()) -
                                  sum((prev.put_volume or {}).values()), 0)
    return out


# BS math (_ncdf/_npdf/_bs_delta/_bs_price/implied_vol) is imported from
# strategy_framework/bs.py above. It used to be re-defined here verbatim;
# the copies were byte-identical and drifted apart by construction.


def _iv_skew_features(chain, dte_days) -> dict:
    """Vol-trader skew — now computed from IV backed out of the LTPs (see bs.iv_skew)."""
    from .. import bs
    return bs.iv_skew(chain, dte_days)


# _iv_skew_features_legacy / _legacy_impl were retired 2026-08-15 (D-SC-03): both had
# ZERO call sites, and _legacy_impl carried its own inline copy of the BS inversion
# that bs.iv_skew now owns. Retired to _to_delete/, not deleted.


def _smile_features(chain) -> dict:
    atm = chain.atm_strike()
    ivs = [v for v in (chain.call_iv.get(atm, 0), chain.put_iv.get(atm, 0)) if v and v > 0]
    atm_iv = (np.mean(ivs) * (100 if np.mean(ivs) < 3 else 1)) if ivs else None
    # premium-based risk-reversal proxy (works even when IV is 0)
    spot = chain.spot; target = 0.03 * spot
    puts = [(k, chain.put_ltp.get(k, 0)) for k in chain.strikes if k < spot and chain.put_ltp.get(k, 0) > 0]
    calls = [(k, chain.call_ltp.get(k, 0)) for k in chain.strikes if k > spot and chain.call_ltp.get(k, 0) > 0]
    rr = None
    if puts and calls:
        pk, pp = min(puts, key=lambda x: abs((spot - x[0]) - target))
        ck, cp = min(calls, key=lambda x: abs((x[0] - spot) - target))
        p_rich = pp / max(spot - pk, 1e-6); c_rich = cp / max(ck - spot, 1e-6)
        rr = round((c_rich - p_rich) / (c_rich + p_rich + 1e-9), 4)
    return {"atm_iv": round(atm_iv, 2) if atm_iv else None, "rr_proxy": rr}


def _rnd_features(chain, dte_days) -> dict:
    """Full RND moments from the risk-neutral density (needs scipy + rnd.py)."""
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.quant.rnd import extract_rnd, rnd_stats  # noqa
    except Exception:
        return {}
    try:
        ks = np.array(chain.strikes, float)
        calls = np.array([chain.call_ltp.get(k, 0.0) for k in chain.strikes], float)
        puts = np.array([chain.put_ltp.get(k, 0.0) for k in chain.strikes], float)
        T = max(dte_days / 365.0, 1e-5)
        grid, dens = extract_rnd(ks, calls, chain.spot, T, 0.0655, put_prices=puts)
        st = rnd_stats(grid, dens, chain.spot, strikes=ks, call_ltp=calls, put_ltp=puts)
        if st.get("provenance") == "FALLBACK":
            return {"rnd_provenance": "FALLBACK"}
        dx = grid[1] - grid[0]
        p = dens / (dens.sum() * dx + 1e-12)
        mean = float((grid * p).sum() * dx)
        var = float(((grid - mean) ** 2 * p).sum() * dx)
        sd = math.sqrt(max(var, 1e-9))
        skew = float(((grid - mean) ** 3 * p).sum() * dx) / (sd ** 3 + 1e-12)
        kurt = float(((grid - mean) ** 4 * p).sum() * dx) / (sd ** 4 + 1e-12)
        entropy = float(-(p * np.log(p + 1e-12)).sum() * dx)
        median = float(grid[np.searchsorted(np.cumsum(p) * dx, 0.5)])
        mode = float(grid[int(np.argmax(dens))])
        p_up = float(p[grid > chain.spot].sum() * dx)
        return {"rnd_mean": round(mean, 1), "rnd_median": round(median, 1),
                "rnd_mode": round(mode, 1), "rnd_sd": round(sd, 1),
                "rnd_skew": round(skew, 3), "rnd_kurtosis": round(kurt, 3),
                "rnd_entropy": round(entropy, 3), "rnd_p_up": round(p_up, 3),
                "rnd_drift_z": round((mean - chain.spot) / (sd + 1e-9), 3),
                "rnd_provenance": st.get("provenance", "PRIMARY")}
    except Exception as e:
        return {"rnd_error": str(e)[:60]}


def vix_regime(vix) -> "str | None":
    """India-VIX volatility regime bucket. Thresholds are PRIOR (judgement) until
    calibrated (D-MA-04): calm <13, normal 13–16, elevated 16–20, stressed >20."""
    if vix is None:
        return None
    try:
        v = float(vix)
    except (TypeError, ValueError):
        return None
    if v < 13:
        return "calm"
    if v < 16:
        return "normal"
    if v < 20:
        return "elevated"
    return "stressed"


def _market_features(da: DataAccess, ts: str, spot: float, vix=None) -> dict:
    out = {"vix": None, "vix_regime": None, "realized_vol_ann_pct": None,
           "spot_ret_15m_pct": None, "spot_ret_60m_pct": None,
           "vwap_dist_pct": None, "breadth": None}
    out["vix"] = vix if vix is not None else da.latest_vix(ts)   # snapshot's own vix (no query)
    out["vix_regime"] = vix_regime(out["vix"])                   # calm/normal/elevated/stressed
    bars = da.bars("NIFTY", "1m", end=ts, limit=400)
    if len(bars) >= 20:
        c = np.array([b["close"] for b in bars], float)
        r = np.diff(np.log(c))
        out["realized_vol_ann_pct"] = round(float(r.std() * np.sqrt(252 * 375) * 100), 2)
        if len(c) > 15:
            out["spot_ret_15m_pct"] = round(float((c[-1] / c[-16] - 1) * 100), 3)
        if len(c) > 60:
            out["spot_ret_60m_pct"] = round(float((c[-1] / c[-61] - 1) * 100), 3)
        v = np.array([b["volume"] or 0.0 for b in bars], float)
        if v.sum() > 0:
            vwap = float((c * v).sum() / v.sum())
            out["vwap_dist_pct"] = round((spot - vwap) / vwap * 100, 3)
    # breadth across ALL available NIFTY-50 constituents (not a hardcoded few),
    # both equal-weighted and INDEX-WEIGHTED (index direction is cap-weighted).
    syms = sorted((set(da.available_symbols("1m")) & set(_K.symbols())) - {"NIFTY"})
    adv = dec = 0
    adv_w = dec_w = tot_w = 0.0
    for sym in syms:
        b = da.bars(sym, "1m", end=ts, limit=60)
        if len(b) >= 3:
            ret = b[-1]["close"] / b[0]["close"] - 1
            w = _K.weight_of(sym)
            tot_w += w
            if ret > 0:
                adv += 1; adv_w += w
            elif ret < 0:
                dec += 1; dec_w += w
    n = adv + dec
    if n > 0:
        out["breadth"] = round((adv - dec) / n, 3)                 # equal-weighted
        out["breadth_weighted"] = round((adv_w - dec_w) / tot_w, 3) if tot_w > 0 else out["breadth"]
        out["breadth_n"] = int(n)              # how many constituents it was computed from
    return out


def _decomposition(db_path: str, ts: str, expiry: str, bar_cache=None, momentum=None) -> dict:
    """Signal decomposition (as-of): each component's score/weight/contribution to
    the blended decision, the final score/confidence/regime, and the primary and
    secondary drivers (largest |contribution|). Lets you attribute *why* the
    blend leaned the way it did — and, joined to the outcome, why it was right/wrong.
    """
    try:
        from ..signals import bundle as _sb
        from ..strategy import regime as _reg
        from ..config.settings import DEFAULT as cfg
        b = _sb.evaluate(db_path, ts, expiry, veto_days=cfg.gates.event_veto_days,
                         bar_cache=bar_cache, momentum=momentum)
        r = _reg.classify(b, cfg.weights, cfg.gates)
    except Exception:
        return {}
    wmap = cfg.weights.as_dict()
    contribs = r.diagnostics.get("contributions", {})
    out = {"decomp_final_score": round(float(r.net_score), 3),
           "decomp_confidence": round(float(r.net_confidence), 3),
           "decomp_regime": r.label}
    mags = {}
    for name, w in wmap.items():
        sig = b.get(name)
        eff_conf = contribs.get(name, {}).get("eff_conf", sig.confidence)
        contribution = w * eff_conf * sig.score
        out[f"sig_{name}_score"] = round(float(sig.score), 3)
        out[f"sig_{name}_ok"] = 1 if sig.status == "OK" else 0    # for the store fast path
        out[f"sig_{name}_weight"] = w
        out[f"sig_{name}_contribution"] = round(float(contribution), 4)
        mags[name] = abs(contribution)
    ranked = sorted(mags.items(), key=lambda kv: -kv[1])
    out["decomp_primary"] = ranked[0][0] if ranked else None
    out["decomp_secondary"] = ranked[1][0] if len(ranked) > 1 else None
    return out


def extract_features(db_path: str, ts: str, expiry: str, da: DataAccess = None,
                     bar_cache=None, momentum=None) -> dict | None:
    """The full as-of feature vector for (ts, expiry). None if no chain snapshot.
    Pass a cache-backed `da` (and `bar_cache`) during backfill to avoid per-snapshot
    bar queries."""
    da = da or DataAccess(db_path, bar_cache=bar_cache)
    chain = da.chain_as_of(ts, expiry)
    if chain is None:
        return None
    spot = chain.spot
    dte = days_to_expiry(chain.ts, expiry)
    feat = {"ts": chain.ts, "expiry": expiry, "spot": round(spot, 2),
            "dte": round(dte, 3), "minutes_since_open": _minutes_since_open(chain.ts)}
    feat.update(_oi_features(chain))
    feat.update(_prev_deltas(da, chain, expiry))
    feat.update(_smile_features(chain))
    feat.update(_iv_skew_features(chain, dte))          # null block until real IV
    feat.update(_rnd_features(chain, dte))
    feat.update(_market_features(da, chain.ts, spot, vix=chain.vix))
    feat.update(_decomposition(db_path, chain.ts, expiry, bar_cache=bar_cache,
                               momentum=momentum))
    # Stamp the window these signal scores were computed at (see store.upsert).
    if momentum is not None:
        feat["lookback_bars"] = int(momentum.bars())
    return feat
