/*
 * src/lib/signalRoster.ts
 * =======================
 * THE frontend's only source of signal metadata — and it owns none of it.
 *
 * Everything here is fetched from `GET /api/strategy/config`, which serves
 * `strategy_framework/signals/registry.py` verbatim. No component may hardcode a
 * signal name, label, family or weight: adding a `SignalSpec` row in the registry
 * must light the signal up in every view with zero frontend edits.
 * See CLAUDE.md (DRY rule) and strategy_framework/SKILL.md HARD RULE 13.
 *
 * IntegrityAgent check `frontend_no_hardcoded_signal_roster` fails the build if a
 * literal signal list reappears in src/.
 */
import { useEffect, useState } from 'react';

export interface SignalSpec {
  name: string;
  label: string;
  family: string;
  kind: 'directional' | 'gate' | 'overlay';
  weight: number;
  blended: boolean;
  momentum_boost: boolean;
  data_ready: boolean;
  method: string;
  detail_keys: string[];
  feature_key: string;          // the feature-store column, e.g. sig_vwap_score
}

export interface StrategyConfig {
  version: string;
  lot_size: number;
  signals: SignalSpec[];
  signal_families: Record<string, string[]>;
  directional_signals: string[];
  blended_signals: string[];
  pinned_zero_signals: string[];
  [k: string]: any;
}

/* One in-flight fetch shared by every caller — the config is static per process,
 * so N components mounting must not produce N requests. */
let _cache: Promise<StrategyConfig> | null = null;

const titleCase = (n: string) =>
  n.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Build a roster from feature-store column names (sig_<name>_score). The FALLBACK
 * path for a backend too old to serve `signals` on /api/strategy/config — still
 * derived from live backend data, never from a list typed into the frontend. */
async function rosterFromFeatureNames(): Promise<SignalSpec[]> {
  const r = await fetch('/api/strategy/feature-names');
  if (!r.ok) return [];
  const j = await r.json();
  return (j.names || [])
    .filter((n: string) => /^sig_.+_score$/.test(n))
    .map((n: string) => {
      const name = n.replace(/^sig_/, '').replace(/_score$/, '');
      return {
        name, label: titleCase(name), family: 'unknown',
        kind: 'directional' as const, weight: 0, blended: false,
        momentum_boost: false, data_ready: true,
        method: 'Metadata unavailable — backend did not serve the signal registry.',
        detail_keys: [], feature_key: n,
      };
    });
}

export function fetchStrategyConfig(force = false): Promise<StrategyConfig> {
  if (force) _cache = null;
  if (!_cache) {
    _cache = (async () => {
      let cfg: any = null;
      try {
        const r = await fetch('/api/strategy/config');
        if (!r.ok) throw new Error(`/api/strategy/config returned ${r.status}`);
        cfg = await r.json();
      } catch (e) {
        console.error('[signalRoster] config fetch failed:', e);
        cfg = null;
      }

      if (cfg?.signals?.length) return cfg as StrategyConfig;

      // Config unreachable or served without a roster (stale backend). Fall back to
      // the feature-store columns so the views still populate, and say so loudly.
      console.warn('[signalRoster] no `signals` in /api/strategy/config — falling '
        + 'back to /api/strategy/feature-names. Restart uvicorn to pick up the registry.');
      const signals = await rosterFromFeatureNames();
      if (!signals.length) {
        _cache = null;                       // nothing usable — let the next caller retry
        throw new Error('Signal roster unavailable: /api/strategy/config served no '
          + '`signals` and /api/strategy/feature-names returned none. Is the backend running?');
      }
      return {
        ...(cfg || {}),
        degraded: true,
        signals,
        directional_signals: signals.map((s) => s.name),
        blended_signals: [], pinned_zero_signals: [], signal_families: {},
      } as unknown as StrategyConfig;
    })().catch((e) => {
      _cache = null;                          // let the next caller retry
      throw e;
    });
  }
  return _cache;
}

/**
 * hydrateContractParams() — pull exchange contract params from the backend and
 * patch them into CONFIG at startup.
 *
 * `CONFIG.lot_size` in constants.ts is only a BOOTSTRAP value for the first paint;
 * the authority is exchange_config.py (NIFTY_LOT_SIZE). Because analytics read it as
 * a default parameter (`lotSize = CONFIG.lot_size`), which is evaluated per call,
 * mutating the object here propagates to every later computation without a refactor.
 * Call once from App.tsx. When NSE revises the lot size you change exchange_config.py
 * only — the UI follows.
 */
export async function hydrateContractParams(): Promise<number | null> {
  try {
    const c = await fetchStrategyConfig();
    const { CONFIG } = await import('./constants');
    if (typeof c.lot_size === 'number' && c.lot_size > 0) {
      if (c.lot_size !== CONFIG.lot_size) {
        console.info(`[config] lot_size ${CONFIG.lot_size} → ${c.lot_size} (from exchange_config.py)`);
      }
      CONFIG.lot_size = c.lot_size;
      return c.lot_size;
    }
  } catch {
    /* backend down → keep the bootstrap value; the UI still renders */
  }
  return null;
}

/* ── global config mutation + live fan-out ────────────────────────────────────
 * Settings served by /api/strategy/config are GLOBAL: set once, applied
 * everywhere. A component that changes one must therefore (a) persist it on the
 * backend, (b) drop the cached config, and (c) tell every mounted consumer to
 * re-read — otherwise the view you clicked updates and the others keep showing
 * the old value until a reload, which is exactly the "same setting, two answers"
 * bug this module exists to prevent. */
type Listener = () => void;
const _listeners = new Set<Listener>();

function _notifyAll() { _listeners.forEach((fn) => { try { fn(); } catch { /* noop */ } }); }

/** Re-read config from the backend and push it to every mounted useSignalRoster. */
export async function refreshStrategyConfig(): Promise<StrategyConfig> {
  const cfg = await fetchStrategyConfig(true);
  _notifyAll();
  return cfg;
}

/**
 * Set THE shared return window (minutes) for every price-return signal.
 * Persisted server-side, then fanned out to all mounted views.
 * Returns the backend's response, which includes a feature-store staleness audit —
 * cached sig_*_score values computed at the old window are now stale.
 */
export async function setMomentumWindow(lookbackMin: number): Promise<any> {
  const r = await fetch(`/api/strategy/config/momentum-window?lookback_min=${lookbackMin}`,
    { method: 'POST' });
  const j = await r.json().catch(() => null);
  if (!r.ok || j?.error) throw new Error(j?.error || `set window failed (${r.status})`);
  await refreshStrategyConfig();
  return j;
}

export interface Roster {
  loading: boolean;
  error: string | null;
  config: StrategyConfig | null;
  /** every signal, registry order */
  all: SignalSpec[];
  /** directional only — the ones with a score worth correlating / IC-testing */
  directional: SignalSpec[];
  byName: Record<string, SignalSpec>;
  /** display label, degrading to the raw name if the roster hasn't loaded */
  label: (name: string) => string;
  lotSize: number | null;
  /** true when the roster came from the feature-store fallback (no registry metadata) */
  degraded: boolean;
}

/**
 * useSignalRoster() — the roster, live from the backend registry.
 *
 *   const { directional, label } = useSignalRoster();
 *   <select>{directional.map(s => <option key={s.name} value={s.name}>{s.label}</option>)}</select>
 */
export function useSignalRoster(): Roster {
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchStrategyConfig()
      .then((c) => { if (alive) { setConfig(c); setError(null); } })
      .catch((e) => { if (alive) setError(String(e?.message || e)); });
    load();
    // re-read whenever ANY view changes a global setting
    _listeners.add(load);
    return () => { alive = false; _listeners.delete(load); };
  }, []);

  const all = config?.signals ?? [];
  const byName: Record<string, SignalSpec> = {};
  all.forEach((s) => { byName[s.name] = s; });

  return {
    loading: !config && !error,
    error,
    config,
    all,
    directional: all.filter((s) => s.kind === 'directional'),
    byName,
    label: (name: string) => byName[name]?.label || name,
    lotSize: config?.lot_size ?? null,
    degraded: Boolean((config as any)?.degraded),
  };
}

/** Inline banner for the roster's failure/degraded states. Rendering this beside a
 * signal control means an empty dropdown can never again be a silent mystery. */
export function rosterStatusText(r: Roster): string | null {
  if (r.error) return `Signal roster unavailable — ${r.error}. Check the backend is running, then reload.`;
  if (r.degraded) return 'Signal names came from the feature store; the backend did not serve the signal registry. Restart uvicorn to restore labels, families and weights.';
  if (!r.loading && !r.all.length) return 'Signal roster is empty — the backend returned no signals.';
  return null;
}
