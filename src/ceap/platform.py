"""Composition root: wires data, MCP, LLM, policy, agents and the harness.

Both the API and the CLI build a :class:`Platform` and call
:meth:`Platform.investigate`.
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ceap.agents import (
    AlphaAgent,
    BacktestAgent,
    CriticAgent,
    EngineeringAgent,
    ExecutionAgent,
    MarketAgent,
    PlannerAgent,
    PortfolioRiskAgent,
    QuantAgent,
    ReportAgent,
    ResearchAgent,
    RiskAgent,
)
from ceap.analytics.signals import SIGNALS
from ceap.config import Settings, load_settings
from ceap.data.historical import RESEARCH_UNIVERSE, HistoricalDataset, HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.data.research_scenarios import get_research_scenario
from ceap.data.scenarios import SYMBOLS
from ceap.data.session import SESSION_TZ
from ceap.domain.policy import PolicyContext
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL, DEFAULT_REBALANCE_DAYS, DEFAULT_SIGNAL
from ceap.domain.tasks import Task, TaskPriority
from ceap.domain.tools import ToolRegistry
from ceap.harness.cancellation import CancellationToken
from ceap.harness.durable import SqlRunJournal
from ceap.harness.engine import AgentHarness, HarnessResult
from ceap.harness.memory import CaseMemory
from ceap.llm.client import LLMClient
from ceap.llm.router import LLMRouter, build_router
from ceap.mcp.client import MCPClient
from ceap.mcp.registry import build_in_process_client, build_tool_registry
from ceap.observability.ai_slo import P95_MS, AISLOMonitor
from ceap.observability.tracing import ExecutionTracer, InMemoryTracer
from ceap.persistence.db import Database
from ceap.persistence.repositories import (
    IdempotencyRepository,
    ResultRepository,
    RetentionPolicy,
    SqlApprovalStore,
    TaskRepository,
    apply_retention,
)
from ceap.policy.approvals import ApprovalGateway, AutoApprovalGateway, QueuedApprovalGateway
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import capabilities_for
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base

LONDON = SESSION_TZ  # desk session clock (America/New_York, 2T.1)
DEFAULT_RESEARCH_DATASET = "R01"
#: upper bound on a research request's gross notional (R4 item 4.4): 10 bn in the dataset currency
MAX_GROSS_NOTIONAL = 1e10
RESTART_REASON = "process restarted while the task was in flight"


@dataclass
class InvestigationRequest:
    question: str
    symbol: str
    window_start: datetime
    window_end: datetime
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    dataset: str | None = None
    principal: str = "anonymous"
    roles: frozenset[str] = frozenset({"trader"})
    priority: TaskPriority = TaskPriority.NORMAL
    attributes: dict[str, Any] = field(default_factory=dict)

    def resolved_baseline(self) -> tuple[datetime, datetime]:
        if self.baseline_start and self.baseline_end:
            return self.baseline_start, self.baseline_end
        length = self.window_end - self.window_start
        return self.window_start - length, self.window_start


class RequestNotPermitted(ValueError):  # noqa: N818 - domain vocabulary
    """The principal lacks a capability the request needs (403 at the API)."""


@dataclass
class ResearchRequest:
    """A Stage 2 request: evaluate a signal on a research dataset and propose whether to promote it."""

    question: str
    signal: str = DEFAULT_SIGNAL
    dataset: str = DEFAULT_RESEARCH_DATASET
    start: date | None = None
    end: date | None = None
    in_sample_end: date | None = None
    rebalance_days: int = DEFAULT_REBALANCE_DAYS
    long_short: bool = True
    gross_notional: float = DEFAULT_GROSS_NOTIONAL
    stage_orders: bool = False
    combine_signals: bool = False  # 2B.2: ridge combination of the signal family (alpha agent)
    principal: str = "anonymous"
    roles: frozenset[str] = frozenset({"quant"})
    priority: TaskPriority = TaskPriority.NORMAL
    attributes: dict[str, Any] = field(default_factory=dict)

    def resolved_window(self, ds: HistoricalDataset) -> tuple[str, str, str]:
        """``(start, end, in_sample_end)`` as ISO dates, defaulting to the dataset's walk-forward split."""
        start = self.start.isoformat() if self.start else ds.start
        end = self.end.isoformat() if self.end else ds.end
        split = self.in_sample_end.isoformat() if self.in_sample_end else ds.in_sample_end
        return start, end, split


class Platform:
    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        mcp_client: MCPClient | None = None,
        approvals: ApprovalGateway | None = None,
        store: DatasetStore | None = None,
        knowledge: KnowledgeBase | None = None,
        history: HistoricalStore | None = None,
        db: Database | None = None,
        case_memory: CaseMemory | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.db = db or Database(self.settings.db_path)
        # R3B 3B.7 (D10): harness-written, append-only, audited; evaluation passes enabled=False
        self.case_memory = case_memory if case_memory is not None else CaseMemory(audit=self.audit)
        self.task_repo = TaskRepository(self.db)
        self.result_repo = ResultRepository(self.db)
        self.idempotency = IdempotencyRepository(self.db)
        self.approval_store = SqlApprovalStore(self.db)
        self.store = store or DatasetStore()
        self.history = history or HistoricalStore()
        self.llm = llm or build_router(self.settings)
        self.slo = self._slo_monitor()
        self.knowledge = knowledge or build_knowledge_base(self.settings.knowledge_dir)
        self.mcp_client = mcp_client or build_in_process_client(self.store, self.knowledge, self.history, self.db)
        self.approvals = approvals or (
            AutoApprovalGateway()
            if self.settings.auto_approve
            else QueuedApprovalGateway(
                self.settings.approval_timeout_s, store=self.approval_store, audit=self.audit
            )
        )
        self.policy = RulePolicyEngine(allowed_symbols=frozenset(SYMBOLS) | frozenset(RESEARCH_UNIVERSE))
        self._registry: ToolRegistry | None = None
        self.results: OrderedDict[str, HarnessResult] = OrderedDict()
        self.tasks: OrderedDict[str, Task] = OrderedDict()
        self.cancellations: dict[str, CancellationToken] = {}
        self.owners: dict[str, str] = {}
        self.recovered: dict[str, list[str]] = self.recover()

    def _slo_monitor(self) -> AISLOMonitor:
        """T11 (R-14): the platform's AI SLO monitor, fed by the router and by the planner.

        A router that already carries a monitor (the evaluation runner) keeps it, and the platform
        reports through the same one. A local model gets the CPU latency objective.
        """
        existing = getattr(self.llm, "slo", None)
        if isinstance(existing, AISLOMonitor):
            return existing
        local = self.settings.llm_provider == "ollama"
        # a CPU-only 7B model takes minutes per planning call (README, local-model section)
        monitor = AISLOMonitor(p95_ms=dict.fromkeys(P95_MS, 1_800_000.0) if local else None)
        if isinstance(self.llm, LLMRouter):
            self.llm.slo = monitor
        return monitor

    # ------------------------------------------------------------ persistence
    def audit(self, actor: str, action: str, subject: str, payload: dict[str, Any] | None = None) -> str:
        """Append to the hash-chained audit log (``ceap db verify-chain`` checks it)."""
        return self.db.append_audit(actor, action, subject, payload)

    def recover(self) -> dict[str, list[str]]:
        """Startup recovery (R4 item 4.2): nothing survives a restart as RUNNING or PENDING.

        Tasks a previous process left in flight become ``INTERRUPTED``; approvals it left pending become
        ``EXPIRED``, so a decision posted after the restart is refused with 409 rather than silently lost.
        """
        interrupted = self.task_repo.interrupt_in_flight()
        expired = self.approval_store.expire_pending(RESTART_REASON)
        for tid in interrupted:
            self.audit("system", "task.interrupted", tid, {"reason": RESTART_REASON})
        for aid in expired:
            self.audit("system", "approval.expired", aid, {"reason": RESTART_REASON})
        apply_retention(
            self.db,
            RetentionPolicy(
                results_days=self.settings.retention_results_days or None,
                traces_days=self.settings.retention_traces_days or None,
                llm_io_days=self.settings.retention_llm_io_days or None,
                max_bytes=self.settings.db_max_bytes,
            ),
        )
        return {"interrupted": interrupted, "expired": expired}

    def owner_of(self, task_id: str) -> str | None:
        owner = self.owners.get(task_id)
        if owner is None:
            row = self.task_repo.get(task_id)
            owner = row["owner"] if row else None
        return owner

    def _persist_task(self, task: Task, kind: str, owner: str) -> None:
        self.owners[task.id] = owner
        self.task_repo.upsert(task.id, kind, owner, task.description, dict(task.input), "PENDING")

    def _persist_result(self, result: HarnessResult) -> None:
        if self.task_repo.get(result.task_id) is None:
            return  # a result for a task this platform never registered (direct harness use)
        self.result_repo.save(result.task_id, result.status.value, result.to_dict(), result.trace)

    def close(self) -> None:
        self.db.close()

    async def registry(self) -> ToolRegistry:
        if self._registry is None:
            self._registry = await build_tool_registry(self.mcp_client)
        return self._registry

    def _agents(self) -> dict[str, Any]:
        return {
            "market": MarketAgent(),
            "execution": ExecutionAgent(),
            "quant": QuantAgent(),
            "risk": RiskAgent(),
            "engineering": EngineeringAgent(),
            "critic": CriticAgent(self.llm),
            "research": ResearchAgent(),
            "alpha": AlphaAgent(),
            "backtest": BacktestAgent(),
            "portfolio_risk": PortfolioRiskAgent(),
        }

    async def harness(self, tracer: ExecutionTracer | None = None) -> AgentHarness:
        return AgentHarness(
            planner=PlannerAgent(self.llm, slo=self.slo),
            agents=self._agents(),
            tools=await self.registry(),
            policy=self.policy,
            reporter=ReportAgent(self.llm, audit=self.audit),
            tracer=tracer or InMemoryTracer(),
            approvals=self.approvals,
            settings=self.settings,
            case_memory=self.case_memory,
            # G5 (0.9.0): opt-in step-boundary journal, CEAP_DURABLE_RUNS=1 (docs/RUNBOOK.md "Restart")
            journal=SqlRunJournal(self.db) if os.getenv("CEAP_DURABLE_RUNS", "0") == "1" else None,
        )

    def build_task(self, req: InvestigationRequest) -> Task:
        bs, be = req.resolved_baseline()
        task = Task.create(
            req.question,
            {
                "symbol": req.symbol,
                "window_start": req.window_start.isoformat(),
                "window_end": req.window_end.isoformat(),
                "baseline_start": bs.isoformat(),
                "baseline_end": be.isoformat(),
                "dataset": req.dataset or self.settings.default_dataset,
                "timezone": str(LONDON),
                **({"signoff": True} if req.attributes.get("signoff") else {}),
            },
            priority=req.priority,
            requested_by=req.principal,
        )
        self.tasks[task.id] = task
        self.cancellations[task.id] = CancellationToken()
        self._persist_task(task, "investigation", req.principal)
        self._evict()
        return task

    def build_research_task(self, req: ResearchRequest) -> Task:
        ds = self.history.get(req.dataset)
        start, end, split = req.resolved_window(ds)
        task = Task.create(
            req.question,
            {
                "kind": "research",
                "dataset": req.dataset,
                "signal": req.signal,
                "start": start,
                "end": end,
                "in_sample_end": split,
                "rebalance_days": int(req.rebalance_days),
                "long_short": bool(req.long_short),
                "gross_notional": float(req.gross_notional),
                "stage_orders": bool(req.stage_orders),
                "combine_signals": bool(req.combine_signals),
                "universe": list(ds.symbols),
                "timezone": "UTC",
            },
            priority=req.priority,
            requested_by=req.principal,
        )
        self.tasks[task.id] = task
        self.cancellations[task.id] = CancellationToken()
        self._persist_task(task, "research", req.principal)
        self._evict()
        return task

    def validate_research_request(self, req: ResearchRequest) -> None:
        """Fail fast: unknown signal/dataset, a window outside coverage, or a principal without the capability."""
        if req.signal not in SIGNALS:
            raise ValueError(f"unknown signal {req.signal}; available: {sorted(SIGNALS)}")
        try:
            ds = self.history.get(req.dataset)
        except KeyError as exc:
            raise ValueError(f"unknown research dataset {req.dataset} (see `ceap research-scenarios`)") from exc
        start, end, split = req.resolved_window(ds)
        try:
            i0, i1 = ds.index_range(start, end)
            s = ds.index_at_or_before(split)
        except ValueError as exc:
            raise ValueError(f"research window outside dataset coverage: {exc}") from exc
        if i0 < ds.start_index:
            raise ValueError(
                f"start {start} falls inside the {ds.warmup_days}-day signal warm-up; the earliest start is {ds.start}"
            )
        if not i0 < s < i1:
            raise ValueError("in_sample_end must fall strictly inside the research window")
        if not 1 <= int(req.rebalance_days) <= 252:
            raise ValueError("rebalance_days must be between 1 and 252")
        if not 0 < req.gross_notional <= MAX_GROSS_NOTIONAL:
            raise ValueError(f"gross_notional must be positive and at most {MAX_GROSS_NOTIONAL:,.0f}")
        caps = capabilities_for(req.roles)
        if "research" not in caps:
            raise RequestNotPermitted(f"roles {sorted(req.roles)} lack the research capability")
        if req.stage_orders and not {"tools:write", "trading:execute"} <= caps:
            raise RequestNotPermitted(
                "staging orders requires the tools:write and trading:execute capabilities (admin role)"
            )

    async def research(self, req: ResearchRequest, task: Task | None = None) -> HarnessResult:
        await asyncio.to_thread(self.validate_research_request, req)
        task = task or self.build_research_task(req)
        self.tasks.setdefault(task.id, task)
        token = self.cancellations.setdefault(task.id, CancellationToken())
        return await self._execute(task, self.policy_context(req, task), token)

    async def _execute(self, task: Task, ctx: PolicyContext, token: CancellationToken) -> HarnessResult:
        if self.task_repo.get(task.id) is not None:
            await self.db.arun(lambda c: c.execute("UPDATE tasks SET status='RUNNING' WHERE id=?", (task.id,)))
        harness = await self.harness()
        result = await harness.execute(task, ctx, token)
        self.results[task.id] = result
        self.cancellations.pop(task.id, None)
        self._persist_result(result)
        return result

    def policy_context(self, req: InvestigationRequest | ResearchRequest, task: Task) -> PolicyContext:
        roles = frozenset(req.roles)
        return PolicyContext(
            principal=req.principal,
            roles=roles,
            capabilities=capabilities_for(roles),
            task_id=task.id,
            environment=self.settings.environment,
            attributes=dict(req.attributes),
        )

    def validate_request(self, req: InvestigationRequest) -> None:
        """Fail fast when the request cannot be answered from available data."""
        if req.symbol not in SYMBOLS:
            raise ValueError(f"symbol {req.symbol} is outside the supported universe {list(SYMBOLS)}")
        if req.window_end <= req.window_start:
            raise ValueError("window_end must be after window_start")
        dataset = req.dataset or self.settings.default_dataset
        try:
            ds = self.store.get(dataset)
        except KeyError as exc:
            raise ValueError(f"unknown dataset {dataset}") from exc
        if req.symbol != ds.symbol:
            raise ValueError(
                f"dataset {dataset} covers {ds.symbol}, not {req.symbol}; choose a scenario for {req.symbol} (see `ceap scenarios --all`)"
            )
        bs, be = req.resolved_baseline()
        if be <= bs:
            raise ValueError("baseline_end must be after baseline_start")
        if be > req.window_start:
            raise ValueError("baseline must end at or before the investigation window starts")
        lo, hi = min(bs, req.window_start), max(be, req.window_end)
        if lo < ds.sim_start or hi > ds.sim_end:
            raise ValueError(
                f"requested window {lo.isoformat()} - {hi.isoformat()} (incl. baseline) is outside dataset coverage "
                f"{ds.sim_start.isoformat()} - {ds.sim_end.isoformat()}"
            )

    async def investigate(self, req: InvestigationRequest, task: Task | None = None) -> HarnessResult:
        await asyncio.to_thread(self.validate_request, req)  # off the loop (G1)
        task = task or self.build_task(req)
        self.tasks.setdefault(task.id, task)
        token = self.cancellations.setdefault(task.id, CancellationToken())
        return await self._execute(task, self.policy_context(req, task), token)

    def record_failure(self, task_id: str, error: str) -> HarnessResult:
        """Record a terminal failure that happened outside the harness (setup, crash)."""
        result = HarnessResult.failed(task_id, error)
        self.results[task_id] = result
        self.cancellations.pop(task_id, None)
        self._persist_result(result)
        return result

    def _evict(self) -> None:
        """Bound memory: keep only the most recent finished results and their tasks."""
        limit = self.settings.max_retained_results
        while len(self.results) > limit:
            oldest, _ = self.results.popitem(last=False)
            self.tasks.pop(oldest, None)
            self.owners.pop(oldest, None)
        # tasks that never ran (validated but not started) are dropped once they are far behind
        stale = [tid for tid in self.tasks if tid not in self.results and tid not in self.cancellations]
        for tid in stale[: max(0, len(self.tasks) - limit * 2)]:
            self.tasks.pop(tid, None)
            self.owners.pop(tid, None)

    def cancel(self, task_id: str, reason: str = "cancelled by user") -> bool:
        token = self.cancellations.get(task_id)
        if token is None:
            return False
        token.cancel(reason)
        return True


def london_time(date_str: str, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime.fromisoformat(date_str).replace(
        hour=int(hh), minute=int(mm), second=0, microsecond=0, tzinfo=LONDON
    )


def default_research_request(
    dataset: str = "R01",
    signal: str | None = None,
    question: str | None = None,
    stage_orders: bool = False,
    roles: frozenset[str] | None = None,
    principal: str = "quant.a",
) -> ResearchRequest:
    """A research request scoped to a scenario: its signal and rebalance frequency, the dataset's full window."""
    spec = get_research_scenario(dataset)
    signal = signal or spec.signal
    return ResearchRequest(
        question=question
        or f"Does the {signal} signal work on the {dataset} universe? Evaluate it out-of-sample with costs and "
        "propose whether to promote it.",
        signal=signal,
        dataset=dataset,
        rebalance_days=spec.rebalance_days,
        long_short=spec.long_short,
        stage_orders=stage_orders,
        principal=principal,
        roles=roles if roles is not None else frozenset({"admin"} if stage_orders else {"quant"}),
    )


def default_request(
    symbol: str = "AAPL", dataset: str = "T01", question: str | None = None, session_date: str = "2026-09-18"
) -> InvestigationRequest:
    ws, we = london_time(session_date, "14:00"), london_time(session_date, "15:00")
    return InvestigationRequest(
        question=question
        or f"Analyse our {symbol} execution between 14:00 and 15:00 and explain why implementation shortfall changed.",
        symbol=symbol,
        window_start=ws,
        window_end=we,
        baseline_start=ws - timedelta(hours=1),
        baseline_end=ws,
        dataset=dataset,
        principal="trader.a",
        roles=frozenset({"trader"}),
    )
