"""Shared plumbing for agents."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ceap.domain.agents import Agent, AgentContext
from ceap.domain.common import utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.harness.memory import InvestigationMemory, ToolOutput


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
        cached = self.output(
            ctx,
            tool_id,
            **{k: v for k, v in arguments.items() if k in ("symbol", "start", "end", "service", "level")},
        )
        if cached is not None:
            return cached.data, cached.evidence_ids
        data = await ctx.tools.invoke(tool_id, arguments)
        fresh = self.memory(ctx).find(
            tool_id,
            **{k: v for k, v in arguments.items() if k in ("symbol", "start", "end", "service", "level")},
        )
        return data, (fresh.evidence_ids if fresh else ())

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
    """Map how far a metric sits beyond its threshold into a calibrated confidence."""
    if not finite(excess) or excess <= 0:
        return floor
    return min(cap, floor + (cap - floor) * (1 - math.exp(-excess / scale)))
