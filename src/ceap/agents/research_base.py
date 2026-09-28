"""Shared helpers and thresholds for the research agents.

The thresholds mirror ``ceap.analytics.research_assessment`` so an agent's
finding and the deterministic assessment can never disagree on a hurdle.
"""

from __future__ import annotations

from typing import Any

from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS
from ceap.domain.agents import AgentContext
from ceap.domain.research import ResearchScope, research_tool_arguments

MIN_IC_T_STAT = DEFAULT_THRESHOLDS.ic_t_stat_min
MIN_OOS_IC_T_STAT = DEFAULT_THRESHOLDS.oos_ic_t_min
MIN_SHARPE = DEFAULT_THRESHOLDS.sharpe_min
MIN_OOS_IS_SHARPE_RATIO = DEFAULT_THRESHOLDS.oos_is_sharpe_ratio_min
MAX_COST_SHARE = DEFAULT_THRESHOLDS.cost_share_max
MAX_PNL_CONCENTRATION = DEFAULT_THRESHOLDS.pnl_concentration_max
# informational hurdles (never used by the assessment; the critic leaves these flags alone)
MAX_TURNOVER = 0.8
MAX_DRAWDOWN = 0.25
FAST_DECAY_RATIO = 0.25
STRESS_LOSS_FRACTION = 0.05
MIN_COVERAGE_DAYS = 200


def scope(ctx: AgentContext) -> ResearchScope:
    return ResearchScope.from_task_input(ctx.task_input)


def tool_args(ctx: AgentContext) -> dict[str, dict[str, Any]]:
    return research_tool_arguments(ctx.task_input)


def describe_scope(s: ResearchScope) -> str:
    book = "long-short" if s.long_short else "long-only"
    return (
        f"{s.signal} on dataset {s.dataset} from {s.start} to {s.end} (in-sample to {s.in_sample_end}), "
        f"{book} quantile portfolio rebalanced every {s.rebalance_days} trading days"
    )
