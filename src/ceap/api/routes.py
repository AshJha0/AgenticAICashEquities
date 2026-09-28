"""HTTP routes."""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from ceap import __version__
from ceap.api.auth import Principal, current_principal, require_capability
from ceap.api.models import (
    ApprovalDecisionIn,
    ApprovalOut,
    HealthOut,
    InvestigationAccepted,
    InvestigationCreate,
    InvestigationStatus,
    ReportOut,
    ResearchAccepted,
    ResearchCreate,
    ResearchScenarioOut,
    ScenarioOut,
    ToolOut,
)
from ceap.api.parsing import LONDON, parse_question, parse_research_question, resolve_session_date
from ceap.data.research_scenarios import RESEARCH_TEMPLATES, all_research_scenarios
from ceap.data.scenarios import SCENARIO_TEMPLATES, all_scenarios
from ceap.domain.common import to_jsonable
from ceap.harness.engine import HarnessResult
from ceap.observability.metrics import metrics
from ceap.platform import (
    DEFAULT_RESEARCH_DATASET,
    InvestigationRequest,
    Platform,
    RequestNotPermitted,
    ResearchRequest,
)
from ceap.policy.approvals import QueuedApprovalGateway

log = logging.getLogger(__name__)

router = APIRouter()
INVESTIGATION = "investigation"
RESEARCH = "research"


def _platform(request: Request) -> Platform:
    return request.app.state.platform


def _task_kind(p: Platform, task_id: str) -> str | None:
    task = p.tasks.get(task_id)
    return str(task.input.get("kind") or INVESTIGATION) if task is not None else None


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts.replace(tzinfo=LONDON) if ts.tzinfo is None else ts


# ------------------------------------------------------------------ health
@router.get("/health", response_model=HealthOut, tags=["system"])
async def health(request: Request) -> HealthOut:
    p = _platform(request)
    registry = await p.registry()
    return HealthOut(
        status="ok",
        version=__version__,
        environment=p.settings.environment,
        llm=p.llm.name,
        servers=p.mcp_client.servers(),
        tools=len(registry),
        llm_usage=getattr(p.llm, "usage", None),
        running=len(request.app.state.ceap.running),
    )


@router.get("/metrics", response_class=PlainTextResponse, tags=["system"])
async def prometheus_metrics() -> str:
    return metrics.render()


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
    return InvestigationAccepted(
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


async def _run_investigation(state: Any, p: Platform, req: InvestigationRequest, task: Any) -> None:
    async with state.semaphore:  # bound concurrent investigations
        await p.investigate(req, task)


async def _run_research(state: Any, p: Platform, req: ResearchRequest, task: Any) -> None:
    async with state.semaphore:  # research tasks share the concurrency bound
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


def _status(request: Request, task_id: str, kind: str = INVESTIGATION) -> InvestigationStatus:
    p = _platform(request)
    actual = _task_kind(p, task_id)
    if actual is not None and actual != kind:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")
    result = p.results.get(task_id)
    if result is not None:
        return InvestigationStatus(
            task_id=task_id,
            status="COMPLETED" if result.success else "FAILED",
            state=result.state.value,
            error=result.error,
            duration_ms=result.duration_ms,
            warnings=result.warnings,
        )
    if task_id in request.app.state.ceap.running:
        return InvestigationStatus(task_id=task_id, status="RUNNING")
    if task_id in p.tasks:
        return InvestigationStatus(task_id=task_id, status="PENDING")
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")


def _finished_result(request: Request, task_id: str, kind: str = INVESTIGATION) -> HarnessResult:
    st = _status(request, task_id, kind)
    if st.status in ("RUNNING", "PENDING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{kind} is {st.status.lower()}")
    return _platform(request).results[task_id]


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


def _list_tasks(request: Request, kind: str) -> list[dict[str, Any]]:
    p = _platform(request)
    ids = set(p.tasks) | set(request.app.state.ceap.running)
    return [
        _status(request, tid, kind).model_dump()
        | {"question": p.tasks[tid].description if tid in p.tasks else None}
        for tid in sorted(ids)
        if _task_kind(p, tid) in (kind, None)
    ]


@router.get("/investigations", tags=["investigations"])
async def list_investigations(
    request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> list[dict[str, Any]]:
    return _list_tasks(request, INVESTIGATION)


@router.get("/investigations/{task_id}", response_model=InvestigationStatus, tags=["investigations"])
async def get_investigation(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> InvestigationStatus:
    return _status(request, task_id)


@router.get("/investigations/{task_id}/report", response_model=ReportOut, tags=["investigations"])
async def get_report(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("investigate:read"))
) -> ReportOut:
    return _report_out(_finished_result(request, task_id), INVESTIGATION)


@router.get("/investigations/{task_id}/result", tags=["investigations"])
async def get_full_result(
    task_id: str,
    request: Request,
    include_trace: bool = False,
    principal: Principal = Depends(require_capability("investigate:read")),
) -> dict[str, Any]:
    return _finished_result(request, task_id).to_dict(include_trace=include_trace)


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
    return _trace_view(_finished_result(request, task_id))


def _cancel(request: Request, task_id: str, principal: Principal, kind: str) -> InvestigationStatus:
    p = _platform(request)
    owner = p.owners.get(task_id)
    if owner is None or _task_kind(p, task_id) not in (kind, None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {kind} {task_id}")
    if owner != principal.name and "approvals:decide" not in principal.capabilities:
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
    return ResearchAccepted(
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


@router.get("/research", tags=["research"])
async def list_research(
    request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> list[dict[str, Any]]:
    return _list_tasks(request, RESEARCH)


@router.get("/research/{task_id}", response_model=InvestigationStatus, tags=["research"])
async def get_research(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> InvestigationStatus:
    return _status(request, task_id, RESEARCH)


@router.get("/research/{task_id}/report", response_model=ReportOut, tags=["research"])
async def get_research_report(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> ReportOut:
    return _report_out(_finished_result(request, task_id, RESEARCH), RESEARCH)


@router.get("/research/{task_id}/result", tags=["research"])
async def get_research_result(
    task_id: str,
    request: Request,
    include_trace: bool = False,
    principal: Principal = Depends(require_capability("research:read")),
) -> dict[str, Any]:
    return _finished_result(request, task_id, RESEARCH).to_dict(include_trace=include_trace)


@router.get("/research/{task_id}/trace", tags=["research"])
async def get_research_trace(
    task_id: str, request: Request, principal: Principal = Depends(require_capability("research:read"))
) -> dict[str, Any]:
    return _trace_view(_finished_result(request, task_id, RESEARCH))


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
        decision = gateway.decide(approval_id, body.approved, principal.name, body.comment)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return to_jsonable(decision)
