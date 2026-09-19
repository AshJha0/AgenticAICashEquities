"""Domain model.

Everything in this package is framework-independent: no MCP, no LLM SDK,
no web framework. These are the contracts the rest of the platform
implements against.
"""

from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.analytics import ExecutionAnalytics
from ceap.domain.common import new_id, utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.execution import (
    Execution,
    ExecutionMetrics,
    Order,
    OrderStatus,
    Side,
    VenueStatistics,
)
from ceap.domain.findings import Finding
from ceap.domain.market import (
    MarketSnapshot,
    MarketStatistics,
    OrderBookLevel,
    OrderBookSnapshot,
    Quote,
    Trade,
)
from ceap.domain.plans import Plan, PlanStep, PlanValidationError, StepType
from ceap.domain.policy import (
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    PolicyEvaluation,
)
from ceap.domain.reports import InvestigationReport
from ceap.domain.repositories import ExecutionRepository, MarketDataRepository
from ceap.domain.tasks import Task, TaskPriority
from ceap.domain.tools import (
    Tool,
    ToolExecutionContext,
    ToolMetadata,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    ToolStatus,
)

__all__ = [
    "Agent",
    "AgentContext",
    "AgentResult",
    "Evidence",
    "EvidenceType",
    "Execution",
    "ExecutionAnalytics",
    "ExecutionMetrics",
    "ExecutionRepository",
    "Finding",
    "InvestigationReport",
    "MarketDataRepository",
    "MarketSnapshot",
    "MarketStatistics",
    "Order",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "OrderStatus",
    "Plan",
    "PlanStep",
    "PlanValidationError",
    "PolicyContext",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyEvaluation",
    "Quote",
    "Side",
    "StepType",
    "Task",
    "TaskPriority",
    "Tool",
    "ToolExecutionContext",
    "ToolMetadata",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "ToolStatus",
    "Trade",
    "VenueStatistics",
    "new_id",
    "utc_now",
]
