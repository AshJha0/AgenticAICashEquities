"""R4 items 4.2 to 4.8: restart and approval semantics, ownership, idempotency and limits, the staging
TOCTOU check, authentication hygiene, replay and observability."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest

from ceap.api.app import create_app
from ceap.api.auth import hash_api_key
from ceap.config import Settings, SettingsError, _parse_api_keys
from ceap.llm.client import MockLLMClient
from ceap.mcp.common import MAX_PAGE, paginate
from ceap.observability.logging import JsonFormatter, RequestIdFilter
from ceap.observability.metrics import MetricsRegistry, metrics
from ceap.persistence.db import Database
from ceap.persistence.repositories import SqlApprovalStore
from ceap.platform import Platform, default_research_request
from ceap.policy.approvals import (
    ApprovalConflictError,
    ApprovalRequest,
    ConcurrencySlot,
    QueuedApprovalGateway,
)

ROOT = Path(__file__).resolve().parents[2]
TRADER = {"X-API-Key": "dev-trader-key"}
QUANT = {"X-API-Key": "dev-quant-key"}
VIEWER = {"X-API-Key": "dev-viewer-key"}
AUDITOR = {"X-API-Key": "dev-auditor-key"}
ADMIN = {"X-API-Key": "dev-admin-key"}
APPROVER = {"X-API-Key": "dev-approver-key"}
INV_BODY = {"question": "Analyse AAPL execution between 14:00 and 15:00", "dataset": "T01"}


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    env["CEAP_LLM_PROVIDER"] = "mock"
    return env


@pytest.fixture
async def api(store, history):
    async def make(**overrides):
        settings = Settings(llm_provider="mock", auto_approve=overrides.pop("auto_approve", True), **overrides)
        p = Platform(settings=settings, llm=MockLLMClient(), store=store, history=history)
        app = create_app(p)
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        opened.append((ctx, client, p))
        return client, p

    opened: list = []
    yield make
    for ctx, client, p in opened:
        await client.aclose()
        await ctx.__aexit__(None, None, None)
        p.close()


async def _wait_done(c: httpx.AsyncClient, path: str, headers: dict, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = (await c.get(path, headers=headers)).json()
        if s.get("status") not in ("RUNNING", "PENDING"):
            return s
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} did not finish")


def _approval(task: str = "TASK-1", by: str = "quant:1") -> ApprovalRequest:
    return ApprovalRequest.create(task, "gov-approval", "harness.human_approval", {"x": 1}, "approve?", by)


# ------------------------------------------------------------------ 4.2
_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from ceap.config import Settings
    from ceap.llm.client import MockLLMClient
    from ceap.platform import Platform, default_research_request

    async def main():
        settings = Settings(llm_provider="mock", auto_approve=False, db_path=sys.argv[1], approval_timeout_s=600)
        p = Platform(settings=settings, llm=MockLLMClient())
        req = default_research_request("R01")
        task = p.build_research_task(req)
        asyncio.get_running_loop().create_task(p.research(req, task))
        while not p.approvals.pending():
            await asyncio.sleep(0.05)
        print("READY", task.id, p.approvals.pending()[0].id, flush=True)
        await asyncio.sleep(3600)

    asyncio.run(main())
    """
)


@pytest.mark.slow
async def test_sigkill_with_pending_approval_then_restart_interrupts_task_and_expires_approval(tmp_path, history):
    db_path = tmp_path / "ceap.db"
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(db_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_child_env(), text=True,
    )
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        assert line.startswith("READY"), (line, proc.stderr.read() if proc.stderr else "")
        _, task_id, approval_id = line.split()
    finally:
        proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no shutdown hooks run
        proc.wait(timeout=30)

    settings = Settings(llm_provider="mock", auto_approve=False, db_path=str(db_path))
    p = Platform(settings=settings, llm=MockLLMClient(), history=history)
    try:
        assert p.task_repo.get(task_id)["status"] == "INTERRUPTED"
        assert p.approval_store.get(approval_id)["status"] == "EXPIRED"
        assert p.recovered == {"interrupted": [task_id], "expired": [approval_id]}
        # a decision posted after the restart is refused (409 at the API) and audited
        with pytest.raises(ApprovalConflictError):
            p.approvals.decide(approval_id, True, "approver:x")
        actions = [r["action"] for r in p.db.query("SELECT action FROM audit_log ORDER BY seq")]
        assert {"task.interrupted", "approval.expired", "approval.late_decide"} <= set(actions)
        assert p.db.verify_chain().ok
    finally:
        p.close()


async def test_late_decide_returns_409_and_is_audited(api):
    client, p = await api(auto_approve=False)
    gateway = QueuedApprovalGateway(0.05, store=p.approval_store, audit=p.audit)
    p.approvals = gateway
    decision = await gateway.request(_approval())
    assert decision.decided_by == "timeout" and not decision.approved
    approval_id = decision.request_id
    r = await client.post(f"/approvals/{approval_id}", json={"approved": True}, headers=APPROVER)
    assert r.status_code == 409 and "EXPIRED" in r.json()["detail"]
    rows = p.db.query("SELECT action, subject FROM audit_log WHERE action='approval.late_decide'")
    assert rows == [{"action": "approval.late_decide", "subject": approval_id}]
    assert (await client.post("/approvals/APR-none", json={"approved": True}, headers=APPROVER)).status_code == 404


async def _race_once(db: Database | None) -> None:
    """Two approvers decide the same approval at the same instant from two threads."""
    gateway = QueuedApprovalGateway(30, store=SqlApprovalStore(db) if db else None)
    approval = _approval()
    waiter = asyncio.create_task(gateway.request(approval))
    while not gateway.pending():
        await asyncio.sleep(0)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def decide(who: str, approved: bool) -> None:
        barrier.wait()
        try:
            outcomes.append(gateway.decide(approval.id, approved, who))
        except ApprovalConflictError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=decide, args=(f"approver:{i}", i == 0)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [o for o in outcomes if not isinstance(o, Exception)]
    losers = [o for o in outcomes if isinstance(o, ApprovalConflictError)]
    assert len(winners) == 1 and len(losers) == 1
    final = await asyncio.wait_for(waiter, 5)
    assert final == winners[0]
    if db is not None:
        expected = "APPROVED" if final.approved else "REJECTED"
        assert SqlApprovalStore(db).status(approval.id) == expected


@pytest.mark.parametrize("store_kind", ["sqlite", "memory"])
async def test_two_concurrent_decides_exactly_one_wins(tmp_path, store_kind):
    db = Database(tmp_path / "race.db") if store_kind == "sqlite" else None
    try:
        for _ in range(10):
            await _race_once(db)
    finally:
        if db is not None:
            db.close()


# ------------------------------------------------------------------ 4.3
async def test_viewer_cannot_read_another_principals_task_auditor_can(api):
    client, _ = await api()
    tid = (await client.post("/investigations", json=INV_BODY, headers=TRADER)).json()["task_id"]
    await _wait_done(client, f"/investigations/{tid}", TRADER)
    for path in ("", "/report", "/result", "/trace"):
        assert (await client.get(f"/investigations/{tid}{path}", headers=VIEWER)).status_code == 404
        assert (await client.get(f"/investigations/{tid}{path}", headers=AUDITOR)).status_code == 200
    assert all(i["task_id"] != tid for i in (await client.get("/investigations", headers=VIEWER)).json())
    assert any(i["task_id"] == tid for i in (await client.get("/investigations", headers=AUDITOR)).json())
    # the auditor reads but cannot act
    assert (await client.post("/investigations", json=INV_BODY, headers=AUDITOR)).status_code == 403
    assert (await client.post(f"/investigations/{tid}/cancel", headers=AUDITOR)).status_code == 403


async def test_status_and_stored_result_survive_a_restart(api, tmp_path, store, history):
    db_path = str(tmp_path / "persist.db")
    client, p = await api(db_path=db_path)
    tid = (await client.post("/investigations", json=INV_BODY, headers=TRADER)).json()["task_id"]
    assert (await _wait_done(client, f"/investigations/{tid}", TRADER))["status"] == "COMPLETED"
    client2, _ = await api(db_path=db_path + "-other")  # unrelated instance: knows nothing
    assert (await client2.get(f"/investigations/{tid}", headers=TRADER)).status_code == 404
    p.close()
    settings = Settings(llm_provider="mock", auto_approve=True, db_path=db_path)
    p2 = Platform(settings=settings, llm=MockLLMClient(), store=store, history=history)
    app = create_app(p2)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get(f"/investigations/{tid}", headers=TRADER)).json()["status"] == "COMPLETED"
            assert (await c.get(f"/investigations/{tid}/result", headers=TRADER)).json()["task_id"] == tid
            assert (await c.get(f"/investigations/{tid}", headers=VIEWER)).status_code == 404
    p2.close()


# ------------------------------------------------------------------ 4.4
async def test_four_pending_approvals_do_not_block_a_fifth_task():
    semaphore = asyncio.Semaphore(4)
    gateway = QueuedApprovalGateway(30)

    async def parked(n: int) -> bool:
        async with ConcurrencySlot(semaphore):
            return (await gateway.request(_approval(f"TASK-{n}"))).approved

    waiting = [asyncio.create_task(parked(n)) for n in range(4)]
    while len(gateway.pending()) < 4:
        await asyncio.sleep(0.01)

    async def fifth() -> str:
        async with ConcurrencySlot(semaphore):
            return "ran"

    assert await asyncio.wait_for(fifth(), 2) == "ran"  # would deadlock with the 0.4.0 semaphore
    for a in gateway.pending():
        gateway.decide(a.id, True, "approver:1")
    assert await asyncio.gather(*waiting) == [True] * 4
    assert semaphore._value == 4  # every slot was given back exactly once


@pytest.mark.slow
async def test_four_research_tasks_awaiting_approval_leave_room_for_a_fifth_over_http(api):
    client, p = await api(auto_approve=False, max_concurrent_investigations=4, approval_timeout_s=120)
    body = {"question": "Does momentum work on R01?", "dataset": "R01"}
    for _ in range(4):
        assert (await client.post("/research", json=body, headers=QUANT)).status_code == 202
    deadline = time.monotonic() + 60
    while len(p.approvals.pending()) < 4 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert len(p.approvals.pending()) == 4
    tid = (await client.post("/investigations", json=INV_BODY, headers=TRADER)).json()["task_id"]
    assert (await _wait_done(client, f"/investigations/{tid}", TRADER, 60))["status"] == "COMPLETED"


async def test_idempotency_key_same_body_same_id_different_body_422(api):
    client, _ = await api()
    h = {**TRADER, "Idempotency-Key": "abc-123"}
    first = await client.post("/investigations", json=INV_BODY, headers=h)
    second = await client.post("/investigations", json=INV_BODY, headers=h)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    other = await client.post("/investigations", json={**INV_BODY, "dataset": "T02"}, headers=h)
    assert other.status_code == 422 and "different request body" in other.json()["detail"]
    # scoped per principal: the same key from another principal is a new request
    admin = await client.post("/investigations", json=INV_BODY, headers={**ADMIN, "Idempotency-Key": "abc-123"})
    assert admin.status_code == 202 and admin.json()["task_id"] != first.json()["task_id"]
    bad = await client.post("/investigations", json=INV_BODY, headers={**TRADER, "Idempotency-Key": "x" * 300})
    assert bad.status_code == 422
    r1 = await client.post("/research", json={"question": "momentum on R01"}, headers={**QUANT, "Idempotency-Key": "r"})
    r2 = await client.post("/research", json={"question": "momentum on R01"}, headers={**QUANT, "Idempotency-Key": "r"})
    assert r1.json()["task_id"] == r2.json()["task_id"]


async def test_request_limits_body_question_notional_and_pagination(api):
    client, _ = await api(max_body_bytes=4096)
    big = {"question": "Analyse AAPL " + "x" * 10_000}
    r = await client.post("/investigations", json=big, headers=TRADER)
    assert r.status_code == 413 and r.headers["x-request-id"]
    long_q = {"question": "Analyse AAPL " + "x" * 2500}
    client2, _ = await api()
    assert (await client2.post("/investigations", json=long_q, headers=TRADER)).status_code == 422
    huge = {"question": "momentum on R01", "gross_notional": 1e12}
    assert (await client2.post("/research", json=huge, headers=QUANT)).status_code == 422
    page = paginate(list(range(5000)), limit=10**9, offset=-5)
    assert page["limit"] == MAX_PAGE and page["offset"] == 0 and page["returned"] == MAX_PAGE
    assert paginate([1, 2], limit=0, offset=99)["returned"] == 0


# ------------------------------------------------------------------ 4.5
class _MutatingApprovals(QueuedApprovalGateway):
    """Approves everything; on the staging approval it first changes the data under the approver."""

    def __init__(self, history) -> None:
        super().__init__(5)
        self.history_store = history

    async def request(self, approval):
        from ceap.policy.approvals import ApprovalDecision

        if approval.step_id == "gov-stage-approval":
            self.seen_hash = approval.arguments.get("portfolio_hash")
            ds = self.history_store.get("R01")
            close = ds.close.copy()
            close[-60:] = close[-60:] * 1.37  # prices moved: share quantities change
            object.__setattr__(ds, "close", close)
        who = "approver:2" if approval.step_id == "gov-stage-approval" else "approver:1"
        return ApprovalDecision(approval.id, True, who, "ok")


async def test_dataset_mutated_between_approval_and_staging_refuses():
    from ceap.data.historical import HistoricalStore

    history = HistoricalStore()  # private: this test mutates it
    settings = Settings(llm_provider="mock", auto_approve=False)
    gateway = _MutatingApprovals(history)
    p = Platform(settings=settings, llm=MockLLMClient(), history=history, approvals=gateway)
    try:
        result = await p.research(default_research_request("R01", stage_orders=True))
        staging = [s for s in result.step_results if s.get("step_id") == "gov-stage-orders"]
        assert staging and staging[0]["status"] != "SUCCESS"
        assert "staging refused" in (staging[0]["error"] or "")
        assert gateway.seen_hash  # the approver signed off a specific portfolio hash
    finally:
        p.close()


async def test_unchanged_dataset_stages_with_the_bound_hash():
    from ceap.data.historical import HistoricalStore

    settings = Settings(llm_provider="mock", auto_approve=True)
    p = Platform(settings=settings, llm=MockLLMClient(), history=HistoricalStore())
    try:
        result = await p.research(default_research_request("R01", stage_orders=True))
        staging = [s for s in result.step_results if s.get("step_id") == "gov-stage-orders"]
        assert staging and staging[0]["status"] == "SUCCESS"
    finally:
        p.close()


# ------------------------------------------------------------------ 4.6
async def test_raw_key_absent_from_logs_errors_and_reprs_and_every_line_has_a_request_id(api, caplog):
    secret = "super-secret-key-123456"
    settings = Settings(llm_provider="mock", api_keys={secret: "trader"})
    assert secret not in repr(settings)
    with pytest.raises(SettingsError) as exc:
        _parse_api_keys(f"{secret}:trader,{secret}:viewer")
    assert secret not in str(exc.value)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        client, _ = await api(api_keys={secret: "trader", "viewer-key-0001": "viewer"})
        bad = await client.get("/tools", headers={"X-API-Key": secret + "x"})
        good = await client.get("/tools", headers={"X-API-Key": secret})
        pinned = await client.get("/tools", headers={"X-API-Key": secret, "X-Request-ID": "corr-42"})
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert bad.status_code == 401 and secret not in bad.text
    assert good.status_code == 200 and good.headers["x-request-id"]
    assert pinned.headers["x-request-id"] == "corr-42"
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "the access log must have written lines"
    assert all(line.get("request_id") for line in lines)
    assert any(line["request_id"] == "corr-42" for line in lines)
    assert secret not in stream.getvalue() and secret not in caplog.text


async def test_salted_hash_entries_and_key_expiry(api):
    entry_ok = hash_api_key("hashed-key-0001") + ":auditor"
    entry_old = hash_api_key("expired-key-0001") + ":viewer:2020-01-01"
    client, _ = await api(api_keys={"plain-key-0001": "viewer"}, api_key_hashes=f"{entry_ok},{entry_old}")
    assert (await client.get("/tools", headers={"X-API-Key": "hashed-key-0001"})).status_code == 200
    expired = await client.get("/tools", headers={"X-API-Key": "expired-key-0001"})
    assert expired.status_code == 401 and expired.json()["detail"] == "API key expired"
    assert (await client.get("/tools", headers={"X-API-Key": "plain-key-0001"})).status_code == 200


# ------------------------------------------------------------------ 4.7
@pytest.mark.slow
async def test_replay_of_a_stored_bundle_equals_the_stored_result(tmp_path, store):
    from ceap.harness.replay import load_bundle, record_bundle, replay_bundle, save_bundle
    from ceap.platform import default_request

    p = Platform(settings=Settings(llm_provider="mock", auto_approve=True), llm=MockLLMClient(), store=store)
    try:
        bundle = await record_bundle(p, default_request())
    finally:
        p.close()
    path = save_bundle(bundle, tmp_path / "bundle.json")
    assert bundle["prompt_hashes"] and bundle["tool_calls"] and bundle["live"]["status"] == "COMPLETED"
    assert (await replay_bundle(load_bundle(path))).equal
    # a fresh process (different hash seed, cold caches) reproduces it byte for byte
    out = subprocess.run(
        [sys.executable, "-m", "ceap.cli", "replay", str(path)], capture_output=True, text=True,
        env=_child_env(), timeout=300,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["equal"] is True
    # tampering with the stored result is detected and located
    doc = load_bundle(path)
    doc["result"]["state"] = "FAILED"
    report = await replay_bundle(doc)
    assert not report.equal and report.first_difference == "$.state"


# ------------------------------------------------------------------ 4.8
def _lint_exposition(text: str) -> None:
    """The promtool rules this exposition must satisfy (used when promtool itself is absent)."""
    from prometheus_client.parser import text_string_to_metric_families

    families = list(text_string_to_metric_families(text))
    assert families
    helps = {line.split()[2] for line in text.splitlines() if line.startswith("# HELP")}
    types = {line.split()[2]: line.split()[3] for line in text.splitlines() if line.startswith("# TYPE")}
    for name, kind in types.items():
        assert name in helps, f"{name} has no HELP"
        if kind == "counter":
            assert name.endswith("_total"), name
        for abbrev in ("_ms", "_sec", "_us", "_ns"):
            assert not name.endswith(abbrev) and f"{abbrev}_" not in name, f"{name}: non-base unit"
    for fam in families:
        if fam.type == "summary":
            assert {s.name for s in fam.samples} <= {f"{fam.name}_count", f"{fam.name}_sum"}


async def test_prometheus_exposition_is_promtool_valid(api, tmp_path):
    client, _ = await api()
    await client.get("/health")
    await client.get("/nope")
    await client.post("/investigations", json=INV_BODY, headers=TRADER)
    metrics.observe("ceap_tool_latency_ms", 12.5, tool='we"ird\\tool')
    r = await client.get("/metrics", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    text = r.text
    assert "ceap_http_requests_total" in text and "ceap_http_request_duration_seconds_count" in text
    assert 'route="unmatched"' in text and "ceap_http_errors_total" in text
    _lint_exposition(text)
    import shutil

    promtool = shutil.which("promtool")
    if promtool:  # authoritative when installed (CI image); otherwise the parser + lint above
        f = tmp_path / "m.txt"
        f.write_text(text, encoding="utf-8")
        out = subprocess.run([promtool, "check", "metrics"], stdin=f.open("rb"), capture_output=True)
        assert out.returncode == 0, out.stdout + out.stderr


def test_exposition_lint_on_a_fresh_registry():
    m = MetricsRegistry()
    m.inc("jobs", kind="a")
    m.observe("work_ms", 3.0)
    m.observe("size_bytes", 10.0)
    _lint_exposition(m.render())
    assert "jobs_total" in m.render() and "work_seconds_sum 0.003" in m.render()


async def test_opentelemetry_span_is_exported():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from ceap.observability.tracing import OpenTelemetryTracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OpenTelemetryTracer.__new__(OpenTelemetryTracer)
    OpenTelemetryTracer.__init__(tracer)
    tracer._otel = provider.get_tracer("ceap-test")  # a private provider: no global state touched
    async with tracer.span("investigation", task_id="TASK-1", steps=3):
        async with tracer.span("tool.call", tool="market.get_quotes"):
            pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"investigation", "tool.call"}
    assert spans["investigation"].attributes["task_id"] == "TASK-1"
    assert spans["tool.call"].parent.span_id == spans["investigation"].context.span_id
    assert tracer.export()["spans"][0]["name"] == "investigation"
    assert trace is not None
