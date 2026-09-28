"""FastAPI gateway: the research lifecycle, RBAC, validation and the queued approval flow."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ceap.api.app import create_app
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform
from ceap.policy.approvals import QueuedApprovalGateway

QUANT = {"X-API-Key": "dev-quant-key"}
TRADER = {"X-API-Key": "dev-trader-key"}
VIEWER = {"X-API-Key": "dev-viewer-key"}
ADMIN = {"X-API-Key": "dev-admin-key"}


@pytest.fixture
async def client(platform: Platform):
    app = create_app(platform)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest.fixture
async def queued_client(platform: Platform, store, settings):
    p = Platform(
        settings=settings,
        llm=MockLLMClient(),
        store=store,
        history=platform.history,
        approvals=QueuedApprovalGateway(timeout_seconds=20.0),
    )
    app = create_app(p)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _wait(c: httpx.AsyncClient, task_id: str, headers: dict[str, str] = QUANT) -> dict:
    for _ in range(600):
        s = (await c.get(f"/research/{task_id}", headers=headers)).json()
        if s["status"] != "RUNNING":
            return s
        await asyncio.sleep(0.05)
    raise AssertionError("research did not finish")


async def _pending_approval(c: httpx.AsyncClient, step_id: str) -> dict:
    for _ in range(600):
        items = (await c.get("/approvals", headers=ADMIN)).json()
        for item in items:
            if item["step_id"] == step_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"no pending approval for {step_id}")


async def test_research_rbac_and_validation(client):
    body = {"question": "Does 12-1 momentum work on R01?"}
    assert (await client.post("/research", json=body)).status_code == 401
    assert (await client.post("/research", json=body, headers=TRADER)).status_code == 403
    assert (await client.post("/research", json=body, headers=VIEWER)).status_code == 403
    staged = await client.post("/research", json={**body, "stage_orders": True}, headers=QUANT)
    assert staged.status_code == 403 and "trading:execute" in staged.json()["detail"]
    r = await client.post("/research", json={**body, "signal": "nope"}, headers=QUANT)
    assert r.status_code == 422 and "unknown signal" in r.json()["detail"]
    r = await client.post("/research", json={**body, "dataset": "R99"}, headers=QUANT)
    assert r.status_code == 422 and "unknown research dataset" in r.json()["detail"]
    r = await client.post("/research", json={**body, "in_sample_end": "2026-09-18"}, headers=QUANT)
    assert r.status_code == 422 and "in_sample_end" in r.json()["detail"]
    assert (await client.post("/research", json={**body, "rebalance_days": 0}, headers=QUANT)).status_code == 422
    assert (await client.get("/research/TASK-nope", headers=QUANT)).status_code == 404
    assert (await client.get("/research", headers=TRADER)).status_code == 200  # research:read
    assert len((await client.get("/research-scenarios")).json()) == 7
    assert len((await client.get("/research-scenarios?all=true")).json()) == 21


async def test_research_lifecycle_with_auto_approval(client):
    r = await client.post("/research", json={"question": "Does 12-1 momentum work on R01?"}, headers=QUANT)
    assert r.status_code == 202
    accepted = r.json()
    assert accepted["signal"] == "momentum_12_1" and accepted["dataset"] == "R01" and accepted["rebalance_days"] == 21
    assert accepted["links"]["report"] == f"/research/{accepted['task_id']}/report"
    task_id = accepted["task_id"]
    assert (await client.get(f"/research/{task_id}/report", headers=QUANT)).status_code in (409, 200)
    status = await _wait(client, task_id)
    assert status["status"] == "COMPLETED" and status["warnings"] == []
    report = (await client.get(f"/research/{task_id}/report", headers=QUANT)).json()
    assert report["kind"] == "research" and report["proposal"]["verdict"] == "PROMOTE"
    assert report["proposal"]["approval"]["approved"] and "VERDICT" in report["narrative"]
    assert report["critique"]["narrative_number_warnings"] == []
    result = (await client.get(f"/research/{task_id}/result", headers=VIEWER)).json()
    assert result["success"] and result["report"]["kind"] == "research"
    trace = (await client.get(f"/research/{task_id}/trace", headers=QUANT)).json()
    assert any(s["step_id"] == "gov-approval" and s["status"] == "APPROVED" for s in trace["step_results"])
    listed = (await client.get("/research", headers=QUANT)).json()
    assert any(item["task_id"] == task_id for item in listed)
    # the two namespaces do not leak into each other
    assert (await client.get(f"/investigations/{task_id}", headers=QUANT)).status_code == 404
    assert not any(item["task_id"] == task_id for item in (await client.get("/investigations", headers=QUANT)).json())


async def test_research_parses_signal_and_dataset_from_the_question(client):
    r = await client.post(
        "/research", json={"question": "Evaluate short-term reversal on RS08 with weekly rebalancing"}, headers=QUANT
    )
    assert r.status_code == 202
    accepted = r.json()
    assert accepted["signal"] == "reversal_5" and accepted["dataset"] == "RS08" and accepted["rebalance_days"] == 5
    assert (await _wait(client, accepted["task_id"]))["status"] == "COMPLETED"


async def test_admin_can_stage_orders_through_the_api(client):
    r = await client.post(
        "/research", json={"question": "Promote momentum on R01 and stage the orders", "stage_orders": True}, headers=ADMIN
    )
    assert r.status_code == 202 and r.json()["stage_orders"] is True
    task_id = r.json()["task_id"]
    assert (await _wait(client, task_id, ADMIN))["status"] == "COMPLETED"
    report = (await client.get(f"/research/{task_id}/report", headers=ADMIN)).json()
    staging = report["proposal"]["staging"]
    assert staging["staged"] and staging["count"] == 20 and staging["staging_id"].startswith("STG-")


async def test_queued_approval_flow_shows_the_proposal_and_decides_it(queued_client):
    c = queued_client
    r = await c.post("/research", json={"question": "Does momentum work on R01?"}, headers=QUANT)
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    pending = await _pending_approval(c, "gov-approval")
    assert pending["task_id"] == task_id and pending["tool_id"] == "harness.human_approval"
    assert pending["arguments"]["proposal"]["verdict"] == "PROMOTE"
    assert pending["arguments"]["target_portfolio"]["names"] == 20 and pending["arguments"]["signal"] == "momentum_12_1"
    assert (await c.post(f"/approvals/{pending['id']}", json={"approved": True}, headers=QUANT)).status_code == 403
    decided = await c.post(f"/approvals/{pending['id']}", json={"approved": True, "comment": "ship it"}, headers=ADMIN)
    assert decided.status_code == 200 and decided.json()["approved"] is True
    assert (await _wait(c, task_id))["status"] == "COMPLETED"
    report = (await c.get(f"/research/{task_id}/report", headers=QUANT)).json()
    approval = report["proposal"]["approval"]
    assert approval["approved"] and approval["decided_by"].startswith("admin:") and approval["comment"] == "ship it"
    assert "ship it" in report["narrative"]

    r = await c.post("/research", json={"question": "Does momentum work on R04?"}, headers=QUANT)
    task_id = r.json()["task_id"]
    pending = await _pending_approval(c, "gov-approval")
    assert pending["arguments"]["proposal"]["verdict"] == "REJECT"
    await c.post(f"/approvals/{pending['id']}", json={"approved": False, "comment": "no"}, headers=ADMIN)
    status = await _wait(c, task_id)
    assert status["status"] == "FAILED" and "human approval declined" in status["error"]
    assert (await c.get(f"/research/{task_id}/report", headers=QUANT)).status_code == 422
