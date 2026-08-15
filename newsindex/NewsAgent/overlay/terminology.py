"""
terminology.py — institutional language for relationship status.

The review: "Rule" sounds deterministic; markets are probabilistic. Replace the rule/confirmed/
overridden vocabulary with economic-prior language. We keep the machine-readable status field
(CONFIRMED/WEAKENED/OVERRIDDEN) for the schema, and add a human `status_label` and a
`prior_language` phrasing used in the report.
"""
from __future__ import annotations

STATUS_LABEL = {
    "CONFIRMED": "Supported",
    "WEAKENED": "Partially supported",
    "OVERRIDDEN": "Dominated by stronger drivers",
}

STATUS_PHRASE = {
    "CONFIRMED": "the economic prior was supported by today's tape",
    "WEAKENED": "the economic prior held only partially today",
    "OVERRIDDEN": "the economic prior was dominated by stronger drivers today (not a failed rule)",
}


def label(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def phrase(status: str) -> str:
    return STATUS_PHRASE.get(status, status)


def rename_relationship(name: str) -> str:
    """Frame rule-ish names as an economic RELATIONSHIP (not 'prior', which sounds Bayesian)."""
    n = name.replace(" rule", "").replace("Rule ", "")
    n = n.replace("Economic prior — ", "")            # migrate any older phrasing
    return f"Economic relationship — {n}" if not n.lower().startswith("economic relationship") else n
