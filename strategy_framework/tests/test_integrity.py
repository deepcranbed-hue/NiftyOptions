"""
Pytest bridge for the Integrity Agent — runs every single-source-of-truth invariant
check as its own test case, so the whole consolidation is protected by `pytest` too
(not only the standalone agent). The check functions live in IntegrityAgent/checks.py.
"""
import pytest

from IntegrityAgent import checks


@pytest.mark.parametrize("fn", checks.CHECKS, ids=[f.__name__ for f in checks.CHECKS])
def test_invariant(fn):
    name, ok, detail = fn()
    assert ok, f"{name}: {detail}"
