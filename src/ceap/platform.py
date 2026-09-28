"""Composition root: wires data, MCP, LLM, policy, agents and the harness.

Both the API and the CLI build a :class:`Platform` and call
:meth:`Platform.investigate`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from ceap.domain.policy import PolicyContext
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL, DEFAULT_REBALANCE_DAYS, DEFAULT_SIGNAL
from ceap.domain.tasks import Task, TaskPriority
from ceap.domain.tools import ToolRegistry
from ceap.harness.cancellation import CancellationToken
from ceap.harness.engine import AgentHarness, HarnessResult
from ceap.llm.client import LLMClient
from ceap.llm.router import build_router
from ceap.mcp.client import MCPClient
from ceap.mcp.registry import build_in_process_client, build_tool_registry
from ceap.observability.tracing import ExecutionTracer, InMemoryTracer
from ceap.policy.approvals import ApprovalGateway, AutoApprovalGateway, QueuedApprovalGateway
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import capabilities_for
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base

LONDON = ZoneInfo("Europe/London")
DEFAULT_RESEARCH_DATASET = "R01"


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
    ) -> None:
        self.settings = settings or load_settings()
        self.store = store or DatasetStore()
        self.history = history or HistoricalStore()
        self.llm = llm or build_router(self.settings)
        self.knowledge = knowledge or build_knowledge_base(self.settings.knowledge_dir)
        self.mcp_client = mcp_client or build_in_process_client(self.store, self.knowledge, self.history)
        self.approvals = approvals or (
            AutoApprovalGateway() if self.settings.auto_approve else QueuedApprovalGateway()
        )
        self.policy = RulePolicyEngine(allowed_symbols=frozenset(SYMBOLS) | frozenset(RESEARCH_UNIVERSE))
        self._registry: ToolRegistry | None = None
        self.results: OrderedDict[str, HarnessResult] = OrderedDict()
        self.tasks: OrderedDict[str, Task] = OrderedDict()
        self.cancellations: dict[str, CancellationToken] = {}
        self.owners: dict[str, str] = {}

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
            planner=PlannerAgent(self.llm),
            agents=self._agents(),
            tools=await self.registry(),
            policy=self.policy,
            reporter=ReportAgent(self.llm),
            tracer=tracer or InMemoryTracer(),
            approvals=self.approvals,
            settings=self.settings,
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
                "timezone": "Europe/London",
            },
            priority=req.priority,
            requested_by=req.principal,
        )
        self.tasks[task.id] = task
        self.cancellations[task.id] = CancellationToken()
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
                "universe": list(ds.symbols),
                "timezone": "UTC",
            },
            priority=req.priority,
            requested_by=req.principal,
        )
        self.tasks[task.id] = task
        self.cancellations[task.id] = CancellationToken()
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
        if req.gross_notional <= 0:
            raise ValueError("gross_notional must be positive")
        caps = capabilities_for(req.roles)
        if "research" not in caps:
            raise RequestNotPermitted(f"roles {sorted(req.roles)} lack the research capability")
        if req.stage_orders and not {"tools:write", "trading:execute"} <= caps:
            raise RequestNotPermitted(
                "staging orders requires the tools:write and trading:execute capabilities (admin role)"
            )

    async def research(self, req: ResearchRequest, task: Task | None = None) -> HarnessResult:
        self.validate_research_request(req)
        task = task or self.build_research_task(req)
        self.tasks.setdefault(task.id, task)
        token = self.cancellations.setdefault(task.id, CancellationToken())
        harness = await self.harness()
        result = await harness.execute(task, self.policy_context(req, task), token)
        self.results[task.id] = result
        self.cancellations.pop(task.id, None)
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
        self.validate_request(req)
        task = task or self.build_task(req)
        self.tasks.setdefault(task.id, task)
        token = self.cancellations.setdefault(task.id, CancellationToken())
        harness = await self.harness()
        result = await harness.execute(task, self.policy_context(req, task), token)
        self.results[task.id] = result
        self.cancellations.pop(task.id, None)
        return result

    def record_failure(self, task_id: str, error: str) -> HarnessResult:
        """Record a terminal failure that happened outside the harness (setup, crash)."""
        result = HarnessResult.failed(task_id, error)
        self.results[task_id] = result
        self.cancellations.pop(task_id, None)
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
