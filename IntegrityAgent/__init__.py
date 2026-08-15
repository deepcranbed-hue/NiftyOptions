"""Integrity Agent — audits the codebase's single-source-of-truth invariants.

Cross-imports the whole project (registry, engine, api, config, exchange_config,
signals) and asserts the DRY / consolidation guarantees hold. See SKILL.md.
"""
