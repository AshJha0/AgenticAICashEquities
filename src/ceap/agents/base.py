"""Shared plumbing for agents."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ceap.domain.agents import Agent, AgentContext, AgentMessage
from ceap.domain.common import utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.harness.executor import race_cancellation
from ceap.harness.memory import InvestigationMemory, ToolOutput
from ceap.llm.client import LLMClient
from ceap.llm.models import Degradation, LLMRequest, LLMResponse

#: specialists every execution investigation must run, whatever the plan says (PLAN 1.6)
REQUIRED_INVESTIGATION_AGENTS = ("market", "execution", "quant", "risk", "engineering")
DEFAULT_MAX_LLM_CALLS_PER_TASK = 12


def llm_disabled(llm: LLMClient | None) -> bool:
    """True when the kill switch (``CEAP_LLM_DISABLED``) is on for this client."""
    return llm is None or bool(getattr(llm, "disabled", False))


def degradation_records(items: list[Degradation] | tuple[Degradation, ...]) -> list[dict[str, Any]]:
    return [{"rung": d.rung, "code": d.code, "message": d.message} for d in items]


@dataclass(frozen=True)
class Window:
    symbol: str
    start: str
    end: str
    baseline_start: str
    baseline_end: str
    dataset: str | None

    @property
    def window_args(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "start": self.start, "end": self.end, "dataset": self.dataset}

    @property
    def baseline_args(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start": self.baseline_start,
            "end": self.baseline_end,
            "dataset": self.dataset,
        }


class BaseAgent(Agent):
    agent_id: str = "base"
    agent_type: str = "specialist"

    @property
    def id(self) -> str:
        return self.agent_id

    @property
    def type(self) -> str:
        return self.agent_type

    # ---------------------------------------------------------------- helpers
    async def call_llm(self, ctx: AgentContext, llm: LLMClient, request: LLMRequest) -> LLMResponse:
        """Every model call goes through here: per-task call cap, then raced against cancellation."""
        scratch = (ctx.state or {}).get("scratch")
        if isinstance(scratch, dict):
            cap = int(getattr(llm, "max_calls_per_task", DEFAULT_MAX_LLM_CALLS_PER_TASK))
            used = int(scratch.get("llm_calls", 0))
            if used >= cap:
                from ceap.llm.router import LLMBudgetExceededError

                raise LLMBudgetExceededError(f"per-task model-call cap of {cap} reached")
            scratch["llm_calls"] = used + 1
        coro = llm.complete(request)
        if ctx.cancellation_event is None:
            return await coro
        return await race_cancellation(coro, ctx.cancellation_event)

    def checkpoint(self, ctx: AgentContext, **partial: Any) -> None:
        """Record partial output; if the agent times out the harness returns it as ``PARTIAL``."""
        scratch = (ctx.state or {}).get("scratch")
        if isinstance(scratch, dict):
            scratch.setdefault("partial", {}).setdefault(self.id, {}).update(partial)

    @staticmethod
    def memory(ctx: AgentContext) -> InvestigationMemory:
        return ctx.state["memory"]

    @staticmethod
    def window(ctx: AgentContext) -> Window:
        i = ctx.task_input
        return Window(
            symbol=i["symbol"],
            start=i["window_start"],
            end=i["window_end"],
            baseline_start=i["baseline_start"],
            baseline_end=i["baseline_end"],
            dataset=i.get("dataset"),
        )

    def output(self, ctx: AgentContext, tool_id: str, **filters: Any) -> ToolOutput | None:
        return self.memory(ctx).find(tool_id, **filters)

    async def ensure(
        self, ctx: AgentContext, tool_id: str, arguments: dict[str, Any]
    ) -> tuple[Any, tuple[str, ...]]:
        """Return cached tool data (from the plan's tool calls) or invoke the tool now.

        Either way the data is backed by evidence recorded in memory.
        """
        # Exact-argument match: a plan step that called the same tool with *different*
        # arguments (another dataset, strategy or quantity) must not be reused as evidence.
        cached = self.memory(ctx).find_exact(tool_id, arguments)
        if cached is not None:
            return cached.data, cached.evidence_ids
        data = await ctx.tools.invoke(tool_id, arguments)
        fresh = self.memory(ctx).find_exact(tool_id, arguments)
        return data, (fresh.evidence_ids if fresh else ())

    async def fetched(
        self, ctx: AgentContext, tool_id: str, arguments: dict[str, Any]
    ) -> tuple[Any, tuple[str, ...]]:
        """R3B 3B.1: the data the plan fetched for ``tool_id`` with exactly these arguments.

        Never invokes a tool. The harness schedules every call an agent declares in
        ``ceap.agents.requirements``; if that call failed, the agent fails with a clear reason
        (and the harness may re-plan once, 3B.6) instead of quietly fetching the data itself.
        """
        cached = self.memory(ctx).find_exact(tool_id, arguments)
        if cached is None:
            raise PlanDataMissingError(
                f"{self.id}: the plan did not fetch {tool_id}({_args_text(arguments)})"
            )
        return cached.data, cached.evidence_ids

    def message(self, ctx: AgentContext, agent_id: str) -> AgentMessage | None:
        """R3B 3B.6: the latest typed message another agent sent in this run (``None`` if none)."""
        for msg in reversed(list((ctx.state or {}).get("messages") or ())):
            if isinstance(msg, AgentMessage) and msg.sender == agent_id:
                return msg
        return None

    def calibrated(self, key: str, raw: float) -> float:
        """Map a raw confidence through the held-out calibration registered for ``key`` (2B.4).

        Returns ``raw`` unchanged unless a calibration map for ``key`` is registered and shipped.
        """
        from ceap.analytics.calibration import apply_calibration

        return apply_calibration(key, raw)

    def calc_evidence(
        self,
        ctx: AgentContext,
        description: str,
        attributes: dict[str, Any],
        source_evidence: tuple[str, ...] = (),
    ) -> Evidence:
        ev = Evidence.create(
            type=EvidenceType.CALCULATION,
            source=f"agent:{self.id}",
            description=description,
            timestamp=utc_now(),
            attributes={**attributes, "derived_from": list(source_evidence), "task_id": ctx.task_id},
        )
        self.memory(ctx).add_evidence(ev)
        return ev


class PlanDataMissingError(RuntimeError):
    """An agent asked for data the plan did not fetch (or whose fetch failed)."""


def _args_text(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(arguments.items()) if v is not None)[:200]


def ratio(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return float("nan")
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(a) or math.isnan(b) or b == 0:
        return float("nan")
    return a / b


def finite(x: Any) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def confidence_from_excess(excess: float, scale: float, floor: float = 0.55, cap: float = 0.95) -> float:
    """Map how far a metric sits beyond its threshold into a heuristic confidence.

    Not calibrated against outcomes; see ``BaseAgent.calibrated`` and ``analytics.calibration`` (2B.4).
    """
    if not finite(excess) or excess <= 0:
        return floor
    return min(cap, floor + (cap - floor) * (1 - math.exp(-excess / scale)))
