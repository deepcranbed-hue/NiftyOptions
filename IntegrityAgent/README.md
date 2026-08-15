# Integrity Agent

Audits the codebase's **single-source-of-truth invariants** — one signal registry, one
correlation/metrics engine, one lot-size source, one index-volume helper — so a future
edit can't silently re-introduce duplication or let a copy drift. See **`SKILL.md`**.

## Run

```bash
python -m IntegrityAgent.run                 # pass/fail report; exit 0 all-pass, 1 on violation
python -m IntegrityAgent.run --report        # + write reports/*.md
python -m IntegrityAgent.run --json          # machine-readable
```

Also runs inside the test suite (each invariant is a case):

```bash
python -m pytest strategy_framework/tests/test_integrity.py -q
```

## What's in here

- `checks.py` — the invariant checks (runtime import-asserts + static source scans).
  Register a new one with `@check`.
- `run.py` — runner + report writer + CI exit code.
- `reports/` — generated `*.md` integrity reports.

## When to add a check

Whenever you introduce a new "single source of truth" (a new canonical home per the
`CLAUDE.md` DRY list or a SKILL.md HARD RULE), add a matching `@check` here — so the rule
is *enforced by a machine*, not just written down. Cheap, deterministic, no DB/network.
