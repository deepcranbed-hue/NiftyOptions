import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Tuple
from bar_store import DB_PATH
from .skew_engine import decompose_skew, PRIOR
from .invariants import evaluate as evaluate_invariants

# The chain_snapshots view labels option type as 'call'/'put'; the skew engine's
# fixtures and delta logic use 'CE'/'PE'. Map at the store boundary (never inside the
# engine) so the adapter is the single place that knows the store's column vocabulary.
_CP_MAP = {"call": "CE", "put": "PE", "CE": "CE", "PE": "PE"}


def load_chain_snapshot(expiry: str, target_time: Optional[str] = None,
                        is_open: bool = False) -> Tuple[Optional[pd.DataFrame], Optional[str], Optional[float]]:
    """
    1. Chains: Load snapshot for the measured expiry from `chain_snapshots`.
       Open snapshot: first captured >= 09:15 IST (UTC+5:30) of the current day.
       Current snapshot: the latest captured snapshot or nearest target_time.

    Returns (chain_df[strike,cp,bid,ask,mid,oi], snapshot_ts, spot). All three are None
    when no snapshot exists — the caller gaps the emission rather than fabricating inputs.
    """
    # The view exposes a DERIVED-ONLY `mid` (NULL unless TWO_SIDED) plus a tagged
    # `price` / `price_source` pair (D-CAP-02). The engine inverts IV from its `mid`
    # column, so we feed it `price` (two-sided mid when available, else LTP) and carry
    # `price_source` through so nothing is anonymous. Excluded (NONE) rows have NULL price.
    cols = "ts, spot, strike, cp, bid, ask, mid, price, price_source, oi, volume"
    if target_time and len(target_time) == 10:
        target_time = f"{target_time}T23:59:59.999Z"
        
    conn = sqlite3.connect(DB_PATH)
    try:
        if is_open:
            # First snapshot >= 09:15:00 IST of target date or today (09:15 IST == 03:45 UTC)
            if target_time:
                target_date = target_time.split("T")[0]
            else:
                target_date = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d')
            start_ts = f"{target_date}T03:45:00.000Z"
            query = (f"SELECT {cols} FROM chain_snapshots "
                     "WHERE expiry = ? AND ts >= ? ORDER BY ts ASC LIMIT 200")
            df = pd.read_sql_query(query, conn, params=(expiry, start_ts))
        elif target_time:
            query = (f"SELECT {cols} FROM chain_snapshots "
                     "WHERE expiry = ? AND ts <= ? ORDER BY ts DESC LIMIT 200")
            df = pd.read_sql_query(query, conn, params=(expiry, target_time))
        else:
            query = (f"SELECT {cols} FROM chain_snapshots "
                     "WHERE expiry = ? ORDER BY ts DESC LIMIT 200")
            df = pd.read_sql_query(query, conn, params=(expiry,))

        if df.empty:
            return None, None, None

        # Restrict to the single snapshot timestamp we anchored on (first row's ts).
        snapshot_ts = df['ts'].iloc[0]
        df = df[df['ts'] == snapshot_ts]

        spot_val = df['spot'].iloc[0]
        spot = None if spot_val is None else float(spot_val)

        # Feed the engine `price` as its `mid` (two-sided mid or LTP), and pass the
        # `price_source` tag through as an extra column (engine ignores extras; §3.1).
        clean_df = df[['strike', 'cp', 'bid', 'ask', 'oi', 'price_source']].copy()
        clean_df['mid'] = df['price'].values
        clean_df['cp'] = clean_df['cp'].map(_CP_MAP)   # call/put -> CE/PE for the engine
        return clean_df, snapshot_ts, spot
    except Exception:
        return None, None, None
    finally:
        conn.close()

def run_skew_pipeline(expiry: str, next_expiry: Optional[str] = None, target_time: Optional[str] = None, pr: dict = PRIOR) -> dict:
    """
    Orchestrate thin adapter logic:
    1. Loads open & current snapshots.
    2. Calculates precise T year fractions.
    3. Handles D-MA-06 Expiry splice.
    4. Evaluates invariants.
    """
    # Load open chain (IST >= 09:15) aligned with target date if specified
    open_df, open_ts, spot_open = load_chain_snapshot(expiry, target_time=target_time, is_open=True)
    curr_df, curr_ts, spot_curr = load_chain_snapshot(expiry, target_time=target_time, is_open=False)
    
    if open_df is None or curr_df is None or open_ts is None or curr_ts is None:
        return {
            "status": "PARTIAL",
            "detail": "Missing required open or current expiry chain snapshots.",
            "invariants": {
                "passed": False,
                "checked": [],
                "failures": [{"id": "ADAPTER", "measured": {}, "rule": "snapshots_present"}],
                "skipped": [],
            }
        }

    # Spot is required by the engine's configuration block. A missing spot is reported,
    # never defaulted to 0.0 (that div would fabricate a spot_chg). Skew/parity math that
    # does not need spot is still gapped here to keep one honest emission contract.
    if spot_open is None or spot_curr is None:
        return {
            "status": "PARTIAL",
            "detail": "Spot unavailable on open and/or current snapshot; configuration ungated.",
            "invariants": {
                "passed": False,
                "checked": [],
                "failures": [{"id": "ADAPTER", "measured": {"spot_open": spot_open,
                              "spot_curr": spot_curr}, "rule": "spot_present"}],
                "skipped": [],
            }
        }

    # Expiry parsing to calculate T (ISO 8601 to datetime)
    try:
        exp_dt = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
        exp_dt = exp_dt.replace(hour=10, minute=0, second=0, microsecond=0)
        open_dt = datetime.fromisoformat(open_ts.replace('Z', '+00:00'))
        curr_dt = datetime.fromisoformat(curr_ts.replace('Z', '+00:00'))
    except Exception:
        # Fallback to naive parse
        exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
        exp_dt = exp_dt.replace(hour=10, minute=0, second=0, microsecond=0)
        open_dt = datetime.fromisoformat(open_ts)
        curr_dt = datetime.fromisoformat(curr_ts)
        
    T_open = (exp_dt - open_dt).total_seconds() / (365.25 * 86400.0)
    T_curr = (exp_dt - curr_dt).total_seconds() / (365.25 * 86400.0)
    dte_days = (exp_dt - curr_dt).total_seconds() / 86400.0
    
    # 3. Expiry selection (D-MA-06 / D-MA-14)
    next_expiry_open_df = None
    next_expiry_curr_df = None
    spliced = False
    if dte_days < pr.get("dte_splice_days", PRIOR["dte_splice_days"]) and next_expiry:
        next_expiry_open_df, _, _ = load_chain_snapshot(next_expiry, target_time=target_time, is_open=True)
        next_expiry_curr_df, _, _ = load_chain_snapshot(next_expiry, target_time=target_time, is_open=False)
        spliced = (next_expiry_open_df is not None) and (next_expiry_curr_df is not None)
        if spliced:
            try:
                next_exp_dt = datetime.fromisoformat(next_expiry.replace('Z', '+00:00'))
                next_exp_dt = next_exp_dt.replace(hour=10, minute=0, second=0, microsecond=0)
                T_open = (next_exp_dt - open_dt).total_seconds() / (365.25 * 86400.0)
                T_curr = (next_exp_dt - curr_dt).total_seconds() / (365.25 * 86400.0)
            except Exception:
                next_exp_dt = datetime.strptime(next_expiry, "%Y-%m-%d")
                next_exp_dt = next_exp_dt.replace(hour=10, minute=0, second=0, microsecond=0)
                T_open = (next_exp_dt - open_dt).total_seconds() / (365.25 * 86400.0)
                T_curr = (next_exp_dt - curr_dt).total_seconds() / (365.25 * 86400.0)

    # Call skew engine decompose_skew
    emission = decompose_skew(
        open_chain=open_df,
        curr_chain=curr_df,
        T_open=T_open,
        T_curr=T_curr,
        dte_days=dte_days,
        spot_open=spot_open,
        spot_curr=spot_curr,
        next_expiry_open_chain=next_expiry_open_df,
        next_expiry_curr_chain=next_expiry_curr_df,
        pr=pr
    )

    # Stamp which expiry the measurement actually used (D-MA-06 splice provenance)
    emission["expiry_measured"] = next_expiry if spliced else expiry
    emission["snapshots"] = {"open_ts": open_ts, "curr_ts": curr_ts, "dte_days": round(dte_days, 3)}

    # Propagate the price-source tag so no derived value is anonymous (D-CAP-02).
    # Summarise across the chain actually consumed: TWO_SIDED only if every row is two-
    # sided; LTP if any row fell back to LTP (also raise MID_IS_LTP); else NONE.
    priced_df = next_expiry_curr_df if spliced else curr_df
    sources = set(priced_df["price_source"].dropna().unique()) if "price_source" in priced_df else set()
    emission["price_source_mix"] = sorted(sources)   # report the full mix on the emission
    if sources and sources <= {"MID_2S"}:
        emission["price_source"] = "MID_2S"
    elif "LTP_RECENT" in sources:
        emission["price_source"] = "LTP_RECENT"
        emission.setdefault("flags", []).append("MID_IS_LTP")
    else:
        emission["price_source"] = "EXCLUDED"

    # 4. Invariant evaluation. Auxiliary streams (floating-leg deltas, OI-join strikes,
    #    VIX/ATM changes, config recompute inputs) are not yet wired here → passed as None,
    #    which the engine reports as SKIPPED with the missing input named. We NEVER
    #    fabricate an auxiliary input to force a PASS (§3.4). Kwarg names must match
    #    invariants.evaluate(floating_legs, oi_join, vix, config_inputs).
    inv_block = evaluate_invariants(
        emission,
        floating_legs=None,
        oi_join=None,
        vix=None,
        config_inputs=None,
    )
    emission["invariants"] = inv_block

    return emission
