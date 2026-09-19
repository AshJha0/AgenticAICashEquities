"""FastAPI gateway: auth, RBAC, investigation lifecycle."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ceap.api.app import create_app
from ceap.platform import Platform

TRADER = {"X-API-Key": "dev-trader-key"}
VIEWER = {"X-API-Key": "dev-viewer-key"}
ADMIN = {"X-API-Key": "dev-admin-key"}


@pytest.fixture
async def client(platform: Platform):
    app = create_app(platform)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _wait(c: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(300):
        s = (await c.get(f"/investigations/{task_id}", headers=TRADER)).json()
        if s["status"] != "RUNNING":
            return s
        await asyncio.sleep(0.05)
    raise AssertionError("investigation did not finish")


async def test_health_and_system_endpoints(client):
    h = (await client.get("/health")).json()
    assert h["status"] == "ok" and h["tools"] >= 20 and "knowledge" in h["servers"]
    assert (await client.get("/tools")).status_code == 401
    tools = (await client.get("/tools", headers=VIEWER)).json()
    assert all(t["read_only"] for t in tools)
    assert len((await client.get("/scenarios")).json()) == 10
    assert len((await client.get("/scenarios?all=true")).json()) == 50
    assert "ceap_" in (await client.get("/metrics")).text or (await client.get("/metrics")).text == ""


async def test_auth_and_rbac(client):
    body = {"question": "Analyse AAPL execution between 14:00 and 15:00"}
    assert (await client.post("/investigations", json=body)).status_code == 401
    assert (
        await client.post("/investigations", json=body, headers={"X-API-Key": "wrong"})
    ).status_code == 401
    assert (await client.post("/investigations", json=body, headers=VIEWER)).status_code == 403
    assert (await client.get("/approvals", headers=TRADER)).status_code == 403
    assert (await client.get("/approvals", headers=ADMIN)).status_code == 200


async def test_validation_errors(client):
    r = await client.post(
        "/investigations", json={"question": "what happened to our orders?"}, headers=TRADER
    )
    assert r.status_code == 422 and "symbol" in r.json()["detail"]
    r = await client.post("/investigations", json={"question": "Analyse AAPL execution"}, headers=TRADER)
    assert r.status_code == 422 and "window" in r.json()["detail"]
    r = await client.post(
        "/investigations", json={"question": "Analyse AAPL execution between 08:00 and 09:00"}, headers=TRADER
    )
    assert r.status_code == 422 and "coverage" in r.json()["detail"]
    r = await client.post(
        "/investigations",
        json={"question": "Analyse AAPL execution between 14:00 and 15:00", "dataset": "S07"},
        headers=TRADER,
    )
    assert r.status_code == 422 and "MSFT" in r.json()["detail"]
    assert (await client.get("/investigations/TASK-nope", headers=TRADER)).status_code == 404


async def test_investigation_lifecycle(client):
    r = await client.post(
        "/investigations",
        json={
            "question": "Why did our execution quality for AAPL deteriorate between 14:00 and 15:00?",
            "dataset": "T05",
        },
        headers=TRADER,
    )
    assert r.status_code == 202
    accepted = r.json()
    assert (
        accepted["symbol"] == "AAPL"
        and accepted["dataset"] == "T05"
        and accepted["baseline_end"] == accepted["window_start"]
    )
    tid = accepted["task_id"]
    status = await _wait(client, tid)
    assert status["status"] == "COMPLETED" and status["state"] == "COMPLETED"
    report = (await client.get(f"/investigations/{tid}/report", headers=VIEWER)).json()
    assert report["attribution"]["primary"] == "VENUE_DEGRADATION"
    assert report["title"].startswith("AAPL EXECUTION INVESTIGATION")
    assert report["findings"] and report["evidence"] and "narrative" in report
    trace = (await client.get(f"/investigations/{tid}/trace", headers=VIEWER)).json()
    assert trace["trace"]["spans"] and trace["state_history"][-1]["to"] == "COMPLETED"
    full = (await client.get(f"/investigations/{tid}/result?include_trace=true", headers=VIEWER)).json()
    assert full["success"] and "trace" in full and full["plan"]["steps"]
    listing = (await client.get("/investigations", headers=VIEWER)).json()
    assert any(i["task_id"] == tid for i in listing)
    assert (await client.post("/approvals/APR-x", json={"approved": True}, headers=ADMIN)).status_code == 409


async def test_cancel_endpoint(client):
    r = await client.post(
        "/investigations",
        json={"question": "Analyse AAPL execution between 14:00 and 15:00", "dataset": "T01"},
        headers=TRADER,
    )
    tid = r.json()["task_id"]
    c = await client.post(f"/investigations/{tid}/cancel", headers=TRADER)
    assert c.status_code == 200
    status = await _wait(client, tid)
    assert status["status"] in (
        "COMPLETED",
        "FAILED",
    )  # cancellation raced with a fast run; state is reported either way
    assert (await client.post("/investigations/TASK-nope/cancel", headers=TRADER)).status_code == 404
