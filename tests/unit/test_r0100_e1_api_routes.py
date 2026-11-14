"""HTTP gateway behaviour below the happy path (0.10.0 E1 coverage for ``ceap.api``).

Status, report, result and trace for every task state (pending, running, failed, restored from
SQLite), ownership and kind scoping, cancel rules, idempotency keys, the per-principal rate limit,
request validation, approvals under auto-approve and the research request guards. Tasks are
registered through the platform rather than run, so no investigation executes here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from ceap.api import routes
from ceap.api.app import create_app
from ceap.api.auth import hash_api_key, principal_label
from ceap.api.parsing import LONDON
from ceap.config import Settings
from ceap.llm.client import MockLLMClient
from ceap.platform import InvestigationRequest, Platform

TRADER = {"X-API-Key": "dev-trader-key"}
QUANT = {"X-API-Key": "dev-quant-key"}
ADMIN = {"X-API-Key": "dev-admin-key"}
AUDITOR = {"X-API-Key": "dev-auditor-key"}
APPROVER = {"X-API-Key": "dev-approver-key"}
VIEWER = {"X-API-Key": "dev-viewer-key"}
TRADER_NAME = principal_label("trader", "dev-trader-key")
VALID_Q = "Analyse AAPL execution between 14:00 and 15:00"


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def api(platform: Platform):
    app = create_app(platform)
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            yield c, platform, app


def _register(p: Platform, owner: str = TRADER_NAME) -> str:
    start = datetime(2026, 3, 2, 14, 0, tzinfo=LONDON)
    req = InvestigationRequest(VALID_Q, "AAPL", start, start + timedelta(hours=1), principal=owner)
    return p.build_task(req).id


async def test_task_lifecycle_views_pending_failed_and_restored(api):
    c, p, _ = api
    tid = _register(p)
    assert (await c.get(f"/investigations/{tid}", headers=TRADER)).json()["status"] == "PENDING"
    for suffix in ("report", "result", "trace"):
        r = await c.get(f"/investigations/{tid}/{suffix}", headers=TRADER)
        assert r.status_code == 409 and r.json()["detail"] == "investigation is pending"
    listed = (await c.get("/investigations", headers=TRADER)).json()
    assert [(row["task_id"], row["question"]) for row in listed] == [(tid, VALID_Q)]

    p.record_failure(tid, "data feed unavailable")
    st = (await c.get(f"/investigations/{tid}", headers=TRADER)).json()
    assert st["status"] == "FAILED" and st["error"] == "data feed unavailable"
    r = await c.get(f"/investigations/{tid}/report", headers=TRADER)
    assert r.status_code == 422 and r.json()["detail"] == "investigation failed: data feed unavailable"
    trace = (await c.get(f"/investigations/{tid}/trace", headers=TRADER)).json()
    assert set(trace) == {"trace", "state_history", "policy_log", "step_results"}
    in_memory = (await c.get(f"/investigations/{tid}/result", headers=TRADER)).json()
    assert in_memory["error"] == "data feed unavailable"

    # a restart loses the in-memory copy; status and /result come from SQLite, /report cannot
    p.results.pop(tid)
    p.tasks.pop(tid)
    assert (await c.get(f"/investigations/{tid}", headers=TRADER)).json()["status"] == "FAILED"
    r = await c.get(f"/investigations/{tid}/report", headers=TRADER)
    assert r.status_code == 404 and "not in memory" in r.json()["detail"]
    stored = (await c.get(f"/investigations/{tid}/result", headers=TRADER)).json()
    assert stored.pop("schema_version") == 1 and stored == in_memory


async def test_running_task_and_unknown_ids(api):
    c, p, app = api
    tid = _register(p)
    app.state.ceap.running[tid] = asyncio.get_running_loop().create_future()  # stands in for the task
    try:
        assert (await c.get(f"/investigations/{tid}", headers=TRADER)).json()["status"] == "RUNNING"
        r = await c.get(f"/investigations/{tid}/report", headers=TRADER)
        assert r.status_code == 409 and r.json()["detail"] == "investigation is running"
    finally:
        app.state.ceap.running.pop(tid).cancel()
    r = await c.get("/investigations/nope", headers=TRADER)
    assert r.status_code == 404 and r.json()["detail"] == "unknown investigation nope"
    r = await c.get(f"/research/{tid}", headers=TRADER)  # an investigation id is not a research id
    assert r.status_code == 404 and r.json()["detail"] == f"unknown research {tid}"
    p.record_failure(tid, "x")
    p.results.pop(tid)
    p.tasks.pop(tid)
    p.db.run(lambda conn: conn.execute("DELETE FROM results WHERE task_id=?", (tid,)))
    r = await c.get(f"/investigations/{tid}/result", headers=TRADER)
    assert r.status_code == 404 and r.json()["detail"] == f"no stored result for investigation {tid} (FAILED)"


async def test_other_principals_tasks_are_invisible_unless_read_any(api):
    c, p, _ = api
    tid = _register(p, owner="trader:someone")
    r = await c.get(f"/investigations/{tid}", headers=TRADER)
    assert r.status_code == 404  # 404, not 403: the id must not be confirmable by probing
    assert (await c.get("/investigations", headers=TRADER)).json() == []
    assert (await c.get(f"/investigations/{tid}", headers=AUDITOR)).json()["status"] == "PENDING"
    assert [row["task_id"] for row in (await c.get("/investigations", headers=AUDITOR)).json()] == [tid]


async def test_cancel_rules(api):
    c, p, _ = api
    r = await c.post("/investigations/nope/cancel", headers=TRADER)
    assert r.status_code == 404
    mine = _register(p)
    assert (await c.post(f"/research/{mine}/cancel", headers=ADMIN)).status_code == 404  # wrong kind
    r = await c.post(f"/investigations/{mine}/cancel", headers=TRADER)
    assert r.status_code == 200 and r.json()["task_id"] == mine
    assert p.cancellations[mine].is_cancelled
    theirs = _register(p, owner="trader:someone")
    r = await c.post(f"/investigations/{theirs}/cancel", headers=TRADER)
    assert (
        r.status_code == 403 and r.json()["detail"] == "only the owner or an admin may cancel a investigation"
    )
    assert not p.cancellations[theirs].is_cancelled
    assert (await c.post(f"/investigations/{theirs}/cancel", headers=ADMIN)).status_code == 200
    assert p.cancellations[theirs].is_cancelled


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"question": VALID_Q, "session_date": "2026-13-40"}, "invalid session_date"),
        (
            {
                "question": "Analyse AAPL execution",
                "window_start": "2026-03-02T15:00:00",
                "window_end": "2026-03-02T14:00:00",
            },
            "window_end must be after window_start",
        ),
        (
            {
                "question": "Analyse AAPL execution",
                "window_start": "2026-03-02T09:30:00",
                "window_end": "2026-03-02T18:00:00",
            },
            "window longer than 8 hours",
        ),
    ],
)
async def test_investigation_request_validation(api, body, detail):
    c, p, _ = api
    r = await c.post("/investigations", json=body, headers=TRADER)
    assert r.status_code == 422 and detail in r.json()["detail"]
    assert not p.tasks  # nothing was registered


async def test_idempotency_key_validation_and_body_mismatch(api):
    c, p, _ = api
    r = await c.post(
        "/investigations", json={"question": VALID_Q}, headers={**TRADER, "Idempotency-Key": "bad key!"}
    )
    assert r.status_code == 422 and "Idempotency-Key must be" in r.json()["detail"]
    body = {"question": VALID_Q}
    p.idempotency.put(TRADER_NAME, "k-1", p.idempotency.body_hash({"x": 1}), "t-old", {"task_id": "t-old"})
    r = await c.post("/investigations", json=body, headers={**TRADER, "Idempotency-Key": "k-1"})
    assert r.status_code == 422 and "different request body" in r.json()["detail"]


async def test_idempotent_replay_returns_first_response_without_new_task(api):
    c, p, _ = api
    body = {"question": VALID_Q, "symbol": "AAPL"}
    from ceap.api.models import InvestigationCreate

    first = {
        "task_id": "t-first",
        "status": "RUNNING",
        "symbol": "AAPL",
        "window_start": "2026-03-02T14:00:00-05:00",
        "window_end": "2026-03-02T15:00:00-05:00",
        "baseline_start": "2026-03-02T13:00:00-05:00",
        "baseline_end": "2026-03-02T14:00:00-05:00",
        "dataset": "T01",
        "links": {},
    }
    h = p.idempotency.body_hash(InvestigationCreate(**body).model_dump(mode="json"))
    p.idempotency.put(TRADER_NAME, "k-2", h, "t-first", first)
    r = await c.post("/investigations", json=body, headers={**TRADER, "Idempotency-Key": "k-2"})
    assert r.status_code == 202 and r.json()["task_id"] == "t-first"
    assert not p.tasks


async def test_rate_limit_answers_429_with_retry_after(platform, monkeypatch):
    monkeypatch.setenv("CEAP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("CEAP_RATE_LIMIT_BURST", "1")
    app = create_app(platform)
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            bad = {"question": VALID_Q, "session_date": "not-a-date"}
            assert (
                await c.post("/investigations", json=bad, headers=TRADER)
            ).status_code == 422  # token used
            r = await c.post("/investigations", json=bad, headers=TRADER)
            assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
            assert r.json()["detail"].startswith(f"rate limit exceeded for {TRADER_NAME}")
            # another principal has its own bucket
            r = await c.post(
                "/research", json={"question": "momentum on R01", "signal": "nope"}, headers=QUANT
            )
            assert r.status_code == 422


async def test_research_guards_and_scenarios(api):
    c, p, _ = api
    scen = (await c.get("/research-scenarios")).json()
    assert scen and {"id", "signal", "expected_verdict", "rebalance_days"} <= set(scen[0])
    assert len((await c.get("/research-scenarios?all=true")).json()) > len(scen)
    r = await c.post("/research", json={"question": "does this work?", "signal": "astrology"}, headers=QUANT)
    assert r.status_code == 422 and "unknown signal astrology" in r.json()["detail"]
    r = await c.post("/research", json={"question": "momentum on R01", "stage_orders": True}, headers=QUANT)
    assert r.status_code == 403 and "staging orders requires" in r.json()["detail"]
    assert (
        await c.post("/research", json={"question": "momentum on R01"}, headers=TRADER)
    ).status_code == 403
    assert not p.tasks
    assert (await c.get("/research", headers=QUANT)).json() == []


async def test_approvals_under_auto_approve(api):
    c, _, _ = api
    r = await c.post("/approvals/a-1", json={"approved": True}, headers=APPROVER)
    assert r.status_code == 409 and "automatic in this environment" in r.json()["detail"]
    r = await c.get("/approvals/a-1", headers=APPROVER)
    assert r.status_code == 404 and r.json()["detail"] == "no pending approval a-1"


async def test_metrics_content_type_and_status_shape(api):
    c, p, _ = api
    r = await c.get("/metrics", headers=AUDITOR)
    assert r.headers["content-type"] == routes.PROMETHEUS_CONTENT_TYPE
    s = (await c.get("/status", headers=VIEWER)).json()
    assert s["environment"] == p.settings.environment and s["running"] == 0 and s["llm"] == p.llm.name


async def test_health_without_a_platform_is_plain_liveness():
    app = create_app()  # no lifespan run: app.state has no platform
    async with await _client(app) as c:
        h = (await c.get("/health")).json()
    assert h["status"] == "ok" and h.get("write_probe_ok") is None


async def test_expired_and_missing_keys_are_401(store):
    expired = "an-expired-key-value"
    settings = Settings(
        llm_provider="mock", auto_approve=True, api_key_hashes=f"{hash_api_key(expired)}:trader:2020-01-01"
    )
    app = create_app(Platform(settings=settings, llm=MockLLMClient(), store=store))
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            r = await c.get("/investigations", headers={"X-API-Key": expired})
            assert r.status_code == 401 and r.json()["detail"] == "API key expired"
            assert r.headers["www-authenticate"] == "ApiKey"
            r = await c.get("/investigations")
            assert r.status_code == 401 and r.json()["detail"] == "missing X-API-Key header"


def _platform_stub():
    failures: dict[str, str] = {}
    return SimpleNamespace(
        results={}, record_failure=lambda tid, err: failures.__setitem__(tid, err)
    ), failures


async def test_on_done_records_crash_and_shutdown_cancel_once():
    p, failures = _platform_stub()
    state = SimpleNamespace(running={})

    async def boom():
        raise RuntimeError("disk full")

    crashed = asyncio.create_task(boom())
    state.running["t1"] = crashed
    await asyncio.gather(crashed, return_exceptions=True)
    routes._on_done(state, p, "t1", crashed)
    assert failures == {"t1": "RuntimeError: disk full"} and state.running == {}

    sleeper = asyncio.create_task(asyncio.sleep(10))
    sleeper.cancel()
    await asyncio.gather(sleeper, return_exceptions=True)
    routes._on_done(state, p, "t2", sleeper)
    assert failures["t2"] == "investigation task cancelled during shutdown"

    p.results["t3"] = object()  # a result already exists: never overwritten by the observer
    routes._on_done(state, p, "t3", crashed)
    sleeper2 = asyncio.create_task(asyncio.sleep(10))
    sleeper2.cancel()
    await asyncio.gather(sleeper2, return_exceptions=True)
    routes._on_done(state, p, "t3", sleeper2)
    assert "t3" not in failures


def test_aware_assumes_the_desk_clock_for_naive_times():
    naive = datetime(2026, 3, 2, 14, 0)
    assert routes._aware(None) is None
    assert routes._aware(naive).tzinfo is LONDON
    aware = datetime(2026, 3, 2, 14, 0, tzinfo=LONDON)
    assert routes._aware(aware) is aware
