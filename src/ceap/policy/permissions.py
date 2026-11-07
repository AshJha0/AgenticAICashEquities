"""Role-based capabilities."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    TRADER = "trader"
    QUANT = "quant"
    ENGINEER = "engineer"
    APPROVER = "approver"
    ADMIN = "admin"


ROLE_CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"tools:read", "investigate:read", "research:read"}),
    Role.TRADER: frozenset({"tools:read", "investigate", "investigate:read", "research:read", "risk:medium"}),
    Role.QUANT: frozenset(
        {
            "tools:read",
            "investigate",
            "investigate:read",
            "research",
            "research:read",
            "risk:medium",
            "data:export",
        }
    ),
    Role.ENGINEER: frozenset(
        {"tools:read", "investigate", "investigate:read", "research:read", "engineering:deep"}
    ),
    # four-eyes (PLAN 1.3, D1): the only role that decides approvals; it cannot investigate or trade
    Role.APPROVER: frozenset({"tools:read", "investigate:read", "research:read", "approvals:decide"}),
    Role.ADMIN: frozenset(
        {
            "tools:read",
            "tools:write",
            "investigate",
            "investigate:read",
            "research",
            "research:read",
            "trading:execute",
            "risk:medium",
            "risk:high",
            "data:export",
            "engineering:deep",
            "tasks:cancel-any",
        }
    ),
}


def capabilities_for(roles: set[str] | frozenset[str] | list[str]) -> frozenset[str]:
    caps: set[str] = set()
    for r in roles:
        try:
            caps |= ROLE_CAPABILITIES[Role(r)]
        except ValueError:
            continue
    return frozenset(caps)
