"""Per-agent least privilege (A16).

A tool call an agent makes runs under the intersection of two grants: the principal's
capabilities (from their roles) and the agent's own profile below. The profile also names the
only tools the agent may call. No agent profile holds ``tools:write``, so no agent can call a
mutating tool directly; order staging is a harness step that goes through approval.

Profiles are derived from ``ceap.agents.requirements`` (plan-fetched tools plus the
``ON_DEMAND_TOOLS`` an agent calls itself). An agent missing there gets :data:`DEFAULT_AGENT_PROFILE`: read-only
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


#: agents that read the harness's memory and call no tools
_NO_TOOL_AGENTS = ("planner", "critic", "reporter")

DEFAULT_AGENT_PROFILE = AgentProfile(_READ, None)


def _derived_profiles() -> dict[str, AgentProfile]:
    """Profiles derived from the tools each agent reads (``ceap.agents.requirements``), not by hand."""
    from ceap.agents.requirements import ON_DEMAND_TOOLS, REQUIREMENTS, agent_tool_ids

    profiles = {a: AgentProfile(frozenset(), frozenset()) for a in _NO_TOOL_AGENTS}
    for agent_id in {*REQUIREMENTS, *ON_DEMAND_TOOLS}:
        profiles[agent_id] = AgentProfile(_READ, agent_tool_ids(agent_id))
    return profiles


_PROFILES: dict[str, AgentProfile] | None = None


def agent_profiles() -> dict[str, AgentProfile]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _derived_profiles()
    return _PROFILES


def agent_profile(agent_id: str) -> AgentProfile:
    return agent_profiles().get(agent_id, DEFAULT_AGENT_PROFILE)


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
