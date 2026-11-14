"""0.10.0 H5 (R-03): one report never gives two values of one delta under the same baseline name.

Acceptance: T06 golden: summary and observations use named baselines; the check fires on a planted
contradiction.

At 0.9.0 the T06 report said "implementation shortfall delta -3.7 bps" in the summary (attribution,
against the matched peer windows) and "decreased by 28.6 bps relative to the baseline's +13.9 bps" in
an observation (execution agent, against the prior window), both under "the baseline".
"""

from __future__ import annotations

from ceap.agents.critic_checks import BASELINE_FLAG, baseline_conflicts
from ceap.analytics.attribution import MATCHED_PEERS, PRIOR_WINDOW
from ceap.config import Settings
from ceap.domain.findings import Finding
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform, default_request


async def test_t06_summary_and_observations_name_their_baselines(store, history):
    p = Platform(
        settings=Settings(llm_provider="mock", auto_approve=True, db_path=":memory:"),
        llm=MockLLMClient(),
        store=store,
        history=history,
    )
    try:
        result = await p.investigate(default_request("AAPL", "T06"))
    finally:
        p.close()
    assert result.report is not None, result.error
    text = result.report.narrative
    summary = text.split("EXECUTIVE SUMMARY", 1)[1].split("PRIMARY OBSERVATIONS", 1)[0]
    observations = text.split("PRIMARY OBSERVATIONS", 1)[1].split("\nEXECUTION\n", 1)[0]

    # the summary's deltas are the attribution's, against the matched peer windows, and say so
    assert f"versus the {MATCHED_PEERS} (implementation shortfall delta -3.7 bps" in summary
    # the observations' deltas are the execution agent's, against the prior window, and say so
    assert f"decreased by 28.6 bps relative to the {PRIOR_WINDOW}'s +13.9 bps" in observations
    assert f"({PRIOR_WINDOW} +0.3 bps, delta +0.5 bps)" in observations
    # no delta line is left under the bare word
    for line in (summary + observations).splitlines():
        if "delta" in line or " by " in line and "bps" in line:
            assert "the baseline" not in line, line
    critique = result.agent_outputs["critic"]["critique"]
    assert not [c for c in critique["contradictions"] if c.startswith(BASELINE_FLAG)]


def _finding(value: float, baseline: str | None) -> Finding:
    attrs = {"is_delta_bps": value, **({"baseline": baseline} if baseline else {})}
    return Finding.create(
        "planted", ("EV-1",), 0.9, category="EXECUTION", produced_by="execution", attributes=attrs
    )


def test_the_check_fires_on_a_planted_contradiction():
    attribution = {"is_delta_bps": -3.7, "vwap_slippage_delta_bps": 0.0}
    # the 0.9.0 defect: the execution figure printed under the attribution's baseline name
    planted = _finding(-28.6, MATCHED_PEERS)
    conflicts = baseline_conflicts([planted], attribution, MATCHED_PEERS)
    assert len(conflicts) == 1 and "-28.6" in conflicts[0] and "-3.7" in conflicts[0]
    # an unnamed baseline on both sides is the same name too
    assert baseline_conflicts([_finding(-28.6, None)], attribution, None)
    # named apart, or agreeing to the printed precision, is not a conflict
    assert baseline_conflicts([_finding(-28.6, PRIOR_WINDOW)], attribution, MATCHED_PEERS) == []
    assert baseline_conflicts([_finding(-3.72, MATCHED_PEERS)], attribution, MATCHED_PEERS) == []
