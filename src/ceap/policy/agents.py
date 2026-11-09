"""Per-agent least privilege (A16).

A tool call an agent makes runs under the intersection of two grants: the principal's
capabilities (from their roles) and the agent's own profile below. The profile also names the
only tools the agent may call. No agent profile holds ``tools:write``, so no agent can call a
mutating tool directly; order staging is a harness step that goes through approval.

An agent missing from :data:`AGENT_PROFILES` gets :data:`DEFAULT_AGENT_PROFILE`: read-only
tools, no allowlist beyond that, and every other policy rule still applies. Add a profile when
you add an agent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ceap.domain.policy import PolicyContext

#: ``PolicyContext.attributes`` keys set for a call made on behalf of an agent
AGENT_ATTRIBUTE = "agent_id"
AGENT_TOOLS_ATTRIBUTE = "agent_tools"

_READ = frozenset({"tools:read"})


@dataclass(frozen=True)
class AgentProfile:
    capabilities: frozenset[str]
    #: tool ids the agent may call; ``None`` means any tool its capabilities allow
    tools: frozenset[str] | None


AGENT_PROFILES: dict[str, AgentProfile] = {
    "market": AgentProfile(
        _READ,
        frozenset({"market_data.get_market_statistics", "market_data.get_order_book_statistics"}),
    ),
    "execution": AgentProfile(
        _READ,
        frozenset(
            {
                "execution.get_execution_metrics",
                "execution.get_venue_statistics",
                "execution.get_parent_orders",
            }
        ),
    ),
    "quant": AgentProfile(
        _READ,
        frozenset(
            {
                "execution.get_execution_metrics",
                "market_data.get_market_statistics",
                "market_data.get_order_book_statistics",
            }
        ),
    ),
    "risk": AgentProfile(
        _READ,
        frozenset({"execution.get_execution_metrics", "risk.get_exposure", "risk.check_limit"}),
    ),
    "engineering": AgentProfile(
        _READ,
        frozenset(
            {
                "engineering.get_latency_metrics",
                "engineering.get_deployments",
                "engineering.search_logs",
                "engineering.get_service_metrics",
            }
        ),
    ),
    "research": AgentProfile(_READ, frozenset({"research_data.get_universe_summary"})),
    "alpha": AgentProfile(_READ, frozenset({"alpha.evaluate_signal", "alpha.combine_signals"})),
    "backtest": AgentProfile(_READ, frozenset({"backtest.run_backtest"})),
    "portfolio_risk": AgentProfile(
        _READ,
        frozenset(
            {"risk.get_portfolio_exposure", "risk.check_portfolio_limits", "risk.calculate_portfolio_stress"}
        ),
    ),
    # these read the harness's memory and call no tools
    "planner": AgentProfile(frozenset(), frozenset()),
    "critic": AgentProfile(frozenset(), frozenset()),
    "reporter": AgentProfile(frozenset(), frozenset()),
}

DEFAULT_AGENT_PROFILE = AgentProfile(_READ, None)


def agent_profile(agent_id: str) -> AgentProfile:
    return AGENT_PROFILES.get(agent_id, DEFAULT_AGENT_PROFILE)


def agent_policy_context(context: PolicyContext, agent_id: str) -> PolicyContext:
    """``context`` narrowed to what ``agent_id`` may do; it can only lose privileges, never gain."""
    profile = agent_profile(agent_id)
    attributes = {**context.attributes, AGENT_ATTRIBUTE: agent_id}
    inherited = context.attributes.get(AGENT_TOOLS_ATTRIBUTE)
    tools = profile.tools
    if inherited is not None:
        tools = frozenset(inherited) if tools is None else tools & frozenset(inherited)
    if tools is not None:
        attributes[AGENT_TOOLS_ATTRIBUTE] = tools
    return replace(context, capabilities=context.capabilities & profile.capabilities, attributes=attributes)
