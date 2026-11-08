"""Round R0: canonical hashing, redaction, cassette scrubbing, contracts, release gate."""

from __future__ import annotations

import importlib.util
import json
import logging
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ceap.domain import (
    EvidenceRef,
    Finding,
    HypothesisTable,
    LLMCallRecord,
    ResultWarning,
    Task,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from ceap.domain.common import SCHEMA_VERSION
from ceap.observability.logging import JsonFormatter
from ceap.policy.approvals import ApprovalRequest
from ceap.util.canonical import canonical_datetime, canonical_json, canonical_text, content_hash
from ceap.util.redact import redact, redact_obj, scrub_cassette

ROOT = Path(__file__).resolve().parents[2]

# Fake secrets are assembled at runtime so the repository itself never contains a key-shaped string
# (gitleaks scans the tree; see the planted-key self-test in ci.yml).
FAKE_KEY = "sk-" + "ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 2


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 0.3 canonical bytes


def test_crlf_and_lf_copies_hash_identically(tmp_path: Path) -> None:
    text = "line one\nline two\n\nend"
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    lf.write_bytes(text.encode())
    crlf.write_bytes(text.replace("\n", "\r\n").encode())
    assert lf.read_bytes() != crlf.read_bytes()
    assert content_hash(lf.read_bytes()) == content_hash(crlf.read_bytes())
    assert canonical_text("a\rb") == b"a\nb"


def test_canonical_json_is_key_order_independent() -> None:
    assert (
        canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1}) == b'{"a":[1,2],"b":1}'
    )


def test_canonical_json_refuses_nan_and_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2026, 1, 1)})


def test_canonical_datetime_is_utc_with_fixed_format() -> None:
    plus_one = timezone(timedelta(hours=1))
    a = datetime(2026, 3, 2, 15, 0, tzinfo=plus_one)
    b = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert canonical_datetime(a) == canonical_datetime(b) == "2026-03-02T14:00:00.000000Z"


@given(st.dictionaries(st.text(max_size=8), st.integers() | st.text(max_size=8), max_size=6))
def test_canonical_json_roundtrips(d: dict) -> None:
    assert json.loads(canonical_json(d)) == d


# ---------------------------------------------------------------- 0.8 redaction


@pytest.mark.parametrize(
    "raw, kind",
    [
        ("contact jane.doe@example.com please", "EMAIL"),
        (f"my key is {FAKE_KEY}", "API_KEY"),
        ("Authorization: Bearer abcdefghijklmnop123", "BEARER"),
        ("account 12345678901 was charged", "ACCOUNT"),
        ("card 4111 1111 1111 1111", "ACCOUNT"),
    ],
)
def test_redact_removes_secrets_and_pii(raw: str, kind: str) -> None:
    out = redact(raw)
    assert f"[REDACTED:{kind}]" in out
    assert redact(out) == out  # idempotent


def test_redact_keeps_trading_questions_intact() -> None:
    q = "Why was AAPL slippage high between 14:00 and 15:00 on 2026-03-02 for 25000 shares at 187.25?"
    assert redact(q) == q


def test_task_description_is_redacted_at_the_boundary() -> None:
    task = Task.create(f"Analyse AAPL, my key is {FAKE_KEY}, mail me at a@b.co")
    assert FAKE_KEY not in task.description
    assert "a@b.co" not in task.description


def test_log_lines_are_redacted() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, f"question with {FAKE_KEY}", None, None)
    assert FAKE_KEY not in JsonFormatter().format(record)


def test_cassette_scrubber_round_trip() -> None:
    interaction = {
        "request": {
            "headers": {"x-api-key": FAKE_KEY, "content-type": "application/json"},
            "body": {"messages": [{"role": "user", "content": "email ops@desk.example.org"}]},
        },
        "response": {
            "headers": {"request-id": "req_011CXYZ", "anthropic-organization-id": "org-123"},
            "id": "msg_01ABC",
            "body": {"content": [{"type": "text", "text": "ok"}]},
        },
    }
    scrubbed = scrub_cassette(interaction)
    dumped = json.dumps(scrubbed)
    for secret in (FAKE_KEY, "req_011CXYZ", "org-123", "msg_01ABC", "ops@desk.example.org"):
        assert secret not in dumped
    assert scrubbed["request"]["headers"]["content-type"] == "application/json"
    assert scrubbed["response"]["body"] == interaction["response"]["body"]
    assert scrub_cassette(scrubbed) == scrubbed  # stable on a second pass
    assert redact_obj(("a", 1)) == ("a", 1)


# ---------------------------------------------------------------- 0.7 contracts


def test_contract_shapes_exist_with_safe_defaults() -> None:
    assert {"DEGRADED", "INCOMPLETE_ANALYSIS", "INTERRUPTED"} <= {s.value for s in TaskStatus}
    f = Finding.create("x", ["E1"], 0.5)
    assert f.evidence_refs == ()
    ref = EvidenceRef("E1", "metrics.slippage_bps", 4.2)
    f2 = Finding(f.id, "x", ("E1",), (), 0.5, evidence_refs=(ref,))
    assert f2.with_confidence(0.6).evidence_refs == (ref,)
    assert HypothesisTable().rows == ()
    assert ResultWarning("FALLBACK", "planner fell back").severity.value == "DEGRADED"
    rec = LLMCallRecord(model="m", sdk="anthropic", prompt_sha="0" * 64)
    assert rec.schema_version == SCHEMA_VERSION
    assert ToolResult(ToolStatus.SUCCESS).schema_version == SCHEMA_VERSION


def test_approval_content_hash_binds_the_payload() -> None:
    a = ApprovalRequest.create("T", "S", "exec.stage", {"qty": 100, "sym": "AAPL"}, "r", "u")
    b = ApprovalRequest.create("T", "S", "exec.stage", {"sym": "AAPL", "qty": 100}, "r", "u")
    c = ApprovalRequest.create("T", "S", "exec.stage", {"sym": "AAPL", "qty": 101}, "r", "u")
    assert a.content_hash == b.content_hash != c.content_hash
    odd = ApprovalRequest.create("T", "S", "x", {"when": datetime(2026, 1, 1)}, "r", "u")
    assert len(odd.content_hash) == 64


def test_contract_checker_flags_signature_changes() -> None:
    mod = _load_script("check_contracts")
    before = {"d.py": "class A:\n    x: int\n"}
    after = {"d.py": "class A:\n    x: int\n    y: str = ''\n"}
    assert mod.signatures(before) != mod.signatures(after)
    assert mod.signatures(before) == mod.signatures({"d.py": "class A:\n    x: int\n    _p = 1\n"})


# ---------------------------------------------------------------- 0.4 release gate


def test_release_gate_refuses_red_or_missing_checks() -> None:
    mod = _load_script("release_check")

    def run(name, conclusion="success", status="completed", created="2026-11-08T10:00:00Z", attempt=1):
        return {
            "name": name,
            "id": 1,
            "event": "push",
            "status": status,
            "conclusion": conclusion,
            "created_at": created,
            "run_attempt": attempt,
        }

    ci = run("CI")
    # only CI gates: a refused daily paper session or a red nightly on the same commit does not block
    assert mod.evaluate_workflow_runs([ci, run("paper-daily", "failure"), run("Nightly", "failure")])
    # the latest CI run decides (a green re-run after a red one releases; a red latest refuses)
    assert mod.evaluate_workflow_runs([run("CI", "failure"), run("CI", created="2026-11-08T11:00:00Z")])
    for runs in (
        [],
        [run("paper-daily")],
        [run("CI", "failure")],
        [run("CI", None, "in_progress")],
        [ci, run("CI", "failure", created="2026-11-08T11:00:00Z")],
    ):
        with pytest.raises(mod.ReleaseRefusedError):
            mod.evaluate_workflow_runs(runs)


def test_release_gate_checks_versions_and_changelog(tmp_path: Path) -> None:
    mod = _load_script("release_check")
    (tmp_path / "src" / "ceap").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.4.0"\n')
    (tmp_path / "src" / "ceap" / "__init__.py").write_text('__version__ = "0.4.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [0.4.0] - 2026-11-29\n")
    assert mod.versions("v0.4.0", tmp_path) == "0.4.0"
    mod.changelog("0.4.0", tmp_path)
    with pytest.raises(mod.ReleaseRefusedError):
        mod.versions("v0.4.1", tmp_path)
    with pytest.raises(mod.ReleaseRefusedError):
        mod.changelog("0.4.1", tmp_path)


def test_version_agrees_in_repo() -> None:
    mod = _load_script("release_check")
    import ceap

    assert mod.versions(f"v{ceap.__version__}") == ceap.__version__
