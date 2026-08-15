"""
Tests for the factor (beliefs) layer: the factor map validates against the registry,
and evaluate_factors aggregates member signals into estimate/confidence/why correctly.
"""
from types import SimpleNamespace
from strategy_framework import factors as F


class _Bundle:
    def __init__(self, d):
        self._d = d

    def get(self, name):
        return self._d.get(name)


def _sig(score, conf=0.6, status="OK"):
    return SimpleNamespace(score=score, confidence=conf, status=status)


def test_factor_map_validates_against_registry():
    v = F.validate()   # unknown members / double-assignment / uncovered directional → assert
    assert v["n_factors"] == len(F.FACTORS)
    assert v["n_assigned"] >= 20     # all directional signals have a home


def test_estimate_role_weighted_and_why():
    b = _Bundle({
        "heavyweight_leadership": _sig(0.8),        # primary (w 1.0)
        "technical_momentum": _sig(-0.2),           # supporting (w 0.6)
        "vwap": _sig(0.5),                          # confidence modifier (no vote)
    })
    out = {f["name"]: f for f in F.evaluate_factors(b)}
    td = out["trend_direction"]
    expect = (1.0 * 0.8 + 0.6 * -0.2) / 1.6
    assert abs(td["estimate"] - round(expect, 3)) < 1e-6
    assert td["agreement"] == 0.5                                  # voters split 1/1
    mem = {m["name"]: m for m in td["members"]}
    assert mem["heavyweight_leadership"]["agrees"] is True
    assert mem["technical_momentum"]["agrees"] is False            # the ✗ in the why
    assert mem["vwap"]["role"] == "confidence"


def test_confidence_rises_with_aligned_modifiers():
    base = {"heavyweight_leadership": _sig(0.8), "technical_momentum": _sig(0.6)}
    with_mod = dict(base, vwap=_sig(0.7), rel_volume=_sig(0.5))     # aligned modifiers
    against = dict(base, vwap=_sig(-0.7), rel_volume=_sig(-0.5))    # opposed modifiers
    c_with = {f["name"]: f for f in F.evaluate_factors(_Bundle(with_mod))}["trend_direction"]["confidence"]
    c_against = {f["name"]: f for f in F.evaluate_factors(_Bundle(against))}["trend_direction"]["confidence"]
    assert c_with > c_against


def test_no_data_factor_is_honest():
    out = {f["name"]: f for f in F.evaluate_factors(_Bundle({}))}
    md = out["macro_risk"]
    assert md["estimate"] is None and md["confidence"] is None      # no fake reads
    assert all(m["ok"] is False for m in md["members"])


def test_diagnostics_never_vote():
    # dealer_positioning: futures_basis is diagnostic — a huge score must not move the estimate
    b1 = _Bundle({"oi_migration": _sig(0.4)})
    b2 = _Bundle({"oi_migration": _sig(0.4), "futures_basis": _sig(-0.9)})
    e1 = {f["name"]: f for f in F.evaluate_factors(b1)}["dealer_positioning"]["estimate"]
    e2 = {f["name"]: f for f in F.evaluate_factors(b2)}["dealer_positioning"]["estimate"]
    assert e1 == e2


def test_factor_map_is_declarative_yaml():
    """Knowledge lives in YAML, not code: the loaded FACTORS mirror factor_map.yaml."""
    from strategy_framework import knowledge as KB
    raw = KB.load_factor_map()
    assert {k for k in raw if not k.startswith("_")} == {f.name for f in F.FACTORS}
    assert F.MAP_VERSION == str(raw["_meta"]["version"])      # versioned knowledge
    td = raw["trend_direction"]["signals"]
    assert td["heavyweight_leadership"] == "primary"


def test_belief_quality_from_evidence(tmp_path, monkeypatch):
    """quality = historical trust (evidence store), orthogonal to today's agreement:
    same bundle, different evidence → same confidence, different quality."""
    import json
    from strategy_framework import knowledge as KB
    b = _Bundle({"heavyweight_leadership": _sig(0.8), "technical_momentum": _sig(0.6)})

    def _with_evidence(iic):
        p = tmp_path / f"ev_{iic}.json"
        p.write_text(json.dumps({"n_sessions": 20, "signals": {
            "heavyweight_leadership": {"ic": iic, "incremental_ic": None, "is_control": True},
            "technical_momentum": {"ic": 0.0, "incremental_ic": iic, "is_control": False}}}))
        monkeypatch.setattr(KB, "EVIDENCE_PATH", str(p))
        return {f["name"]: f for f in F.evaluate_factors(b)}["trend_direction"]

    good = _with_evidence(0.05)     # strong historical evidence → quality 1.0
    poor = _with_evidence(0.005)    # weak historical evidence → quality 0.1
    assert good["confidence"] == poor["confidence"]          # today's agreement identical
    assert good["quality"] == 1.0 and poor["quality"] == 0.1  # history differs
    assert good["quality_basis"] == "provisional"            # 20 sessions < 60

    # no evidence file at all → quality honestly unmeasured
    monkeypatch.setattr(KB, "EVIDENCE_PATH", str(tmp_path / "missing.json"))
    none = {f["name"]: f for f in F.evaluate_factors(b)}["trend_direction"]
    assert none["quality"] is None and none["quality_basis"] is None
