"""R2B wiring: change-point findings in the engineering agent, the alpha combination tool."""

from __future__ import annotations

from ceap.platform import default_request


async def test_t06_change_point_finding_names_feature_onset_and_deployment(platform):
    result = await platform.investigate(default_request("AAPL", "T06"))
    cps = [f for f in result.findings if f.attributes.get("change_point")]
    assert cps, [f.statement for f in result.findings]
    f = next(f for f in cps if f.produced_by == "engineering")
    assert f.attributes["change_onset"].startswith("2") and "T14:00" in f.attributes["change_onset"]
    assert "8 minutes after deployment CHG-40388" in f.statement
    assert f.attributes["model_hash"]
    ids = {e.id for e in result.evidence}
    assert set(f.supporting_evidence) <= ids
    calc = next(e for e in result.evidence if e.id in f.supporting_evidence and e.attributes.get("change_points"))
    assert calc.attributes["label"].startswith("fitted to synthetic generator ")
    # the change point is extra evidence: the attribution is unchanged
    assert result.report.attribution["primary"] == "TECHNOLOGY_LATENCY"


async def test_clean_scenario_has_no_change_point_finding(platform):
    result = await platform.investigate(default_request("AAPL", "T01"))
    assert not [f for f in result.findings if f.attributes.get("change_point")]


async def test_alpha_combine_signals_tool(mcp_client):
    out = await mcp_client.invoke("alpha", "combine_signals", {"dataset": "R01"})
    assert out["label"].startswith("fitted to synthetic generator ")
    assert out["n_trials"] >= 9 and out["gross_of_costs"] is True
    assert {"combination", "momentum_12_1", "reversal_5", "low_vol_60"} == set(out["out_of_sample"])
