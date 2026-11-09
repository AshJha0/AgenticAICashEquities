"""HTTP routes."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ceap import __version__
from ceap.api.auth import Principal, current_principal, require_capability
from ceap.api.models import (
    ApprovalDecisionIn,
    ApprovalOut,
    ApprovalReviewOut,
    HealthOut,
    InvestigationAccepted,
    InvestigationCreate,
    InvestigationStatus,
    ReportOut,
    ResearchAccepted,
    ResearchCreate,
    ResearchScenarioOut,
    ScenarioOut,
    StatusOut,
    ToolOut,
)
from ceap.api.parsing import LONDON, parse_question, parse_research_question, resolve_session_date
from ceap.data.research_scenarios import RESEARCH_TEMPLATES, all_research_scenarios
from ceap.data.scenarios import SCENARIO_TEMPLATES, all_scenarios
from ceap.domain.common import to_jsonable
from ceap.harness.engine import HarnessResult
from ceap.observability.metrics import metrics
from ceap.persistence.repositories import IdempotencyRepository
from ceap.platform import (
    DEFAULT_RESEARCH_DATASET,
    InvestigationRequest,
    Platform,
    RequestNotPermitted,
    ResearchRequest,
)
from ceap.policy.approvals import ConcurrencySlot, FourEyesViolationError, QueuedApprovalGateway
from ceap.policy.permissions import METRICS_READ, READ_ANY

log = logging.getLogger(__name__)

router = APIRouter()
INVESTIGATION = "investigation"
RESEARCH = "research"
IDEMPOTENCY_HEADER = "Idempotency-Key"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _platform(request: Request) -> Platform:
    return request.app.state.platform


def _task_kind(p: Platform, task_id: str) -> str | None:
    task = p.tasks.get(task_id)
    if task is not None:
        return str(task.input.get("kind") or INVESTIGATION)
    row = p.task_repo.get(task_id)
    return str(row["kind"]) if row else None


def _can_read(p: Platform, task_id: str, principal: Principal) -> bool:
    """Ownership scoping (R4 item 4.3): the owner, or a role with ``tasks:read-any`` (auditor, approver, admin)."""
    owner = p.owner_of(task_id)
    return owner is not None and (owner == principal.name or READ_ANY in principal.capabilities)


def _idempotency(
    request: Request, p: Platform, principal: Principal, body: BaseModel
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """``Idempotency-Key`` (R4 item 4.4): per principal, 24 h. Same key + same body returns the first
    response; same key + different body is a 422."""
    key = request.headers.get(IDEMPOTENCY_HEADER)
    if key is None:
        return None, None, None
    if not _SAFE_KEY.match(key):
        raise HTTPException(422, f"{IDEMPOTENCY_HEADER} must be 1-128 characters of [A-Za-z0-9._:-]")
    body_hash = IdempotencyRepository.body_hash(body.model_dump(mode="json"))
    row = p.idempotency.get(principal.name, key)
    if row is None:
        return key, body_hash, None
    if row["body_hash"] != body_hash:
        raise HTTPException(422, f"{IDEMPOTENCY_HEADER} was already used with a different request body")
    return key, body_hash, json.loads(row["response_json"])


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts.replace(tzinfo=LONDON) if ts.tzinfo is None else ts


# ------------------------------------------------------------------ health
@router.get("/health", response_model=HealthOut, tags=["system"])
async def health(request: Request) -> HealthOut:
    """Liveness plus database writer health; topology and counts moved to the authenticated ``/status``
    (R4 item 4.6). The write probe takes and releases the SQLite write lock without writing (0.8.0, G7);
    when it fails the status is ``degraded``. The key setting (dev default or not) is not shown here."""
    db = getattr(getattr(request.app.state, "platform", None), "db", None)
    if db is None:
        return HealthOut(status="ok", version=__version__)
    h = await asyncio.to_thread(db.health)
    return HealthOut(
        status="ok" if h["write_probe_ok"] else "degraded",
        version=__version__,
        writer_queue=h["writer_queue"],
        write_probe_ok=h["write_probe_ok"],
        write_probe_ms=h["write_probe_ms"],
    )


@router.get("/status", response_model=StatusOut, tags=["system"])
async def platform_status(
    request: Request, principal: Principal = Depends(require_capability("tools:read"))
) -> StatusOut:
    p = _platform(request)
    registry = await p.registry()
    return StatusOut(
        status="degraded" if p.settings.llm_disabled else "ok",
        version=__version__,
        environment=p.settings.environment,
        llm=p.llm.name,
        servers=p.mcp_client.servers(),
        tools=len(registry),
        llm_usage=getattr(p.llm, "usage", None),
        running=len(request.app.state.ceap.running),
    )


@router.get("/metrics", tags=["system"])
async def prometheus_metrics(principal: Principal = Depends(require_capability(METRICS_READ))) -> Response:
    """Prometheus exposition; needs a key with ``metrics:read`` (engineer, auditor, admin)."""
    return Response(metrics.render(), media_type=PROMETHEUS_CONTENT_TYPE)


@router.get("/tools", response_model=list[ToolOut], tags=["system"])
async def list_tools(request: Request, principal: Principal = Depends(current_principal)) -> list[ToolOut]:
    registry = await _platform(request).registry()
    return [
        ToolOut(
            id=m.id,
            name=m.name,
            description=m.description,
            read_only=m.read_only,
            risk_level=m.risk_level.value,
            server=m.server,
            required_capabilities=sorted(m.required_capabilities),
        )
        for m in registry.list()
    ]


@router.get("/scenarios", response_model=list[ScenarioOut], tags=["system"])
async def list_scenarios(all: bool = False) -> list[ScenarioOut]:  # noqa: A002 - query parameter name
    specs = all_scenarios() if all else list(SCENARIO_TEMPLATES)
    return [
        ScenarioOut(
            id=s.id,
            template=s.template,
            name=s.name,
            description=s.description,
            symbol=s.symbol,
            ground_truth=[c.value for c in s.ground_truth],
        )
        for s in specs
    ]


# ---------------------------------------------------------- investigations
@router.post(
    "/investigations",
    response_model=InvestigationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["investigations"],
)
async def create_investigation(
    body: InvestigationCreate,
    request: Request,
    principal: Principal = Depends(require_capability("investigate")),
) -> InvestigationAccepted:
    p = _platform(request)
    idem_key, body_hash, replay = _idempotency(request, p, principal, body)
    if replay is not None:
        return InvestigationAccepted(**replay)
    try:
        session_date = resolve_session_date(body.session_date)
    except ValueError as exc:
        raise HTTPException(422, f"invalid session_date: {exc}") from exc
    parsed = parse_question(body.question, session_date)
    symbol = (body.symbol or parsed.symbol or "").upper()
    ws = _aware(body.window_start) or parsed.window_start
    we = _aware(body.window_end) or parsed.window_end
    if not symbol:
        raise HTTPException(422, "could not determine symbol; pass 'symbol' explicitly")
    if not ws or not we:
        raise HTTPException(
            422, "could not determine window; pass window_start/window_end or say 'between 14:00 and 15:00'"
        )
    if we <= ws:
        raise HTTPException(422, "window_end must be after window_start")
    if (we - ws).total_seconds() > 8 * 3600:
        raise HTTPException(422, "window longer than 8 hours is not supported")

    req = InvestigationRequest(
        question=body.question,
        symbol=symbol,
        window_start=ws,
        window_end=we,
        baseline_start=_aware(body.baseline_start),
        baseline_end=_aware(body.baseline_end),
        dataset=body.dataset,
        principal=principal.name,
        roles=principal.roles,
        attributes={"signoff": True} if body.signoff else {},
    )
    try:
        p.validate_request(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state = request.app.state.ceap
    task = p.build_task(req)
    p.owners[task.id] = principal.name
    background = asyncio.create_task(_run_investigation(state, p, req, task), name=f"investigation:{task.id}")
    state.running[task.id] = background
    background.add_done_callback(functools.partial(_on_done, state, p, task.id))
    bs, be = req.resolved_baseline()
    accepted = InvestigationAccepted(
        task_id=task.id,
        status="RUNNING",
        symbol=symbol,
        window_start=ws,
        window_end=we,
        baseline_start=bs,
        baseline_end=be,
        dataset=task.input["dataset"],
        links={
            "status": f"/investigations/{task.id}",
            "report": f"/investigations/{task.id}/report",
            "trace": f"/investigations/{task.id}/trace",
        },
    )
    if idem_key is not None and body_hash is not None:
        p.idempotency.put(principal.name, idem_key, body_hash, task.id, accepted.model_dump(mode="json"))
    return accepted


async def _run_investigation(state: Any, p: Platform, req: InvestigationRequest, task: Any) -> None:
    # bounded concurrency; the slot is handed back while the task waits on a human approval (4.4)
    async with ConcurrencySlot(state.semaphore):
        await p.investigate(req, task)


async def _run_research(state: Any, p: Platform, req: ResearchRequest, task: Any) -> None:
    async with ConcurrencySlot(state.semaphore):  # research tasks share the concurrency bound
        await p.research(req, task)


def _on_done(state: Any, p: Platform, task_id: str, background: asyncio.Task[Any]) -> None:
    """Observe the background task: record failures so nothing stays RUNNING forever."""
    state.running.pop(task_id, None)
    if background.cancelled():
        if task_id not in p.results:
            p.record_failure(task_id, "investigation task cancelled during shutdown")
        return
    exc = background.exception()
    if exc is not None:
        log.error("investigation %s crashed outside the harness: %s", task_id, exc)
        if task_id not in p.results:
            p.record_failure(task_id, f"{type(exc).__name__}: {exc}")


def _status(
    request: Request, task_id: str, kind: str = INVESTIGATION, principal: Principal | None = None
) -> InvestigationStatus:
    p = _platform(request)
    actual = _task_kind(p, task_id)
    if actual is not None and actual != kind:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")
    if principal is not None and not _can_read(p, task_id, principal):
        # 404, not 403: another principal's task id must not be confirmable by probing
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")
    result = p.results.get(task_id)
    if result is not None:
        return InvestigationStatus(
            task_id=task_id,
            status=result.status.value,
            state=result.state.value,
            error=result.error,
            duration_ms=result.duration_ms,
            warnings=result.warning_messages(),
        )
    if task_id in request.app.state.ceap.running:
        return InvestigationStatus(task_id=task_id, status="RUNNING")
    if task_id in p.tasks:
        return InvestigationStatus(task_id=task_id, status="PENDING")
    row = p.task_repo.get(task_id)  # survived a restart: INTERRUPTED, or finished before it
    if row is not None:
        return InvestigationStatus(task_id=task_id, status=row["status"])
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")


def _finished_result(
    request: Request, task_id: str, kind: str = INVESTIGATION, principal: Principal | None = None
) -> HarnessResult:
    st = _status(request, task_id, kind, principal)
    if st.status in ("RUNNING", "PENDING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{kind} is {st.status.lower()}")
    result = _platform(request).results.get(task_id)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{kind} {task_id} is {st.status}; its full result is not in memory (see /result for the stored copy)",
        )
    return result


def _result_doc(
    request: Request, task_id: str, kind: str, principal: Principal, include_trace: bool
) -> dict[str, Any]:
    """The in-memory result, else the copy persisted in SQLite (survives a restart)."""
    st = _status(request, task_id, kind, principal)
    if st.status in ("RUNNING", "PENDING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{kind} is {st.status.lower()}")
    p = _platform(request)
    result = p.results.get(task_id)
    if result is not None:
        return result.to_dict(include_trace=include_trace)
    stored = p.result_repo.get(task_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no stored result for {kind} {task_id} ({st.status})")
    return stored


def _report_out(result: HarnessResult, kind: str) -> ReportOut:
    if result.report is None:
        raise HTTPException(422, f"{kind} failed: {result.error or 'unknown'}")
    r = result.report
    return ReportOut(
        task_id=r.task_id,
        execution_id=r.execution_id,
        title=r.title,
        executive_summary=r.executive_summary,
        primary_observations=list(r.primary_observations),
        conclusion=r.conclusion,
        alternative_explanations=list(r.alternative_explanations),
        attribution=r.attribution,
        metrics=to_jsonable(r.metrics),
        findings=to_jsonable(r.findings),
        evidence=to_jsonable(r.evidence),
        critique=to_jsonable(r.critique),
        narrative=r.narrative,
        generated_at=r.generated_at,
        kind=r.kind,
        proposal=to_jsonable(r.proposal),
    )


def _list_tasks(request: Request, kind: str, principal: Principal) -> list[dict[str, Any]]:
    p = _platform(request)
    read_any = READ_ANY in principal.capabilities
    rows = {r["id"]: r for r in p.task_repo.find(owner=None if read_any else principal.name, kind=kind)}
    ids = set(p.tasks) | set(request.app.state.ceap.running) | set(rows)
    out = []
    for tid in sorted(ids):
        if _task_kind(p, tid) not in (kind, None) or not _can_read(p, tid, principal):
            continue
        question = p.tasks[tid].description if tid in p.tasks else (rows[tid]["description"] if tid in rows else None)
        out.append(_status(request, tid, kind).model_dump() | {"question": question})
    return out


@router.get("/investigations", tags=["investigations"])
async def list_investigations(
    request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> list[dict[str, Any]]:
    return _list_tasks(request, INVESTIGATION, principal)


@router.get("/investigations/{task_id}", response_model=InvestigationStatus, tags=["investigations"])
async def get_investigation(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> InvestigationStatus:
    return _status(request, task_id, INVESTIGATION, principal)


@router.get("/investigations/{task_id}/report", response_model=ReportOut, tags=["investigations"])
async def get_report(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> ReportOut:
    return _report_out(_finished_result(request, task_id, INVESTIGATION, principal), INVESTIGATION)


@router.get("/investigations/{task_id}/result", tags=["investigations"])
async def get_full_result(
    task_id: str,
    request: Request,
    include_trace: bool = False,
    principal: Principal = Depends(require_capability("investigate:read")),
) -> dict[str, Any]:
    return _result_doc(request, task_id, INVESTIGATION, principal, include_trace)


def _trace_view(result: HarnessResult) -> dict[str, Any]:
    return {
        "trace": result.trace,
        "state_history": result.state_history,
        "policy_log": result.policy_log,
        "step_results": result.step_results,
    }


@router.get("/investigations/{task_id}/trace", tags=["investigations"])
async def get_trace(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> dict[str, Any]:
    return _trace_view(_finished_result(request, task_id, INVESTIGATION, principal))


def _cancel(request: Request, task_id: str, principal: Principal, kind: str) -> InvestigationStatus:
    p = _platform(request)
    owner = p.owner_of(task_id)
    if owner is None or _task_kind(p, task_id) not in (kind, None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")
    if owner != principal.name and "tasks:cancel-any" not in principal.capabilities:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"only the owner or an admin may cancel a {kind}")
    p.cancel(task_id, f"cancelled by {principal.name}")
    return _status(request, task_id, kind)


@router.post("/investigations/{task_id}/cancel", response_model=InvestigationStatus, tags=["investigations"])
async def cancel_investigation(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate"))
) -> InvestigationStatus:
    return _cancel(request, task_id, principal, INVESTIGATION)


# ---------------------------------------------------------------- research
@router.get("/research-scenarios", response_model=list[ResearchScenarioOut], tags=["system"])
async def list_research_scenarios(all: bool = False) -> list[ResearchScenarioOut]:  # noqa: A002 - query parameter name
    specs = all_research_scenarios() if all else list(RESEARCH_TEMPLATES)
    return [
        ResearchScenarioOut(
            id=s.id,
            template=s.template,
            name=s.name,
            description=s.description,
            signal=s.signal,
            expected_verdict=s.expected_verdict.value,
            expected_flags=[f.value for f in s.expected_flags],
            rebalance_days=s.rebalance_days,
        )
        for s in specs
    ]


@router.post("/research", response_model=ResearchAccepted, status_code=status.HTTP_202_ACCEPTED, tags=["research"])
async def create_research(
    body: ResearchCreate,
    request: Request,
    principal: Principal = Depends(require_capability("research")),
) -> ResearchAccepted:
    p = _platform(request)
    idem_key, body_hash, replay = _idempotency(request, p, principal, body)
    if replay is not None:
        return ResearchAccepted(**replay)
    parsed = parse_research_question(body.question)
    signal = body.signal or parsed.signal or "momentum_12_1"
    dataset = (body.dataset or parsed.dataset or DEFAULT_RESEARCH_DATASET).upper()
    from ceap.analytics.signals import SIGNALS

    rebalance = body.rebalance_days or (SIGNALS[signal].default_rebalance_days if signal in SIGNALS else 21)
    req = ResearchRequest(
        question=body.question,
        signal=signal,
        dataset=dataset,
        start=body.start or parsed.start,
        end=body.end or parsed.end,
        in_sample_end=body.in_sample_end,
        rebalance_days=rebalance,
        long_short=body.long_short,
        gross_notional=body.gross_notional,
        stage_orders=body.stage_orders,
        principal=principal.name,
        roles=principal.roles,
    )
    try:
        p.validate_research_request(req)
    except RequestNotPermitted as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state = request.app.state.ceap
    task = p.build_research_task(req)
    p.owners[task.id] = principal.name
    background = asyncio.create_task(_run_research(state, p, req, task), name=f"research:{task.id}")
    state.running[task.id] = background
    background.add_done_callback(functools.partial(_on_done, state, p, task.id))
    accepted = ResearchAccepted(
        task_id=task.id,
        status="RUNNING",
        signal=signal,
        dataset=dataset,
        start=datetime.fromisoformat(task.input["start"]).date(),
        end=datetime.fromisoformat(task.input["end"]).date(),
        in_sample_end=datetime.fromisoformat(task.input["in_sample_end"]).date(),
        rebalance_days=rebalance,
        long_short=body.long_short,
        stage_orders=body.stage_orders,
        links={
            "status": f"/research/{task.id}",
            "report": f"/research/{task.id}/report",
            "result": f"/research/{task.id}/result",
            "trace": f"/research/{task.id}/trace",
        },
    )
    if idem_key is not None and body_hash is not None:
        p.idempotency.put(principal.name, idem_key, body_hash, task.id, accepted.model_dump(mode="json"))
    return accepted


@router.get("/research", tags=["research"])
async def list_research(
    request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> list[dict[str, Any]]:
    return _list_tasks(request, RESEARCH, principal)


@router.get("/research/{task_id}", response_model=InvestigationStatus, tags=["research"])
async def get_research(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> InvestigationStatus:
    return _status(request, task_id, RESEARCH, principal)


@router.get("/research/{task_id}/report", response_model=ReportOut, tags=["research"])
async def get_research_report(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> ReportOut:
    return _report_out(_finished_result(request, task_id, RESEARCH, principal), RESEARCH)


@router.get("/research/{task_id}/result", tags=["research"])
async def get_research_result(
    task_id: str,
    request: Request,
    include_trace: bool = False,
    principal: Principal = Depends(require_capability("research:read")),
) -> dict[str, Any]:
    return _result_doc(request, task_id, RESEARCH, principal, include_trace)


@router.get("/research/{task_id}/trace", tags=["research"])
async def get_research_trace(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> dict[str, Any]:
    return _trace_view(_finished_result(request, task_id, RESEARCH, principal))


@router.post("/research/{task_id}/cancel", response_model=InvestigationStatus, tags=["research"])
async def cancel_research(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research"))
) -> InvestigationStatus:
    return _cancel(request, task_id, principal, RESEARCH)


# ---------------------------------------------------------------- approvals
@router.get("/approvals", response_model=list[ApprovalOut], tags=["approvals"])
async def list_approvals(
    request: Request, principal: Principal = Depends(require_capability("approvals:decide"))
) -> list[ApprovalOut]:
    gateway = _platform(request).approvals
    return [ApprovalOut(**to_jsonable(a)) for a in gateway.pending()]


@router.get("/approvals/{approval_id}", response_model=ApprovalReviewOut, tags=["approvals"])
async def get_approval(
    approval_id: str, request: Request, principal: Principal = Depends(require_capability("approvals:decide"))
) -> ApprovalReviewOut:
    """R3B 3B.8: one pending request with its review payload (hypotheses, evidence, pre-trade diff, disputes)."""
    gateway = _platform(request).approvals
    pending = {a.id: a for a in gateway.pending()}
    approval = pending.get(approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no pending approval {approval_id}")
    return ApprovalReviewOut(
        id=approval.id,
        task_id=approval.task_id,
        step_id=approval.step_id,
        reason=approval.reason,
        requested_by=approval.requested_by,
        requested_at=approval.requested_at,
        content_hash=approval.content_hash,
        review=to_jsonable(approval.arguments.get("review") or {}),
    )


@router.post("/approvals/{approval_id}", tags=["approvals"])
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionIn,
    request: Request,
    principal: Principal = Depends(require_capability("approvals:decide")),
) -> dict[str, Any]:
    gateway = _platform(request).approvals
    if not isinstance(gateway, QueuedApprovalGateway):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "approvals are automatic in this environment (CEAP_AUTO_APPROVE=true)"
        )
    try:
        decision = gateway.decide(
            approval_id, body.approved, principal.name, body.comment, action=body.action or ""
        )
    except FourEyesViolationError as exc:  # four-eyes: requester or earlier-stage approver (T17)
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return to_jsonable(decision)
