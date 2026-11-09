"""R4 item 4.9: golden report snapshots and report invariants.

Each golden is the full report of one scenario, run offline (mock model) under the replay clock
(``ceap.harness.replay.injected_clock``), so ids and timestamps are deterministic and the snapshot can
be compared byte for byte. Regenerate after an intended change with ``CEAP_UPDATE_GOLDENS=1`` and review
the diff.

Status for 0.5.0: research goldens are committed. The TCA (investigation) goldens depend on the R2T
re-baseline running in parallel; they are generated at integration on the combined branch
(``CEAP_UPDATE_GOLDENS=1 pytest tests/golden``). Until a TCA golden file exists, its snapshot test is
skipped with that reason, and the invariants still run.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from ceap.config import Settings
from ceap.domain.common import to_jsonable
from ceap.domain.research import ResearchVerdict
from ceap.harness.replay import DeterministicClock, injected_clock
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform, default_request, default_research_request

HERE = Path(__file__).parent
RESEARCH = ["R01", "R03"]
TCA = ["T01", "T05"]
TCA_PENDING = "TCA goldens are regenerated at 0.5.0 integration after the R2T re-baseline (CEAP_UPDATE_GOLDENS=1)"
UPDATE = os.getenv("CEAP_UPDATE_GOLDENS") == "1"


async def _report(dataset: str, store, history) -> dict[str, Any]:
    settings = Settings(llm_provider="mock", auto_approve=True)
    p = Platform(settings=settings, llm=MockLLMClient(), store=store, history=history)
    try:
        with injected_clock(DeterministicClock()):
            if dataset.startswith("R"):
                result = await p.research(default_research_request(dataset))
            else:
                result = await p.investigate(default_request(dataset=dataset))
    finally:
        p.close()
    assert result.report is not None, result.error
    return json.loads(json.dumps(to_jsonable(result.report), sort_keys=True, default=str))


def _check_invariants(report: dict[str, Any]) -> None:
    assert report["title"] and report["executive_summary"].strip() and report["conclusion"].strip()
    evidence_ids = {e["id"] for e in report["evidence"]}
    for finding in report["findings"]:
        assert set(finding.get("supporting_evidence") or []) <= evidence_ids, finding["id"]
        assert 0.0 <= float(finding.get("confidence", 0.0)) <= 1.0

    def finite(v: Any) -> None:
        if isinstance(v, float):
            assert math.isfinite(v)
        elif isinstance(v, dict):
            for x in v.values():
                finite(x)
        elif isinstance(v, list):
            for x in v:
                finite(x)

    finite(report["metrics"])
    if report["kind"] == "research":
        assert report["proposal"]["verdict"] in {v.value for v in ResearchVerdict}


SIG_DIGITS = 9  # floats compared to 9 significant digits: last-ulp noise differs across OS/BLAS builds


def _round_floats(v: Any) -> Any:
    if isinstance(v, float):
        return float(f"{v:.{SIG_DIGITS}g}") if math.isfinite(v) else v
    if isinstance(v, dict):
        # Evidence digests hash raw (unrounded) tool output, so they move with last-ulp noise across OS
        # builds; digest integrity is covered by the evidence tests, the golden pins everything else.
        return {k: ("<digest>" if k == "digest" else _round_floats(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_round_floats(x) for x in v]
    return v


def _compare(name: str, report: dict[str, Any]) -> None:
    path = HERE / f"{name}.json"
    text = json.dumps(_round_floats(report), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")  # LF on every OS: byte-stable goldens
        return
    if not path.exists():
        pytest.skip(TCA_PENDING if name.startswith("tca_") else f"golden {path.name} missing")
    assert path.read_text(encoding="utf-8") == text, (
        f"report drifted from {path.name}; if intended, regenerate with CEAP_UPDATE_GOLDENS=1 and review the diff"
    )


@pytest.mark.parametrize("dataset", RESEARCH)
async def test_research_report_golden(dataset, store, history):
    report = await _report(dataset, store, history)
    _check_invariants(report)
    _compare(f"research_{dataset}", report)


@pytest.mark.parametrize("dataset", TCA)
async def test_tca_report_golden(dataset, store, history):
    report = await _report(dataset, store, history)
    _check_invariants(report)  # always enforced
    _compare(f"tca_{dataset}", report)  # skipped until regenerated at integration


async def test_golden_run_is_deterministic(store, history):
    a = await _report("R01", store, history)
    b = await _report("R01", store, history)
    assert a == b
