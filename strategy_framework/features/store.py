"""
strategy_framework/features/store.py
====================================
Persist the per-snapshot feature vectors (the feature store).

Design: a thin, JSON-blob table so the feature set can evolve without schema
migrations — a few indexed columns (ts, expiry, spot, dte) for slicing, plus a
`features` JSON column holding the full vector. Analysis loads it into pandas and
expands the JSON. Keyed on (ts, expiry); idempotent upsert so backfill can re-run.

Lives in the SAME SQLite file as everything else (option_chains.db) so it sits
alongside captures / realized_metrics.
"""
from __future__ import annotations
import sqlite3, json, time, os
from .extractor import extract_features


def _fdb(db_path: str) -> str:
    """Where the feature store lives — its OWN file, never the capture DB.

    Prefer a LOCAL path via NIFTY_FEATURES_DB env var (recommended: keep the
    actively-written DB off Google Drive / synced folders). If unset, it sits as
    a sibling of the capture DB.
    """
    env = os.environ.get("NIFTY_FEATURES_DB")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "snapshot_features.db")


def _connect(db_path: str) -> sqlite3.Connection:
    # Rollback journal (SQLite default), NOT WAL — WAL needs shared-memory mmap
    # that Google Drive / network filesystems don't support and would error on.
    # A longer busy-timeout rides out a background sync touching the file.
    return sqlite3.connect(_fdb(db_path), timeout=30)


def init(db_path: str):
    with _connect(db_path) as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_features (
            ts          TEXT NOT NULL,
            expiry      TEXT NOT NULL,
            spot        REAL,
            dte         REAL,
            features    TEXT,          -- JSON dict (full vector)
            computed_at TEXT,
            PRIMARY KEY (ts, expiry)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sf_expiry ON snapshot_features(expiry, ts)")


def upsert(db_path: str, feat: dict):
    init(db_path)
    # Stamp the momentum window the scores were computed at. Signal scores are a
    # FUNCTION of the lookback (config/settings.MomentumWindow), so a row computed
    # at 15 bars is not comparable with one computed at 60. Without this stamp a
    # window change would silently leave a mixed-vintage feature store and every
    # IC / correlation number would be computed across two different signals.
    feat.setdefault("lookback_bars", _active_lookback_bars())
    with _connect(db_path) as c:
        c.execute("INSERT OR REPLACE INTO snapshot_features "
                  "(ts, expiry, spot, dte, features, computed_at) VALUES (?,?,?,?,?,?)",
                  (feat["ts"], feat["expiry"], feat.get("spot"), feat.get("dte"),
                   json.dumps(feat), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def _active_lookback_bars() -> int:
    try:
        from ..config.settings import FrameworkConfig
        return int(FrameworkConfig().momentum.bars())
    except Exception:
        return 15


def window_audit(db_path: str, expiry: str | None = None) -> dict:
    """Which momentum window(s) the stored rows were computed at, vs the active one.

    `stale` rows were computed at a DIFFERENT lookback than the config now uses —
    their sig_*_score values are not comparable with fresh ones and should be
    rebuilt (features backfill --force) before trusting any IC/correlation output.
    Rows written before this stamp existed report as 'unstamped'."""
    active = _active_lookback_bars()
    counts: dict = {}
    for r in query(db_path, expiry, limit=1_000_000):
        key = r.get("lookback_bars", "unstamped")
        counts[key] = counts.get(key, 0) + 1
    stale = sum(n for k, n in counts.items() if k != active)
    return {"active_lookback_bars": active, "rows_by_window": counts,
            "stale_or_unstamped_rows": stale, "clean": stale == 0,
            "action": "features backfill --force" if stale else "none"}


def _outcomes(caps, times, i, spot0, final_score):
    """Forward-looking labels for the snapshot at index i (backfill only — these
    use FUTURE data and must never be fed back in as inputs live)."""
    from datetime import timedelta
    out = {}
    for label, mins in (("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60)):
        j = next((k for k in range(i + 1, len(caps)) if times[k] >= times[i] + timedelta(minutes=mins)), None)
        out[f"fwd_ret_{label}_pct"] = round((caps[j]["spot"] - spot0) / spot0 * 100, 3) if j else None
    date0 = caps[i]["captured_at"][:10]
    same = [c for c in caps if c["captured_at"][:10] == date0 and c["captured_at"] > caps[i]["captured_at"]]
    out["fwd_ret_eod_pct"] = round((same[-1]["spot"] - spot0) / spot0 * 100, 3) if same else None
    # direction hit + adverse-move magnitude vs the blended final score (60m ref)
    r60 = out.get("fwd_ret_60m_pct")
    if r60 is not None and final_score is not None and abs(final_score) >= 0.1:
        s = 1 if final_score > 0 else -1
        out["hit_60m"] = bool((s > 0) == (r60 > 0))
        out["adverse_move_60m_pct"] = round(max(0.0, -s * r60), 3)   # move against the call
    return out


def _complete_ts(db_path: str, expiry: str) -> set:
    """ts already stored WITH a complete forward outcome (safe to skip). Rows that
    are missing, or whose outcome was still null (tail of the data when computed),
    are NOT here — so they get re-run and filled once later data exists."""
    init(db_path)
    with _connect(db_path) as c:
        done = set()
        for ts, feat in c.execute("SELECT ts, features FROM snapshot_features WHERE expiry=?", (expiry,)):
            try:
                if feat and json.loads(feat).get("fwd_ret_60m_pct") is not None:
                    done.add(ts)
            except Exception:
                pass
    return done


def backfill(db_path: str, expiry: str, stride: int = 1, force: bool = False,
             progress_cb=None, lookback_min: int | None = None) -> dict:
    """Incremental: compute features + outcomes only for snapshots not already
    stored with a complete outcome. New dates run; already-done rows are skipped;
    previously-incomplete tails get re-run. `force=True` recomputes everything.
    `progress_cb(done, total)` is called as it scans (for a live progress bar)."""
    from datetime import datetime
    from ..signals.data_access import DataAccess, BarCache, CROSS_ASSET_SYMBOLS
    from ..config import constituents as _K
    from datetime import timedelta
    init(db_path)
    # Optional per-run RETURN WINDOW override. Rebuilding at a different lookback
    # produces different sig_*_score values, so this forces a full recompute — a
    # partial backfill would leave a mixed-vintage store (see window_audit).
    momentum = None
    if lookback_min:
        from ..config.settings import MomentumWindow
        momentum = MomentumWindow(lookback_min=int(lookback_min))
        if int(momentum.bars()) != _active_lookback_bars():
            force = True
    _probe = DataAccess(db_path)
    caps = _probe.list_captures(expiry=expiry)
    if len(caps) < 2:
        note = f"only {len(caps)} capture(s) for {expiry}"
        return {"expiry": expiry, "captures": len(caps), "written": 0,
                "skipped": 0, "errors": 0, "note": note}
    times = [datetime.fromisoformat(c["captured_at"].replace("Z", "+00:00")) for c in caps]
    # Pre-load constituent + NIFTY 1m bars once, SCOPED to this expiry's window
    # (+ a lookback buffer for the trailing 60–400-bar features). RAM stays bounded
    # to one expiry regardless of how much total history exists.
    # constituents (for breadth/volume signals) + NIFTY + cross-asset/macro symbols
    # (present ones cached, absent ones negative-cached → no per-snapshot DB hits).
    _bar_syms = sorted((set(_probe.available_symbols("1m")) & set(_K.symbols()))
                       | {"NIFTY"} | set(CROSS_ASSET_SYMBOLS))
    win_start = (times[0] - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    win_end = caps[-1]["captured_at"]
    bar_cache = BarCache(db_path, _bar_syms, "1m", start=win_start, end=win_end)
    da = DataAccess(db_path, bar_cache=bar_cache)
    skip_set = set() if force else _complete_ts(db_path, expiry)
    done = skipped = err = 0
    dates = set()
    total = len(caps)
    for i, cap in enumerate(caps):
        if progress_cb and (i % 10 == 0 or i == total - 1):
            progress_cb(i + 1, total)
        if i % max(1, stride) != 0:
            continue
        if cap["captured_at"] in skip_set:
            skipped += 1
            continue
        try:
            feat = extract_features(db_path, cap["captured_at"], expiry,
                                    da=da, bar_cache=bar_cache, momentum=momentum)
            if feat:
                feat.update(_outcomes(caps, times, i, feat.get("spot"),
                                      feat.get("decomp_final_score")))
                upsert(db_path, feat); done += 1
                dates.add(cap["captured_at"][:10])
        except Exception:
            err += 1
    if progress_cb:
        progress_cb(total, total)
    return {"expiry": expiry, "captures": len(caps), "written": done,
            "skipped": skipped, "errors": err, "dates_written": sorted(dates),
            "lookback_bars": int(momentum.bars()) if momentum else _active_lookback_bars()}


def clear(db_path: str, expiry: str | None = None) -> int:
    """Wipe stored features (for one expiry, or all). Returns rows deleted."""
    init(db_path)
    with _connect(db_path) as c:
        if expiry:
            c.execute("DELETE FROM snapshot_features WHERE expiry = ?", (expiry,))
        else:
            c.execute("DELETE FROM snapshot_features")
        return c.execute("SELECT changes()").fetchone()[0]


def query(db_path: str, expiry: str | None = None, limit: int = 500) -> list[dict]:
    init(db_path)
    with _connect(db_path) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT ts, expiry, spot, dte, features FROM snapshot_features"
        args: list = []
        if expiry:
            q += " WHERE expiry = ?"; args.append(expiry)
        q += " ORDER BY ts ASC LIMIT ?"; args.append(limit)
        rows = []
        for r in c.execute(q, args):
            d = json.loads(r["features"]) if r["features"] else {}
            rows.append(d)
    return rows


def feature_names(db_path: str, expiry: str | None = None) -> list[str]:
    """Numeric feature columns present in ANY row (union — a feature may be null
    early in the session but populated later)."""
    rows = query(db_path, expiry, limit=200)
    names: set = set()
    skip = {"spot", "minutes_since_open"}
    for d in rows:
        for k, v in d.items():
            if isinstance(v, (int, float)) and k not in skip:
                names.add(k)
    return sorted(names)
