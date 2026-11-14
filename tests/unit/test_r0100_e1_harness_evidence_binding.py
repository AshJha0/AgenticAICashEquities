"""Evidence binding rules in ceap.harness.memory (0.10.0 E1): value lookup, tolerance, ref binding."""

from __future__ import annotations

import math

from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import EvidenceRef, Finding
from ceap.harness.memory import (
    MAX_BIND_DEPTH,
    bind_evidence_refs,
    evidence_problems,
    same_value,
    value_at,
)


def _ev(eid: str, attrs: dict, etype: EvidenceType = EvidenceType.EXECUTION_DATA) -> Evidence:
    return Evidence(eid, etype, "test", "d", attributes=attrs)


def _finding(attrs: dict, support: tuple[str, ...] = ("E1",), refs: tuple[EvidenceRef, ...] = ()) -> Finding:
    return Finding("F1", "s", support, (), 0.5, attributes=attrs, evidence_refs=refs)


def test_value_at_walks_dicts_and_list_indices() -> None:
    ev = _ev("E1", {"values": {"rows": [{"bps": 4.5}, {"bps": 7.0}]}})
    assert value_at(ev, "values.rows.1.bps") == 7.0
    assert value_at(ev, "") == ev.attributes
    missing = value_at(ev, "values.rows.2.bps")
    assert missing is value_at(ev, "values.nope") is value_at(ev, "values.rows.x")


def test_same_value_tolerance_nan_and_bool_identity() -> None:
    assert same_value(1.0, 1.0 + 1e-12)
    assert not same_value(1.0, 1.001)
    assert same_value(math.nan, float("nan"))
    assert not same_value(math.nan, 1.0)
    assert same_value(True, True) and not same_value(True, 1)
    assert same_value("a", "a") and not same_value("a", "b")


def test_binding_prefers_calculation_records_and_skips_non_scalars() -> None:
    tool = _ev("E1", {"values": {"slippage_bps": 12.0}})
    calc = _ev("C1", {"result": {"slippage_bps": 12.0}}, EvidenceType.CALCULATION)
    f = _finding({"slippage_bps": 12.0, "flag": True, "rows": [1, 2], "venue": "ARCA"}, ("E1", "C1"))
    bound = bind_evidence_refs(f, {"E1": tool, "C1": calc})
    assert bound.evidence_refs == (EvidenceRef(id="C1", path="result.slippage_bps", value=12.0),)
    assert evidence_problems(bound, {"E1": tool, "C1": calc}) == []


def test_binding_ignores_skip_keys_and_respects_depth() -> None:
    ev = _ev("E1", {"arguments": {"x": 1}, "a": {"b": {"c": {"d": {"e": {"x": 1}}}}}})
    f = _finding({"x": 1})
    assert bind_evidence_refs(f, {"E1": ev}) is f  # skip key at top level, match too deep
    assert MAX_BIND_DEPTH == 3


def test_explicit_refs_are_kept() -> None:
    ref = EvidenceRef(id="E1", path="v", value=2)
    f = _finding({"v": 1}, refs=(ref,))
    assert bind_evidence_refs(f, {"E1": _ev("E1", {"v": 1})}) is f


def test_problems_name_missing_absent_path_wrong_value_and_bad_derivation() -> None:
    evidence = {
        "E1": _ev("E1", {"v": 1.0}),
        "C1": _ev("C1", {"derived_from": ["E1", "E9"]}, EvidenceType.CALCULATION),
        "C2": _ev("C2", {"derived_from": "E1"}, EvidenceType.CALCULATION),
        "C3": _ev("C3", {}, EvidenceType.CALCULATION),
    }
    refs = (
        EvidenceRef(id="E7", path="v", value=1.0),
        EvidenceRef(id="E1", path="w", value=1.0),
        EvidenceRef(id="E1", path="v", value=2.0),
    )
    problems = evidence_problems(_finding({}, ("E1", "C1", "C2", "C3"), refs), evidence)
    assert problems == [
        "evidence_ref E7 does not exist",
        "evidence_ref E1:w has no value at that path",
        "evidence_ref E1:v cites 2.0 but the evidence holds 1.0",
        "calculation C1 derives from unknown evidence ['E9']",
        "calculation C2 derives from unknown evidence ['(malformed)']",
    ]
