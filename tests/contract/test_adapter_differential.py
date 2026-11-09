"""3A.8 adapter equivalence (replaces v1 "mock parity"); never "model fidelity".

The same ten scenario templates run twice: once with the deterministic mock client, once with the
real ``AnthropicLLMClient`` over the real SDK replaying a hand-built cassette. Everything the
harness decides - plan, step outcomes, findings and confidences, evidence, metrics, attribution,
status - must be identical; only narrative text and model names may differ. This proves the
adapter (wire format, parse path, response mapping) is transparent. It says nothing about how a
real model behaves.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

pytest.importorskip("anthropic")

from cassettes.golden import (  # noqa: E402
    NOTE,
    TEMPLATES,
    cassette_path,
    deterministic_uuid4,
    golden_responder,
)
from ceap.llm.client import AnthropicLLMClient, MockLLMClient  # noqa: E402
from ceap.llm.pins import load_pins  # noqa: E402
from ceap.llm.recording import (  # noqa: E402
    CassetteTransport,
    RecordingTransport,
    UnpublishableMetricError,
    assert_consumed,
    assert_publishable,
    build_cassette,
    load_cassette,
    sdk_over,
    write_cassette,
)
from ceap.platform import Platform, default_request  # noqa: E402

UPDATE = os.environ.get("CEAP_UPDATE_CASSETTES") == "1"


def structure(result: Any) -> dict[str, Any]:
    """Everything the harness decided, minus narrative text and model identity."""
    report = result.report
    out = {
        "state": result.state.value,
        "plan": [
            (
                s.type.value,
                s.agent_id,
                s.tool_request.tool_id if s.tool_request else None,
                sorted((s.tool_request.arguments or {}).items()) if s.tool_request else None,
            )
            for s in result.plan.ordered_steps()
        ],
        "steps": sorted((r["step_id"], r["status"]) for r in result.step_results),
        "findings": sorted(
            (f.id, f.statement, f.confidence, tuple(f.supporting_evidence), tuple(f.contradicting_evidence))
            for f in report.findings
        ),
        "evidence": sorted(e.id for e in report.evidence),
        "metrics": report.metrics,
        "attribution": report.attribution,
        "assessments": [
            {k: v for k, v in a.items() if k != "llm_comment"} for a in report.critique.get("assessments", [])
        ],
        "unsupported": report.critique.get("unsupported"),
        "contradictions": report.critique.get("contradictions"),
    }
    # through JSON so NaN compares equal to NaN (an absent metric is NaN on both paths)
    return json.loads(json.dumps(out, sort_keys=True, default=str))


async def _run(store, settings, llm, template: str, monkeypatch) -> Any:
    monkeypatch.setattr(uuid, "uuid4", deterministic_uuid4())
    platform = Platform(settings=settings, llm=llm, store=store)
    return await platform.investigate(default_request("AAPL", template))


@pytest.mark.parametrize("template", TEMPLATES)
async def test_adapter_equivalence_structural_diff_is_empty(template, store, settings, monkeypatch):
    mock_result = await _run(store, settings, MockLLMClient(), template, monkeypatch)
    path = cassette_path(template)
    if UPDATE or not path.exists():
        recorder = RecordingTransport(golden_responder, prefix_purposes=("report", "research_report"))
        await _run(store, settings, AnthropicLLMClient(client=sdk_over(recorder)), template, monkeypatch)
        pins = load_pins()
        write_cassette(
            path,
            build_cassette(
                recorder.recorded,
                provenance="hand_built",
                model="claude-opus-5-5 / claude-sonnet-5-5",
                sdk=f"{pins.sdk_package}=={pins.sdk_version}",
                note=NOTE,
            ),
        )
    cassette = load_cassette(path)
    transport = CassetteTransport(cassette)
    adapter_result = await _run(
        store, settings, AnthropicLLMClient(client=sdk_over(transport)), template, monkeypatch
    )
    assert transport.stale == [], transport.stale
    assert_consumed(transport)  # A19: every recorded interaction replayed
    assert structure(adapter_result) == structure(mock_result)
    assert adapter_result.plan.generated_by.startswith("llm:claude-")
    with pytest.raises(UnpublishableMetricError):
        assert_publishable(cassette, "planner_tool_recall")
